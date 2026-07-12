"""
Step 1 — Hard Filter.

거래유동성, 상장기간, PIT 데이터 존재, 상장폐지 여부를 검사한다.
R06(감사의견), R08(관리종목)은 DB 데이터 미수집 → 미구현 (Phase 3 이후 추가 예정).
"""
import logging
from datetime import date

from backtest.data_access import (
    PriceDataUnavailable,
    get_avg_turnover,
    get_first_price_date,
    get_listed_date,
    has_recent_trade,
    is_delisted_at,
)

log = logging.getLogger(__name__)


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

    # 가격 데이터 자체가 없는 종목(미수집/미상장)은 '거래정지'·'거래대금 부족'과
    # 구분해 명시 사유로 제외한다 (CORR-DA-001 — 조용한 유니버스 왜곡을 가시화).
    try:
        # 직전 5 영업일 중 거래 0건 → 거래정지 상태로 간주, 편입 불가
        if not has_recent_trade(conn, ticker, rebalance_date, window=5):
            return False, '5일 이상 거래정지'

        # 거래유동성: 최근 20 영업일 일평균 거래대금 (최대 90일 이내 데이터만 사용)
        if get_avg_turnover(conn, ticker, rebalance_date, 20) < min_turnover:
            return False, '거래대금 부족'
    except PriceDataUnavailable as e:
        log.warning(f'[가격 데이터 없음] {ticker} @ {rebalance_date}: {e} — 유니버스 제외')
        return False, '가격 데이터 없음 (미수집/미상장)'

    # 상장기간: listed_date 기준 min_listed_months 개월 이상.
    # listed_date NULL(운영 DB 92% — CORR-HARD-001)이면 검사를 건너뛰지 않고
    # 가격 이력 최초일을 프록시로 판정한다 (수집 시작일 절단 때문에 실제 상장일보다
    # 늦을 수 있으나 '최근 상장' 판정에는 보수 방향 — 조기 편입만은 막는다).
    ld = get_listed_date(conn, ticker)
    if ld is None:
        ld = get_first_price_date(conn, ticker)
    if ld is not None and (rebalance_date - ld).days < min_listed_months * 30:
        return False, '상장 6개월 미만 (listed_date 또는 가격이력 프록시)'

    # PIT FY 데이터 존재 여부: pit_series에 데이터가 없으면 제외
    if not pit_series_for_ticker:
        return False, 'PIT FY 데이터 없음'

    # R07: 상장폐지 확인 (stock_listing_events 기준)
    if is_delisted_at(conn, ticker, rebalance_date):
        return False, '상장폐지'

    # R06: 감사의견 비적정·한정 — 미구현 (DB 수집 미완)
    # R08: 관리종목 지정 — 미구현 (DB 수집 미완)

    return True, ''
