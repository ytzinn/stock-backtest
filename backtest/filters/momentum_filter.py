"""
Step 4 — 모멘텀 필터.

MA20/MA60 이중 조건: 두 조건 동시 충족 시에만 제외 (하락 추세 제거).
  조건 1: MA_short < MA_long 이 confirm_days 영업일 연속
  조건 2: MA_long(현재) < MA_long(slope_lookback일 전) — 우하향

Phase 2에서 파라미터 4개 모두 고정. Phase 4 민감도 분석에서만 변경.
"""
from datetime import date

from backtest.data_access import get_adj_close_range


class MomentumFilter:
    """UniverseFilter Protocol 구현체."""

    def __init__(
        self,
        ma_short:      int = 20,   # 단기 이동평균 기간 (Phase 2 고정)
        ma_long:       int = 60,   # 장기 이동평균 기간 (Phase 2 고정)
        confirm_days:  int = 5,    # 연속 확인 영업일 (Phase 2 고정)
        slope_lookback: int = 20,  # MA_long 기울기 계산 기준 기간 (Phase 2 고정)
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
