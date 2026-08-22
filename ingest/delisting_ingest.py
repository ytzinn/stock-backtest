"""
상장폐지 종목 수집.

실행:
    python -m ingest.delisting_ingest
"""
import argparse
import logging
from datetime import date, datetime, timezone

import FinanceDataReader as fdr

from ingest.connection import db_conn
from ingest.price_ingest import collect_price_and_turnover

BACKTEST_START = date(2014, 1, 1)

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


def _upsert_delisted_stock(cur, item: dict) -> int:
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
        ON CONFLICT ON CONSTRAINT stock_listing_events_natural_key DO NOTHING
        """,
        (item['ticker'], item['corp_name'], item['market'],
         item['listed_date'], item['delisted_date'], item['delist_reason']),
    )
    return cur.rowcount          # 1 = 신규 삽입, 0 = 자연키 중복으로 스킵


# 평시 신규 상폐는 월 5~30건이다. 한 번에 이보다 훨씬 많이 들어오면 소스 스펙 변화나
# 제약 무력화를 의심해야 한다 (SOURCE-NOTE-NAN-CAST 를 고치면 실제로 그렇게 된다).
INSERT_WARN_THRESHOLD = 50


def ingest_delisting_universe() -> None:
    """상장폐지 종목 목록을 stocks + stock_listing_events에 저장.

    재실행 안전(idempotent)하다 — 자연키 유니크 제약(v10) + ON CONFLICT DO NOTHING.
    실행마다 조회/신규/스킵 건수를 남긴다. **종전에는 이 잡이 crontab 에 아예 없어
    로그가 남을 수 없었고, 그래서 3개월 반 동안 적재 중단을 아무도 몰랐다.**
    """
    started = datetime.now(timezone.utc)
    items = collect_delisting_universe()
    inserted = skipped = 0
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT COALESCE(MAX(id), 0) FROM stock_listing_events')
        before_max_id = cur.fetchone()[0]
        for item in items:
            if _upsert_delisted_stock(cur, item):
                inserted += 1
            else:
                skipped += 1

        cur.execute("""SELECT MAX(delisted_date) FROM stock_listing_events
                       WHERE event_type = 'delisted'""")
        latest = cur.fetchone()[0]

        # 경고 2: 같은 (ticker, event_type, delisted_date) 인데 source_note 만 다른 신규 행.
        # A′ 키 설계의 알려진 단점("사유 문구가 바뀌면 행이 하나 는다")의 직접 탐지기다.
        cur.execute("""
            SELECT n.ticker, n.delisted_date, o.source_note, n.source_note
            FROM stock_listing_events n
            JOIN stock_listing_events o
              ON  o.ticker      = n.ticker
              AND o.event_type  = n.event_type
              AND o.delisted_date IS NOT DISTINCT FROM n.delisted_date
              AND o.id <= %s
            WHERE n.id > %s AND o.source_note IS DISTINCT FROM n.source_note
            ORDER BY n.ticker
        """, (before_max_id, before_max_id))
        note_variants = cur.fetchall()

    log.info(
        '[delisting] %s | 조회 %d · 신규 %d · 중복스킵 %d · 최종상폐일 %s · %.1f초',
        started.isoformat(timespec='seconds'), len(items), inserted, skipped,
        latest, (datetime.now(timezone.utc) - started).total_seconds(),
    )
    if inserted > INSERT_WARN_THRESHOLD:
        log.warning('[경고1] 신규 삽입 %d건 > 임계 %d — 소스 스펙 변화나 제약 무력화 의심',
                    inserted, INSERT_WARN_THRESHOLD)
    if note_variants:
        log.warning('[경고2] 같은 상폐 건에 사유만 다른 행이 %d건 새로 들어왔다 '
                    '— 사유 정정이면 기존 행과 중복 보관된다', len(note_variants))
        for tk, dd, old_note, new_note in note_variants[:10]:
            log.warning('   %s %s: %r → %r', tk, dd, old_note, new_note)


def ingest_delisting_prices(allow_full_rewrite: bool) -> None:
    """상장폐지 종목 가격 이력 **전체 재수집**.

    `[차단 2026-08-22]` 이 함수는 상폐종목 694개의 전 이력을 pykrx 로 다시 받아
    price_history 를 덮어쓴다 (`ON CONFLICT DO UPDATE` — adj_close 포함).
    haircut 이 읽는 값이 실행 시점마다 달라지므로 DRIFT-INGEST-001 위반이다.
    상폐 **목록** 갱신과는 무관한 작업인데 main() 이 조건 없이 함께 불러 왔다.

    allow_full_rewrite 에 기본값을 주지 않는다 — 호출자가 재작성을 의도했다고
    명시해야 한다.
    """
    if not allow_full_rewrite:
        raise ValueError(
            'ingest_delisting_prices 는 과거 가격 행을 재작성한다 — '
            'allow_full_rewrite=True 를 명시해야 실행된다 (DRIFT-INGEST-001)'
        )
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
        start = max(listed_date, BACKTEST_START).strftime('%Y%m%d')
        end   = delisted_date.strftime('%Y%m%d')
        if start > end:
            continue
        try:
            collect_price_and_turnover(ticker, start=start, end=end,
                                       rewrite_reason='full')
        except Exception as e:
            log.warning(f'{ticker} 가격 수집 실패: {e}')


def main() -> None:
    """CLI.

    **크론은 반드시 `--universe-only` 로 돈다.** ingest_delisting_prices() 는 상폐종목
    4,000여개의 가격 이력을 통째로 다시 긁어 과거 행을 재작성한다 — DRIFT-INGEST-001
    (과거 행 재작성은 `--full` · 수정주가 조정 감지 · `--rebuild-from-snapshot` 세 경로뿐)
    위반이고, 백테스트 기준선을 매일 흔든다. 상폐 목록 갱신과는 무관한 작업이다.
    """
    parser = argparse.ArgumentParser(description='상장폐지 종목 수집')
    parser.add_argument('--universe-only', action='store_true',
                        help='상폐 목록만 갱신 (크론 기본값). 가격 재수집을 하지 않는다.')
    parser.add_argument('--allow-full-rewrite', action='store_true',
                        help='상폐종목 가격 이력을 전체 재작성한다. '
                             'DRIFT-INGEST-001 — 백테스트 기준선이 바뀐다.')
    args = parser.parse_args()

    ingest_delisting_universe()
    if args.universe_only:
        log.info('--universe-only: 상폐종목 가격 재수집 생략')
        return
    ingest_delisting_prices(allow_full_rewrite=args.allow_full_rewrite)


if __name__ == '__main__':
    main()
