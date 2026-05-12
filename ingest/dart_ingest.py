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
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential

from ingest.connection import db_conn
from ingest.logging_config import configure_logging

load_dotenv()
configure_logging('dart.log')
log = logging.getLogger(__name__)

DART_BASE = 'https://opendart.fss.or.kr/api'


class QuotaExceededError(RuntimeError):
    """DART API 일일 쿼터 초과 (status 020). 재시도 없이 즉시 배치 중단."""

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
    '자본총계':             ['자본총계', '자본합계'],
    '지배기업소유주지분':   ['지배기업소유주지분', '지배기업소유주지분합계',
                             '지배기업소유주에게귀속되는자본합계', '지배주주지분'],
    '자산총계':             ['자산총계', '자산합계'],
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

# 각 계정의 정규 출처 재무제표. 동일 계정명이 여러 재무제표에 등장할 때
# 자본변동표의 0값 등이 손익계산서 정답을 덮어쓰는 버그를 방지한다.
_CANONICAL_SJ: dict[str, str] = {
    '매출액':           '손익계산서',
    '매출총이익':       '손익계산서',
    '영업이익':         '손익계산서',
    '당기순이익':       '손익계산서',
    '자산총계':         '재무상태표',
    '부채총계':         '재무상태표',
    '자본총계':             '재무상태표',
    '지배기업소유주지분':   '재무상태표',
    '유동자산':             '재무상태표',
    '유동부채':         '재무상태표',
    '단기차입금':       '재무상태표',
    '유동성장기부채':   '재무상태표',
    '장기차입금':       '재무상태표',
    '사채':             '재무상태표',
    '영업활동현금흐름': '현금흐름표',
    '재무활동현금흐름': '현금흐름표',
    '배당금지급':       '현금흐름표',
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
           wait=wait_exponential(multiplier=1, min=4, max=30),
           retry=retry_if_not_exception_type(QuotaExceededError))
    def _get(self, endpoint: str, params: dict) -> dict:
        params['crtfc_key'] = self.api_key
        resp = self.session.get(f'{DART_BASE}/{endpoint}', params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get('status') == '020':
            raise QuotaExceededError('DART 일일 쿼터 초과 (020)')
        if data.get('status') not in ('000', '013'):
            raise RuntimeError(f"DART API 오류: {data.get('status')} {data.get('message')}")
        return data

    def get_financial_statement(self, corp_code: str, year: int,
                                 reprt_code: str, fs_div: str) -> list[dict]:
        """단일 회사 재무제표 전체 계정 조회. status 013(없음)이면 빈 리스트.

        fnlttSinglAcntAll: 현금흐름표 포함 전체 계정 반환.
        fnlttSinglAcnt(구): 주요계정만 — 현금흐름표 계정 제외됨.
        """
        try:
            data = self._get('fnlttSinglAcntAll.json', {
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

        # 정규 출처 재무제표가 지정된 계정은 해당 sj_nm에서 온 행만 허용.
        # fnlttSinglAcntAll은 동일 계정명을 여러 재무제표(손익/포괄손익/현금흐름/자본변동)에
        # 걸쳐 반환하며, 자본변동표 행은 열별 분해값이라 0이 다수 포함된다.
        canonical = _CANONICAL_SJ.get(std_nm)
        if canonical:
            sj_nm = (item.get('sj_nm') or '').replace(' ', '')
            if canonical.replace(' ', '') not in sj_nm:
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
                year        = COALESCE(EXCLUDED.year, disclosures.year)
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


def repair_ni() -> None:
    """
    당기순이익 0 버그 복구.

    자본변동표의 0값이 손익계산서 정답을 upsert로 덮어쓴 종목만 대상.
    조건: 당기순이익=0 AND 매출>0. 영업손실 기업도 포함(이전 영업이익>0 조건보다 넓음).
    수정된 _upsert_financials(sj_nm 필터)로 재수집하므로 손익계산서 값만 저장된다.
    """
    dart = DartAPI()

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT ni.ticker, s.corp_code, ni.year, ni.report_type, ni.fs_div
            FROM financials ni
            JOIN financials rev ON rev.ticker = ni.ticker
                               AND rev.year = ni.year
                               AND rev.report_type = ni.report_type
                               AND rev.fs_div = ni.fs_div
                               AND rev.account_nm = '매출액'
            JOIN stocks s ON s.ticker = ni.ticker
            WHERE ni.account_nm = '당기순이익'
              AND (ni.amount IS NULL OR ni.amount = 0)
              AND rev.amount > 0
              AND s.corp_code IS NOT NULL
            ORDER BY ni.ticker, ni.year, ni.report_type
        """)
        targets = cur.fetchall()

    log.info(f'당기순이익 복구 대상: {len(targets)}개 (ticker×year×report_type×fs_div)')
    ok = 0
    for ticker, corp_code, year, report_type, fs_div in targets:
        reprt_code = REPRT_CODE.get(report_type)
        if not reprt_code:
            continue
        try:
            items = dart.get_financial_statement(corp_code, year, reprt_code, fs_div)
            if not items:
                log.warning(f'{ticker} {year} {report_type} {fs_div} — API 빈 응답')
                continue
            with db_conn() as conn:
                cur = conn.cursor()
                _upsert_financials(cur, ticker, corp_code, year, report_type, fs_div, items)
            ok += 1
            log.info(f'{ticker} {year} {report_type} {fs_div} 복구 완료')
        except QuotaExceededError:
            log.error('DART 쿼터 초과 — 복구 중단, 내일 재실행')
            break
        except Exception as e:
            log.error(f'{ticker} {year} {report_type} 복구 실패: {e}')

    log.info(f'당기순이익 복구 완료: {ok}/{len(targets)}개')


def repair_equity() -> None:
    """
    자산 ≠ 부채+자본 버그 복구.

    원인: '자본및부채총계'(=자산총계)나 '지배기업소유주지분'이 자본총계 alias로
    잘못 매핑돼 upsert로 덮어쓰여진 케이스.
    조건: |자산 - 부채 - 자본| / 자산 > 1% (1% 초과 오차).
    수정된 _upsert_financials + ACCOUNT_ALIASES로 재수집하면
    자본총계와 지배기업소유주지분이 분리 저장된다.
    """
    dart = DartAPI()

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT a.ticker, s.corp_code, a.year, a.report_type, a.fs_div
            FROM financials a
            JOIN financials l ON l.ticker=a.ticker AND l.year=a.year
                             AND l.report_type=a.report_type AND l.fs_div=a.fs_div
                             AND l.account_nm='부채총계'
            JOIN financials e ON e.ticker=a.ticker AND e.year=a.year
                             AND e.report_type=a.report_type AND e.fs_div=a.fs_div
                             AND e.account_nm='자본총계'
            JOIN stocks s ON s.ticker=a.ticker
            WHERE a.account_nm='자산총계'
              AND a.amount > 0
              AND l.amount IS NOT NULL AND e.amount IS NOT NULL
              AND ABS(a.amount - l.amount - e.amount) / a.amount > 0.01
              AND s.corp_code IS NOT NULL
            ORDER BY a.ticker, a.year, a.report_type
        """)
        targets = cur.fetchall()

    log.info(f'자산=부채+자본 복구 대상: {len(targets)}개 (ticker×year×report_type×fs_div)')
    ok = 0
    for ticker, corp_code, year, report_type, fs_div in targets:
        reprt_code = REPRT_CODE.get(report_type)
        if not reprt_code:
            continue
        try:
            items = dart.get_financial_statement(corp_code, year, reprt_code, fs_div)
            if not items:
                log.warning(f'{ticker} {year} {report_type} {fs_div} — API 빈 응답')
                continue
            with db_conn() as conn:
                cur = conn.cursor()
                _upsert_financials(cur, ticker, corp_code, year, report_type, fs_div, items)
            ok += 1
            log.info(f'{ticker} {year} {report_type} {fs_div} 복구 완료')
        except QuotaExceededError:
            log.error('DART 쿼터 초과 — 복구 중단, 내일 재실행')
            break
        except Exception as e:
            log.error(f'{ticker} {year} {report_type} 복구 실패: {e}')

    log.info(f'자산=부채+자본 복구 완료: {ok}/{len(targets)}개')


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


def supplement_cf(start_year: int = 2014, max_tickers: int = 300) -> None:
    """
    현금흐름표 계정 보완 수집 v2 (Lean).

    개선 사항 (v1 대비):
    - 공시목록(get_disclosures) 완전 스킵 → 13콜/종목 절약
    - 기존 financials의 fs_div 재사용 → CFS/OFS fallback 시도 불필요
    - max_tickers로 일일 쿼터 제어

    콜 수: 13년 × (FY + H1) × 1 fs_div = 26콜/종목
    기본값 300종목/일 × 26 = 7,800콜 (10,000 한도 대비 2,200 여유)
    2,522개 대상 기준 약 8.4일 완료.
    cron 재실행 시 NOT EXISTS 조건으로 자동 이어받기.
    """
    dart = DartAPI()
    end_year = date.today().year

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT s.ticker, s.corp_code,
                COALESCE(
                    (SELECT f.fs_div FROM financials f
                     WHERE f.ticker = s.ticker LIMIT 1),
                    'CFS'
                ) AS fs_div
            FROM stocks s
            WHERE s.is_excluded = FALSE
              AND s.corp_code IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM financials f
                WHERE f.ticker = s.ticker
                  AND f.account_nm = '영업활동현금흐름'
              )
            ORDER BY s.ticker
            LIMIT %s
        """, (max_tickers,))
        targets = cur.fetchall()  # (ticker, corp_code, fs_div)

    log.info(f'CF 보완 대상: {len(targets)}개 종목 (max={max_tickers})')
    ok, skip = 0, 0
    for ticker, corp_code, fs_div in targets:
        try:
            total = 0
            with db_conn() as conn:
                cur = conn.cursor()
                for year in range(start_year, end_year + 1):
                    for report_type in TARGET_REPORTS:  # FY + H1
                        reprt_code = REPRT_CODE[report_type]
                        items = dart.get_financial_statement(
                            corp_code, year, reprt_code, fs_div
                        )
                        if items:
                            total += _upsert_financials(
                                cur, ticker, corp_code, year, report_type, fs_div, items
                            )
                        time.sleep(0.1)
            if total > 0:
                ok += 1
                log.debug(f'{ticker} CF 추가: {total}행')
            else:
                skip += 1
                log.debug(f'{ticker} DART 데이터 없음')
        except QuotaExceededError:
            log.warning(f'DART 일일 쿼터 초과 — {ticker}에서 배치 중단 (성공={ok}, 건너뜀={skip})')
            break
        except Exception as e:
            log.error(f'{ticker} CF 보완 실패: {e}')
            _mark_error(ticker, str(e))

    log.info(f'CF 보완 완료: 성공={ok}, 건너뜀={skip}/{len(targets)}')


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
    parser.add_argument('--supplement-cf', action='store_true',
                        help='영업활동현금흐름 누락 종목에 CF 데이터 보완 수집')
    parser.add_argument('--max-tickers', type=int, default=300,
                        help='--supplement-cf 일일 처리 종목 수 (기본 300 ≈ 7,800콜/일)')
    parser.add_argument('--repair-ni', action='store_true',
                        help='당기순이익=0 버그 복구 (자본변동표 0값 덮어쓰기 수정)')
    parser.add_argument('--repair-equity', action='store_true',
                        help='자산≠부채+자본 버그 복구 (자본총계 alias 오매핑 수정)')
    parser.add_argument('--ticker', help='단일 종목 테스트')
    args = parser.parse_args()
    if args.skip_if_done:
        configure_logging('dart_retry.log')

    dart = DartAPI()

    if args.supplement_cf:
        configure_logging('dart_cf_supplement.log')
        supplement_cf(max_tickers=args.max_tickers)
    elif args.repair_ni:
        configure_logging('dart_ni_repair.log')
        repair_ni()
    elif args.repair_equity:
        configure_logging('dart_equity_repair.log')
        repair_equity()
    elif args.ticker:
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
