"""
DART 재무제표 + 공시 목록 수집.

실행:
    python -m ingest.dart_ingest                  # 전체 수집 (14일+ 분산 실행)
    python -m ingest.dart_ingest --skip-if-done   # 오늘 이미 수집된 종목 건너뜀
    python -m ingest.dart_ingest --ticker 005930  # 단일 종목 테스트
"""
import argparse
import io
import logging
import os
import time
import zipfile
from datetime import date, datetime
from typing import Optional

import requests
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

from ingest.connection import db_conn
from ingest.logging_config import configure_logging

load_dotenv()
configure_logging('dart.log')
log = logging.getLogger(__name__)

DART_BASE = 'https://opendart.fss.or.kr/api'

REPRT_CODE = {
    'FY': '11011',
    'H1': '11013',
    'Q1': '11012',
    'Q3': '11014',
}

# 수집 대상 보고서 (Phase 0: FY + H1만, Q1/Q3는 Phase 1 이후)
TARGET_REPORTS = ('FY', 'H1')

# 표준 계정명 매핑 — DART account_nm → 내부 표준명
# 순서가 중요: 더 구체적인 패턴을 먼저 배치
ACCOUNT_ALIASES: dict[str, list[str]] = {
    '매출액':           ['매출액', '수익(매출액)', '영업수익', '매출', '순매출액'],
    '매출총이익':       ['매출총이익', '매출이익'],
    '영업이익':         ['영업이익', '영업이익(손실)', '영업손익'],
    '당기순이익':       ['당기순이익', '당기순이익(손실)', '분기순이익',
                        '분기순이익(손실)', '연결당기순이익'],
    '자본총계':         ['자본총계', '자본합계', '지배기업소유주지분',
                        '자본및부채총계'],
    '자산총계':         ['자산총계', '자산합계'],
    '부채총계':         ['부채총계', '부채합계'],
    '유동자산':         ['유동자산'],
    '유동부채':         ['유동부채'],
    '영업활동현금흐름': ['영업활동현금흐름', '영업활동으로인한현금흐름',
                        '영업활동현금흐름합계'],
    '재무활동현금흐름': ['재무활동현금흐름', '재무활동으로인한현금흐름'],
    '배당금지급':       ['배당금지급', '배당금의지급', '현금배당금지급'],
    '단기차입금':       ['단기차입금'],
    '유동성장기부채':   ['유동성장기부채', '유동성장기차입금', '유동성사채'],
    '장기차입금':       ['장기차입금'],
    '사채':             ['사채', '장기사채'],
}

# 역방향 조회 테이블: raw name → standard name
_RAW_TO_STD: dict[str, str] = {}
for std, aliases in ACCOUNT_ALIASES.items():
    for alias in aliases:
        _RAW_TO_STD[alias.replace(' ', '')] = std


def standardize_account(raw_nm: str) -> Optional[str]:
    """DART 계정명 → 표준명. 매핑 없으면 None."""
    return _RAW_TO_STD.get(raw_nm.replace(' ', ''))


# ── DART API ───────────────────────────────────────────────────────────────────

class DartAPI:
    def __init__(self):
        self.api_key = os.getenv('DART_API_KEY', '')
        if not self.api_key:
            raise RuntimeError('DART_API_KEY 환경변수 없음')
        self.session = requests.Session()

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=4, max=30))
    def _get(self, endpoint: str, params: dict) -> dict:
        params['crtfc_key'] = self.api_key
        resp = self.session.get(f'{DART_BASE}/{endpoint}', params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get('status') not in ('000', '013'):
            raise RuntimeError(f"DART API 오류: {data.get('status')} {data.get('message')}")
        return data

    def get_financial_statement(self, corp_code: str, year: int,
                                 reprt_code: str, fs_div: str) -> list[dict]:
        """단일 회사 재무제표 조회. status 013(없음)이면 빈 리스트."""
        try:
            data = self._get('fnlttSinglAcnt.json', {
                'corp_code': corp_code,
                'bsns_year': str(year),
                'reprt_code': reprt_code,
                'fs_div': fs_div,
            })
        except RuntimeError as e:
            if '013' in str(e):
                return []
            raise
        return data.get('list', [])

    def get_disclosures(self, corp_code: str, year: int) -> list[dict]:
        """공시 목록 조회 (rcept_dt 확보용)."""
        # pblntf_ty: A=사업보고서 계열
        pblntf_type_map = {'11011': 'A', '11013': 'A', '11012': 'A', '11014': 'A'}
        try:
            data = self._get('list.json', {
                'corp_code':  corp_code,
                'bgn_de':     f'{year}0101',
                'end_de':     f'{year + 1}0630',
                'pblntf_ty':  'A',
                'page_count': 40,
            })
        except RuntimeError:
            return []
        return data.get('list', [])

    def download_corp_codes(self) -> dict[str, str]:
        """
        DART 법인코드 다운로드 (ZIP XML).
        반환: {corp_code: corp_name}
        """
        resp = self.session.get(
            f'{DART_BASE}/corpCode.xml',
            params={'crtfc_key': self.api_key},
            timeout=60,
        )
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_name = zf.namelist()[0]
            xml_bytes = zf.read(xml_name)
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml_bytes)
        result = {}
        for item in root.findall('list'):
            corp_code = (item.findtext('corp_code') or '').strip()
            corp_name = (item.findtext('corp_name') or '').strip()
            if corp_code:
                result[corp_code] = corp_name
        log.info(f'법인코드 {len(result)}개 다운로드')
        return result


# ── corp_code 매핑 ─────────────────────────────────────────────────────────────

def init_corp_codes(dart: DartAPI) -> None:
    """DART 법인코드를 stocks 테이블의 corp_code 컬럼에 매핑."""
    corp_map = dart.download_corp_codes()  # {corp_code: corp_name}
    name_to_code = {v: k for k, v in corp_map.items()}

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT ticker, corp_name FROM stocks WHERE corp_code IS NULL")
        rows = cur.fetchall()
        matched = 0
        for ticker, corp_name in rows:
            code = name_to_code.get(corp_name)
            if code:
                cur.execute(
                    "UPDATE stocks SET corp_code = %s WHERE ticker = %s",
                    (code, ticker),
                )
                matched += 1
        log.info(f'법인코드 매핑: {matched}/{len(rows)}개 매칭')


# ── 수집 핵심 로직 ─────────────────────────────────────────────────────────────

def _upsert_financials(cur, ticker: str, corp_code: str, year: int,
                        report_type: str, fs_div: str, items: list[dict]) -> int:
    """재무제표 항목 → financials 테이블 upsert. 반환: 저장 행 수."""
    saved = 0
    for item in items:
        raw_nm = (item.get('account_nm') or '').strip()
        std_nm = standardize_account(raw_nm)
        if std_nm is None:
            continue
        amount_str = item.get('thstrm_amount', '') or ''
        try:
            amount = float(amount_str.replace(',', ''))
        except (ValueError, AttributeError):
            amount = None

        cur.execute(
            """
            INSERT INTO financials (ticker, corp_code, year, report_type, fs_div, account_nm, amount)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker, year, report_type, fs_div, account_nm) DO UPDATE SET
                amount = EXCLUDED.amount
            """,
            (ticker, corp_code, year, report_type, fs_div, std_nm, amount),
        )
        saved += 1
    return saved


def _upsert_disclosures(cur, ticker: str, items: list[dict]) -> None:
    """공시 목록 → disclosures 테이블 upsert."""
    reprt_nm_map = {
        '사업보고서': 'FY', '반기보고서': 'H1',
        '1분기보고서': 'Q1', '3분기보고서': 'Q3',
    }
    for item in items:
        rcept_no  = (item.get('rcept_no')  or '').strip()
        report_nm = (item.get('report_nm') or '').strip()
        rcept_dt  = (item.get('rcept_dt')  or '').strip()
        bsns_year = item.get('bsns_year')

        report_type = None
        for k, v in reprt_nm_map.items():
            if k in report_nm:
                report_type = v
                break
        if not rcept_no or not report_type:
            continue

        try:
            rcept_date = datetime.strptime(rcept_dt, '%Y%m%d').date() if rcept_dt else None
        except ValueError:
            rcept_date = None

        if bsns_year:
            year = int(bsns_year)
        elif rcept_date:
            # list.json API는 bsns_year를 반환하지 않으므로 rcept_dt에서 역산
            # FY: 3~6월 접수 → 전년도 결산, H1/Q1/Q3: 접수연도 = 사업연도
            if report_type == 'FY' and rcept_date.month <= 6:
                year = rcept_date.year - 1
            else:
                year = rcept_date.year
        else:
            year = None

        cur.execute(
            """
            INSERT INTO disclosures (rcept_no, ticker, rcept_dt, report_nm, report_type, year)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (rcept_no) DO UPDATE SET
                rcept_dt    = EXCLUDED.rcept_dt,
                report_nm   = EXCLUDED.report_nm,
                report_type = EXCLUDED.report_type,
                year        = EXCLUDED.year
            """,
            (rcept_no, ticker, rcept_date, report_nm, report_type, year),
        )


def ingest_company(dart: DartAPI, ticker: str, corp_code: str,
                    start_year: int = 2014) -> None:
    """단일 회사 재무제표 + 공시 수집."""
    end_year = date.today().year

    with db_conn() as conn:
        cur = conn.cursor()

        # 공시 목록 먼저 수집 (rcept_dt 확보)
        for year in range(start_year, end_year + 1):
            disclosures = dart.get_disclosures(corp_code, year)
            _upsert_disclosures(cur, ticker, disclosures)
            time.sleep(0.05)  # 과도한 API 호출 방지

        # 재무제표 수집
        total = 0
        for year in range(start_year, end_year + 1):
            for report_type in TARGET_REPORTS:
                reprt_code = REPRT_CODE[report_type]
                # CFS 우선, 없으면 OFS
                for fs_div in ('CFS', 'OFS'):
                    items = dart.get_financial_statement(
                        corp_code, year, reprt_code, fs_div
                    )
                    if items:
                        n = _upsert_financials(
                            cur, ticker, corp_code, year, report_type, fs_div, items
                        )
                        total += n
                        time.sleep(0.1)
                        break  # CFS 성공하면 OFS 건너뜀
                    time.sleep(0.1)

        # ingest_status 갱신
        cur.execute(
            """
            INSERT INTO ingest_status (ticker, status, last_attempt, call_count)
            VALUES (%s, 'done', now(), 1)
            ON CONFLICT (ticker) DO UPDATE SET
                status       = 'done',
                last_attempt = now(),
                call_count   = ingest_status.call_count + 1,
                error_msg    = NULL
            """,
            (ticker,),
        )
        log.info(f'{ticker} 완료: {total}개 계정 저장')


def _mark_error(ticker: str, msg: str) -> None:
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO ingest_status (ticker, status, last_attempt, error_msg)
            VALUES (%s, 'error', now(), %s)
            ON CONFLICT (ticker) DO UPDATE SET
                status       = 'error',
                last_attempt = now(),
                error_msg    = EXCLUDED.error_msg
            """,
            (ticker, msg[:500]),
        )


def ingest_all(skip_if_done: bool = False) -> None:
    """stocks 테이블 전종목 DART 수집 (14일+ 분산 실행)."""
    dart = DartAPI()

    with db_conn() as conn:
        cur = conn.cursor()
        # corp_code 없는 종목은 매핑 먼저
        cur.execute("SELECT 1 FROM stocks WHERE corp_code IS NULL LIMIT 1")
        if cur.fetchone():
            log.info('법인코드 미매핑 종목 발견 — 매핑 실행')
            init_corp_codes(dart)

        # 수집 대상 조회
        if skip_if_done:
            cur.execute("""
                SELECT s.ticker, s.corp_code
                FROM stocks s
                LEFT JOIN ingest_status i ON i.ticker = s.ticker
                WHERE s.is_excluded = FALSE
                  AND s.corp_code IS NOT NULL
                  AND (i.status IS NULL OR i.status != 'done')
                ORDER BY s.ticker
            """)
        else:
            cur.execute("""
                SELECT ticker, corp_code FROM stocks
                WHERE is_excluded = FALSE AND corp_code IS NOT NULL
                ORDER BY ticker
            """)
        targets = cur.fetchall()

    log.info(f'DART 수집 대상: {len(targets)}개 종목')
    for ticker, corp_code in targets:
        try:
            ingest_company(dart, ticker, corp_code)
        except Exception as e:
            log.error(f'{ticker} 수집 실패: {e}')
            _mark_error(ticker, str(e))


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-if-done', action='store_true')
    parser.add_argument('--ticker', help='단일 종목 테스트')
    args = parser.parse_args()
    if args.skip_if_done:
        configure_logging('dart_retry.log')

    dart = DartAPI()

    if args.ticker:
        with db_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT corp_code FROM stocks WHERE ticker = %s", (args.ticker,))
            row = cur.fetchone()
        if not row or not row[0]:
            log.error(f'{args.ticker} corp_code 없음 — universe_loader --init 먼저 실행')
            return
        ingest_company(dart, args.ticker, row[0])
    else:
        ingest_all(skip_if_done=args.skip_if_done)


if __name__ == '__main__':
    main()
