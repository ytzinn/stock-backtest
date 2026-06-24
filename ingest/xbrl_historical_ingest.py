"""
DART XBRL 기반 역사적 원본 재무값 소급 수집.

정정공시가 있는 (ticker, year, report_type)를 대상으로:
  1. disclosures에서 원본(비정정) rcept_no 조회
  2. DART fnlttXbrl.xml로 원본 XBRL ZIP 다운로드
  3. xbrl_mapper.parse_xbrl_zip()로 값 추출
  4. DB 기준 단위 스케일 자동 감지 (auto_scale)
  5. financials.original_amount UPDATE (IS NULL 행만, 기존값 덮어쓰지 않음)

amendment_checker.py와 역할 분리:
  - amendment_checker: 2026-05 이후 신규 정정 감지 → original_amount 보전
  - xbrl_historical_ingest: 2026-05 이전 역사적 정정의 원본값 소급 수집

실행:
    python -m ingest.xbrl_historical_ingest              # 정정 있는 전체 종목
    python -m ingest.xbrl_historical_ingest --limit 200  # API 콜 200콜 제한 (기본값)
    python -m ingest.xbrl_historical_ingest --ticker 330350 --year 2020
    python -m ingest.xbrl_historical_ingest --migrate    # v8 마이그레이션 먼저 적용
    python -m ingest.xbrl_historical_ingest --dry-run    # 대상 목록만 출력
"""
import argparse
import io
import logging
import os
import time
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

from ingest.connection import db_conn
from ingest.dart_ingest import DART_BASE, REPRT_CODE, DartAPI, QuotaExceededError
from ingest.logging_config import configure_logging
from ingest.xbrl_mapper import auto_scale, entries_to_amounts, parse_xbrl_zip

load_dotenv()
configure_logging('xbrl_historical.log')
log = logging.getLogger(__name__)


# ── 스키마 마이그레이션 ────────────────────────────────────────────────────────

def apply_migration(cur) -> None:
    """v8_xbrl_original.sql 적용 (IF NOT EXISTS 이므로 중복 적용 안전)."""
    sql_path = Path(__file__).parent / 'migrations' / 'v8_xbrl_original.sql'
    if not sql_path.exists():
        log.error(f'마이그레이션 파일 없음: {sql_path}')
        raise FileNotFoundError(sql_path)
    sql = sql_path.read_text(encoding='utf-8')
    cur.execute(sql)
    log.info('v8_xbrl_original 마이그레이션 적용 완료')


def check_migration(cur) -> bool:
    """financials 테이블에 original_amount 컬럼이 있으면 True."""
    cur.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'financials' AND column_name = 'original_amount'
    """)
    return cur.fetchone() is not None


def check_is_amendment_col(cur) -> bool:
    """disclosures 테이블에 is_amendment 컬럼이 있으면 True."""
    cur.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'disclosures' AND column_name = 'is_amendment'
    """)
    return cur.fetchone() is not None


# ── 대상 종목 탐색 ─────────────────────────────────────────────────────────────

def find_targets(cur, has_is_amendment: bool) -> list[tuple[str, int, str]]:
    """
    정정공시 있고 original_amount 미채워진 (ticker, year, report_type) 목록 반환.
    amendment lag(일수) 내림차순 정렬 — 실제 오류 수정 가능성 높은 순.
    """
    if has_is_amendment:
        amend_filter = 'is_amendment = TRUE'
        orig_filter  = 'is_amendment = FALSE'
    else:
        amend_filter = "report_nm LIKE '%정정%'"
        orig_filter  = "report_nm NOT LIKE '%정정%'"

    cur.execute(f"""
        SELECT
            d.ticker,
            d.year,
            d.report_type,
            MIN(d.rcept_dt) FILTER (WHERE {orig_filter})  AS orig_dt,
            MAX(d.rcept_dt) FILTER (WHERE {amend_filter}) AS amend_dt
        FROM disclosures d
        WHERE d.year IS NOT NULL
          AND d.report_type IN ('FY', 'H1')
        GROUP BY d.ticker, d.year, d.report_type
        HAVING
            COUNT(*) FILTER (WHERE {amend_filter}) > 0
            AND COUNT(*) FILTER (WHERE {orig_filter})  > 0
            AND EXISTS (
                SELECT 1 FROM financials f
                WHERE f.ticker      = d.ticker
                  AND f.year        = d.year
                  AND f.report_type = d.report_type
                  AND f.original_amount IS NULL
            )
        ORDER BY
            (MAX(d.rcept_dt) FILTER (WHERE {amend_filter})
             - MIN(d.rcept_dt) FILTER (WHERE {orig_filter})) DESC NULLS LAST
    """)
    rows = cur.fetchall()
    log.info(f'find_targets: {len(rows)}개 대상 (정정공시 있고 original_amount 미채움)')
    return [(r[0], r[1], r[2]) for r in rows]


def get_original_rcept_no(cur, ticker: str, year: int, report_type: str) -> Optional[str]:
    """
    해당 (ticker, year, report_type)의 원본(비정정) rcept_no 중 가장 이른 것 반환.
    기한연장신고서는 재무제표 없는 공시이므로 제외.
    """
    cur.execute("""
        SELECT rcept_no, report_nm
        FROM disclosures
        WHERE ticker = %s AND year = %s AND report_type = %s
        ORDER BY rcept_dt ASC
    """, (ticker, year, report_type))
    for rcept_no, report_nm in cur.fetchall():
        nm = report_nm or ''
        if '정정' not in nm and '연장신고서' not in nm:
            return rcept_no
    return None


# ── XBRL 다운로드 ──────────────────────────────────────────────────────────────

def download_xbrl(session: requests.Session, api_key: str,
                  rcept_no: str, reprt_code: str) -> Optional[bytes]:
    """
    DART fnlttXbrl.xml (DS003) — 재무제표 원본 XBRL ZIP.
    성공: ZIP bytes. XBRL 없음 / 오류: None.
    """
    try:
        resp = session.get(
            f'{DART_BASE}/fnlttXbrl.xml',
            params={
                'rcept_no':   rcept_no,
                'reprt_code': reprt_code,
                'crtfc_key':  api_key,
            },
            timeout=90,
        )
        resp.raise_for_status()
    except Exception as e:
        log.warning(f'download_xbrl {rcept_no}: HTTP 오류 — {e}')
        return None

    ct = resp.headers.get('Content-Type', '')
    if 'json' in ct:
        try:
            data = resp.json()
            status = data.get('status', '')
            if status == '020':
                raise QuotaExceededError('DART 일일 쿼터 초과 (020)')
            log.warning(f'download_xbrl {rcept_no}: API 오류 status={status}')
        except QuotaExceededError:
            raise
        except Exception:
            pass
        return None

    if not resp.content:
        log.debug(f'download_xbrl {rcept_no}: 빈 응답')
        return None

    # ZIP magic bytes 확인 (PK\x03\x04) — PDF/HTML 응답 필터링
    if resp.content[:4] != b'PK\x03\x04':
        log.debug(f'download_xbrl {rcept_no}: ZIP 아님 (XBRL 미제공 구형 공시)')
        return None

    return resp.content


# ── DB 금액 조회 ───────────────────────────────────────────────────────────────

def load_db_amounts(cur, ticker: str, year: int, report_type: str,
) -> dict[tuple[str, str], float]:
    """
    financials 테이블에서 current-period 금액 로드.
    반환: {(account_nm, fs_div): amount}
    """
    cur.execute("""
        SELECT account_nm, fs_div, amount
        FROM financials
        WHERE ticker = %s AND year = %s AND report_type = %s
          AND amount IS NOT NULL
    """, (ticker, year, report_type))
    return {(r[0], r[1]): float(r[2]) for r in cur.fetchall() if r[2] is not None}


# ── DB 업데이트 ────────────────────────────────────────────────────────────────

def update_original_amounts(
    cur,
    ticker: str,
    year: int,
    report_type: str,
    amounts: dict[tuple[str, str, str], float],
) -> int:
    """
    financials.original_amount 업데이트.
    amounts: {(account_nm, fs_div, period_type): value}
    original_amount IS NULL 행만 (amendment_checker가 보전한 값 덮어쓰지 않음).
    반환: 업데이트된 행 수.
    """
    updated = 0
    for (account_nm, fs_div, period_type), orig_amount in amounts.items():
        if period_type != 'current':
            continue  # 전기(prior) 값은 저장하지 않음
        cur.execute("""
            UPDATE financials
            SET original_amount = %s
            WHERE ticker      = %s
              AND year         = %s
              AND report_type  = %s
              AND fs_div       = %s
              AND account_nm   = %s
              AND original_amount IS NULL
        """, (orig_amount, ticker, year, report_type, fs_div, account_nm))
        updated += cur.rowcount
    return updated


# ── 단일 대상 처리 ─────────────────────────────────────────────────────────────

def process_one(
    dart: DartAPI,
    ticker: str,
    year: int,
    report_type: str,
    dry_run: bool = False,
) -> dict:
    """
    단일 (ticker, year, report_type) 처리.
    반환: {'status': 'ok'|'no_rcept'|'no_xbrl'|'no_entries'|'skipped', 'updated': int}
    """
    with db_conn() as conn:
        cur = conn.cursor()

        orig_rcept = get_original_rcept_no(cur, ticker, year, report_type)
        if not orig_rcept:
            log.warning(f'{ticker} {year} {report_type}: 원본 rcept_no 없음')
            return {'status': 'no_rcept', 'updated': 0}

        db_amounts = load_db_amounts(cur, ticker, year, report_type)
        if not db_amounts:
            log.warning(f'{ticker} {year} {report_type}: financials 행 없음')
            return {'status': 'no_db', 'updated': 0}

    reprt_code = REPRT_CODE[report_type]
    zip_bytes = download_xbrl(dart.session, dart.api_key, orig_rcept, reprt_code)
    if zip_bytes is None:
        log.debug(f'{ticker} {year} {report_type}: XBRL 없음 ({orig_rcept})')
        return {'status': 'no_xbrl', 'updated': 0}

    entries = parse_xbrl_zip(zip_bytes)
    if not entries:
        log.debug(f'{ticker} {year} {report_type}: XBRL 파싱 결과 없음')
        return {'status': 'no_entries', 'updated': 0}

    scale = auto_scale(entries, db_amounts)
    if scale != 1:
        log.debug(f'{ticker} {year} {report_type}: 단위 스케일 ÷{scale:,}')

    amounts = entries_to_amounts(entries, scale)

    if dry_run:
        matched = sum(
            1 for (a, f, p) in amounts if p == 'current' and (a, f) in db_amounts
        )
        log.info(f'[dry-run] {ticker} {year} {report_type}: {len(amounts)}개 추출, DB 매칭 {matched}개')
        return {'status': 'dry_run', 'updated': 0}

    with db_conn() as conn:
        cur = conn.cursor()
        updated = update_original_amounts(cur, ticker, year, report_type, amounts)

    log.info(f'{ticker} {year} {report_type}: original_amount {updated}개 저장 (scale=÷{scale:,}, rcept={orig_rcept})')
    return {'status': 'ok', 'updated': updated}


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description='XBRL 기반 원본 재무값 소급 수집')
    parser.add_argument('--migrate',  action='store_true', help='v8 마이그레이션 먼저 적용')
    parser.add_argument('--dry-run',  action='store_true', help='대상 조회만, DB 변경 없음')
    parser.add_argument('--limit',    type=int, default=200, help='최대 처리 건수 (0=전체, 기본 200)')
    parser.add_argument('--ticker',   help='단일 종목 티커')
    parser.add_argument('--year',     type=int, help='단일 연도 (--ticker와 함께 사용)')
    parser.add_argument('--report-type', default='FY', help='보고서 유형 (기본 FY)')
    args = parser.parse_args()

    with db_conn() as conn:
        cur = conn.cursor()

        if args.migrate:
            apply_migration(cur)
            conn.commit()

        if not check_migration(cur):
            log.error('original_amount 컬럼 없음 — --migrate 먼저 실행하세요')
            return

        has_is_amendment = check_is_amendment_col(cur)

        if args.ticker:
            targets = [(args.ticker, args.year or 2022, args.report_type)]
        else:
            targets = find_targets(cur, has_is_amendment)

    if args.dry_run:
        print(f'대상: {len(targets)}개')
        for ticker, year, report_type in targets[:50]:
            print(f'  {ticker}  {year}  {report_type}')
        if len(targets) > 50:
            print(f'  ... ({len(targets) - 50}개 추가)')
        return

    if args.limit and not args.ticker:
        targets = targets[: args.limit]

    dart = DartAPI()
    stats = {'ok': 0, 'no_rcept': 0, 'no_xbrl': 0, 'no_entries': 0, 'no_db': 0, 'error': 0}
    total_updated = 0

    for i, (ticker, year, report_type) in enumerate(targets, 1):
        log.info(f'[{i}/{len(targets)}] {ticker} {year} {report_type}')
        try:
            result = process_one(dart, ticker, year, report_type, dry_run=False)
            stats[result['status']] = stats.get(result['status'], 0) + 1
            total_updated += result['updated']
        except QuotaExceededError:
            log.error('DART 일일 쿼터 초과 — 배치 중단')
            break
        except Exception as e:
            log.error(f'{ticker} {year} {report_type} 처리 실패: {e}')
            stats['error'] += 1

        time.sleep(0.5)  # DART API 일일 10,000콜 한도 (dart-watcher 공유)

    log.info(
        f'완료: original_amount {total_updated}개 저장 | '
        f'ok={stats["ok"]} no_rcept={stats["no_rcept"]} no_xbrl={stats["no_xbrl"]} '
        f'no_entries={stats["no_entries"]} error={stats["error"]} / 처리={len(targets)}'
    )


if __name__ == '__main__':
    main()
