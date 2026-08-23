"""
일별 시가총액·상장주식수 수집.

기본(증분) 동작 (2026-07-17 재설계, 드리프트 사고 후속 — DRIFT-INGEST-001):
  market_cap_history 마지막 날짜 이후의 신규 거래일만 채운다.
  1순위 소스는 krx_daily_snapshot (KRX Open API의 날짜별 LIST_SHRS·MKTCAP —
  주식수가 PIT 정확). 스냅샷에 없는 날짜는 krx_daily_ingest.collect_date로
  즉석 수집 후 유도한다. 스냅샷 미커버 종목(비상장·정지 등)은 FDR 현재
  주식수 × price_history 종가로 폴백(source='fdr_shares').
  **과거 행은 재작성하지 않는다** — 매일 전체 이력을 다시 쓰던 구 동작은
  현재 주식수를 전 기간에 소급(PIT 위반)하면서 백테스트 재현성을 깼다.

일회성 PIT 재구축:
    python -m ingest.market_cap_ingest --rebuild-from-snapshot
  krx_daily_snapshot의 날짜별 주식수·시총으로 market_cap_history 전체를
  교체한다(source='krx_pit'). RIM 주식수 입력이 바뀌므로 **백테스트 기준선이
  변한다** — 공식 수치 재발행과 함께 실행할 것.

과거 행 재작성은 `rewrite_reason` 필수 인자로 게이트돼 있다 (REWRITE_REASONS).
기본값이 없으므로 호출자가 어느 허용 경로인지 명시해야 한다 — 주석 경고가 아니라
실행 시점 검사다. 시각은 `market_cap_history.updated_at`(v12)에 남는다.

구(전체 재수집) 동작:
    python -m ingest.market_cap_ingest --full          # FDR 현재 주식수 × 전체 이력
    python -m ingest.market_cap_ingest --supplement-delisted
"""
import argparse
import logging
from datetime import date

import FinanceDataReader as fdr

from ingest.connection import db_conn
from ingest.krx_daily_ingest import collect_date as collect_snapshot_date
from ingest.logging_config import configure_logging
from ingest.schema_guard import MARKET_CAP_INGEST, require_schema

configure_logging('market_cap.log')
log = logging.getLogger(__name__)

DEFAULT_START = '20140101'

# 과거 행 재작성이 허용되는 경로. 주석 경고가 아니라 **실행 시점 검사**다
# (price_ingest.REWRITE_REASONS 와 같은 원칙 — DRIFT-INGEST-001).
#   full        — `--full` 명시 실행
#   new_ticker  — market_cap_history 에 행이 없는 종목 (재작성이 아니라 최초 수집)
#   rebuild_pit — `--rebuild-from-snapshot` 명시 실행. **백테스트 기준선이 변한다**
REWRITE_REASONS = frozenset({'full', 'new_ticker', 'rebuild_pit'})


def _check_rewrite(who: str, reason: str) -> None:
    if reason not in REWRITE_REASONS:
        raise ValueError(
            f'{who}: 과거 행 재작성은 {sorted(REWRITE_REASONS)} 경로에서만 허용된다 '
            f'(받은 값: {reason!r}) — DRIFT-INGEST-001'
        )


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
                        start: str = DEFAULT_START,
                        end: str | None = None, *,
                        rewrite_reason: str) -> int:
    """
    FDR 종가 × 상장주식수 → market_cap 추정 후 market_cap_history upsert.
    **과거 행을 재작성한다.**

    `[강제 2026-08-23]` 종전에는 "--full / --supplement-delisted에서만 호출"이라는
    **주석 경고만** 있었고 코드가 강제하지 않았다. price_ingest 가 같은 구조에서
    delisting_ingest 에 뚫린 것과 동일한 결함이라, 사실 주장을 실행 시점 검사로 옮긴다.
    여기서 쓰는 shares 는 **FDR 현재 주식수**라 과거 구간에 소급되면 PIT 위반이다.

    rewrite_reason 은 기본값이 없다 — 호출자가 어느 허용 경로인지 명시해야 한다.
    반환: 저장된 행 수.
    """
    _check_rewrite(ticker, rewrite_reason)
    end = end or _today()
    try:
        df = fdr.DataReader(ticker, start, end)
    except Exception as e:
        log.warning(f'{ticker} FDR 조회 실패: {e}')
        return 0

    if df is None or df.empty:
        return 0

    rows = [
        (ticker, idx.date() if hasattr(idx, 'date') else idx,
         float(row['Close']) * shares if row.get('Close') else None,
         shares, 'fdr_shares')
        for idx, row in df.iterrows()
        if row.get('Close')
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
                shares     = EXCLUDED.shares,
                updated_at = now()
            """,
            rows,
        )
    return len(rows)


def _missing_dates() -> list[date]:
    """price_history에는 있으나 market_cap_history에는 없는 신규 거래일 (오름차순)."""
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT MAX(date) FROM market_cap_history")
        last = cur.fetchone()[0]
        if last is None:
            return []
        cur.execute(
            "SELECT DISTINCT date FROM price_history WHERE date > %s ORDER BY date",
            (last,),
        )
        return [r[0] for r in cur.fetchall()]


def _fill_date_from_snapshot(d: date, fdr_shares: dict[str, int] | None) -> tuple[int, int]:
    """
    krx_daily_snapshot의 d일 행으로 market_cap_history 채움.
    스냅샷 미커버 종목은 FDR 현재 주식수 × price_history 종가로 폴백.
    반환: (스냅샷 유도 행 수, 폴백 행 수)
    """
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 1 FROM krx_daily_snapshot WHERE date = %s LIMIT 1
            """,
            (d,),
        )
        if cur.fetchone() is None:
            n = collect_snapshot_date(d.strftime('%Y%m%d'))
            log.info(f'  {d} 스냅샷 없음 → KRX API 수집 ({n}행)')

        cur.execute(
            """
            INSERT INTO market_cap_history (ticker, date, market_cap, shares, source)
            SELECT ticker, date, market_cap, shares, 'krx_snapshot'
            FROM krx_daily_snapshot
            WHERE date = %s AND market_cap IS NOT NULL AND shares IS NOT NULL
            ON CONFLICT (ticker, date) DO UPDATE SET
                market_cap = EXCLUDED.market_cap,
                shares     = EXCLUDED.shares,
                source     = EXCLUDED.source,
                updated_at = now()
            """,
            (d,),
        )
        n_snap = cur.rowcount

        # 폴백: 이날 가격은 있는데 시총이 아직 없는 종목 (스냅샷 미커버)
        cur.execute(
            """
            SELECT p.ticker, p.close FROM price_history p
            WHERE p.date = %s AND p.close IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM market_cap_history m
                WHERE m.ticker = p.ticker AND m.date = p.date
              )
            """,
            (d,),
        )
        uncovered = cur.fetchall()
        n_fb = 0
        if uncovered and fdr_shares:
            rows = [
                (t, d, float(close) * fdr_shares[t], fdr_shares[t], 'fdr_shares')
                for t, close in uncovered
                if t in fdr_shares and close
            ]
            if rows:
                cur.executemany(
                    """
                    INSERT INTO market_cap_history (ticker, date, market_cap, shares, source)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (ticker, date) DO NOTHING
                    """,
                    rows,
                )
                n_fb = len(rows)
    return n_snap, n_fb


def ingest_incremental() -> None:
    """신규 거래일만 krx_daily_snapshot 기반으로 채움 (과거 행 불변)."""
    dates = _missing_dates()
    if not dates:
        log.info('신규 거래일 없음 — 시가총액 최신 상태')
        return

    log.info(f'시가총액 증분 수집: {len(dates)}개 거래일 ({dates[0]} ~ {dates[-1]})')
    fdr_shares: dict[str, int] | None = None
    for d in dates:
        try:
            if fdr_shares is None:
                fdr_shares = _load_shares()
        except Exception as e:
            log.warning(f'FDR 주식수 로드 실패 (폴백 비활성): {e}')
            fdr_shares = {}
        n_snap, n_fb = _fill_date_from_snapshot(d, fdr_shares)
        log.info(f'  {d}: 스냅샷 {n_snap}행 + 폴백 {n_fb}행')
    log.info('시가총액 증분 수집 완료')


def rebuild_from_snapshot(start: str = DEFAULT_START, *,
                          rewrite_reason: str) -> None:
    """
    krx_daily_snapshot 전체로 market_cap_history를 PIT 주식수 기준으로 재구축.
    ⚠️ RIM 주식수 입력이 바뀌어 백테스트 기준선이 변한다 — 공식 수치 재발행과 함께 실행.

    `[계측 2026-08-23]` 현재 이력의 96.91%(7,201,395행)가 source='krx_pit' 이다.
    이 값을 쓰는 경로는 여기뿐이므로 과거에 최소 1회 실행됐다 — 그런데 v12 이전에는
    시각 컬럼이 없어 **언제였는지 답할 수 없었다.**
    """
    _check_rewrite('rebuild_from_snapshot', rewrite_reason)
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO market_cap_history (ticker, date, market_cap, shares, source)
            SELECT ticker, date, market_cap, shares, 'krx_pit'
            FROM krx_daily_snapshot
            WHERE date >= %s AND market_cap IS NOT NULL AND shares IS NOT NULL
            ON CONFLICT (ticker, date) DO UPDATE SET
                market_cap = EXCLUDED.market_cap,
                shares     = EXCLUDED.shares,
                source     = EXCLUDED.source,
                updated_at = now()
            """,
            (start,),
        )
        log.info(f'PIT 재구축 완료: {cur.rowcount}행 (source=krx_pit)')


def _load_delisted_shares() -> dict[str, int]:
    """
    KRX-DELISTING에서 상장폐지 종목 ListingShares 로드.
    2015년 이후 상장폐지 종목은 100% 커버.
    """
    try:
        dl = fdr.StockListing('KRX-DELISTING')
        dl['Symbol'] = dl['Symbol'].astype(str).str.zfill(6)
        result = {}
        for _, row in dl.iterrows():
            shares = row.get('ListingShares')
            if shares and shares > 0:
                result[row['Symbol']] = int(shares)
        log.info(f'KRX-DELISTING 주식수: {len(result)}개 종목')
        return result
    except Exception as e:
        log.warning(f'KRX-DELISTING 로드 실패: {e}')
        return {}


def supplement_delisted(start: str = DEFAULT_START) -> None:
    """
    market_cap_history 없는 상장폐지 종목에 대해 보완 수집.
    KRX-DELISTING ListingShares × 종가(FDR)로 시가총액 추정.
    FDR은 상장폐지 전 과거 데이터를 조회 가능.
    """
    with db_conn() as conn:
        cur = conn.cursor()
        # market_cap_history 없는 종목
        cur.execute("""
            SELECT s.ticker FROM stocks s
            WHERE s.is_excluded = FALSE
              AND NOT EXISTS (
                SELECT 1 FROM market_cap_history m WHERE m.ticker = s.ticker
              )
            ORDER BY s.ticker
        """)
        missing = [r[0] for r in cur.fetchall()]

    log.info(f'market_cap_history 없는 종목: {len(missing)}개')
    if not missing:
        return

    delisted_shares = _load_delisted_shares()
    ok, skip = 0, 0
    for ticker in missing:
        shares = delisted_shares.get(ticker)
        if not shares:
            log.debug(f'{ticker} KRX-DELISTING에도 주식수 없음 — 건너뜀')
            skip += 1
            continue
        # NOT EXISTS 로 고른 종목이라 재작성이 아니라 최초 수집이다.
        n = collect_market_cap(ticker, shares, start=start, rewrite_reason='new_ticker')
        if n > 0:
            ok += 1
        else:
            log.debug(f'{ticker} FDR 가격 데이터 없음')

    log.info(f'상장폐지 종목 보완 완료: 성공={ok}, 건너뜀={skip}')


def ingest_all_full(start: str = DEFAULT_START, *, rewrite_reason: str) -> None:
    """구 동작: FDR 현재 주식수 × 전체 이력 재수집. **전 종목 과거 행을 재작성한다.**

    FDR 현재 주식수를 2014년까지 소급하므로 PIT 를 정면으로 위반한다 — 시총은
    랭킹의 분자라(`_PBRRankPipeline`: pbr = mktcap/equity) 한 종목이 틀리면
    그 뒤 순위가 전부 밀린다. rewrite_reason 기본값 없음.
    """
    _check_rewrite('ingest_all_full', rewrite_reason)
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT ticker FROM stocks WHERE is_excluded = FALSE ORDER BY ticker")
        tickers = [r[0] for r in cur.fetchall()]

    log.info('FDR에서 상장주식수 로드 중...')
    shares_map = _load_shares()
    log.info(f'시가총액 전체 재수집 (--full): {len(tickers)}개 종목, {start}~')
    for i, ticker in enumerate(tickers, 1):
        shares = shares_map.get(ticker)
        if not shares:
            continue
        n = collect_market_cap(ticker, shares, start=start, rewrite_reason=rewrite_reason)
        if i % 200 == 0:
            log.info(f'  진행: {i}/{len(tickers)}  {ticker} ({n}행)')
    log.info('시가총액 수집 완료')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-if-done', action='store_true')
    parser.add_argument('--full', action='store_true',
                        help='FDR 현재 주식수로 전체 이력 재수집 (과거 행 재작성 — 구 동작)')
    parser.add_argument('--from', dest='start', default=DEFAULT_START)
    parser.add_argument('--supplement-delisted', action='store_true',
                        help='market_cap_history 없는 상장폐지 종목 보완 수집')
    parser.add_argument('--rebuild-from-snapshot', action='store_true',
                        help='krx_daily_snapshot으로 PIT 주식수 재구축 (백테스트 기준선 변경 주의)')
    args = parser.parse_args()

    # 어떤 경로든 DB 에 쓰기 전에 스키마부터 본다 — 실패를 진입 시점으로 당긴다.
    require_schema(MARKET_CAP_INGEST, who='market_cap_ingest')

    if args.supplement_delisted:
        supplement_delisted(start=args.start)
    elif args.rebuild_from_snapshot:
        rebuild_from_snapshot(start=args.start, rewrite_reason='rebuild_pit')
    elif args.full:
        ingest_all_full(start=args.start, rewrite_reason='full')
    else:
        if args.skip_if_done:
            with db_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT 1 FROM market_cap_history WHERE date = %s LIMIT 1",
                    (date.today(),),
                )
                if cur.fetchone():
                    log.info('오늘 시가총액 이미 존재 — 건너뜀')
                    return
        ingest_incremental()


if __name__ == '__main__':
    main()
