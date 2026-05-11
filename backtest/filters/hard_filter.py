"""
Step 1 — Hard Filter.

거래유동성, 상장기간, PIT 데이터 존재, 상장폐지 여부를 검사한다.
R06(감사의견), R08(관리종목)은 DB 데이터 미수집 → 미구현 (Phase 3 이후 추가 예정).
"""
from datetime import date

from backtest.data_access import get_avg_turnover, get_listed_date, is_delisted_at


class HardFilter:
    """UniverseFilter Protocol 구현체."""

    def __init__(
        self,
        min_turnover:      float = 100_000_000,  # 일평균 거래대금 1억원
        min_listed_months: int   = 6,
    ):
        self.min_turnover      = min_turnover
        self.min_listed_months = min_listed_months

    def apply(
        self,
        tickers:        list[str],
        rebalance_date: date,
        pit_series:     dict[str, list[dict]],
        conn,
    ) -> tuple[list[str], dict]:
        passed, rejected = [], {}
        for ticker in tickers:
            ok, reason = _hard_filter(
                ticker,
                rebalance_date,
                pit_series.get(ticker, []),
                conn,
                self.min_turnover,
                self.min_listed_months,
            )
            if ok:
                passed.append(ticker)
            else:
                rejected[ticker] = reason
        return passed, rejected


def _hard_filter(
    ticker:                str,
    rebalance_date:        date,
    pit_series_for_ticker: list[dict],
    conn,
    min_turnover:          float = 100_000_000,
    min_listed_months:     int   = 6,
) -> tuple[bool, str]:
    """True = 통과. 반환: (pass_flag, reason)"""

    # 거래유동성: 최근 20 영업일 일평균 거래대금
    if get_avg_turnover(conn, ticker, rebalance_date, 20) < min_turnover:
        return False, '거래대금 부족'

    # 상장기간: listed_date 기준 min_listed_months 개월 이상
    ld = get_listed_date(conn, ticker)
    if ld is not None and (rebalance_date - ld).days < min_listed_months * 30:
        return False, '상장 6개월 미만'

    # PIT FY 데이터 존재 여부: pit_series에 데이터가 없으면 제외
    if not pit_series_for_ticker:
        return False, 'PIT FY 데이터 없음'

    # R07: 상장폐지 확인 (stock_listing_events 기준)
    if is_delisted_at(conn, ticker, rebalance_date):
        return False, '상장폐지'

    # R06: 감사의견 비적정·한정 — 미구현 (DB 수집 미완)
    # R08: 관리종목 지정 — 미구현 (DB 수집 미완)

    return True, ''
