"""
일별 OHLCV + 수정주가 + 거래대금 수집.

실행:
    python -m ingest.price_ingest                   # 전종목 오늘 날짜 수집
    python -m ingest.price_ingest --skip-if-done    # 이미 수집된 날짜 건너뜀
    python -m ingest.price_ingest --from 20140101   # 특정 날짜부터

데이터 소스: pykrx get_market_ohlcv_by_date(adjusted=True).
OHLCV 전체(open/high/low/close)가 동일 수정 계수로 조정되므로 스케일 일치 보장.
배당 미반영; 액면분할·무상증자 수정 적용.
"""
import argparse
import logging
from datetime import date

from pykrx import stock as krx

from ingest.connection import db_conn
from ingest.logging_config import configure_logging

configure_logging('price.log')
log = logging.getLogger(__name__)


def _today() -> str:
    return date.today().strftime('%Y%m%d')


def _today_already_collected(conn) -> bool:
    today = date.today()
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM price_history WHERE date = %s LIMIT 1",
        (today,),
    )
    return cur.fetchone() is not None


def collect_price_and_turnover(ticker: str, start: str = '20140101',
                                end: str | None = None) -> int:
    """
    pykrx로 일별 OHLCV 수집 → price_history upsert.

    adjusted=True: open/high/low/close 전체가 동일 수정 계수 적용.
    adj_close = close (동일 값; 스키마 일관성 유지).
    수익률 계산 및 모멘텀 MA는 adj_close 기준.
    반환: 저장된 행 수.
    """
    end = end or _today()
    try:
        df = krx.get_market_ohlcv_by_date(start, end, ticker, adjusted=True)
    except Exception as e:
        log.warning(f'{ticker} pykrx 조회 실패: {e}')
        return 0

    if df is None or df.empty:
        return 0

    rows = []
    for idx, row in df.iterrows():
        close  = float(row.get('종가', 0)) or None
        volume = int(row.get('거래량', 0)) if row.get('거래량') is not None else None
        if volume == 0:
            volume = None
        adj_close    = close
        turnover     = (volume * close) if (volume and close) else None
        is_suspended = volume is None
        rows.append((
            ticker,
            idx.date() if hasattr(idx, 'date') else idx,
            float(row.get('시가', 0)) or None,
            float(row.get('고가', 0)) or None,
            float(row.get('저가', 0)) or None,
            close,
            adj_close,
            volume,
            turnover,
            is_suspended,
        ))

    if not rows:
        return 0

    with db_conn() as conn:
        cur = conn.cursor()
        cur.executemany(
            """
            INSERT INTO price_history
                (ticker, date, open, high, low, close, adj_close,
                 volume, turnover, is_suspended)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (ticker, date) DO UPDATE SET
                open         = EXCLUDED.open,
                high         = EXCLUDED.high,
                low          = EXCLUDED.low,
                close        = EXCLUDED.close,
                adj_close    = EXCLUDED.adj_close,
                volume       = EXCLUDED.volume,
                turnover     = EXCLUDED.turnover,
                is_suspended = EXCLUDED.is_suspended
            """,
            rows,
        )
    return len(rows)


def ingest_all(start: str = '20140101', skip_if_done: bool = False) -> None:
    """stocks 테이블 전종목 가격 수집."""
    with db_conn() as conn:
        if skip_if_done and _today_already_collected(conn):
            log.info('오늘 데이터 이미 존재 — 건너뜀 (--skip-if-done)')
            return
        cur = conn.cursor()
        cur.execute(
            "SELECT ticker FROM stocks WHERE is_excluded = FALSE ORDER BY ticker"
        )
        tickers = [r[0] for r in cur.fetchall()]

    log.info(f'가격 수집 시작 (pykrx): {len(tickers)}개 종목, {start}~')
    for i, ticker in enumerate(tickers, 1):
        n = collect_price_and_turnover(ticker, start=start)
        if i % 100 == 0:
            log.info(f'  진행: {i}/{len(tickers)}  {ticker} ({n}행)')
    log.info('가격 수집 완료')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-if-done', action='store_true')
    parser.add_argument('--from',         dest='start', default='20140101')
    args = parser.parse_args()
    ingest_all(start=args.start, skip_if_done=args.skip_if_done)


if __name__ == '__main__':
    main()
