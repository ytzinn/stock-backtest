"""
일별 OHLCV + 수정주가 + 거래대금 수집.

실행:
    python -m ingest.price_ingest                   # 전종목 오늘 날짜 수집
    python -m ingest.price_ingest --skip-if-done    # 이미 수집된 날짜 건너뜀
    python -m ingest.price_ingest --from 20140101   # 특정 날짜부터
"""
import argparse
import logging
from datetime import date

from pykrx import stock as krx

from ingest.connection import db_conn

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
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
    pykrx OHLCV + 수정주가 수집 → price_history upsert.

    adj_close: 액면분할·무상증자 반영 수정주가 (배당 미반영).
    수익률 계산 및 모멘텀 MA는 adj_close 기준.
    반환: 저장된 행 수.
    """
    end = end or _today()
    try:
        df_raw = krx.get_market_ohlcv(start, end, ticker, adjusted=False)
        df_adj = krx.get_market_ohlcv(start, end, ticker, adjusted=True)
    except Exception as e:
        log.warning(f'{ticker} pykrx 조회 실패: {e}')
        return 0

    if df_raw is None or df_raw.empty:
        return 0

    rows = []
    for idx in df_raw.index:
        raw = df_raw.loc[idx]
        close  = float(raw.get('종가', 0)) or None
        volume = int(raw.get('거래량', 0)) or None
        adj_close = float(df_adj.loc[idx]['종가']) if idx in df_adj.index else close
        turnover  = (volume * close) if (volume and close) else None
        is_suspended = volume is None or volume == 0
        rows.append((
            ticker,
            idx.date(),
            float(raw.get('시가', 0)) or None,
            float(raw.get('고가', 0)) or None,
            float(raw.get('저가', 0)) or None,
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

    log.info(f'가격 수집 시작: {len(tickers)}개 종목, {start}~')
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
