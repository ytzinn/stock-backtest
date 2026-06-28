# SPEC_04 — 적정가 모델 & 분류기 & 포트폴리오 & 백테스트 엔진

> **관련 파일**: `backtest/models/rim.py`, `backtest/portfolio.py`, `backtest/engine.py`,
>   `backtest/ablation.py`, `backtest/metrics.py`
> **선행 조건**: SPEC_03 완료 (BacktestPipeline 동작 확인)
> **Phase 2 완료 (2026-06-21)**: 13개 Ablation 시나리오 전체 실행. 결과: MASTER.md §Phase 2 결과.
> **Claude Code 지시**:
>   1. `RIMModel`은 `ValuationModel` Protocol을 반드시 준수하라 (SPEC_03 interfaces.py 참조).
>   2. Phase 3: `classifier.py` 활성화. Phase 2에서는 모든 종목에 RIM 동일 적용.
>   3. 엔진 루프는 Phase 2 버전(분류기 미적용)으로 구현됨. Phase 3 분류기 도입 시 최소 수정으로 교체 가능.
>   4. 상장폐지 청산은 반드시 3개 시나리오(낙관/기준/보수)를 병렬 실행하라.

> **v4.9 변경사항 (SPEC_03과 연동):**
>   - `UniverseFilter.apply()` 시그니처: `pit_series: dict[str, list[dict]]` 통일 (`[0]`=현재, `[1]`=t-1, `[2]`=t-2). `HardFilter`·`MomentumFilter`는 `[0]`만 사용, `StabilityFilter`는 `[0][1][2]` 모두 활용.
>   - `RIMModel.__init__(beta_adj: float = 0.0)` 추가: r 오프셋 파라미터 (§7-1 참조).
>   - `fv_total ≤ 0` 방어 처리 추가 (§7-1 참조).
>   - `configs/rebalance_dates.py` 신규: 23개 날짜 하드코딩 (2015-04·08 TTM 미충족 빈 구간 포함, 유효 21개). `price_history` DISTINCT date(대표 KOSPI 종목 기준)로 생성.
>   - `backtest/data_access.py` 신규: DB 조회 헬퍼 집중 (`get_adj_close_range`, `get_shares_outstanding`, `get_market_cap` 등). 필터마다 직접 DB 접속 대체.

---

# 7. RIM 모델 및 밸류에이션 필터

## 7-1. RIM 적정가 계산

v4.2에서 확정된 산식 그대로 유지. `stock-analysis/api/services/fair_value.py`와 동일 로직의 독립 구현.
v4.8에서 `RIMModel` 클래스로 래핑해 `ValuationModel` Protocol을 준수한다.

```python
# backtest/models/rim.py
"""
RIM (Residual Income Model) 적정가 모델.

adjROE = (0.5×NI + 0.5×CFO) / equity — Dechow(1994) Method C, λ=0.5
g      = adjROE × (1 - payout), clamp [0, r×0.9]
         ↳ 상한 r×0.9: 분모 (1+r-g) 발산 방지 수학적 안전장치. 고정값, 튜닝 제외.
FV     = equity + equity × (adjROE - r) × g / (1+r-g)
FV_per_share = FV / shares

equity 우선순위: 지배기업소유주지분 > 자본총계
  CFS(연결)에서 자본총계는 비지배지분을 포함하므로 지배주주 기준 적정가를 산출하려면
  지배기업소유주지분을 먼저 사용한다. OFS(개별) 또는 비지배지분=0인 법인은 동일값.

payout=0 가정: KOSDAQ 소형주 배당 데이터 누락 다수 → 낙관적 편향 허용 (§3-1).
β=1.0 고정: Phase 2~4. Phase 3 이후 rolling β 도입 검토.
beta_adj: r 오프셋 [-0.02, +0.02]. β=1.0 고정 유지하면서 r 수준만 미세 조정.

constants: RF, RK — stability_filter.py와 반드시 동일 값 유지.
"""
from __future__ import annotations

RF, RK = 0.0263, 0.0873   # stock-analysis 기존값 유지


class RIMModel:
    """ValuationModel Protocol 구현체."""

    name = 'RIM'

    def __init__(self, beta_adj: float = 0.0):
        # beta_adj: r 오프셋 [-0.02, +0.02]. β=1.0 고정 유지하면서 r 수준만 미세 조정.
        # beta_adj < 0 → r 낙관적(할인율 낮음) → 적정가 상승
        # beta_adj > 0 → r 보수적 → 적정가 하락
        self.beta_adj = beta_adj

    def fair_value(
        self,
        ticker:   str,
        pit_data: dict,
        shares:   float,   # 주식수 (주 단위). market_cap_history에서 조회.
        beta:     float = 1.0,
    ) -> float | None:
        """
        주당 적정가(KRW) 반환. 계산 불가 시 None.

        산식 (stock-analysis fair_value.py 동일):
          equity = 지배기업소유주지분 (없으면 자본총계 fallback)
          adjROE = (0.5×NI + 0.5×CFO) / equity   Dechow(1994) Method C
          g      = adjROE × (1 - payout), clamp [0, r×0.9]
          FV     = equity + equity × (adjROE - r) × g / (1+r-g)
          FV_per_share = FV / shares

        배당금지급 missing → payout=0 (낙관적 편향 허용, KOSDAQ 소형주 누락 다수).
        beta_adj: r 오프셋 [-0.02, +0.02]. β=1.0 고정.
        fv_total ≤ 0: None 반환. R6로 대부분 걸러지지만 PIT 타이밍 불일치 방어.
        """
        import logging

        ni     = pit_data.get('당기순이익')
        cfo    = pit_data.get('영업활동현금흐름')
        equity = pit_data.get('지배기업소유주지분') or pit_data.get('자본총계')

        if None in (ni, cfo, equity) or equity <= 0 or (shares or 0) <= 0:
            return None

        r = RF + beta * (RK - RF) + self.beta_adj
        if r <= 0:
            return None

        adj_roe = (0.5 * ni + 0.5 * cfo) / equity

        # 배당금지급 3분류 처리 (v4.7): confirmed_zero | reported_positive | missing
        # missing → payout=0 (낙관적 편향). Phase 4 민감도 테스트에서 missing 제외 시나리오 비교.
        # 제외하지 않는 이유: KOSDAQ 소형주 상당수가 missing → 제외 시 소형주 편향 발생.
        div_raw = pit_data.get('배당금지급')
        if div_raw is None:
            logging.debug(f'[RIM] {ticker} 배당금지급 missing — payout=0 적용')
            div = 0.0
        else:
            div = float(div_raw)

        payout = abs(div) / max(abs(ni), 1) if ni != 0 else 0.0
        g      = max(0.0, min(adj_roe * (1 - payout), r * 0.9))

        denom = 1 + r - g
        if abs(denom) < 1e-6:
            return None

        fv_total = equity + equity * (adj_roe - r) * g / denom
        if fv_total <= 0:
            return None   # 방어 처리: R6 후에도 PIT 타이밍 불일치로 발생 가능

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
- 백테스트 구간: 2015년 상반기 ~ 2026년 상반기 (**23개 날짜**)
  - **TTM 제약**: 2015-04-03·2015-08-19 두 날짜는 FY2014/H1_2014 PIT 미충족 → 유니버스 0개 (빈 구간).
  - **유효 포트폴리오 구간: 21개** (2016-04-05 ~ 2026-04-03).
- **재현성 보장**: 23개 날짜는 `configs/rebalance_dates.py`에 하드코딩. 매 실행마다 동일 결과 보장.

**생성 방법 (v4.9)**: pykrx `get_index_ohlcv_by_date('KOSPI')`는 KRX 리뉴얼 이후 KeyError 반환.
대신 `price_history` DISTINCT date를 영업일 캘린더로 사용 (대표 KOSPI 종목 기준, 예: 삼성전자 005930).

```python
# scripts/generate_rebalance_dates.py (1회 실행 후 configs/rebalance_dates.py에 하드코딩)

from datetime import date
import psycopg2
from ingest.connection import get_conn

def get_trading_days(year: int, month: int) -> list[date]:
    """price_history에서 삼성전자 거래일 조회 → KOSPI 영업일 대용."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT date FROM price_history
                WHERE ticker = '005930'
                  AND date >= %s AND date <= %s
                ORDER BY date
            """, (date(year, month, 1), date(year, month + 1 if month < 12 else 1,
                                             1 if month < 12 else 31)))
            return [row[0] for row in cur.fetchall()]

def nth_trading_day_after(base: date, n: int) -> date:
    """base 날짜 이후 n번째 거래일 반환."""
    month = base.month
    year  = base.year
    # 해당 월 + 다음 월 조회로 충분
    trading_days = get_trading_days(year, month) + get_trading_days(
        year if month < 12 else year + 1,
        month + 1 if month < 12 else 1
    )
    after = [d for d in trading_days if d > base]
    if len(after) < n:
        raise ValueError(f'영업일 부족: {base}, n={n}')
    return after[n - 1]

# 실행 결과를 configs/rebalance_dates.py에 복사 후 하드코딩
REBALANCE_DATES = []
for yr in range(2015, 2027):
    REBALANCE_DATES.append(nth_trading_day_after(date(yr, 3, 31), 3))
    if yr < 2026:
        REBALANCE_DATES.append(nth_trading_day_after(date(yr, 8, 14), 3))
REBALANCE_DATES.sort()
# 총 23개 생성됨 (2015-04~2026-04).
# 2015-04-03·2015-08-19는 TTM PIT 미충족으로 실행 시 gate=0 (빈 구간).
# 유효 포트폴리오 구간: 21개 (2016-04-05 ~ 2026-04-03).
```

```python
# backtest/configs/rebalance_dates.py  ← 하드코딩 (재현성 보장)
from datetime import date

REBALANCE_DATES = [
    # 2015~2026 상반기, 23개 날짜 (생성 스크립트로 1회 계산 후 고정)
    # ※ 2015-04-03·2015-08-19는 TTM 미충족 빈 구간. 유효 21개는 2016-04-05~2026-04-03.
    # 예: date(2015, 4, 3), date(2015, 8, 19), ...
    # 실제 날짜는 scripts/generate_rebalance_dates.py 실행 결과 사용
]
```

## 10-2. 루프 구조 (Phase 2 버전 — 분류기 미적용)

```python
# backtest/engine.py (Phase 2)

for i, rebalance_date in enumerate(rebalance_dates):
    next_date = rebalance_dates[i + 1] if i + 1 < len(rebalance_dates) else None

    # pit_series: {ticker: [현재 FY, t-1 FY, t-2 FY]}
    # available_from <= rebalance_date 조건 필수 (룩어헤드 방지)
    # [0]=현재, [1]=t-1, [2]=t-2 — StabilityFilter가 [0][1][2] 모두 사용
    pit_series  = load_pit_series(rebalance_date, n_years=3)
    gate_passed = load_gate_passed_tickers()

    # 4단계 유니버스 구성 (BacktestPipeline 사용, v4.8 이후)
    result   = pipeline.build_universe(gate_passed, rebalance_date, pit_series)
    universe = result['universe']
    log_universe_stats(rebalance_date, result['stats'])

    # RIM 적정가 계산 + 밸류에이션 필터 (현재 시점 PIT만 사용)
    pit_current = {t: pit_series[t][0] for t in universe if pit_series.get(t)}
    ranked = pipeline.score_and_rank(universe, rebalance_date, pit_current)

    # 포트폴리오 구성
    portfolio = build_portfolio(ranked, n_stocks=params['n_stocks'])

    if next_date:
        record_performance(portfolio, rebalance_date, next_date)
```

**`load_pit_series_ttm()` 설계 (`backtest/data_access.py`, v5.0 구현):**
- FY 리밸런싱(4월): `load_pit_series(n_years=3)` 그대로 반환
- H1 리밸런싱(8월): TTM = FY_prev − H1_prev + H1_curr (IS/CF 계정만, BS는 H1_curr)
- 반환 타입: `dict[str, list[dict]]` — `{ticker: [pit_t0, pit_t1, pit_t2]}`
- XBRL 정정 반영: `amendment_from <= rebalance_date`이면 정정값, 미공개면 `original_amount`
- `conn` 주입 패턴: 커넥션을 외부에서 받아 함수 내부에서 처리 (테스트 가능성 확보)

---

