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


def inspect_xbrl_zip(zip_bytes: bytes) -> dict[str, list[tuple[str, str, str]]]:
    """
    ZIP 내부 구조 출력 + 네임스페이스/태그명 추출.
    반환: {파일명: [(namespace, local_name, text), ...]}
    """
    result = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        filenames = zf.namelist()
        print(f"\n  ZIP 내부 파일 목록 ({len(filenames)}개):")
        for name in filenames:
            size = zf.getinfo(name).file_size
            print(f"    {name}  ({size:,} bytes)")

        for name in filenames:
            if not name.endswith('.xbrl'):
                continue
            print(f"\n  --- {name} 파싱 ---")
            try:
                xml_bytes = zf.read(name)
                root = ET.fromstring(xml_bytes)
            except ET.ParseError as e:
                print(f"    XML 파싱 실패: {e}")
                continue

            # 네임스페이스 수집
            ns_set: set[str] = set()
            entries: list[tuple[str, str, str]] = []
            for elem in root.iter():
                tag = elem.tag
                if tag.startswith('{'):
                    ns, local = tag[1:].split('}', 1)
                    ns_set.add(ns)
                else:
                    ns, local = '', tag
                if elem.text and elem.text.strip():
                    entries.append((ns, local, elem.text.strip()))

            print(f"    네임스페이스 ({len(ns_set)}개):")
            for ns in sorted(ns_set):
                print(f"      {ns}")

            print(f"\n    값 있는 element ({len(entries)}개) — 앞 40개:")
            for ns, local, text in entries[:40]:
                ns_short = ns.split('/')[-1] if ns else ''
                print(f"      [{ns_short}] {local} = {text}")

            result[name] = entries

    return result


# ── DB 값과 비교 ────────────────────────────────────────────────────────────────

def compare_with_db(cur, ticker: str, year: int, report_type: str,
                    xbrl_entries: dict[str, list]) -> None:
    """XBRL에서 추출한 값과 현재 DB amount를 비교. 같으면 경고."""
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

    print(f"\n=== DB 값 ({ticker} {year} {report_type}) ===")
    print(f"{'fs_div':>6} {'account_nm':>20} {'DB amount':>16} {'original_amount':>16}")
    for (fs_div, acct), (amt, frmtrm, orig) in sorted(db_rows.items()):
        orig_str = f"{orig:,.0f}" if orig is not None else "NULL"
        amt_str  = f"{amt:,.0f}"  if amt  is not None else "NULL"
        print(f"{fs_div:>6} {acct:>20} {amt_str:>16} {orig_str:>16}")

    # XBRL에서 숫자값 추출 시도 (단순 비교용)
    all_xbrl_vals: list[tuple[str, str, float]] = []
    for filename, entries in xbrl_entries.items():
        for ns, local, text in entries:
            try:
                val = float(text.replace(',', ''))
                all_xbrl_vals.append((local, ns, val))
            except ValueError:
                pass

    if not all_xbrl_vals:
        print("\n  XBRL에서 숫자값 추출 실패")
        return

    print(f"\n  XBRL 숫자값 ({len(all_xbrl_vals)}개) — 앞 20개:")
    for local, ns, val in all_xbrl_vals[:20]:
        print(f"    {local:40s} = {val:>18,.0f}")

    # 핵심 검증: DB amount와 일치하는 XBRL 값이 있으면 경고
    db_amounts = {amt for _, (amt, _, _) in db_rows.items() if amt is not None}
    xbrl_amounts = {v for _, _, v in all_xbrl_vals}
    overlap = db_amounts & xbrl_amounts
    print(f"\n핵심 검증:")
    if overlap:
        print(f"  ⚠️  DB amount와 XBRL 값이 일치하는 항목 {len(overlap)}개 발견")
        print(f"     → DART가 원본 rcept_no에도 정정 후 값을 내려줄 가능성 있음")
        print(f"     → 정정이 실제로 없었던 케이스이거나, 접근법 전제 재검토 필요")
        for v in sorted(overlap)[:5]:
            print(f"     {v:,.0f}")
    else:
        print(f"  ✅  DB amount와 XBRL 값 불일치 — 원본 rcept_no가 정정 전 값을 반환함")


# ── 구형 케이스 스캔 ────────────────────────────────────────────────────────────

def scan_old_coverage(dart: DartAPI, cur, sample_size: int = 5) -> None:
    """
    2015~2016년 공시 중 정정공시 있는 케이스 샘플링해서 XBRL 커버리지 확인.
    """
    print("\n=== [구형 커버리지] FY2015~2016 XBRL 제공 여부 ===")

    cur.execute(
        """
        SELECT DISTINCT d.ticker, d.year, d.report_type,
               MIN(d.rcept_no) FILTER (WHERE d.report_nm NOT LIKE '%정정%') AS orig_rcept
        FROM disclosures d
        WHERE d.year BETWEEN 2014 AND 2016
          AND d.report_type = 'FY'
        GROUP BY d.ticker, d.year, d.report_type
        HAVING COUNT(*) FILTER (WHERE d.report_nm NOT LIKE '%정정%') > 0
        LIMIT %s
        """,
        (sample_size,),
    )
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
        xbrl_entries = inspect_xbrl_zip(xbrl_bytes)

        if not xbrl_entries:
            print("  .xbrl 파일 없음 — 이 보고서는 XBRL 미제공")
        else:
            compare_with_db(cur, ticker, year, report_type, xbrl_entries)


if __name__ == '__main__':
    main()
