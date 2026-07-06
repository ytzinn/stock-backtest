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

# 6. Universe 구성 — 3단계 필터 구조

Universe 구성은 다음 3단계를 순서대로 적용한다. 단계별로 탈락 종목 수를 기록해 리밸런싱
리포트에 포함한다. (팩터 스크리닝 폐기 경위: MASTER 버전이력 v5.2, SPEC_05 §11)

```
[영구제외(stocks.is_excluded=FALSE) + DQ Gate PASS(universe_gate_pit) + 실제 상장(stock_listing_events) 종목]
    │
    ▼ Step 1: Hard Filter (filters/hard_filter.py)
    │  거래유동성 + 상장기간 + PIT 존재 + R06~R08 시점 기준 필터
    │
    ▼ Step 2: 재무안정성 필터 (filters/stability_filter.py)    ← v4.3 신규
    │  부채비율, 차입금비율, 회전율, 영업CF 기준
    │
    ▼ Step 3: 모멘텀 필터 (filters/momentum_filter.py)
       MA20/MA60 이중 조건 — 하락 추세 제외
```

> **(폐기, 미사용) 팩터 스크리닝** (`filters/factor_screener.py`): 코드는 실험 기록으로 §6-3에
> 보존되나 현재 채택 파이프라인에는 포함하지 않는다.

## 6-1. Step 1 — Hard Filter

> **클래스 구조**: `HardFilter`, `StabilityFilter`, `FactorScreener`(폐기, 미사용), `MomentumFilter`
> 모두 동일한 패턴을 따른다. 생성자(`__init__`)에서 파라미터를 주입받고, `apply(tickers, rebalance_date, pit_series)`
> 메서드에서 종목별로 내부 로직 함수(`_hard_filter`, `_financial_stability_filter` 등)를 호출해
> 통과/탈락을 분류한 뒤 `(passed_list, rejected_dict)`를 반환한다.
>
> `pit_series[ticker]` 인덱스 규칙: `[0]`=현재 FY, `[1]`=t-1 FY, `[2]`=t-2 FY.
> DB 조회 헬퍼는 모두 `backtest/data_access.py`에서 import한다.

```python
# backtest/filters/hard_filter.py  (v5.0 — 거래정지 5일 검사 추가)
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
    # 직전 5 영업일 중 거래 0건 → 거래정지 상태로 간주, 편입 불가
    # 근거: 거래정지 종목이 감자 후 재개 시 수백% 허위 수익 발생 (제일바이오 사례)
    if not has_recent_trade(conn, ticker, rebalance_date, window=5):
        return False, '5일 이상 거래정지'

    # 거래유동성: 최근 20 영업일 일평균 거래대금 (최대 90일 이내 데이터만)
    # max_lookback_days=90: 2년 이상 전 거래량이 현재 유동성인 것처럼 계산되는 것 방지
    if get_avg_turnover(conn, ticker, rebalance_date, 20, max_lookback_days=90) < min_turnover:
        return False, '거래대금 부족'

    # 상장기간
    ld = get_listed_date(conn, ticker)
    if ld and (rebalance_date - ld).days < min_listed_months * 30:
        return False, '상장 6개월 미만'

    # PIT 데이터 존재
    if not pit_series_for_ticker:
        return False, 'PIT FY 데이터 없음'

    # R07: 상장폐지 사유 이력 (시점 기준)
    if is_delisted_at(conn, ticker, rebalance_date):
        return False, '상장폐지'

    # R06: 감사의견 비적정·한정 — DB 미수집, Phase 3 이후 추가 예정
    # R08: 관리종목 지정 — DB 미수집, Phase 3 이후 추가 예정

    return True, ''
```

## 6-2. Step 2 — 재무안정성 필터

**설계 원칙**: 가치 함정(Value Trap) 중에서도 재무 구조적 위험이 명확한 종목을 선제 제거.
활성 하드 룰 4개(R1,R4,R5,R6) + 참고 플래그 2개. 하드 룰은 Bayesian 튜닝 대상에서 제외.
R2·R3는 leave-one-out 검증 결과로 폐기됨(아래 요약표, 근거는 MASTER §3-6·버전이력 v5.4,
SPEC_05 §11 참조). 구현: `backtest/filters/stability_filter.py`
(`StabilityFilter(active_rules={'R1','R4','R5','R6'})`가 현재 채택 설정,
`active_rules`로 규칙별 on/off 가능 — 클래스는 `_financial_stability_filter()`가 각 규칙을
`'RX' in active_rules` 가드로 감싸는 구조).

**재무안정성 필터 기준 요약표:**

| 지표 | 기준 | 상태 | 비고 |
|------|------|------|------|
| R1 부채비율 | 총부채/자기자본 > 200% | ✅ 활성 | leave-one-out: 제거 시 CAGR·MDD 모두 악화 → 유효 |
| ~~R2 차입금비율~~ | (단·장기차입금+사채)/자기자본 > 150% (3FY 단조감소+누적10%p 예외) | ❌ 폐기 | R1과 완전 중복 — 어떤 조합에서도 결과 불변 확인 |
| ~~R3 매출 역성장~~ | 최근 3FY 중 2회 이상 YoY < -5% | ❌ 폐기 | 제거 시 CAGR·MDD 모두 개선 — 역효과 확인 |
| R4 영업CF | 2년 연속 음수 | ✅ 활성 | 기여 미미하나 경제적 논리는 유효, 유지 |
| R5 영업CF(-)+재무CF(+) | 차입 운영 1회 발생 | ✅ 활성 | 약하게 유효 |
| R6 adjROE < r | adjROE < 8.73%(β=1.0) | ✅ 활성 | 가장 큰 기여 (RIM 밸류에이션 가드) |
| 재고자산 회전율 | 전년비 -30% 이상 하락 | 참고 플래그 | Phase 3+ |
| 매출채권 회전율 | 전년비 -30% 이상 하락 | 참고 플래그 | Phase 3+ |

## 6-3. (폐기) 팩터 스크리닝 (v4.3 신규 → 2026-07-05 폐기)

> **더 이상 채택 파이프라인에 포함되지 않음.** 폐기 근거·단일팩터 진단 결과는 SPEC_05 §11,
> MASTER 버전이력 v5.2 참조. 아래 코드·설명은 실험 기록으로 보존한다.

**목적(폐기 전)**: 전체 유니버스에서 펀더멘털 퀄리티 상위 종목을 먼저 선별해 RIM 계산 부하를 줄이고, 스크리닝 자체가 독립 Alpha 인자로 작동하는지 Ablation Test로 검증.

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

Phase 2 채택 파이프라인 조립은 `backtest/configs/phase2_rim.py`(`build_phase2_pipeline()`)
참조 — 코드 중복 대신 소스를 직접 확인할 것. 현재 구성: Hard → Stability(R1,R4,R5,R6) →
Momentum → RIM (FactorScreener 폐기 반영, 2026-07-05·07-07 변경 이력은 MASTER 버전이력
v5.2·v5.4 참조).

Phase 5 멀티모델 조립(`backtest/configs/phase5_multimodel.py`)은 미작성 — Phase 5 착수 시
`backtest/models/ensemble.py`(`EnsembleModel`) 기반으로 신규 작성 예정.
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

## 6-7. data_access.py — DB 조회 헬퍼 (v5.0 구현 완료)

모든 필터와 파이프라인에서 공통으로 사용하는 DB 조회 함수를 한 파일에 집중한다.
커넥션은 엔진 레벨에서 열어 인자로 전달(`conn` 주입 패턴). `ingest/connection.py`의 팩토리 재사용.

**v5.0 추가 함수:**
- `has_recent_trade(conn, ticker, as_of, window=5)`: 최근 5 영업일 내 실제 거래 여부
- `get_avg_turnover(conn, ticker, as_of, window=20, max_lookback_days=90)`: 90일 내 데이터만 사용
- `load_pit_series_ttm(conn, rebalance_date, report_type)`: H1 TTM 계산 포함

```python
# backtest/data_access.py (구현 완료 상태)

# ── 가격 / 거래대금 ─────────────────────────────────────────────────────────
def get_avg_turnover(conn, ticker: str, as_of: date, window: int = 20,
                     max_lookback_days: int = 90) -> float:
    """최근 window 영업일 평균 거래대금(원). max_lookback_days 이내 데이터만 사용."""

def has_recent_trade(conn, ticker: str, as_of: date, window: int = 5) -> bool:
    """최근 window 영업일 중 거래(is_suspended=FALSE)가 하나라도 있으면 True."""

def get_adj_close_range(conn, ticker: str, as_of: date, lookback: int) -> pd.Series:
    """as_of 이전 lookback 영업일 adj_close 시계열 (오름차순). 없으면 빈 Series."""

def get_close_price(conn, ticker: str, as_of: date) -> float | None:
    """as_of 기준 가장 가까운 adj_close. 없으면 None."""

# ── 시가총액 / 주식수 ────────────────────────────────────────────────────────
def get_market_cap(conn, ticker: str, as_of: date) -> float | None:
    """as_of 기준 가장 가까운 시가총액(KRW). 없으면 None."""

def get_shares_outstanding(conn, ticker: str, as_of: date) -> int | None:
    """as_of 기준 가장 가까운 상장주식수. 없으면 None."""

# ── 종목 메타 ───────────────────────────────────────────────────────────────
def get_listed_date(conn, ticker: str) -> date | None:
    """stocks.listed_date 반환. 없으면 None."""

def is_delisted_at(conn, ticker: str, as_of: date) -> bool:
    """as_of 시점에 상장폐지 여부. stock_listing_events 기준."""

# ── PIT 데이터 ──────────────────────────────────────────────────────────────
def load_gate_passed_tickers(conn, rebalance_date: date,
                              report_type: str = 'FY') -> list[str]:
    """리밸런싱 기준일 투자 가능 종목 (is_excluded=FALSE + PASS + 상장 중)."""

def load_pit_series(conn, rebalance_date: date, n_years: int = 3,
                    report_type: str = 'FY') -> dict[str, list[dict]]:
    """
    universe_gate_pit PASS 종목 전체에 대해 rebalance_date 기준 최신 n_years 개 PIT 로드.

    반환: {ticker: [FY현재dict, FY(t-1)dict, FY(t-2)dict]}
      - available_from <= rebalance_date 조건 (룩어헤드 방지)
      - XBRL 정정 반영: amendment_from <= rebalance_date이면 정정값, 미공개면 original_amount
      - CFS 우선, OFS fallback
    """

def load_pit_series_ttm(conn, rebalance_date: date,
                         report_type: str = 'FY') -> dict[str, list[dict]]:
    """
    TTM(Trailing Twelve Months) 적용 PIT 시계열.
    FY 리밸런싱(4월): load_pit_series() 그대로 반환.
    H1 리밸런싱(8월): TTM = FY_prev − H1_prev + H1_curr (IS/CF 계정만, BS는 H1_curr 그대로).
    반환: [ttm_curr, ttm_prev, ttm_pp] 3개.
    """
```

---

## 6-8. configs/rebalance_dates.py — 리밸런싱 날짜 생성 (v4.8)

Phase 2 시작 시 아래 스크립트를 서버에서 **1회** 실행해 23개 날짜를 계산하고,
그 결과를 `configs/rebalance_dates.py`에 하드코딩한다 (재현성 보장).
(2015-04·2015-08 두 날짜 포함, TTM 미충족으로 실행 시 gate=0. 유효 구간은 21개.)

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
    # 2015~2026 (23개 날짜) — scripts/generate_rebalance_dates.py 실행 후 채움
    # 2015-04·08은 TTM 미충족 빈 구간. 유효 21개는 2016-04-05 ~ 2026-04-03.
    # date(2015, 4, X), date(2015, 8, X), ...
]
```

---

