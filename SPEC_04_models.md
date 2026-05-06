# SPEC_04 — 적정가 모델 & 분류기 & 포트폴리오 & 백테스트 엔진

> **관련 파일**: `backtest/models/rim.py`, `backtest/models/_skeleton.py`,
>   `backtest/classifier.py`, `backtest/portfolio.py`, `backtest/engine.py`
> **선행 조건**: SPEC_03 완료 (BacktestPipeline 동작 확인)
> **Claude Code 지시**:
>   1. `RIMModel`은 `ValuationModel` Protocol을 반드시 준수하라 (SPEC_03 interfaces.py 참조).
>   2. `classifier.py`는 Phase 2에서 skeleton만 작성하라. 함수 본체는 `raise NotImplementedError`.
>   3. 엔진 루프는 Phase 2 버전(분류기 미적용)으로 작성하라.
>      Phase 3 분류기 도입 시 엔진 루프를 수정하지 않아도 되도록 설계하라.
>   4. 상장폐지 청산은 반드시 3개 시나리오(낙관/기준/보수)를 병렬 실행하라.

---

# 7. RIM 모델 및 밸류에이션 필터

## 7-1. RIM 적정가 계산

v4.2에서 확정된 산식 그대로 유지. `stock-analysis/api/services/fair_value.py`와 동일 로직의 독립 구현.
v4.8에서 `RIMModel` 클래스로 래핑해 `ValuationModel` Protocol을 준수한다.

```python
# backtest/models/rim.py

RF, RK = 0.0263, 0.0873

class RIMModel:
    """
    ValuationModel Protocol 구현체.
    산식: stock-analysis fair_value.py와 동일. Dechow(1994) Method C, λ=0.5 고정.
    """
    name = 'RIM'

    def fair_value(
        self,
        ticker:   str,
        pit_data: dict,
        shares:   float,   # 주식수 (주 단위). market_cap_history에서 조회.
        beta:     float = 1.0,
    ) -> float | None:
        """
        RIM 적정가 (주당).

        adjROE = (0.5×NI + 0.5×CFO) / equity    Dechow(1994) Method C, λ=0.5 고정
        g      = adjROE × (1 - divPayout),  clamp [0, r×0.9]
        FV     = equity + equity × (adjROE - r) × g / (1+r-g)
        → 주당 = FV / shares
        """
        ni     = pit_data.get('당기순이익')
        cfo    = pit_data.get('영업활동현금흐름')
        equity = pit_data.get('자본총계')

        # 배당금지급 3분류 처리 (v4.7)  ← dividend_status: confirmed_zero | reported_positive | missing
        # - confirmed_zero  : DART에서 0 또는 음수(현금 유입)로 명시적으로 수집됨 → 실제 무배당
        # - reported_positive: 양수로 수집됨 → 실제 배당 지급
        # - missing         : 계정 자체가 없거나 매핑 실패 → 구분 불가
        #
        # missing 처리 원칙 (Phase 2):
        #   payout=0으로 처리하되 dividend_status='missing'을 pit_data에 기록.
        #   Phase 4 민감도 테스트에서 missing 종목 제외 시나리오와 성과 비교.
        #   제외하지 않는 이유: KOSDAQ 소형주 상당수가 missing → 제외 시 소형주 편향 발생.
        #   payout=0이면 g = adjROE (최대), clamp [0, r×0.9]로 상한 고정 → FV 낙관적 편향 허용.
        div_raw = pit_data.get('배당금지급')
        if div_raw is None:
            dividend_status = 'missing'
            import logging
            logging.info(f'[RIM] {ticker} 배당금지급 missing — payout=0 적용 (낙관적 편향).')
        elif float(div_raw) > 0:
            dividend_status = 'reported_positive'
        else:
            dividend_status = 'confirmed_zero'
        div = float(div_raw) if div_raw is not None else 0.0

        if None in (ni, cfo, equity) or equity <= 0 or shares <= 0:
            return None

        r       = RF + beta * (RK - RF)
        adj_roe = (0.5 * ni + 0.5 * cfo) / equity
        payout  = abs(div) / max(abs(ni), 1) if ni != 0 else 0
        g       = max(0.0, min(adj_roe * (1 - payout), r * 0.9))

        if abs(1 + r - g) < 1e-6:
            return None

        fv_total = equity + equity * (adj_roe - r) * g / (1 + r - g)
        return fv_total / shares


# ── 멀티모델 skeleton (Phase 5 이후 models/ 하위에 별도 파일로 구현) ─────────
# backtest/models/ev_sales.py   → EVSalesModel  (GROWTH/TURNAROUND 타입)
# backtest/models/peer_per.py   → PeerPERModel  (STABLE 보조)
# backtest/models/fcff.py       → FCFFModel     (LEVERAGED 타입)
# backtest/models/nav.py        → NAVModel      (ASSET 타입)
# backtest/models/ensemble.py   → EnsembleModel (Phase 5 멀티모델 조합)
```

## 7-2. 밸류에이션 필터 및 랭킹 (v4.8 통합)

v4.8에서 `valuation_filter()`와 `score_and_rank()`는 `BacktestPipeline.score_and_rank()`로 통합됐다.
`scorer.py`는 리밸런싱 리포트 생성 유틸리티만 남긴다.

**통합 위치**: `backtest/pipeline.py` → `BacktestPipeline.score_and_rank()` (§6-5 참조)

**설계 원칙 (변경 없음)**:
- 밸류에이션 필터: 현재가 > RIM적정가 × (1 + rim_threshold) 이면 제외
- rim_threshold 초기값 0.05 (Bayesian 튜닝 범위: [-0.10, +0.20])
- 포트폴리오 정렬: upside% 내림차순 (저평가 강도 순)
- 매도 우선순위: upside% 낮은 순 (가장 고평가 종목 먼저)
- 적정가 모델: `BacktestPipeline.model` (Phase 2 = `RIMModel`, Phase 5 = `EnsembleModel` 교체 가능)

---

# 8. 기업 분류기 (`backtest/classifier.py`)

Phase 2에서는 분류기를 사용하지 않고 전종목에 RIM을 동일하게 적용한다.
Phase 3에서 분류기를 도입하며, 이 파일은 Phase 2 구현 시 skeleton만 작성하고 비워둔다.

## 8-1. 타입 정의 (Phase 3 이후 활성화)

| 타입 | 핵심 특성 | Phase 2에서의 취급 |
|------|----------|-----------------|
| STABLE | 매출 안정, 배당 지속, 저성장 | RIM 적용 (이 타입이 RIM 적합) |
| GROWTH | 고성장, 저배당 | RIM 적용 (결과 왜곡 가능, Phase 3에서 분리) |
| CYCLICAL | 업황 의존, 이익 변동성 높음 | RIM 적용 (단, 매출 역성장 필터에서 상당수 제외됨) |
| ASSET | 자산 대비 저평가 | RIM 적용 |
| LEVERAGED | 업종 조정 부채비율 초과 | 부채비율 필터에서 상당수 제외됨 |
| FINANCIAL | 금융업 특수 구조 | DQ Gate에서 제외 |
| TURNAROUND | 적자 → 흑자 전환 | 영업CF 필터에서 상당수 제외됨 |

## 8-2. 분류 로직 (v4.2 그대로 — Phase 3 활성화 시 사용)

(v4.2 §7-5~§7-8 전체 내용 유지. Phase 2 구현 시 이 섹션은 비활성 상태)

---

# 9. 포트폴리오 구성 (`backtest/portfolio.py`)

## 9-1. 구성 규칙

| 항목 | 기준 |
|------|------|
| 가중 방식 | 동일가중 (초기 버전) |
| 종목 수 | 20개 (Bayesian 튜닝 대상: 10~30) |
| 종목당 최대 비중 | 5% |
| 업종 최대 비중 | 25% |
| KOSDAQ 최대 비중 | 60% |
| AUM 가정 | 5억원 |
| 주문 규모 제한 | 종목당 주문금액 > 20일 평균 거래대금 × 10% 시 편입 제외 |
| 종목 미달 시 | 20개 미만이면 충족 종목 수만큼만 편입, 현금 보유 허용 |
| 거래정지 발생 시 | 다음 리밸런싱까지 보유 유지, 청산 불가 포지션으로 별도 기록 |
| 상장폐지 청산 | 낙관/기준(종가×70%)/보수(100%손실) 3개 시나리오 병렬. 기준을 메인 지표로 사용 |
| 매도 우선순위 | upside% 낮은 순 (가장 고평가된 종목 먼저 매도) |

## 9-2. 거래비용 모델

```python
# ── 거래세 (매도 시에만 발생) ────────────────────────────────────────────────
# KOSPI:  증권거래세 0.18% + 농어촌특별세 0.15% = 0.33%
# KOSDAQ: 증권거래세 0.18% (농특세 없음)
TAX_KOSPI   = 0.0033
TAX_KOSDAQ  = 0.0018

# ── 수수료 (매수·매도 각각 발생) ─────────────────────────────────────────────
COMMISSION  = 0.0015   # 0.15% (온라인 증권사 기준)

# ── 슬리피지 ─────────────────────────────────────────────────────────────────
SLIPPAGE    = 0.002    # 0.2% 고정 (Phase 2 단순화)
# [향후 검토] Phase 3+: 주문금액/일평균거래대금 비율의 함수로 모델링
#   예: 비율 5% 이하 → 0.1%, 10% 이하 → 0.2%, 초과 → 0.5%

# ── 편도 총비용 (시장별) ─────────────────────────────────────────────────────
# KOSPI  매수: COMMISSION + SLIPPAGE = 0.35%
#        매도: COMMISSION + TAX_KOSPI + SLIPPAGE = 0.68%
# KOSDAQ 매수: COMMISSION + SLIPPAGE = 0.35%
#        매도: COMMISSION + TAX_KOSDAQ + SLIPPAGE = 0.53%
def total_cost(market: str, side: str) -> float:
    tax = (TAX_KOSPI if market == 'KOSPI' else TAX_KOSDAQ) if side == 'sell' else 0.0
    return COMMISSION + tax + SLIPPAGE
```

---

# 10. 백테스트 엔진 (`backtest/engine.py`)

## 10-1. 리밸런싱 날짜

- 상반기: 사업보고서 법정 마감(3월 31일) + 3 영업일
- 하반기: 반기보고서 법정 마감(8월 14일) + 3 영업일
- 백테스트 구간: 2015년 상반기 ~ 2026년 상반기 (21개 구간)

```python
from pykrx import stock as krx

def nth_trading_day_after(base: date, n: int) -> date:
    d, count = base + timedelta(days=1), 0
    while True:
        cal = krx.get_index_ohlcv_by_date(
            d.strftime('%Y%m01'), d.strftime('%Y%m%d'), 'KOSPI'
        )
        if d in [idx.date() for idx in cal.index]:
            count += 1
            if count == n: return d
        d += timedelta(days=1)
        if (d - base).days > 30:
            raise ValueError(f'영업일 탐색 실패: {base}')

rebalance_dates = []
for yr in range(2015, 2027):
    rebalance_dates.append(nth_trading_day_after(date(yr, 3, 31), 3))
    if yr < 2026:
        rebalance_dates.append(nth_trading_day_after(date(yr, 8, 14), 3))
rebalance_dates.sort()
```

## 10-2. 루프 구조 (Phase 2 버전 — 분류기 미적용)

```python
# backtest/engine.py (Phase 2)

for i, rebalance_date in enumerate(rebalance_dates):
    next_date = rebalance_dates[i + 1] if i + 1 < len(rebalance_dates) else None

    pit_data  = load_pit_data(rebalance_date)     # available_from <= rebalance_date
    pit_prev  = load_pit_data_prev(rebalance_date)
    gate_passed = load_gate_passed_tickers()

    # 4단계 유니버스 구성
    result = build_universe(gate_passed, rebalance_date, pit_data, pit_prev)
    universe = result['universe']
    log_universe_stats(rebalance_date, result['stats'])

    # RIM 적정가 계산 + 밸류에이션 필터
    ranked = score_and_rank(universe, rebalance_date, pit_data,
                            rim_threshold=params['rim_threshold'])

    # 포트폴리오 구성
    portfolio = build_portfolio(ranked, n_stocks=params['n_stocks'])

    if next_date:
        record_performance(portfolio, rebalance_date, next_date)
```

---

