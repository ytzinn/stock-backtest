"""
financials + disclosures → financials_pit (Point-in-Time) 변환.

실행:
    python -m ingest.pit_loader
    python -m ingest.pit_loader --ticker 005930
"""
import argparse
import logging
from datetime import date

from ingest.connection import db_conn
from ingest.logging_config import configure_logging

configure_logging('pit.log')
log = logging.getLogger(__name__)

# 법정 제출 마감일 + 5일 fallback
FALLBACK_OFFSET = {
    'FY': (1,  4,  5),   # year+1, 4월 5일
    'H1': (0,  8, 19),   # year,   8월 19일
    'Q1': (0,  5, 20),   # year,   5월 20일
    'Q3': (0, 11, 19),   # year,   11월 19일
}


def resolve_dates(cur, ticker: str, year: int,
                  report_type: str) -> tuple[date, 'date | None', bool]:
    """
    원본 공시일, 정정 공시일, fallback 여부 반환.
      available_from  = MIN(rcept_dt) — 데이터 최초 공개일 (룩어헤드 기준)
      amendment_from  = MAX(rcept_dt) if MAX > MIN else None — 정정 공개일
      fallback_used   = True if disclosures 없어 법정마감+5일 사용
    """
    cur.execute(
        """
        SELECT MIN(rcept_dt), MAX(rcept_dt) FROM disclosures
        WHERE ticker = %s AND year = %s AND report_type = %s
        """,
        (ticker, year, report_type),
    )
    row = cur.fetchone()
    if row and row[0]:
        min_dt, max_dt = row
        amendment_from = max_dt if max_dt > min_dt else None
        return min_dt, amendment_from, False

    yr_off, mo, day = FALLBACK_OFFSET[report_type]
    fb_date = date(year + yr_off, mo, day)
    return fb_date, None, True


def build_financials_pit(ticker: str | None = None) -> None:
    """
    financials 테이블 → financials_pit 변환.
    fallback_used=TRUE: 법정마감+5일 사용 (룩어헤드 오염 없음, 항상 실제 공시일보다 늦음).
    """
    with db_conn() as conn:
        cur  = conn.cursor()
        cur2 = conn.cursor()

        if ticker:
            cur.execute(
                """
                SELECT DISTINCT ticker, corp_code, year, report_type, fs_div
                FROM financials WHERE ticker = %s
                """,
                (ticker,),
            )
        else:
            cur.execute(
                """
                SELECT DISTINCT ticker, corp_code, year, report_type, fs_div
                FROM financials
                ORDER BY ticker, year, report_type
                """
            )

        groups = cur.fetchall()
        log.info(f'PIT 변환 대상 그룹: {len(groups)}개')
        saved = 0

        for tkr, corp_code, year, report_type, fs_div in groups:
            avail, amend_from, fallback = resolve_dates(cur2, tkr, year, report_type)

            # rcept_no 첫 번째 값 (source 추적용)
            cur2.execute(
                """
                SELECT rcept_no FROM disclosures
                WHERE ticker = %s AND year = %s AND report_type = %s
                LIMIT 1
                """,
                (tkr, year, report_type),
            )
            rcept_row = cur2.fetchone()
            rcept_no  = rcept_row[0] if rcept_row else None

            # 해당 그룹의 계정 목록 조회 (original_amount 포함)
            cur2.execute(
                """
                SELECT account_nm, amount, original_amount FROM financials
                WHERE ticker = %s AND year = %s AND report_type = %s AND fs_div = %s
                """,
                (tkr, year, report_type, fs_div),
            )
            accounts = cur2.fetchall()
            if not accounts:
                continue

            pit_rows = [
                (tkr, corp_code, year, report_type, fs_div,
                 account_nm, amount, original_amount,
                 avail, amend_from, rcept_no, fallback)
                for account_nm, amount, original_amount in accounts
            ]
            cur2.executemany(
                """
                INSERT INTO financials_pit
                    (ticker, corp_code, year, report_type, fs_div,
                     account_nm, amount, original_amount,
                     available_from, amendment_from,
                     source_rcept_no, fallback_used)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ticker, year, report_type, fs_div, account_nm)
                DO UPDATE SET
                    amount          = EXCLUDED.amount,
                    original_amount = EXCLUDED.original_amount,
                    available_from  = EXCLUDED.available_from,
                    amendment_from  = EXCLUDED.amendment_from,
                    source_rcept_no = EXCLUDED.source_rcept_no,
                    fallback_used   = EXCLUDED.fallback_used
                """,
                pit_rows,
            )
            saved += len(pit_rows)

        log.info(f'PIT 변환 완료: {saved}개 저장')


def check_fallback_rate(sample_tickers: list[str] | None = None) -> float:
    """
    fallback_used 비율 계산.
    Phase 0A 게이팅 기준: 20% 초과 시 경고.
    """
    with db_conn() as conn:
        cur = conn.cursor()
        if sample_tickers:
            cur.execute(
                """
                SELECT COUNT(*) FILTER (WHERE fallback_used) * 1.0 / COUNT(*)
                FROM financials_pit
                WHERE ticker = ANY(%s)
                """,
                (sample_tickers,),
            )
        else:
            cur.execute(
                "SELECT COUNT(*) FILTER (WHERE fallback_used) * 1.0 / COUNT(*) FROM financials_pit"
            )
        row = cur.fetchone()
        rate = float(row[0]) if row and row[0] else 0.0

    if rate > 0.20:
        log.error(f'fallback_used 비율 {rate:.1%} > 20% — Phase 0A 게이팅 위반')
    else:
        log.info(f'fallback_used 비율: {rate:.1%} (정상)')
    return rate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--ticker', help='단일 종목만 변환')
    parser.add_argument('--check-fallback', action='store_true')
    args = parser.parse_args()

    if args.check_fallback:
        check_fallback_rate()
    else:
        build_financials_pit(ticker=args.ticker)


if __name__ == '__main__':
    main()
