"""
상장폐지 종목 수집.

실행:
    python -m ingest.delisting_ingest
"""
import logging

import FinanceDataReader as fdr

from ingest.connection import db_conn
from ingest.price_ingest import collect_price_and_turnover

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)


def collect_delisting_universe() -> list[dict]:
    """FDR KRX-DELISTING으로 상장폐지 종목 목록 수집."""
    df = fdr.StockListing('KRX-DELISTING')
    result = []
    for _, row in df.iterrows():
        ticker = str(row.get('Symbol', row.get('Code', ''))).strip()
        if not ticker:
            continue
        result.append({
            'ticker':        ticker,
            'corp_name':     str(row.get('Name',          '')).strip(),
            'market':        str(row.get('Market',        '')).strip(),
            'listed_date':   row.get('ListingDate'),
            'delisted_date': row.get('DelistingDate'),
            'delist_reason': str(row.get('Reason',        '')).strip(),
        })
    return result


def _upsert_delisted_stock(cur, item: dict) -> None:
    cur.execute(
        """
        INSERT INTO stocks (ticker, corp_name, market, is_excluded, exclude_reason, listed_date)
        VALUES (%s, %s, %s, FALSE, NULL, %s)
        ON CONFLICT (ticker) DO UPDATE SET
            corp_name  = EXCLUDED.corp_name,
            market     = EXCLUDED.market,
            updated_at = now()
        """,
        (item['ticker'], item['corp_name'], item['market'], item['listed_date']),
    )
    cur.execute(
        """
        INSERT INTO stock_listing_events
            (ticker, corp_name, market, listed_date, delisted_date,
             event_type, source, source_note)
        VALUES (%s, %s, %s, %s, %s, 'delisted', 'fdr', %s)
        """,
        (item['ticker'], item['corp_name'], item['market'],
         item['listed_date'], item['delisted_date'], item['delist_reason']),
    )


def ingest_delisting_universe() -> None:
    """상장폐지 종목 목록을 stocks + stock_listing_events에 저장."""
    items = collect_delisting_universe()
    log.info(f'상장폐지 종목 {len(items)}개 수집')
    with db_conn() as conn:
        cur = conn.cursor()
        for item in items:
            _upsert_delisted_stock(cur, item)
    log.info('상장폐지 종목 저장 완료')


def ingest_delisting_prices() -> None:
    """상장폐지 종목 가격 이력 수집."""
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT e.ticker, e.listed_date, e.delisted_date
            FROM stock_listing_events e
            WHERE e.event_type = 'delisted'
              AND e.listed_date IS NOT NULL
              AND e.delisted_date IS NOT NULL
        """)
        rows = cur.fetchall()

    log.info(f'상장폐지 가격 수집 대상: {len(rows)}개')
    for ticker, listed_date, delisted_date in rows:
        start = listed_date.strftime('%Y%m%d')
        end   = delisted_date.strftime('%Y%m%d')
        try:
            collect_price_and_turnover(ticker, start=start, end=end)
        except Exception as e:
            log.warning(f'{ticker} 가격 수집 실패: {e}')


def main() -> None:
    ingest_delisting_universe()
    ingest_delisting_prices()


if __name__ == '__main__':
    main()
