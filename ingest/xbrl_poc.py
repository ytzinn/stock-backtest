"""
DART XBRL 원본 공시 파싱 PoC.

목적:
  1. 전제 조건 확인 — disclosures에 원본(비정정) rcept_no가 실제로 있는지
  2. 최근 케이스 — 정정이 확인된 종목(FY2022~)에서 XBRL 파싱 + DB값 비교
  3. 구형 케이스 — FY2015~2016 종목에서 XBRL 인스턴스 제공 여부 확인
  4. 핵심 검증 — XBRL원본값 ≠ 현재 DB amount (같으면 접근법 전제 붕괴 → 중단)

실행 (서버에서):
  cd /opt/stock-backtest
  venv/bin/python -m ingest.xbrl_poc --ticker 039230 --year 2022
  venv/bin/python -m ingest.xbrl_poc --ticker 039230 --year 2022 --report-type FY
  venv/bin/python -m ingest.xbrl_poc --old          # 2015~2016 구형 케이스 스캔
  venv/bin/python -m ingest.xbrl_poc --prereq-check # 전제조건만 확인
"""
import argparse
import io
import logging
import os
import xml.etree.ElementTree as ET
import zipfile
from typing import Optional

from dotenv import load_dotenv

from ingest.connection import db_conn
from ingest.dart_ingest import DART_BASE, REPRT_CODE, DartAPI, QuotaExceededError
from ingest.logging_config import configure_logging

load_dotenv()
configure_logging('xbrl_poc.log')
log = logging.getLogger(__name__)

# 테스트 대상 — 실제 정정공시가 있었던 종목으로 교체 가능
DEFAULT_TICKER = '039230'   # FRMTRM 불일치 확인된 케이스
DEFAULT_YEAR   = 2022
DEFAULT_REPORT = 'FY'


# ── 전제조건 확인 ──────────────────────────────────────────────────────────────

def check_prerequisite(cur) -> None:
    """
    disclosures 테이블에 정정 전 원본 rcept_no가 실제로 저장돼 있는지 확인.
    정정공시(is_amendment 예정)가 있는 그룹 중 원본도 함께 있는 비율을 출력.
    """
    print("\n=== [전제조건 확인] disclosures 원본 rcept_no 존재 여부 ===")

    # 전체 통계
    cur.execute("SELECT COUNT(*) FROM disclosures")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM disclosures WHERE report_nm LIKE '%정정%'")
    amendments = cur.fetchone()[0]
    print(f"전체 공시 수: {total:,}  /  정정공시 수: {amendments:,}")

    # 정정공시가 있는 (ticker, year, report_type) 그룹에서 원본도 함께 있는지
    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE original_cnt > 0) AS groups_with_original,
            COUNT(*) AS total_amendment_groups
        FROM (
            SELECT
                ticker, year, report_type,
                COUNT(*) FILTER (WHERE report_nm NOT LIKE '%정정%') AS original_cnt,
                COUNT(*) FILTER (WHERE report_nm LIKE '%정정%')     AS amendment_cnt
            FROM disclosures
            GROUP BY ticker, year, report_type
            HAVING COUNT(*) FILTER (WHERE report_nm LIKE '%정정%') > 0
        ) sub
    """)
    row = cur.fetchone()
    if row and row[1]:
        groups_with_orig, total_groups = row
        pct = groups_with_orig / total_groups * 100
        print(f"정정공시 있는 그룹: {total_groups}개  /  원본 rcept_no도 있는 그룹: {groups_with_orig}개 ({pct:.1f}%)")
        if pct < 80:
            print("⚠️  원본 rcept_no 보유율이 80% 미만 — list.json 재수집 선행 작업 필요 가능성")
        else:
            print("✅  원본 rcept_no 충분히 확보됨")
    else:
        print("정정공시 있는 그룹 없음 또는 쿼리 실패")

    # 샘플 출력 (원본 있는 것)
    cur.execute("""
        SELECT ticker, year, report_type,
               MIN(rcept_no) FILTER (WHERE report_nm NOT LIKE '%정정%') AS orig_rcept,
               MIN(rcept_dt) FILTER (WHERE report_nm NOT LIKE '%정정%') AS orig_dt,
               MAX(rcept_dt) FILTER (WHERE report_nm LIKE '%정정%')     AS amend_dt
        FROM disclosures
        GROUP BY ticker, year, report_type
        HAVING COUNT(*) FILTER (WHERE report_nm LIKE '%정정%') > 0
           AND COUNT(*) FILTER (WHERE report_nm NOT LIKE '%정정%') > 0
        ORDER BY amend_dt DESC
        LIMIT 10
    """)
    rows = cur.fetchall()
    if rows:
        print("\n샘플 (정정 + 원본 모두 있는 최근 10개):")
        print(f"{'ticker':>8} {'year':>4} {'type':>4}  {'원본rcept_no':>20}  {'원본공시일':>12}  {'정정공시일':>12}")
        for r in rows:
            print(f"{r[0]:>8} {r[1]:>4} {r[2]:>4}  {r[3] or 'N/A':>20}  {str(r[4]):>12}  {str(r[5]):>12}")


def get_original_rcept_no(cur, ticker: str, year: int, report_type: str) -> Optional[str]:
    """해당 종목/연도/보고서의 원본(비정정) rcept_no 중 가장 이른 것 반환."""
    cur.execute(
        """
        SELECT rcept_no, rcept_dt, report_nm
        FROM disclosures
        WHERE ticker      = %s
          AND year        = %s
          AND report_type = %s
        ORDER BY rcept_dt ASC
        """,
        (ticker, year, report_type),
    )
    rows = cur.fetchall()
    if not rows:
        return None

    print(f"\n{ticker} {year} {report_type} — 공시 목록:")
    for rcept_no, rcept_dt, report_nm in rows:
        flag = "[정정]" if "정정" in (report_nm or "") else "[원본]"
        print(f"  {flag}  {rcept_dt}  {rcept_no}  {report_nm}")

    # 정정이 아닌 것 중 첫 번째
    for rcept_no, rcept_dt, report_nm in rows:
        if "정정" not in (report_nm or ""):
            return rcept_no
    return None


# ── XBRL 다운로드 + 파싱 ───────────────────────────────────────────────────────

def download_xbrl(dart: DartAPI, rcept_no: str, reprt_code: str) -> Optional[bytes]:
    """
    fnlttXbrl.xml (DS003) — 재무제표 원본 XBRL ZIP 다운로드.
    document.xml(DS001)과 다름: DS001은 보고서 전체 텍스트, DS003은 재무 XBRL 전용.
    """
    resp = dart.session.get(
        f'{DART_BASE}/fnlttXbrl.xml',
        params={
            'rcept_no':   rcept_no,
            'reprt_code': reprt_code,
            'crtfc_key':  dart.api_key,
        },
        timeout=90,
    )
    if resp.status_code != 200:
        print(f"  HTTP {resp.status_code} — XBRL 없음 또는 접근 불가")
        return None
    content_type = resp.headers.get('Content-Type', '')
    if 'json' in content_type:
        # 에러 응답이 JSON으로 올 수 있음
        try:
            data = resp.json()
            print(f"  API 에러 응답: {data.get('status')} {data.get('message')}")
        except Exception:
            pass
        return None
    return resp.content


def _split_tag(tag: str) -> tuple[str, str]:
    """'{namespace}localname' → (namespace, localname)."""
    if tag.startswith('{'):
        ns, local = tag[1:].split('}', 1)
        return ns, local
    return '', tag


def parse_xbrl_zip(zip_bytes: bytes) -> dict[tuple[str, str, str], float]:
    """
    XBRL ZIP에서 재무값 추출.

    XBRL 구조:
    - <context id="..."> 요소: 기간(period)과 엔티티(CFS/OFS) 정의
    - 실제 재무값 요소: contextRef 속성으로 context를 참조

    반환: {(account_label, fs_div, period_type): amount}
      - account_label: XBRL 로컬명 (예: 'Revenue', 'OperatingIncome')
      - fs_div: 'CFS' or 'OFS'
      - period_type: 'current' or 'prior' (당기/전기)
    """
    CONSOLIDATED_MEMBER = 'ConsolidatedMember'
    SEPARATE_MEMBER     = 'SeparateMember'
    # ConsolidatedAndSeparateFinancialStatementsAxis의 dimension을 보고 CFS/OFS 판단
    FS_DIMENSION = 'ConsolidatedAndSeparateFinancialStatementsAxis'

    result: dict[tuple[str, str, str], float] = {}

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        filenames = zf.namelist()
        print(f"\n  ZIP 내부 파일 목록 ({len(filenames)}개):")
        for name in filenames:
            size = zf.getinfo(name).file_size
            print(f"    {name}  ({size:,} bytes)")

        xbrl_files = [n for n in filenames if n.endswith('.xbrl')]
        if not xbrl_files:
            print("  .xbrl 파일 없음")
            return result

        xbrl_name = xbrl_files[0]
        print(f"\n  --- {xbrl_name} 파싱 ---")
        try:
            xml_bytes = zf.read(xbrl_name)
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as e:
            print(f"  XML 파싱 실패: {e}")
            return result

    # Step 1: context 맵 구성 — {context_id: {fs_div, period_type, start, end}}
    # context_id → {'fs_div': 'CFS'|'OFS'|None, 'period_type': 'current'|'prior'|'instant'}
    contexts: dict[str, dict] = {}

    # 모든 context 요소 찾기
    for elem in root.iter():
        _, local = _split_tag(elem.tag)
        if local != 'context':
            continue
        ctx_id = elem.get('id', '')
        if not ctx_id:
            continue

        fs_div = None
        period_type = None
        start_date = end_date = instant = None

        for child in elem.iter():
            _, child_local = _split_tag(child.tag)
            if child_local == 'explicitMember':
                dim = child.get('dimension', '')
                val = (child.text or '').strip()
                if FS_DIMENSION in dim:
                    if CONSOLIDATED_MEMBER in val:
                        fs_div = 'CFS'
                    elif SEPARATE_MEMBER in val:
                        fs_div = 'OFS'
            elif child_local == 'startDate':
                start_date = (child.text or '').strip()
            elif child_local == 'endDate':
                end_date = (child.text or '').strip()
            elif child_local == 'instant':
                instant = (child.text or '').strip()

        # period_type 판단: 가장 긴 endDate(또는 instant)가 당기, 그 외 전기
        if instant:
            period_type = 'instant'
            period_date = instant
        elif end_date:
            period_type = 'duration'
            period_date = end_date
        else:
            period_date = ''

        contexts[ctx_id] = {
            'fs_div':      fs_div,
            'period_type': period_type,
            'period_date': period_date,
            'start':       start_date,
            'end':         end_date,
            'instant':     instant,
        }

    if not contexts:
        print("  context 요소 없음 — XBRL 구조 예상과 다름")
        return result

    # 당기/전기 날짜 분류: period_date 중 최대값 = 당기
    all_dates = sorted({c['period_date'] for c in contexts.values() if c.get('period_date')}, reverse=True)
    if len(all_dates) >= 2:
        current_date = all_dates[0]   # 가장 늦은 날짜 = 당기
        prior_date   = all_dates[1]   # 그 다음 = 전기
    elif len(all_dates) == 1:
        current_date = all_dates[0]
        prior_date   = None
    else:
        current_date = prior_date = None

    print(f"  당기 기준일: {current_date}  /  전기 기준일: {prior_date}")
    print(f"  context 수: {len(contexts)}")

    # context별 당기/전기 분류
    def resolve_period(ctx: dict) -> str:
        pd = ctx.get('period_date', '')
        if pd == current_date:
            return 'current'
        elif pd == prior_date:
            return 'prior'
        return 'other'

    # Step 2: 재무값 elements 추출 (contextRef 속성 있는 것)
    ns_set: set[str] = set()
    extracted: list[tuple[str, str, str, float]] = []  # (local_name, fs_div, period, amount)

    for elem in root.iter():
        ctx_ref = elem.get('contextRef')
        if ctx_ref is None:
            continue  # 데이터 요소가 아님 (context, unit 등)
        ns, local = _split_tag(elem.tag)
        if not elem.text or not elem.text.strip():
            continue
        try:
            amount = float(elem.text.strip())
        except ValueError:
            continue

        ns_set.add(ns)
        ctx = contexts.get(ctx_ref, {})
        fs_div_ctx = ctx.get('fs_div')
        period     = resolve_period(ctx)

        if fs_div_ctx and period in ('current', 'prior'):
            key = (local, fs_div_ctx, period)
            # 같은 key 충돌 시 절댓값 큰 쪽 우선 (더 상위 합계 계정일 가능성)
            if key not in result or abs(amount) > abs(result.get(key, 0)):
                result[key] = amount
            extracted.append((local, fs_div_ctx, period, amount))

    print(f"  사용된 네임스페이스 ({len(ns_set)}개):")
    for ns in sorted(ns_set):
        ns_short = ns.rstrip('/').split('/')[-1]
        print(f"    [{ns_short}] {ns}")

    # 추출값 샘플 출력
    # 당기 CFS 위주로 정렬해서 출력
    cfs_current = [(l, p, v) for l, fs, p, v in extracted if fs == 'CFS' and p == 'current']
    ofs_current = [(l, p, v) for l, fs, p, v in extracted if fs == 'OFS' and p == 'current']
    print(f"\n  당기 CFS 값 ({len(cfs_current)}개) — 앞 25개:")
    for local, _, val in sorted(cfs_current, key=lambda x: abs(x[2]), reverse=True)[:25]:
        print(f"    {local:50s} = {val:>20,.0f}")
    if ofs_current:
        print(f"\n  당기 OFS 값 ({len(ofs_current)}개) — 앞 10개:")
        for local, _, val in sorted(ofs_current, key=lambda x: abs(x[2]), reverse=True)[:10]:
            print(f"    {local:50s} = {val:>20,.0f}")

    return result


def inspect_xbrl_zip(zip_bytes: bytes) -> dict[str, list[tuple[str, str, str]]]:
    """레거시 호환용 래퍼 — parse_xbrl_zip()으로 대체됨. compare_with_db에 전달할 형식으로 변환."""
    values = parse_xbrl_zip(zip_bytes)
    # {filename: [(ns, local, text), ...]} 형식으로 변환
    entries = [(ns, local, str(v)) for (local, ns, period), v in values.items() if period == 'current']
    return {'parsed': entries}


# ── DB 값과 비교 ────────────────────────────────────────────────────────────────

# XBRL 로컬명 → 표준 account_nm (매핑 후보, PoC에서 실태 확인용)
# fnlttXbrl.xml의 IFRS taxonomy 태그명 기반 (실제 PoC 실행 후 보완 필요)
_XBRL_CANDIDATE_MAP: dict[str, str] = {
    # ifrs-full taxonomy
    'Revenue':                        '매출액',
    'RevenueFromContractsWithCustomers': '매출액',
    'GrossProfit':                    '매출총이익',
    'ProfitLossFromOperatingActivities': '영업이익',
    'OperatingIncomeLoss':            '영업이익',
    'ProfitLoss':                     '당기순이익',
    'Assets':                         '자산총계',
    'Liabilities':                    '부채총계',
    'Equity':                         '자본총계',
    'CurrentAssets':                  '유동자산',
    'CurrentLiabilities':             '유동부채',
    'CashFlowsFromUsedInOperatingActivities': '영업활동현금흐름',
    'CashFlowsFromUsedInInvestingActivities': '투자활동현금흐름',
    'CashFlowsFromUsedInFinancingActivities': '재무활동현금흐름',
    'EquityAttributableToOwnersOfParent': '지배기업소유주지분',
    'DividendsPaid':                  '배당금지급',
    # dart taxonomy (dart:* prefix)
    'OperatingIncome':                '영업이익',
    'NetIncome':                      '당기순이익',
    'TotalAssets':                    '자산총계',
    'TotalLiabilities':               '부채총계',
    'TotalEquity':                    '자본총계',
    'CashFlowsFromOperatingActivities': '영업활동현금흐름',
}


def compare_with_db(cur, ticker: str, year: int, report_type: str,
                    xbrl_values: dict[tuple[str, str, str], float]) -> None:
    """XBRL 파싱값과 현재 DB amount를 계정명 매핑 기반으로 비교. 같으면 경고."""
    cur.execute(
        """
        SELECT account_nm, fs_div, amount, frmtrm_amount, original_amount
        FROM financials
        WHERE ticker = %s AND year = %s AND report_type = %s
        ORDER BY fs_div, account_nm
        """,
        (ticker, year, report_type),
    )
    db_rows = {(r[1], r[0]): (r[2], r[3], r[4]) for r in cur.fetchall()}

    if not db_rows:
        print(f"\n⚠️  DB에 {ticker} {year} {report_type} 데이터 없음")
        return

    print(f"\n=== DB vs XBRL 비교 ({ticker} {year} {report_type}) ===")
    print(f"{'fs':>4} {'account_nm':>24} {'DB amount':>18} {'XBRL current':>18} {'diff%':>7} {'match?':>7}")

    xbrl_by_std: dict[tuple[str, str], float] = {}
    for (xbrl_local, fs_div, period), val in xbrl_values.items():
        std_name = _XBRL_CANDIDATE_MAP.get(xbrl_local)
        if std_name and period == 'current':
            key = (fs_div, std_name)
            if key not in xbrl_by_std or abs(val) > abs(xbrl_by_std[key]):
                xbrl_by_std[key] = val

    match_count = same_as_db = 0
    for (fs_div, acct), (db_amt, frmtrm, orig) in sorted(db_rows.items()):
        xbrl_val = xbrl_by_std.get((fs_div, acct))
        db_str  = f"{float(db_amt):>18,.0f}" if db_amt is not None else f"{'NULL':>18}"
        xbrl_str = f"{xbrl_val:>18,.0f}" if xbrl_val is not None else f"{'(없음)':>18}"
        if db_amt is not None and xbrl_val is not None:
            diff_pct = abs(float(db_amt) - xbrl_val) / max(abs(float(db_amt)), 1) * 100
            diff_str = f"{diff_pct:>7.1f}"
            if diff_pct < 0.01:
                match = "같음 ⚠️"
                same_as_db += 1
            else:
                match = "다름 ✅"
                match_count += 1
        else:
            diff_str = f"{'—':>7}"
            match = f"{'(매핑없음)':>7}"
        print(f"{fs_div:>4} {acct:>24} {db_str} {xbrl_str} {diff_str} {match}")

    print(f"\n핵심 검증 결과:")
    if match_count > 0:
        print(f"  ✅  XBRL값이 DB amount와 다른 계정: {match_count}개")
        print(f"     → 원본 rcept_no가 정정 전 값을 반환함 (접근법 전제 유효)")
    if same_as_db > 0:
        print(f"  ⚠️  XBRL값이 DB amount와 같은 계정: {same_as_db}개")
        print(f"     → 이 계정들은 정정이 없었거나, 원본에도 정정값이 반영됐을 수 있음")
    if match_count == 0 and same_as_db == 0:
        print(f"  ⚠️  XBRL→표준 계정명 매핑 실패 — _XBRL_CANDIDATE_MAP 보완 필요")
        print(f"  XBRL 로컬명 전체 목록:")
        for (local, fs_div, period), val in xbrl_values.items():
            if period == 'current':
                print(f"    [{fs_div}] {local} = {val:,.0f}")


# ── 구형 케이스 스캔 ────────────────────────────────────────────────────────────

def scan_old_coverage(dart: DartAPI, cur, sample_size: int = 5) -> None:
    """
    2015~2016년 공시 중 정정공시 있는 케이스 샘플링해서 XBRL 커버리지 확인.
    """
    print("\n=== [구형 커버리지] FY2015~2016 XBRL 제공 여부 ===")

    # f-string: LIMIT에 정수 직접 삽입, SQL에 psycopg2 %s 파라미터 없음
    cur.execute(f"""
        SELECT d.ticker, d.year, d.report_type,
               MIN(d.rcept_no) FILTER (WHERE d.report_nm NOT LIKE '%정정%') AS orig_rcept
        FROM disclosures d
        WHERE d.year BETWEEN 2014 AND 2016
          AND d.report_type = 'FY'
        GROUP BY d.ticker, d.year, d.report_type
        HAVING COUNT(*) FILTER (WHERE d.report_nm NOT LIKE '%정정%') > 0
        LIMIT {int(sample_size)}
    """)
    rows = cur.fetchall()
    if not rows:
        print("  2014~2016 공시 없음 (DB에 해당 연도 데이터 미수집 가능성)")
        return

    success, fail = 0, 0
    for ticker, year, report_type, orig_rcept in rows:
        reprt_code = REPRT_CODE[report_type]
        print(f"\n  {ticker} {year} {report_type} ({orig_rcept})")
        xbrl_bytes = download_xbrl(dart, orig_rcept, reprt_code)
        if xbrl_bytes:
            try:
                with zipfile.ZipFile(io.BytesIO(xbrl_bytes)) as zf:
                    xbrl_files = [n for n in zf.namelist() if n.endswith('.xbrl')]
                print(f"    ✅  XBRL 파일 {len(xbrl_files)}개 포함")
                success += 1
            except zipfile.BadZipFile:
                print(f"    ✗  ZIP 파싱 실패 (XBRL 없음)")
                fail += 1
        else:
            fail += 1

    total = success + fail
    if total:
        print(f"\n  구형 커버리지: {success}/{total} ({success/total*100:.0f}%)")
        if success / total < 0.3:
            print("  ⚠️  커버리지 30% 미만 — 백테스트 시작연도 상향(2018년 이후) 검토 권장")


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='DART XBRL 원본 공시 파싱 PoC')
    parser.add_argument('--ticker',      default=DEFAULT_TICKER)
    parser.add_argument('--year',        type=int, default=DEFAULT_YEAR)
    parser.add_argument('--report-type', default=DEFAULT_REPORT, choices=['FY', 'H1'])
    parser.add_argument('--prereq-check', action='store_true', help='전제조건만 확인')
    parser.add_argument('--old',          action='store_true', help='구형(2015~2016) 커버리지 스캔')
    args = parser.parse_args()

    dart = DartAPI()

    with db_conn() as conn:
        cur = conn.cursor()

        check_prerequisite(cur)

        if args.prereq_check:
            return

        if args.old:
            scan_old_coverage(dart, cur)
            return

        ticker      = args.ticker
        year        = args.year
        report_type = args.report_type
        reprt_code  = REPRT_CODE[report_type]

        print(f"\n=== [{ticker} {year} {report_type}] XBRL PoC ===")

        orig_rcept = get_original_rcept_no(cur, ticker, year, report_type)
        if not orig_rcept:
            print("  원본 rcept_no 없음 — 전제조건 미충족 또는 정정공시 없는 종목")
            return

        print(f"\nfnlttXbrl.xml 다운로드: rcept_no={orig_rcept}, reprt_code={reprt_code}")
        xbrl_bytes = download_xbrl(dart, orig_rcept, reprt_code)
        if not xbrl_bytes:
            print("XBRL 다운로드 실패 — 종료")
            return

        print(f"  ZIP 크기: {len(xbrl_bytes):,} bytes")
        xbrl_values = parse_xbrl_zip(xbrl_bytes)

        if not xbrl_values:
            print("  재무값 추출 실패 — .xbrl 파일 없거나 파싱 오류")
        else:
            compare_with_db(cur, ticker, year, report_type, xbrl_values)


if __name__ == '__main__':
    main()
