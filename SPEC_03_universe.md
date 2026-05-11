# SPEC_03 — Universe 구성 & 모듈화 파이프라인

> **관련 파일**: `backtest/interfaces.py`, `backtest/data_access.py`, `backtest/pipeline.py`,
>   `backtest/filters/hard_filter.py`, `backtest/filters/stability_filter.py`,
>   `backtest/filters/factor_screener.py`, `backtest/filters/momentum_filter.py`,
>   `backtest/configs/phase2_rim.py`, `backtest/configs/rebalance_dates.py`
> **선행 조건**: SPEC_02 완료 (DB에 샘플 데이터 적재 확인)
> **Claude Code 지시**:
>   1. `interfaces.py`를 가장 먼저 작성하라. 이후 모든 필터/모델은 이 Protocol을 준수해야 한다.
>   2. `data_access.py`를 두 번째로 작성하라. 필터가 DB를 직접 조회하지 않도록 이 모듈만 import한다.
>   3. 각 필터는 독립 파일로 작성하라. `universe.py` 단일 파일에 몰아 넣지 말 것.
>   4. 모든 필터는 생성자에서 파라미터를 주입받고, `apply(tickers, rebalance_date, pit_series)` 메서드를 구현한다.
>   5. `BacktestPipeline`은 필터 목록·모델을 생성자 주입으로 받아야 한다. 내부 하드코딩 금지.
>   6. Phase 2 파이프라인 조립은 `configs/phase2_rim.py`에서만 한다.
>   7. `pit_series[ticker] = [현재FY, t-1FY, t-2FY]` 인덱스 규칙을 전체에서 일관되게 유지한다.

---

# 6. Universe 구성 — 4단계 필터 구조

v4.3에서 Universe 구성은 다음 4단계를 순서대로 적용한다.
단계별로 탈락 종목 수를 기록해 리밸런싱 리포트에 포함한다.

```
[영구제외(stocks.is_excluded=FALSE) + DQ Gate PASS(universe_gate_pit) + 실제 상장(stock_listing_events) 종목]
    │
    ▼ Step 1: Hard Filter (filters/hard_filter.py)
    │  거래유동성 + 상장기간 + PIT 존재 + R06~R08 시점 기준 필터
    │
    ▼ Step 2: 재무안정성 필터 (filters/stability_filter.py)    ← v4.3 신규
    │  부채비율, 차입금비율, 회전율, 영업CF 기준
    │
    ▼ Step 3: 팩터 스크리닝 (filters/factor_screener.py)      ← v4.3 신규
    │  매출YoY + 영업이익YoY + GP/A + 1/PBR → 상위 20%
    │
    ▼ Step 4: 모멘텀 필터 (filters/momentum_filter.py)
       MA20/MA60 이중 조건 — 하락 추세 제외
```

## 6-1. Step 1 — Hard Filter

> **클래스 구조**: `HardFilter`, `StabilityFilter`, `FactorScreener`, `MomentumFilter` 모두
> 동일한 패턴을 따른다. 생성자(`__init__`)에서 파라미터를 주입받고, `apply(tickers, rebalance_date, pit_series)`
> 메서드에서 종목별로 내부 로직 함수(`_hard_filter`, `_financial_stability_filter` 등)를 호출해
> 통과/탈락을 분류한 뒤 `(passed_list, rejected_dict)`를 반환한다.
>
> `pit_series[ticker]` 인덱스 규칙: `[0]`=현재 FY, `[1]`=t-1 FY, `[2]`=t-2 FY.
> DB 조회 헬퍼는 모두 `backtest/data_access.py`에서 import한다.

```python
# backtest/filters/hard_filter.py
# HardFilter.apply() 내부에서 호출되는 단일 종목 로직:
    ticker: str,
    rebalance_date: date,
    pit_series_for_ticker: list[dict],
    conn,
    min_turnover: float = 100_000_000,   # 일평균 거래대금 1억원
    min_listed_months: int = 6,
) -> tuple[bool, str]:
    """
    True = 통과, False = 제외.
    반환: (pass_flag, reason)
    """
    # 거래유동성
    if get_avg_turnover(ticker, rebalance_date, 20) < min_turnover:
        return False, '거래대금 부족'

    # 상장기간
    ld = get_listed_date(ticker)
    if ld and (rebalance_date - ld).days < min_listed_months * 30:
        return False, '상장 6개월 미만'

    # PIT 데이터 존재
    if not has_pit_fy_data(ticker, rebalance_date):
        return False, 'PIT FY 데이터 없음'

    # R06: 감사의견 비적정·한정 (시점 기준)
    if has_adverse_audit_opinion(ticker, rebalance_date):
        return False, '감사의견 비적정·한정'

    # R07: 상장폐지 사유 이력 (시점 기준)
    if is_delisted_at(ticker, rebalance_date):
        return False, '상장폐지'

    # R08: 관리종목 지정 (시점 기준)
    if is_under_admin_at(ticker, rebalance_date):
        return False, '관리종목'

    return True, ''
```

## 6-2. Step 2 — 재무안정성 필터 (v4.3 신규)

**설계 원칙**: 가치 함정(Value Trap) 중에서도 재무 구조적 위험이 명확한 종목을 선제 제거.
하드 룰 6개 + 참고 플래그 2개로 구성. 하드 룰은 Bayesian 튜닝 대상에서 제외.

```python
# backtest/filters/stability_filter.py

RF, RK = 0.0263, 0.0873   # CAPM 상수 — RIM 모델(models/rim.py)과 동일 값 유지


class StabilityFilter:
    """UniverseFilter Protocol 구현체. 생성자로 파라미터 주입."""

    def __init__(self, r2_exception: bool = True):
        self.r2_exception = r2_exception

    def apply(
        self,
        tickers:        list[str],
        rebalance_date: date,
        pit_series:     dict[str, list[dict]],
    ) -> tuple[list[str], dict]:
        passed, rejected = [], {}
        for t in tickers:
            series  = pit_series.get(t, [])
            pit0    = series[0] if len(series) > 0 else {}
            pit1    = series[1] if len(series) > 1 else None
            pit2    = series[2] if len(series) > 2 else None
            ok, reasons = _financial_stability_filter(t, rebalance_date, pit0, pit1, pit2)
            if ok:
                passed.append(t)
            else:
                rejected[t] = reasons
        return passed, rejected


def _financial_stability_filter(
    ticker: str,
    rebalance_date: date,
    pit_data: dict,
    pit_prev: dict | None,
    pit_2y_ago: dict | None,
) -> tuple[bool, list[str]]:
    """
    True = 통과, False = 제외.
    반환: (pass_flag, fail_reasons)

    pit_data:    pit_series[ticker][0] — 최신 FY
    pit_prev:    pit_series[ticker][1] — t-1 FY
    pit_2y_ago:  pit_series[ticker][2] — t-2 FY
    """
    fails = []

    # ── 하드 룰 (Bayesian 튜닝 제외) ──────────────────────────────────────

    # [R1] 부채비율 > 200%
    # 근거: 한국 제조업 평균 100~120%. 200% 초과는 재무 충격 흡수력 극히 낮음.
    # 금융업은 DQ Gate에서 is_financial=TRUE로 이미 처리됨.
    debt   = pit_data.get('부채총계', 0) or 0
    equity = pit_data.get('자본총계', 1) or 1
    if equity > 0 and (debt / equity) > 2.0:
        fails.append('부채비율 > 200%')

    # [R2] 차입금비율 > 150% (단, 3년 단조 감소 + 누적 10%p 이상 개선 시 예외)
    # 근거: 금융성 부채만 측정. 150% 초과는 차입 의존도 극단적.
    # 예외 근거: 차입금이 실제로 줄고 있다면 현금흐름으로 부채 상환 중 → 직접 개선 증거.
    #
    # 예외 조건 (두 가지 동시 충족):
    #   1. 최근 3 FY 차입금비율이 단조 감소 (ratio[t-2] > ratio[t-1] > ratio[t])
    #   2. 3년간 누적 감소폭 >= 10%p (ratio[t-2] - ratio[t] >= 0.10)
    #      데이터가 2개 FY만 있으면: ratio[t-1] > ratio[t] AND 감소폭 >= 10%p
    #      데이터가 1개 이하이면: 트렌드 판단 불가 → 예외 없이 제외
    #
    # [향후 검토] 감소폭 하한 10%p는 Phase 2 결과 확인 후 Bayesian 튜닝 대상 추가 가능.
    #             현재는 파라미터 10개 상한 유지를 위해 하드 룰로 고정.
    borrowings = sum(pit_data.get(k, 0) or 0 for k in
                     ['단기차입금', '유동성장기부채', '장기차입금', '사채'])

    def _borrow_ratio(pit: dict) -> float | None:
        eq = pit.get('자본총계', 0) or 0
        if eq <= 0:
            return None
        br = sum(pit.get(k, 0) or 0 for k in
                 ['단기차입금', '유동성장기부채', '장기차입금', '사채'])
        return br / eq

    if equity > 0 and (borrowings / equity) > 1.5:
        # 트렌드 예외 판단
        br_series = [_borrow_ratio(p) for p in
                     [d for d in [pit_2y_ago, pit_prev, pit_data] if d is not None]]
        br_series = [r for r in br_series if r is not None]

        trend_ok = False
        if len(br_series) >= 2:
            monotonic = all(br_series[i] > br_series[i+1]
                            for i in range(len(br_series) - 1))
            drop_ok   = (br_series[0] - br_series[-1]) >= 0.10   # 누적 10%p 이상 감소
            trend_ok  = monotonic and drop_ok

        if not trend_ok:
            fails.append('차입금비율 > 150% (개선 추세 없음)')

    # [R3] 매출 역성장 — 최근 3 FY 중 2회 이상 YoY < -5%
    # 근거: CYCLICAL 섹터는 Phase 5 이후 별도 모델 적용 예정.
    #       현 단계에서는 매출 연속성이 RIM 모델 신뢰도와 직결됨.
    # 1회(-5% 미만) 역성장은 일시적 사업 재편·외부 충격으로 허용.
    # 2회 이상이면 구조적 매출 훼손으로 판단해 제외.
    rev_series = get_revenue_series_pit(ticker, rebalance_date, years=3)
    if len(rev_series) >= 2:
        yoy_list = [(rev_series[i] / rev_series[i-1]) - 1
                    for i in range(1, len(rev_series))
                    if rev_series[i-1] and rev_series[i-1] != 0]
        if sum(1 for yoy in yoy_list if yoy < -0.05) >= 2:
            fails.append('최근 3FY 내 매출 -5% 이상 역성장 2회 이상')

    # [R4] 영업CF 2년 연속 음수
    # 근거: 본업이 2년 연속 현금을 못 씀 → 구조적 수익성 문제.
    cfo_cur  = pit_data.get('영업활동현금흐름')
    cfo_prev = pit_prev.get('영업활동현금흐름') if pit_prev else None
    if cfo_cur is not None and cfo_prev is not None:
        if cfo_cur < 0 and cfo_prev < 0:
            fails.append('영업CF 2년 연속 음수')

    # [R5] 영업CF < 0 AND 재무CF > 0 (차입으로 운영)
    # 근거: 본업이 현금을 못 내면서 차입으로 운영 → 즉각적 위험 신호.
    fin_cf = pit_data.get('재무활동현금흐름')
    if cfo_cur is not None and fin_cf is not None:
        if cfo_cur < 0 and fin_cf > 0:
            fails.append('영업CF(-) + 재무CF(+): 차입 운영')

    # [R6] adjROE < 종목별 요구수익률 r (RIM 기준 가치 파괴 구간)
    # 근거: adjROE < r이면 RIM 산식에서 (adjROE - r) < 0 → FV < equity.
    #       PBR이 낮아 저평가처럼 보이지만 수익성이 자본비용 미달인 가치 함정 선제 제거.
    #       밸류에이션 필터(현재가 > RIM적정가 × 1.05)는 PBR < 1 케이스를 못 잡으므로 별도 필요.
    # adjROE 정의: (0.5×NI + 0.5×CFO) / equity — RIM 내부 계산과 동일 기준 (Dechow 1994).
    #
    # [β 처리] Phase 2에서는 β=1.0 고정 → r ≈ 6.73%.
    #   근거: 설계서 어디에도 get_beta() 구현 없음. 기존 stock-analysis도 β를 API
    #         파라미터로 외부 입력받는 구조로, 자동 수집/계산 로직 미정의.
    #         소형주 β 추정은 거래량 부족으로 불안정한 경우가 많아 고정값이 오히려 안정적.
    #   [향후 검토] Phase 3 이후 옵션 비교:
    #     옵션 A — price_history adj_close로 rolling 52주 KOSPI 회귀 β 직접 계산 (PIT 준수)
    #     옵션 B — pykrx get_market_fundamental_by_date() β 수집 (로컬 전용, 과거 시계열 부담)
    #   두 옵션 도입 시 β=1.0 고정 대비 성과 차이를 별도 실험으로 비교 후 채택 여부 결정.
    # 파라미터 없음 → Phase 2 파라미터 10개 상한 영향 없음.
    ni_r6 = pit_data.get('당기순이익')
    if ni_r6 is not None and cfo_cur is not None and equity > 0:
        adj_roe = (0.5 * ni_r6 + 0.5 * cfo_cur) / equity
        beta    = 1.0   # Phase 2 고정값. 향후 검토 메모 위 주석 참고.
        r       = RF + beta * (RK - RF)
        if adj_roe < r:
            fails.append(f'adjROE({adj_roe:.1%}) < 요구수익률({r:.1%}): RIM 적정가 < 장부가')

    # ── 참고 플래그 (제외 아닌 감점·기록) ──────────────────────────────────

    flags = []

    # [F1] 재고자산 회전율 전년 대비 30% 이상 하락
    # 재고 없는 업종(서비스) 자동 스킵
    rev_cur  = pit_data.get('매출액', 0) or 0
    inv_cur  = pit_data.get('재고자산')
    inv_prev = pit_prev.get('재고자산') if pit_prev else None
    if inv_cur and inv_prev and inv_prev > 0 and rev_cur > 0:
        inv_prev_rev = (pit_prev.get('매출액', 0) or 0)
        turnover_cur  = rev_cur  / inv_cur  if inv_cur  > 0 else None
        turnover_prev = inv_prev_rev / inv_prev if inv_prev > 0 else None
        if turnover_cur and turnover_prev and turnover_prev > 0:
            if (turnover_cur - turnover_prev) / turnover_prev < -0.30:
                flags.append('재고자산회전율 -30% 이상')

    # [F2] 매출채권 회전율 전년 대비 30% 이상 하락
    ar_cur  = pit_data.get('매출채권')
    ar_prev = pit_prev.get('매출채권') if pit_prev else None
    if ar_cur and ar_prev and ar_prev > 0 and rev_cur > 0:
        ar_prev_rev  = (pit_prev.get('매출액', 0) or 0)
        ar_turnover_cur  = rev_cur / ar_cur  if ar_cur  > 0 else None
        ar_turnover_prev = ar_prev_rev / ar_prev if ar_prev > 0 else None
        if ar_turnover_cur and ar_turnover_prev and ar_turnover_prev > 0:
            if (ar_turnover_cur - ar_turnover_prev) / ar_turnover_prev < -0.30:
                flags.append('매출채권회전율 -30% 이상')

    return (len(fails) == 0), fails
```

**재무안정성 필터 기준 요약표:**

| 지표 | 기준 | 분류 | 향후 튜닝 |
|------|------|------|----------|
| 부채비율 | > 200% | 하드 룰 (제외) | 아니오 |
| 차입금비율 | > 150% (단, 3년 단조 감소 + 누적 10%p 이상이면 통과) | 하드 룰 (제외) | 아니오 (감소폭 하한은 향후 검토) |
| 매출 역성장 | 최근 3FY 중 2회 이상 YoY < -5% | 하드 룰 (제외) | 아니오 |
| 영업CF | 2년 연속 음수 | 하드 룰 (제외) | 아니오 |
| 영업CF(-) + 재무CF(+) | 1회 발생 | 하드 룰 (제외) | 아니오 |
| adjROE vs 요구수익률 | adjROE < r (종목별 β 기반, fallback β=1.0) | 하드 룰 (제외) | 아니오 (파라미터 없음) |
| 재고자산 회전율 | 전년비 -30% 이상 하락 | 참고 플래그 | Phase 3+ |
| 매출채권 회전율 | 전년비 -30% 이상 하락 | 참고 플래그 | Phase 3+ |

## 6-3. Step 3 — 팩터 스크리닝 (v4.3 신규)

**목적**: 전체 유니버스에서 펀더멘털 퀄리티 상위 종목을 먼저 선별해 RIM 계산 부하를 줄이고, 스크리닝 자체가 독립 Alpha 인자로 작동하는지 Ablation Test로 검증.

```python
# backtest/filters/factor_screener.py

class FactorScreener:
    """UniverseFilter Protocol 구현체."""

    def __init__(
        self,
        weights: dict | None = None,   # None이면 동일가중
        top_pct: float = 0.20,         # 상위 20% (Bayesian 튜닝 대상: 10~40%)
    ):
        self.weights = weights or {
            'rev_yoy':  1/6,
            'op_yoy':   1/6,
            'gpa':      1/3,
            'inv_pbr':  1/3,
        }
        self.top_pct = top_pct

    def apply(
        self,
        tickers:        list[str],
        rebalance_date: date,
        pit_series:     dict[str, list[dict]],
    ) -> tuple[list[str], dict]:
        selected_set = set(_factor_screening(
            tickers, rebalance_date, pit_series, self.weights, self.top_pct
        ))
        rejected = {t: '팩터 스크리닝 하위' for t in tickers if t not in selected_set}
        return [t for t in tickers if t in selected_set], rejected


def _factor_screening(
    universe: list[str],
    rebalance_date: date,
    pit_series: dict[str, list[dict]],
    weights: dict,
    top_pct: float,
) -> list[str]:
    """
    4개 팩터 합산 점수 기준 상위 top_pct 종목 반환.

    팩터 키 (영어 통일, phase2_rim.py에서도 동일 키 사용):
      rev_yoy   — 매출액 YoY    (초기 1/6)
      op_yoy    — 영업이익 YoY  (초기 1/6)
      gpa       — GP/A          (초기 1/3)
      inv_pbr   — 1/PBR         (초기 1/3)

    가중치 합은 항상 1.0. 튜닝 시 자유도 3개 (4번째 inv_pbr = 1-나머지).

    점수 계산:
      - 각 팩터를 유니버스 내 백분위(percentile rank)로 변환 → [0, 1]
      - 1/PBR 팩터: PBR이 낮을수록 점수 높게 → 이미 역수이므로 동일 방향
      - 가중 합산 → 최종 점수
    """
    scores = {}
    raw = {ticker: _compute_factors(ticker, rebalance_date, pit_series.get(ticker, [{}]))
           for ticker in universe}

    # 팩터별 percentile rank 계산 (NaN은 최하위 처리)
    for factor in ['rev_yoy', 'op_yoy', 'gpa', 'inv_pbr']:
        vals = {t: raw[t].get(factor) for t in universe}
        ranked = _percentile_rank(vals)   # {ticker: 0~1}
        for ticker in universe:
            scores[ticker] = scores.get(ticker, 0) + weights[factor] * ranked.get(ticker, 0)

    # 상위 top_pct 추출
    n = max(1, int(len(universe) * top_pct))
    selected = sorted(scores, key=scores.get, reverse=True)[:n]
    return selected


def _compute_factors(ticker: str, rebalance_date: date, series: list[dict]) -> dict:
    """단일 종목 팩터 원시값 계산. series = pit_series[ticker]."""
    from backtest.data_access import get_market_cap
    pit     = series[0] if len(series) > 0 else {}
    pit_prev = series[1] if len(series) > 1 else {}

    cur_rev  = pit.get('매출액')
    cur_op   = pit.get('영업이익')
    prev_rev = pit_prev.get('매출액')
    prev_op  = pit_prev.get('영업이익')
    assets   = pit.get('자산총계')
    gross    = (cur_rev or 0) - (pit.get('매출원가') or 0)   # 매출총이익

    # PBR 직접 계산 (market_cap_history 사용, data_access 경유)
    mktcap = get_market_cap(ticker, rebalance_date)
    equity = pit.get('자본총계')
    pbr    = (mktcap / equity) if mktcap and equity and equity > 0 else None

    return {
        'rev_yoy':  (cur_rev / prev_rev - 1) if prev_rev and cur_rev and prev_rev > 0 else None,
        'op_yoy':   (cur_op  / prev_op  - 1) if prev_op  and cur_op  and prev_op  > 0 else None,
        'gpa':      (gross / assets)          if assets   and assets > 0            else None,
        'inv_pbr':  (1 / pbr)                 if pbr      and pbr    > 0            else None,
    }
```

**스크리닝 팩터 설계 원칙:**
- 매출YoY와 영업이익YoY는 상관관계가 있으나 서로 다른 정보를 일부 보완(마진 변화). 합쳐서 1/3 가중.
- GP/A는 Novy-Marx(2013) 기반 수익성 팩터. 자산 대비 총이익으로 자본 효율성 측정.
- 1/PBR은 낮은 PBR이 상위로 오도록 역수 처리. PBR 계산은 pykrx 없이 `market_cap_history`에서 직접 계산.
- percentile rank 변환으로 팩터 간 스케일 차이 제거.

## 6-4. Step 4 — 모멘텀 필터

v4.2에서 확정된 내용 그대로 유지.

```python
# backtest/filters/momentum_filter.py
# MomentumFilter.apply() 내부에서 호출되는 단일 종목 로직:
#   _momentum_filter(ticker, rebalance_date, conn, short_window, long_window, ...) -> bool
#
# MomentumFilter 클래스:
#   def __init__(self, ma_short=20, ma_long=60, confirm_days=5, slope_lookback=20): ...
#   def apply(self, tickers, rebalance_date, pit_series) -> tuple[list[str], dict]: ...
#
# 단일 종목 로직 시그니처:
    ticker:         str,
    rebalance_date: date,
    conn,
    short_window:   int = 20,    # 고정값 (Phase 2 튜닝 제외. Phase 4 민감도 분석에서만 확인)
    long_window:    int = 60,    # 고정값
    confirm_days:   int = 5,     # 고정값
    slope_lookback: int = 20,    # 고정값
) -> bool:
    """
    True = 편입 가능, False = 제외.

    두 조건 동시 충족 시에만 제외:
    - 조건 1: MA_short < MA_long 이 confirm_days 영업일 연속
    - 조건 2: MA_long(현재) < MA_long(slope_lookback일 전) — 우하향

    adj_close 기준으로 계산. price_history 조회는 data_access.get_adj_close_range() 사용.
    """
    from backtest.data_access import get_adj_close_range
    prices = get_adj_close_range(conn, ticker, rebalance_date,
                                  lookback=long_window + slope_lookback + confirm_days)
    if len(prices) < long_window + slope_lookback:
        return True   # 데이터 부족 → 통과

    ma_long_now  = prices.tail(long_window).mean()
    ma_long_prev = prices.iloc[-(long_window + slope_lookback):-slope_lookback].mean()
    if ma_long_now >= ma_long_prev:
        return True   # MA 우하향 아님 → 통과

    for i in range(confirm_days):
        end_idx   = len(prices) - i
        window    = prices.iloc[:end_idx]
        ma_short  = window.tail(short_window).mean()
        ma_long   = window.tail(long_window).mean()
        if ma_short >= ma_long:
            return True

    return False   # 두 조건 모두 충족 → 하락 추세 → 제외
```

## 6-0. interfaces.py — Protocol 정의 (v4.8 신규)

모든 필터와 적정가 모델은 아래 Protocol을 준수한다.
Protocol을 만족하는 클래스는 `BacktestPipeline`에 주입 가능하다.

```python
# backtest/interfaces.py
from typing import Protocol
from datetime import date

class UniverseFilter(Protocol):
    """
    유니버스 필터 인터페이스.
    구현체: HardFilter, StabilityFilter, FactorScreener, MomentumFilter
    Phase 5 이후: ClassifiedScreener 등 추가 가능
    """
    def apply(
        self,
        tickers:        list[str],
        rebalance_date: date,
        pit_series:     dict[str, list[dict]],
        # pit_series[ticker] = [현재FY, t-1FY, t-2FY] (내림차순, 없으면 리스트 짧아짐)
        # [0] = rebalance_date 기준 최신 FY (available_from <= rebalance_date)
        # [1] = 전년도 FY
        # [2] = 2년 전 FY
        # StabilityFilter(R2/R3)는 [2]까지, HardFilter/Momentum은 [0]만 사용
    ) -> tuple[list[str], dict]:
        """
        반환: (통과 종목 리스트, 탈락 상세 dict {ticker: reason_str 또는 reason_list})
        """
        ...


class ValuationModel(Protocol):
    """
    적정가 모델 인터페이스.
    구현체: RIMModel (Phase 2~4)
    Phase 5 이후: EVSalesModel, FCFFModel, EnsembleModel 등 추가 가능
    """
    name: str   # 'RIM' | 'EV_SALES' | 'FCFF' | 'ENSEMBLE' 등

    def fair_value(
        self,
        ticker:   str,
        pit_data: dict,   # 단일 연도 PIT dict (pit_series[ticker][0])
        shares:   float,
        beta:     float,
    ) -> float | None:
        """주당 적정가 반환. 계산 불가 시 None."""
        ...
```

---

## 6-5. BacktestPipeline — 파이프라인 조립 (v4.8 교체)

기존 `build_universe()` 함수를 대체한다.
필터 목록과 적정가 모델을 생성자에서 주입받아 교체 가능하게 만든다.
Phase별 구체적인 파이프라인 조립은 `configs/`에서 관리한다.

```python
# backtest/pipeline.py

from backtest.interfaces import UniverseFilter, ValuationModel

class BacktestPipeline:
    def __init__(
        self,
        filters:          list[UniverseFilter],  # 순서대로 적용
        valuation_model:  ValuationModel,
        top_pct:          float = 0.20,
        rim_threshold:    float = 0.05,
        n_stocks:         int   = 20,
    ):
        self.filters         = filters
        self.model           = valuation_model
        self.top_pct         = top_pct
        self.rim_threshold   = rim_threshold
        self.n_stocks        = n_stocks

    def build_universe(
        self,
        gate_passed:    list[str],
        rebalance_date: date,
        pit_series:     dict[str, list[dict]],
        # pit_series[ticker] = [현재FY, t-1FY, t-2FY]  ← pit_prev 제거, pit_series로 통일
    ) -> dict:
        """
        filters를 순서대로 적용. 단계별 탈락 수 반환.

        반환: {
            'universe': [ticker, ...],
            'stats': {
                'HardFilter':       {'passed': N, 'rejected': {ticker: reason}},
                'StabilityFilter':  {'passed': N, 'rejected': {ticker: reason_list}},
                'FactorScreener':   {'passed': N, 'rejected': {ticker: reason}},
                'MomentumFilter':   {'passed': N, 'rejected': {ticker: reason}},
            }
        }
        """
        tickers = gate_passed
        stats   = {}
        for f in self.filters:
            tickers, step_stats = f.apply(tickers, rebalance_date, pit_series)
            stats[f.__class__.__name__] = {
                'passed':   len(tickers),
                'rejected': step_stats,
            }
        return {'universe': tickers, 'stats': stats}

    def score_and_rank(
        self,
        universe:       list[str],
        rebalance_date: date,
        pit_series:     dict[str, list[dict]],
        conn,           # psycopg2 connection (data_access 헬퍼에 전달)
    ) -> list[dict]:
        """
        valuation_model로 적정가 계산 → 밸류에이션 필터 → upside% 내림차순 정렬.
        매도 우선순위: upside% 낮은 순.
        """
        from backtest.data_access import get_shares_outstanding, get_close_price
        result = []
        for ticker in universe:
            pit0   = pit_series.get(ticker, [{}])[0]
            shares = get_shares_outstanding(conn, ticker, rebalance_date)
            fv     = self.model.fair_value(ticker, pit0, shares, beta=1.0)
            price  = get_close_price(conn, ticker, rebalance_date)
            if fv is None or price is None or price <= 0:
                continue
            upside = (fv / price - 1) * 100
            if price > fv * (1 + self.rim_threshold):
                continue   # 고평가 제외
            result.append({'ticker': ticker, 'upside_pct': upside,
                           'model': self.model.name, 'fair_value': fv})
        return sorted(result, key=lambda x: x['upside_pct'], reverse=True)
```

---

## 6-6. configs/ — Phase별 파이프라인 조립 (v4.8 신규)

```python
# backtest/configs/phase2_rim.py  ← Phase 2 기본 파이프라인

from backtest.pipeline import BacktestPipeline
from backtest.filters.hard_filter     import HardFilter
from backtest.filters.stability_filter import StabilityFilter
from backtest.filters.factor_screener  import FactorScreener
from backtest.filters.momentum_filter  import MomentumFilter
from backtest.models.rim               import RIMModel

PHASE2_PIPELINE = BacktestPipeline(
    filters=[
        HardFilter(),
        StabilityFilter(r2_exception=True),
        FactorScreener(
            weights={'rev_yoy': 1/6, 'op_yoy': 1/6, 'gpa': 1/3, 'inv_pbr': 1/3},
            top_pct=0.20,
        ),
        MomentumFilter(ma_short=20, ma_long=60, confirm_days=5, slope_lookback=20),
    ],
    valuation_model=RIMModel(),
    rim_threshold=0.05,
    n_stocks=20,
)


# backtest/configs/phase5_multimodel.py  ← Phase 5 skeleton (미래 작성)
# from backtest.models.ensemble import EnsembleModel
# from backtest.models.ev_sales import EVSalesModel
# from backtest.models.fcff     import FCFFModel
#
# PHASE5_PIPELINE = BacktestPipeline(
#     filters=[...],
#     valuation_model=EnsembleModel(
#         models=[RIMModel(), EVSalesModel(), FCFFModel()],
#         weights='equal',
#     ),
# )
```

---

## 6-7. data_access.py — DB 조회 헬퍼 (v4.8 신규)

모든 필터와 파이프라인에서 공통으로 사용하는 DB 조회 함수를 한 파일에 집중한다.
커넥션은 엔진 레벨에서 열어 인자로 전달(`conn` 주입 패턴). `ingest/connection.py`의 팩토리 재사용.

```python
# backtest/data_access.py
from ingest.connection import get_conn   # 팩토리 재사용 (DB 포트 5433)
import pandas as pd

# ── 가격 / 거래대금 ─────────────────────────────────────────────────────────
def get_avg_turnover(conn, ticker: str, date, window: int = 20) -> float:
    """최근 window 영업일 평균 거래대금(원). 데이터 없으면 0 반환."""
    ...

def get_adj_close_range(conn, ticker: str, date, lookback: int) -> pd.Series:
    """date 이전 lookback 영업일 adj_close 시계열. 데이터 없으면 빈 Series."""
    ...

def get_close_price(conn, ticker: str, date) -> float | None:
    """date 기준 종가(adj_close). 없으면 None."""
    ...

# ── 시가총액 / 주식수 ────────────────────────────────────────────────────────
def get_market_cap(conn, ticker: str, date) -> float | None:
    """date 기준 가장 가까운 시가총액(원). 없으면 None."""
    ...

def get_shares_outstanding(conn, ticker: str, date) -> int | None:
    """date 기준 가장 가까운 상장주식수. 없으면 None."""
    ...

# ── PIT 데이터 ──────────────────────────────────────────────────────────────
def load_pit_series(
    conn,
    rebalance_date,
    n_years: int = 3,
) -> dict[str, list[dict]]:
    """
    universe_gate_pit PASS 종목 전체에 대해 rebalance_date 기준
    최신 n_years 개 FY PIT 데이터를 로드.

    반환: {ticker: [FY현재dict, FY(t-1)dict, FY(t-2)dict]}
      - available_from <= rebalance_date 조건 필수 (룩어헤드 방지)
      - 각 원소는 {account_nm: amount, ...} flat dict (피벗된 형태)
      - 연도가 부족한 종목은 리스트 길이가 짧아짐 (len 1 또는 2)
    """
    ...

def load_gate_passed_tickers(conn, rebalance_date) -> list[str]:
    """
    리밸런싱 기준일에 투자 가능한 종목 목록.

    조건:
      1. stocks.is_excluded = FALSE
      2. universe_gate_pit.status = 'PASS' (해당 시점 FY)
      3. stock_listing_events 기준 실제 상장 중 (listed_date <= date < delisted_date)
    """
    ...
```

---

## 6-8. configs/rebalance_dates.py — 리밸런싱 날짜 생성 (v4.8)

Phase 2 시작 시 아래 스크립트를 서버에서 **1회** 실행해 21개 날짜를 계산하고,
그 결과를 `configs/rebalance_dates.py`에 하드코딩한다 (재현성 보장).

```python
# scripts/generate_rebalance_dates.py  ← Phase 2 시작 시 1회 실행
# 출력: 아래 REBALANCE_DATES 리스트에 붙여넣을 값

from datetime import date, timedelta
from pykrx import stock as krx

def nth_trading_day_after(base: date, n: int) -> date:
    d, count = base + timedelta(days=1), 0
    while True:
        ym_start = d.strftime('%Y%m01')
        ym_end   = d.strftime('%Y%m%d')
        cal = krx.get_index_ohlcv_by_date(ym_start, ym_end, 'KOSPI')
        if d in [idx.date() for idx in cal.index]:
            count += 1
            if count == n:
                return d
        d += timedelta(days=1)
        if (d - base).days > 30:
            raise ValueError(f'영업일 탐색 실패: {base}')

dates = []
for yr in range(2015, 2027):
    dates.append(nth_trading_day_after(date(yr, 3, 31), 3))   # 상반기: FY 사업보고서
    if yr < 2026:
        dates.append(nth_trading_day_after(date(yr, 8, 14), 3))  # 하반기: H1 반기보고서
dates.sort()
print([d.isoformat() for d in dates])
```

```python
# backtest/configs/rebalance_dates.py  ← 위 스크립트 출력 결과를 하드코딩
from datetime import date

REBALANCE_DATES = [
    # 2015~2026 (21개 구간) — scripts/generate_rebalance_dates.py 실행 후 채움
    # date(2015, 4, X), date(2015, 8, X), ...
]
```

---

