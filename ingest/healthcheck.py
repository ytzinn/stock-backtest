"""
수집 완료 여부 healthcheck — 평일 KST 21:00 cron 실행.

실행:
    python -m ingest.healthcheck
"""
import logging
from datetime import date

from ingest.connection import db_conn
from ingest.logging_config import configure_logging

configure_logging('healthcheck.log')
log = logging.getLogger(__name__)

PRICE_MIN_ROWS   = 100   # 오늘 가격 데이터 최소 종목 수
MARKET_CAP_MIN   = 100


def check_price_history(today: date) -> bool:
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(DISTINCT ticker) FROM price_history WHERE date = %s",
            (today,),
        )
        cnt = cur.fetchone()[0]
    if cnt < PRICE_MIN_ROWS:
        log.error(f'[FAIL] price_history 오늘({today}) 종목 수 {cnt} < {PRICE_MIN_ROWS}')
        return False
    log.info(f'[OK] price_history 오늘 {cnt}개 종목')
    return True


def check_market_cap_history(today: date) -> bool:
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(DISTINCT ticker) FROM market_cap_history WHERE date = %s",
            (today,),
        )
        cnt = cur.fetchone()[0]
    if cnt < MARKET_CAP_MIN:
        log.error(f'[FAIL] market_cap_history 오늘({today}) 종목 수 {cnt} < {MARKET_CAP_MIN}')
        return False
    log.info(f'[OK] market_cap_history 오늘 {cnt}개 종목')
    return True


def check_ingest_status() -> None:
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT status, COUNT(*) FROM ingest_status GROUP BY status
        """)
        rows = cur.fetchall()
    for status, cnt in rows:
        log.info(f'  ingest_status {status}: {cnt}개')


def check_fallback_rate() -> None:
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) FILTER (WHERE fallback_used) * 1.0 / NULLIF(COUNT(*), 0)
            FROM financials_pit
        """)
        row = cur.fetchone()
    rate = float(row[0]) if row and row[0] else 0.0
    if rate > 0.20:
        log.error(f'[FAIL] fallback_used 비율 {rate:.1%} > 20%')
    else:
        log.info(f'[OK] fallback_used 비율 {rate:.1%}')


def main() -> None:
    today = date.today()
    log.info(f'=== healthcheck {today} ===')
    check_price_history(today)
    check_market_cap_history(today)
    check_ingest_status()
    check_fallback_rate()
    log.info('=== healthcheck 완료 ===')


if __name__ == '__main__':
    main()
