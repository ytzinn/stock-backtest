"""
KRX 상장 스냅샷 기반으로 stocks 테이블 재구성 + DART 관련 테이블 초기화.

기존 corp_code / fscl_month / is_financial / is_excluded 보존.
krx_listing_snapshots에 없는 ticker는 stocks에서 제거.
스팩·ETF·리츠 등 제외 패턴은 universe_loader._is_excluded() 재사용.

실행:
    python -m scripts.rebuild_stocks_from_krx
    python -m scripts.rebuild_stocks_from_krx --dry-run   # DB 변경 없이 통계만 출력
"""
import argparse
import logging

from ingest.connection import db_conn
from ingest.logging_config import configure_logging

EXCLUDE_NAME_PATTERNS = [
    '스팩', '기업인수목적', '리츠',
    '선박펀드', '인프라펀드', '해운펀드',
    'ETF', 'ETN', 'KODEX', 'TIGER', 'KBSTAR', 'ARIRANG', 'HANARO',
]


def _is_excluded(corp_name: str) -> tuple[bool, str]:
    for p in EXCLUDE_NAME_PATTERNS:
        if p in corp_name:
            return True, f'사전제외: {p!r} 포함'
    return False, ''

configure_logging('rebuild_stocks.log')
log = logging.getLogger(__name__)

# 초기화 대상 테이블 순서 (FK 의존성 고려)
_TRUNCATE_ORDER = [
    'financials_pit',
    'financials',
    'disclosures',
    'classification_history',
    'validation_log',
    'universe_gate_pit',
    'rim_input_status',
    'ingest_status',
]


def _load_krx_tickers(conn) -> dict[str, dict]:
    """
    krx_listing_snapshots 전체에서 ticker별 집계.
    company_name·market은 가장 최신 snapshot_date 기준.
    반환: {ticker: {'company_name': str, 'market': str, 'snapshot_count': int}}
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT ON (ticker)
            TRIM(ticker)  AS ticker,
            company_name,
            market,
            COUNT(*) OVER (PARTITION BY ticker) AS snapshot_count
        FROM krx_listing_snapshots
        ORDER BY ticker, snapshot_date DESC
    """)
    result = {}
    for ticker, company_name, market, cnt in cur.fetchall():
        result[ticker] = {
            'company_name': company_name or '',
            'market':       market or '',
            'snapshot_count': int(cnt),
        }
    log.info(f'KRX 스냅샷 고유 ticker 수: {len(result)}')
    return result


def _load_existing_stocks(conn) -> dict[str, tuple]:
    """기존 stocks 테이블에서 보존할 필드 로드."""
    cur = conn.cursor()
    cur.execute("""
        SELECT ticker, corp_name, corp_code, fscl_month,
               is_financial, is_excluded, exclude_reason
        FROM stocks
    """)
    return {row[0]: row for row in cur.fetchall()}


def rebuild_stocks(dry_run: bool = False) -> None:
    with db_conn() as conn:
        cur = conn.cursor()

        # 1. KRX 스냅샷 ticker 집계
        krx_tickers = _load_krx_tickers(conn)

        # 2. 기존 stocks 필드 보존용 로드
        existing = _load_existing_stocks(conn)
        log.info(f'기존 stocks 행 수: {len(existing)}')

        # 3. 재삽입 행 구성
        upsert_rows = []
        excluded_count = 0
        for ticker, info in krx_tickers.items():
            raw_name  = info['company_name']
            market    = info['market']
            prev      = existing.get(ticker)

            # corp_name: KRX 최신 이름 우선, 없으면 기존, 없으면 ticker
            corp_name = raw_name.strip() or (prev[1] if prev else '') or ticker

            # 보존 필드
            corp_code    = prev[2]    if prev else None
            fscl_month   = prev[3]    if prev else None
            is_financial = prev[4]    if prev else False

            # 제외 판정: 기존 is_excluded=TRUE 우선, 이름 패턴 추가
            name_excl, name_reason = _is_excluded(corp_name)
            if prev and prev[5]:
                is_excluded_final = True
                exclude_reason    = prev[6] or name_reason
            elif name_excl:
                is_excluded_final = True
                exclude_reason    = name_reason
            else:
                is_excluded_final = False
                exclude_reason    = None

            if is_excluded_final:
                excluded_count += 1

            upsert_rows.append((
                ticker, corp_name, corp_code, market,
                fscl_month, is_financial, is_excluded_final, exclude_reason,
            ))

        krx_set    = set(krx_tickers.keys())
        removed    = [t for t in existing if t not in krx_set]

        if dry_run:
            log.info(f'[DRY-RUN] stocks 재삽입 예정: {len(upsert_rows)}개')
            log.info(f'[DRY-RUN] 제외(스팩/ETF 등): {excluded_count}개')
            log.info(f'[DRY-RUN] KRX 미등장으로 제거 예정: {len(removed)}개')
            if removed[:20]:
                log.info(f'[DRY-RUN] 제거 예시: {removed[:20]}')
            return

        # 4. 초기화 (FK 의존 순서)
        log.info('DART 관련 테이블 초기화 시작')
        for table in _TRUNCATE_ORDER:
            cur.execute(f'TRUNCATE TABLE {table}')
            log.info(f'  TRUNCATE {table}')

        # stocks CASCADE (financials 등 이미 비워졌으므로 안전)
        cur.execute('TRUNCATE TABLE stocks CASCADE')
        log.info('  TRUNCATE stocks CASCADE')

        # 5. stocks 재삽입
        cur.executemany(
            """
            INSERT INTO stocks
                (ticker, corp_name, corp_code, market,
                 fscl_month, is_financial, is_excluded, exclude_reason, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
            """,
            upsert_rows,
        )
        log.info(f'stocks 재삽입 완료: {len(upsert_rows)}개 (제외: {excluded_count}개)')

        # 6. ingest_status pending 삽입 (is_excluded 여부 관계없이 등록)
        cur.executemany(
            """
            INSERT INTO ingest_status (ticker, status)
            VALUES (%s, 'pending')
            ON CONFLICT (ticker) DO UPDATE SET
                status       = 'pending',
                last_attempt = NULL,
                error_msg    = NULL,
                call_count   = 0
            """,
            [(ticker,) for ticker in krx_tickers],
        )
        log.info(f'ingest_status pending 리셋: {len(krx_tickers)}개')

        log.info(f'KRX 미등장으로 제거된 기존 ticker: {len(removed)}개')
        if removed[:20]:
            log.info(f'제거 예시: {removed[:20]}')


def main() -> None:
    parser = argparse.ArgumentParser(
        description='KRX 스냅샷 기반 stocks 재구성 + DART 테이블 초기화'
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='DB 변경 없이 통계만 출력')
    args = parser.parse_args()
    rebuild_stocks(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
