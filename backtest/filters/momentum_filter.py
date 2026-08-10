"""
Step 4 — 모멘텀 필터.

MA20/MA60 이중 조건: 두 조건 동시 충족 시에만 제외 (하락 추세 제거).
  조건 1: MA_short < MA_long 이 confirm_days 영업일 연속
  조건 2: MA_long(현재) < MA_long(slope_lookback일 전) — 우하향

**`[지위 강등 2026-08-10, SPEC_14 §14-3]` 아래 4개 값은 "확정"이 아니라 "미확정·검토중"이다.**
4·8월 앵커에서의 그리드서치 산출물이며 독립적 근거가 없다(SPEC_12 §6-2 경고 대상).
앵커를 5·11월로 옮기면 20/60 은 17개 중 4위→12위로 떨어지고, 인접 조합 3개
(confirm 3·7, slope 30)도 −8~−9계단 함께 떨어진다 — 파라미터 영역 전체가 흔들린다.
**값은 그대로 쓰되 검증된 것으로 인용하지 마라.** 교체도 하지 않는다(§8-4.3 —
in-sample 선택을 또 다른 in-sample 선택으로 대체하는 것은 문제의 재생산).
"""
from datetime import date

from backtest.data_access import get_adj_close_range


class MomentumFilter:
    """UniverseFilter Protocol 구현체."""

    def __init__(
        self,
        ma_short:      int = 20,   # 미확정 (§14-3)
        ma_long:       int = 60,   # 미확정 (§14-3)
        confirm_days:  int = 5,    # 미확정 (§14-3)
        slope_lookback: int = 20,  # 미확정 (§14-3)
    ):
        self.ma_short       = ma_short
        self.ma_long        = ma_long
        self.confirm_days   = confirm_days
        self.slope_lookback = slope_lookback

    def apply(
        self,
        tickers:        list[str],
        rebalance_date: date,
        pit_series:     dict[str, list[dict]],
        conn,
    ) -> tuple[list[str], dict]:
        passed, rejected = [], {}
        for ticker in tickers:
            if _momentum_filter(
                ticker, rebalance_date, conn,
                self.ma_short, self.ma_long,
                self.confirm_days, self.slope_lookback,
            ):
                passed.append(ticker)
            else:
                rejected[ticker] = '하락 추세 (MA 이중 조건)'
        return passed, rejected


def _momentum_filter(
    ticker:         str,
    rebalance_date: date,
    conn,
    ma_short:       int = 20,
    ma_long:        int = 60,
    confirm_days:   int = 5,
    slope_lookback: int = 20,
) -> bool:
    """
    True = 편입 가능, False = 하락 추세 → 제외.

    두 조건 동시 충족 시에만 False:
      1. MA_short < MA_long 이 confirm_days 영업일 연속
      2. MA_long(현재) < MA_long(slope_lookback일 전) — 장기 MA 우하향
    """
    lookback = ma_long + slope_lookback + confirm_days
    prices   = get_adj_close_range(conn, ticker, rebalance_date, lookback)

    if len(prices) < ma_long + slope_lookback:
        return True   # 데이터 부족 → 통과 (보수적 포함)

    # 조건 2 먼저 확인 (MA_long 기울기)
    ma_long_now  = prices.iloc[-ma_long:].mean()
    ma_long_prev = prices.iloc[-(ma_long + slope_lookback):-slope_lookback].mean()
    if ma_long_now >= ma_long_prev:
        return True   # 장기 MA 우상향 → 통과

    # 조건 1: 최근 confirm_days 연속으로 MA_short < MA_long
    for i in range(confirm_days):
        end     = len(prices) - i
        window  = prices.iloc[:end]
        if len(window) < ma_long:
            return True   # 데이터 부족 → 통과
        ma_s = window.iloc[-ma_short:].mean()
        ma_l = window.iloc[-ma_long:].mean()
        if ma_s >= ma_l:
            return True   # 이 날은 MA_short >= MA_long → 조건 1 불충족 → 통과

    return False   # 두 조건 모두 충족 → 하락 추세 → 제외
