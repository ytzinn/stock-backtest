"""
정정공시 감지 및 financials 재수집.

정정공시(report_nm LIKE '%정정%')가 있는 종목 또는 fallback_used=TRUE 종목을
DART API에서 재수집해 financials 테이블을 최신화한다.

v7: 재수집 전 기존 amount를 original_amount에 보전 (정정 전 원본값 유지).
financials_pit 재빌드는 하지 않는다 — pit_loader.py(resolve_dates 변경) 배포 후
Step D에서 전종목 일괄 재빌드한다.

실행:
    python -m ingest.amendment_checker              # 감지 + 전체 재수집
    python -m ingest.amendment_checker --dry-run    # 대상만 출력, 재수집 없음
    python -m ingest.amendment_checker --limit 200  # 최대 200건만 처리
"""
import argparse
import logging
import time

from ingest.connection import db_conn
from ingest.dart_ingest import (
    REPRT_CODE,
    DartAPI,
    QuotaExceededError,
    _deduplicate_equity_variants,
    _upsert_financials,
)
from ingest.logging_config import configure_logging

configure_logging('amendment_checker.log')
log = logging.getLogger(__name__)


def detect_targets(cur) -> list[tuple[str, str, int, str, str]]:
    """
    재수집 대상 (ticker, corp_code, year, report_type, fs_div) 목록 반환.

    조건:
      1. 정정공시가 현재 available_from 이후에 존재 — 정정 내용이 아직 미반영
      2. fallback_used=TRUE — 원본 공시 없이 가상 날짜로 기록, 실제 공시 여부 재확인 필요
    """
    cur.execute(
        """
        SELECT DISTINCT
            d.ticker,
            s.corp_code,
            d.year,
            d.report_type,
            fp_fs.fs_div
        FROM disclosures d
        JOIN stocks s ON s.ticker = d.ticker
        JOIN LATERAL (
            SELECT DISTINCT ON (ticker, year, report_type) fs_div
            FROM financials_pit
            WHERE ticker = d.ticker
              AND year   = d.year
              AND report_type = d.report_type
            ORDER BY ticker, year, report_type,
                     CASE fs_div WHEN 'CFS' THEN 1 ELSE 2 END
        ) fp_fs ON TRUE
        JOIN financials_pit fp
          ON  fp.ticker      = d.ticker
          AND fp.year        = d.year
          AND fp.report_type = d.report_type
          AND fp.fs_div      = fp_fs.fs_div
        WHERE d.report_nm LIKE '%정정%'
          AND d.year IS NOT NULL
          AND d.report_type IN ('FY', 'H1')
          AND s.corp_code IS NOT NULL
          AND (
            fp.available_from < d.rcept_dt
            OR fp.fallback_used = TRUE
          )
        ORDER BY d.ticker, d.year DESC, d.report_type
        """
    )
    rows = cur.fetchall()
    return [(r[0], r[1], r[2], r[3], r[4]) for r in rows]


def refetch_financials(
    dart: DartAPI,
    ticker: str,
    corp_code: str,
    year: int,
    report_type: str,
    fs_div: str,
) -> int:
    """
    DART API에서 단일 (ticker, year, report_type)를 재수집해 financials upsert.

    v7: 재수집 전 기존 amount → original_amount 보전 (original_amount IS NULL 행만).
    반환: 저장된 행 수 (0이면 DART에 데이터 없음).
    """
    reprt_code = REPRT_CODE[report_type]
    items = dart.get_financial_statement(corp_code, year, reprt_code, fs_div)
    if not items:
        log.warning(f'{ticker} {year} {report_type}: DART 응답 없음 (status 013 또는 빈 list)')
        return 0

    with db_conn() as conn:
        cur = conn.cursor()

        # 1. 기존 amount를 original_amount로 보전 (최초 1회만 — IS NULL 조건)
        cur.execute(
            """
            UPDATE financials
            SET original_amount = amount
            WHERE ticker = %s AND year = %s AND report_type = %s
              AND fs_div = %s AND original_amount IS NULL
            """,
            (ticker, year, report_type, fs_div),
        )
        saved = cur.rowcount
        if saved:
            log.debug(f'{ticker} {year} {report_type} {fs_div}: original_amount {saved}개 보전')

        # 2. DART 정정값으로 amount 갱신
        n = _upsert_financials(cur, ticker, corp_code, year, report_type, fs_div, items)
        _deduplicate_equity_variants(cur, ticker, year, report_type, fs_div)
        log.info(f'{ticker} {year} {report_type} {fs_div}: {n}개 upsert 완료')
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description='정정공시 재수집')
    parser.add_argument('--dry-run', action='store_true', help='대상만 출력, 재수집 없음')
    parser.add_argument('--limit', type=int, default=0, help='최대 처리 건수 (0=전체)')
    args = parser.parse_args()

    with db_conn() as conn:
        cur = conn.cursor()
        targets = detect_targets(cur)

    log.info(f'재수집 대상: {len(targets)}건')

    if args.dry_run:
        for ticker, corp_code, year, report_type, fs_div in targets:
            print(f'{ticker}  {year}  {report_type}  {fs_div}  corp={corp_code}')
        return

    if args.limit:
        targets = targets[: args.limit]
        log.info(f'--limit {args.limit} 적용 → {len(targets)}건 처리')

    dart = DartAPI()
    success = 0
    skipped = 0

    for i, (ticker, corp_code, year, report_type, fs_div) in enumerate(targets, 1):
        log.info(f'[{i}/{len(targets)}] {ticker} {year} {report_type}')
        try:
            n = refetch_financials(dart, ticker, corp_code, year, report_type, fs_div)
            if n:
                success += 1
            else:
                skipped += 1
        except QuotaExceededError:
            log.error('DART 일일 쿼터 초과 — 배치 중단')
            break
        except Exception as e:
            log.error(f'{ticker} {year} {report_type} 재수집 실패: {e}')
            skipped += 1

        time.sleep(0.5)  # DART API 10,000콜/일 한도, dart-watcher 공유

    log.info(f'완료: 성공={success} 스킵={skipped} / 전체={len(targets)}')


if __name__ == '__main__':
    main()
