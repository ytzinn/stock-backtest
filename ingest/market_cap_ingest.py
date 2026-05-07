"""
일별 시가총액·상장주식수 수집.

FDR StockListing에서 현재 상장주식수를 가져오고,
pykrx get_market_ohlcv로 과거 종가를 곱해 시가총액을 추정한다.

실행:
    python -m ingest.market_cap_ingest
    python -m ingest.market_cap_ingest --skip-if-done
"""
import argparse
import logging
from datetime import date

import FinanceDataReader as fdr
from pykrx import stock as krx

from ingest.connection import db_conn

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)


def _today() -> str:
    return date.today().strftime('%Y%m%d')


def _load_shares() -> dict[str, int]:
    """FDR StockListing으로 현재 상장주식수 로드."""
    listing = fdr.StockListing('KRX')
    return {
        str(row['Code']).strip(): int(row['Stocks'])
        for _, row in listing.iterrows()
        if row.get('Stocks') and int(row['Stocks']) > 0
    }


def collect_market_cap(ticker: str, shares: int,
                        start: str = '20140101',
                        end: str | None = None) -> int:
    """
    pykrx 종가 × 상장주식수 → market_cap 추정 후 market_cap_history upsert.
    반환: 저장된 행 수.
    """
    end = end or _today()
    try:
        df = krx.get_market_ohlcv(start, end, ticker, adjusted=False)
    except Exception as e:
        log.warning(f'{ticker} 가격 조회 실패: {e}')
        return 0

    if df is None or df.empty:
        return 0

    rows = [
        (ticker, idx.date(),
         float(row['종가']) * shares if row.get('종가') else None,
         shares, 'fdr_shares')
        for idx, row in df.iterrows()
        if row.get('종가')
    ]

    if not rows:
        return 0

    with db_conn() as conn:
        cur = conn.cursor()
        cur.executemany(
            """
            INSERT INTO market_cap_history (ticker, date, market_cap, shares, source)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (ticker, date) DO UPDATE SET
                market_cap = EXCLUDED.market_cap,
                shares     = EXCLUDED.shares
            """,
            rows,
        )
    return len(rows)


def ingest_all(start: str = '20140101', skip_if_done: bool = False) -> None:
    with db_conn() as conn:
        if skip_if_done:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM market_cap_history WHERE date = %s LIMIT 1",
                (date.today(),),
            )
            if cur.fetchone():
                log.info('오늘 시가총액 이미 존재 — 건너뜀')
                return
        cur = conn.cursor()
        cur.execute("SELECT ticker FROM stocks WHERE is_excluded = FALSE ORDER BY ticker")
        tickers = [r[0] for r in cur.fetchall()]

    log.info('FDR에서 상장주식수 로드 중...')
    shares_map = _load_shares()
    log.info(f'시가총액 수집: {len(tickers)}개 종목, {start}~')
    for i, ticker in enumerate(tickers, 1):
        shares = shares_map.get(ticker)
        if not shares:
            continue
        n = collect_market_cap(ticker, shares, start=start)
        if i % 200 == 0:
            log.info(f'  진행: {i}/{len(tickers)}  {ticker} ({n}행)')
    log.info('시가총액 수집 완료')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-if-done', action='store_true')
    parser.add_argument('--from', dest='start', default='20140101')
    args = parser.parse_args()
    ingest_all(start=args.start, skip_if_done=args.skip_if_done)


if __name__ == '__main__':
    main()
