"""
일별 OHLCV + 수정주가 + 거래대금 수집.

실행:
    python -m ingest.price_ingest                   # 증분 수집 (기본)
    python -m ingest.price_ingest --skip-if-done    # 오늘 데이터 있으면 건너뜀
    python -m ingest.price_ingest --full            # 전종목 전체 이력 재수집 (구동작)
    python -m ingest.price_ingest --from 20140101   # --full과 함께 시작일 지정

데이터 소스: pykrx get_market_ohlcv_by_date(adjusted=True).
OHLCV 전체(open/high/low/close)가 동일 수정 계수로 조정되므로 스케일 일치 보장.
배당 미반영; 액면분할·무상증자 수정 적용.

증분 수집 원칙 (2026-07-17, 드리프트 사고 후속 — DRIFT-INGEST-001):
  매일 전체 이력을 재작성하면 수정주가 리베이스로 과거 데이터가 조용히 변해
  백테스트가 날마다 다른 결과를 낸다 (2026-07-15 재실행 오염 사고).
  기본 동작은 종목별 마지막 수집일 이후만 추가하되, 겹침 구간(OVERLAP_DAYS)의
  종가를 저장값과 대조해 수정주가 조정(분할·무상증자)이 감지된 종목만
  전체 이력을 재수집한다. 과거 행 재작성은 이 경로로만 일어난다.
"""
import argparse
import logging
from datetime import date, timedelta

from pykrx import stock as krx

from ingest.connection import db_conn
from ingest.logging_config import configure_logging

configure_logging('price.log')
log = logging.getLogger(__name__)

DEFAULT_START = '20140101'
OVERLAP_DAYS  = 14      # 증분 수집 시 수정주가 조정 감지용 겹침 구간 (캘린더 일수)
ADJ_REL_TOL   = 1e-6    # 종가 상대 오차 허용치 — 초과 시 수정주가 조정으로 판정


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


def _fetch_df(ticker: str, start: str, end: str):
    """pykrx 조회. 실패·빈 결과 시 None."""
    try:
        df = krx.get_market_ohlcv_by_date(start, end, ticker, adjusted=True)
    except Exception as e:
        log.warning(f'{ticker} pykrx 조회 실패: {e}')
        return None
    if df is None or df.empty:
        return None
    return df


def _rows_from_df(ticker: str, df) -> list[tuple]:
    """pykrx DataFrame → price_history 행 튜플 목록."""
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
    return rows


def _upsert_rows(rows: list[tuple]) -> int:
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


def collect_price_and_turnover(ticker: str, start: str = DEFAULT_START,
                                end: str | None = None) -> int:
    """
    전체 구간 수집 → price_history upsert (과거 행 재작성 포함 — --full 및
    수정주가 조정 감지 시에만 호출할 것).

    adjusted=True: open/high/low/close 전체가 동일 수정 계수 적용.
    adj_close = close (동일 값; 스키마 일관성 유지).
    반환: 저장된 행 수.
    """
    df = _fetch_df(ticker, start, end or _today())
    if df is None:
        return 0
    return _upsert_rows(_rows_from_df(ticker, df))


def _stored_closes(ticker: str, since: date) -> dict[date, float]:
    """since 이후 저장된 (date → close). close IS NULL 행은 제외."""
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT date, close FROM price_history
            WHERE ticker = %s AND date >= %s AND close IS NOT NULL
            """,
            (ticker, since),
        )
        return {r[0]: float(r[1]) for r in cur.fetchall()}


def collect_incremental(ticker: str, last_date: date) -> tuple[int, bool]:
    """
    last_date 이후만 추가 수집. 겹침 구간 종가가 저장값과 어긋나면 수정주가
    조정으로 보고 해당 종목 전체 이력을 재수집한다.

    반환: (저장 행 수, 전체 재수집 여부)
    """
    overlap_start = last_date - timedelta(days=OVERLAP_DAYS)
    df = _fetch_df(ticker, overlap_start.strftime('%Y%m%d'), _today())
    if df is None:
        return 0, False

    rows   = _rows_from_df(ticker, df)
    stored = _stored_closes(ticker, overlap_start)

    for r in rows:
        d, close = r[1], r[5]
        if d > last_date or close is None:
            continue
        old = stored.get(d)
        if old is None:
            continue
        if abs(close - old) > ADJ_REL_TOL * max(abs(old), 1.0):
            log.info(
                f'{ticker} 수정주가 조정 감지 ({d}: 저장 {old} → 조회 {close}) '
                f'— 전체 이력 재수집'
            )
            return collect_price_and_turnover(ticker, start=DEFAULT_START), True

    new_rows = [r for r in rows if r[1] > last_date]
    return _upsert_rows(new_rows), False


def _last_dates() -> dict[str, date]:
    """종목별 price_history 마지막 수집일."""
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT ticker, MAX(date) FROM price_history GROUP BY ticker")
        return {r[0]: r[1] for r in cur.fetchall()}


def ingest_all(start: str = DEFAULT_START, skip_if_done: bool = False,
               full: bool = False) -> None:
    """stocks 테이블 전종목 가격 수집. 기본 증분, --full 시 전체 재수집."""
    with db_conn() as conn:
        if skip_if_done and _today_already_collected(conn):
            log.info('오늘 데이터 이미 존재 — 건너뜀 (--skip-if-done)')
            return
        cur = conn.cursor()
        cur.execute(
            "SELECT ticker FROM stocks WHERE is_excluded = FALSE ORDER BY ticker"
        )
        tickers = [r[0] for r in cur.fetchall()]

    if full:
        log.info(f'가격 전체 재수집 (--full, pykrx): {len(tickers)}개 종목, {start}~')
        for i, ticker in enumerate(tickers, 1):
            n = collect_price_and_turnover(ticker, start=start)
            if i % 100 == 0:
                log.info(f'  진행: {i}/{len(tickers)}  {ticker} ({n}행)')
        log.info('가격 수집 완료')
        return

    last_dates = _last_dates()
    log.info(f'가격 증분 수집 (pykrx): {len(tickers)}개 종목')
    n_refetch = 0
    for i, ticker in enumerate(tickers, 1):
        last = last_dates.get(ticker)
        if last is None:
            # 이력 없는 신규 종목 — 전체 수집
            n = collect_price_and_turnover(ticker, start=start)
        else:
            n, refetched = collect_incremental(ticker, last)
            n_refetch += int(refetched)
        if i % 100 == 0:
            log.info(f'  진행: {i}/{len(tickers)}  {ticker} ({n}행)')
    log.info(f'가격 증분 수집 완료 (수정주가 조정 재수집: {n_refetch}종목)')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-if-done', action='store_true')
    parser.add_argument('--full',         action='store_true',
                        help='전종목 전체 이력 재수집 (과거 행 재작성 — 구 기본 동작)')
    parser.add_argument('--from',         dest='start', default=DEFAULT_START,
                        help='--full 재수집 시작일 (기본 20140101)')
    args = parser.parse_args()
    ingest_all(start=args.start, skip_if_done=args.skip_if_done, full=args.full)


if __name__ == '__main__':
    main()
