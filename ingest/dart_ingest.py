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
import xml.etree.ElementTree as ET
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
    '투자활동현금흐름': ['투자활동현금흐름', '투자활동으로인한현금흐름'],
    '재무활동현금흐름': ['재무활동현금흐름', '재무활동으로인한현금흐름'],
    '배당금지급':       ['배당금지급', '배당금의지급', '현금배당금지급'],
    '단기차입금':       ['단기차입금'],
    '유동성장기부채':   ['유동성장기부채', '유동성장기차입금', '유동성사채'],
    '장기차입금':       ['장기차입금'],
    '사채':             ['사채', '장기사채'],
}

# 각 계정의 허용 sj_nm 집합. 포괄손익계산서·자본변동표 등 오염 출처를 명시적으로 차단한다.
# fnlttSinglAcntAll은 동일 계정명을 여러 재무제표에 걸쳐 반환하므로 정확한 집합 매칭 사용.
_SJ_BS  = frozenset({'재무상태표', '연결재무상태표'})
_SJ_IS  = frozenset({'손익계산서', '연결손익계산서',
                     '포괄손익계산서', '연결포괄손익계산서'})
_SJ_CF  = frozenset({'현금흐름표', '연결현금흐름표'})

_CANONICAL_SJ: dict[str, frozenset] = {
    '매출액':             _SJ_IS,
    '매출총이익':         _SJ_IS,
    '영업이익':           _SJ_IS,
    '당기순이익':         _SJ_IS,
    '자산총계':           _SJ_BS,
    '부채총계':           _SJ_BS,
    '자본총계':           _SJ_BS,
    '지배기업소유주지분': _SJ_BS,
    '유동자산':           _SJ_BS,
    '유동부채':           _SJ_BS,
    '단기차입금':         _SJ_BS,
    '유동성장기부채':     _SJ_BS,
    '장기차입금':         _SJ_BS,
    '사채':               _SJ_BS,
    '영업활동현금흐름':   _SJ_CF,
    '투자활동현금흐름':   _SJ_CF,
    '재무활동현금흐름':   _SJ_CF,
    '배당금지급':         _SJ_CF,
}

# 역방향 조회 테이블: raw name → standard name
_RAW_TO_STD: dict[str, str] = {}
for std, aliases in ACCOUNT_ALIASES.items():
    for alias in aliases:
        _RAW_TO_STD[alias.replace(' ', '')] = std

# 재무상태표 계정 집합 — H1 시 frmtrm이 전기 FY말 잔액을 가리킴 (IS/CF는 전기 동기간)
_BS_ACCOUNTS: frozenset = frozenset(
    k for k, v in _CANONICAL_SJ.items() if v is _SJ_BS
)


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
        반환: {stock_code(ticker): corp_code}  — 상장법인만 포함 (stock_code 비어있는 항목 제외)
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
        root = ET.fromstring(xml_bytes)
        result = {}
        for item in root.findall('list'):
            corp_code  = (item.findtext('corp_code')  or '').strip()
            stock_code = (item.findtext('stock_code') or '').strip()
            if corp_code and stock_code:          # 상장법인만 (비상장은 stock_code 없음)
                result[stock_code] = corp_code
        log.info(f'상장법인 ticker→corp_code 매핑 {len(result)}개 다운로드')
        return result


# ── corp_code 매핑 ─────────────────────────────────────────────────────────────

def init_corp_codes(dart: DartAPI) -> None:
    """DART 법인코드 + 결산월을 stocks 테이블에 매핑 (ticker 기준 정확 매칭)."""
    try:
        ticker_to_corp = dart.download_corp_codes()  # {stock_code(ticker): corp_code}
    except Exception as e:
        # ZIP 다운로드 실패 시 경고 후 계속 (기존 DB 매핑 유지, 신규 종목만 누락)
        log.warning(f'corp_code ZIP 다운로드 실패 — 기존 매핑 유지하고 진행: {e}')
        ticker_to_corp = {}

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT ticker FROM stocks WHERE corp_code IS NULL")
        rows = cur.fetchall()
        matched = 0
        for (ticker,) in rows:
            code = ticker_to_corp.get(ticker)
            if code:
                cur.execute(
                    "UPDATE stocks SET corp_code = %s WHERE ticker = %s",
                    (code, ticker),
                )
                matched += 1
            else:
                log.debug(f'{ticker}: DART corp_code 없음 (비상장 or 상폐)')
        log.info(f'법인코드 매핑: {matched}/{len(rows)}개 매칭')

        # 결산월(fscl_month) 수집 — corp_code 있는 종목 전체 대상
        cur.execute("SELECT ticker, corp_code FROM stocks WHERE corp_code IS NOT NULL AND fscl_month IS NULL")
        fscl_rows = cur.fetchall()
        fscl_updated = 0
        for ticker, corp_code in fscl_rows:
            try:
                resp = dart.session.get(
                    f'{DART_BASE}/company.json',
                    params={'crtfc_key': dart.api_key, 'corp_code': corp_code},
                    timeout=10,
                )
                data = resp.json()
                fscl_month_str = (data.get('acc_mt') or '').strip()
                if fscl_month_str.isdigit():
                    cur.execute(
                        "UPDATE stocks SET fscl_month = %s WHERE ticker = %s",
                        (int(fscl_month_str), ticker),
                    )
                    fscl_updated += 1
                time.sleep(0.05)
            except Exception as e:
                log.debug(f'{ticker} fscl_month 조회 실패: {e}')
        log.info(f'결산월 수집: {fscl_updated}/{len(fscl_rows)}개 완료')


# ── 수집 핵심 로직 ─────────────────────────────────────────────────────────────

def _upsert_financials(cur, ticker: str, corp_code: str, year: int,
                        report_type: str, fs_div: str, items: list[dict]) -> int:
    """재무제표 항목 → financials 테이블 upsert. 반환: 저장 행 수."""
    saved = 0
    _jibae_idx = 0      # BS '지배' 키워드 fallback 카운터
    _nci_idx   = 0      # BS '비지배' 키워드 fallback 카운터
    for item in items:
        raw_nm = (item.get('account_nm') or '').strip()
        sj_nm  = (item.get('sj_nm') or '').replace(' ', '')
        std_nm = standardize_account(raw_nm)
        if std_nm is None:
            cleaned = raw_nm.replace(' ', '')
            if sj_nm in _SJ_BS:
                if '비지배' in cleaned:
                    _nci_idx += 1
                    std_nm = f'비지배지분_{_nci_idx}'
                elif '지배' in cleaned:
                    _jibae_idx += 1
                    std_nm = f'지배기업소유주지분_{_jibae_idx}'
                else:
                    continue
            else:
                continue

        allowed_sj = _CANONICAL_SJ.get(std_nm)
        if allowed_sj:
            if sj_nm not in allowed_sj:
                continue

        # H1/Q1/Q3 IS·CF 계정은 YTD 누계(thstrm_add_amount) 우선, BS 계정은 thstrm_amount 그대로
        # 키워드 fallback(_N 형태)은 _BS_ACCOUNTS 미등록이므로 sj_nm으로 BS 여부 판단
        is_bs = (std_nm in _BS_ACCOUNTS) or (sj_nm in _SJ_BS)
        amount = None
        if report_type in ('H1', 'Q1', 'Q3') and not is_bs:
            add_str = item.get('thstrm_add_amount', '') or ''
            try:
                amount = float(add_str.replace(',', ''))
            except (ValueError, AttributeError):
                pass

        if amount is None:
            amount_str = item.get('thstrm_amount', '') or ''
            try:
                amount = float(amount_str.replace(',', ''))
            except (ValueError, AttributeError):
                log.debug(f'{ticker} {year} {report_type} {std_nm}: thstrm 파싱 실패')

        frmtrm_str = item.get('frmtrm_amount', '') or ''
        try:
            frmtrm_amount = float(frmtrm_str.replace(',', ''))
        except (ValueError, AttributeError):
            frmtrm_amount = None

        cur.execute(
            """
            INSERT INTO financials (ticker, corp_code, year, report_type, fs_div, account_nm, amount, frmtrm_amount)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker, year, report_type, fs_div, account_nm) DO UPDATE SET
                amount = EXCLUDED.amount,
                frmtrm_amount = EXCLUDED.frmtrm_amount
            """,
            (ticker, corp_code, year, report_type, fs_div, std_nm, amount, frmtrm_amount),
        )
        saved += 1
    return saved


def _check_bs_integrity(cur, ticker: str, year: int,
                         report_type: str, fs_div: str) -> None:
    """upsert 직후 두 가지 BS 항등식 검증. 1% 초과 오차 시 WARNING 로그.
    ① 자산 = 부채 + 자본
    ② 자본 = 지배기업소유주지분 + 비지배지분  (두 항목이 모두 있을 때만)
    """
    cur.execute(
        """
        SELECT account_nm, amount FROM financials
        WHERE ticker=%s AND year=%s AND report_type=%s AND fs_div=%s
          AND (account_nm IN ('자산총계','부채총계','자본총계','지배기업소유주지분','비지배지분_1')
               OR account_nm LIKE '지배기업소유주지분_%%')
        """,
        (ticker, year, report_type, fs_div),
    )
    row = {r[0]: float(r[1]) for r in cur.fetchall() if r[1] is not None}
    assets = row.get('자산총계')
    liab   = row.get('부채총계')
    equity = row.get('자본총계')

    # ① 자산 = 부채 + 자본
    if assets and liab is not None and equity is not None and assets != 0:
        err = abs(assets - liab - equity) / abs(assets)
        if err > 0.01:
            log.warning(
                f'BS_INTEGRITY {ticker} {year} {report_type} {fs_div}: '
                f'자산≠부채+자본 오차 {err:.1%} '
                f'(자산={assets:.0f} 부채={liab:.0f} 자본={equity:.0f})'
            )

    # ② 자본 = 지배기업소유주지분 + 비지배지분
    # alias 매핑 우선, 없을 때만 _N fallback 사용
    ctrl = row.get('지배기업소유주지분')
    if ctrl is None:
        for k, v in row.items():
            if k.startswith('지배기업소유주지분_'):
                ctrl = v
                break
    nci = row.get('비지배지분_1')
    if equity is not None and ctrl is not None and nci is not None and equity != 0:
        err2 = abs(equity - ctrl - nci) / abs(equity)
        if err2 > 0.01:
            log.warning(
                f'EQUITY_SPLIT {ticker} {year} {report_type} {fs_div}: '
                f'자본≠지배+비지배 오차 {err2:.1%} '
                f'(자본={equity:.0f} 지배={ctrl:.0f} 비지배={nci:.0f})'
            )


def _check_frmtrm_consistency(cur, ticker: str, year: int,
                               report_type: str, fs_div: str) -> None:
    """frmtrm_amount vs 전년도 저장 amount 교차검증.
    재무상태표·손익계산서·현금흐름표 전 계정 대상.
    1% 초과 불일치 시 FRMTRM_MISMATCH 경고 로그.
    H1 재무상태표 계정: frmtrm = FY 전년말 잔액 기준으로 비교.
    H1 손익/현금흐름 계정: frmtrm = H1 전년 동기 기준으로 비교.
    """
    cur.execute(
        """
        SELECT account_nm, frmtrm_amount
        FROM financials
        WHERE ticker=%s AND year=%s AND report_type=%s AND fs_div=%s
          AND frmtrm_amount IS NOT NULL
        """,
        (ticker, year, report_type, fs_div),
    )
    rows = cur.fetchall()
    if not rows:
        return

    prev_year = year - 1
    for account_nm, frmtrm_val in rows:
        frmtrm_float = float(frmtrm_val)
        prev_rpt = 'FY' if (report_type == 'H1' and account_nm in _BS_ACCOUNTS) else report_type

        cur.execute(
            """
            SELECT amount FROM financials
            WHERE ticker=%s AND year=%s AND report_type=%s AND fs_div=%s AND account_nm=%s
            """,
            (ticker, prev_year, prev_rpt, fs_div, account_nm),
        )
        row = cur.fetchone()
        if row is None or row[0] is None:
            continue

        prev_amount = float(row[0])
        if prev_amount == 0:
            continue

        diff = abs(frmtrm_float - prev_amount) / abs(prev_amount)
        if diff > 0.10:
            log.warning(
                f'FRMTRM_MISMATCH {ticker} {year} {report_type} {fs_div} {account_nm}: '
                f'frmtrm={frmtrm_float:,.0f} vs {prev_year}/{prev_rpt}={prev_amount:,.0f} '
                f'(차이 {diff:.1%})'
            )
        elif diff > 0.01:
            log.debug(
                f'FRMTRM_MINOR {ticker} {year} {report_type} {fs_div} {account_nm}: '
                f'차이 {diff:.1%} (재작성 가능성)'
            )


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

        # ticker fs_div 결정 — 연도별 CFS/OFS 혼재 방지
        # 가장 최근 FY 기준으로 CFS 존재 여부 확인 후 전 연도에 일관 적용
        fs_div = 'OFS'
        for check_year in (end_year, end_year - 1):
            probe = dart.get_financial_statement(corp_code, check_year, REPRT_CODE['FY'], 'CFS')
            if probe:
                fs_div = 'CFS'
                break
            time.sleep(0.1)
        log.info(f'{ticker} fs_div={fs_div}')

        # 재무제표 수집
        total = 0
        for year in range(start_year, end_year + 1):
            for report_type in TARGET_REPORTS:
                reprt_code = REPRT_CODE[report_type]
                items = dart.get_financial_statement(corp_code, year, reprt_code, fs_div)
                if items:
                    n = _upsert_financials(
                        cur, ticker, corp_code, year, report_type, fs_div, items
                    )
                    total += n
                    _check_bs_integrity(cur, ticker, year, report_type, fs_div)
                    _check_frmtrm_consistency(cur, ticker, year, report_type, fs_div)
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



def ingest_all(skip_if_done: bool = False, max_tickers: int = 0) -> None:
    """stocks 테이블 전종목 DART 수집 (14일+ 분산 실행).

    max_tickers > 0 이면 해당 수만큼만 처리하고 중단 (파일럿/일별 분산용).
    """
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

    if max_tickers > 0:
        targets = targets[:max_tickers]
    log.info(f'DART 수집 대상: {len(targets)}개 종목')
    for ticker, corp_code in targets:
        try:
            ingest_company(dart, ticker, corp_code)
        except QuotaExceededError as e:
            log.error(f'{ticker} 수집 실패: {e}')
            _mark_error(ticker, str(e))
            break  # 쿼터 초과 시 배치 즉시 중단
        except Exception as e:
            log.error(f'{ticker} 수집 실패: {e}')
            _mark_error(ticker, str(e))


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-if-done', action='store_true')
    parser.add_argument('--max-tickers', type=int, default=0,
                        help='처리 종목 수 제한 (0=무제한). 파일럿/일별 분산용')
    parser.add_argument('--repair-ni', action='store_true',
                        help='당기순이익=0 버그 복구 (자본변동표 0값 덮어쓰기 수정)')
    parser.add_argument('--repair-equity', action='store_true',
                        help='자산≠부채+자본 버그 복구 (자본총계 alias 오매핑 수정)')
    parser.add_argument('--ticker', help='단일 종목 테스트')
    args = parser.parse_args()
    if args.skip_if_done:
        configure_logging('dart_retry.log')

    dart = DartAPI()

    if args.repair_ni:
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
        ingest_all(skip_if_done=args.skip_if_done, max_tickers=args.max_tickers)


if __name__ == '__main__':
    main()
