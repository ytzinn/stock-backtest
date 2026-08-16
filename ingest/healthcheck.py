"""
수집 완료 여부 healthcheck — 평일 KST 21:00 cron 실행.

실행:
    python -m ingest.healthcheck
"""
import logging
from datetime import date

from ingest.connection import db_conn
from ingest.logging_config import configure_logging

configure_logging('healthcheck.log')
log = logging.getLogger(__name__)

PRICE_MIN_ROWS   = 100   # 오늘 가격 데이터 최소 종목 수
MARKET_CAP_MIN   = 100


def check_price_history(today: date) -> bool:
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(DISTINCT ticker) FROM price_history WHERE date = %s",
            (today,),
        )
        cnt = cur.fetchone()[0]
    if cnt < PRICE_MIN_ROWS:
        log.error(f'[FAIL] price_history 오늘({today}) 종목 수 {cnt} < {PRICE_MIN_ROWS}')
        return False
    log.info(f'[OK] price_history 오늘 {cnt}개 종목')
    return True


def check_market_cap_history(today: date) -> bool:
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(DISTINCT ticker) FROM market_cap_history WHERE date = %s",
            (today,),
        )
        cnt = cur.fetchone()[0]
    if cnt < MARKET_CAP_MIN:
        log.error(f'[FAIL] market_cap_history 오늘({today}) 종목 수 {cnt} < {MARKET_CAP_MIN}')
        return False
    log.info(f'[OK] market_cap_history 오늘 {cnt}개 종목')
    return True


def check_ingest_status() -> None:
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT status, COUNT(*) FROM ingest_status GROUP BY status
        """)
        rows = cur.fetchall()
    for status, cnt in rows:
        log.info(f'  ingest_status {status}: {cnt}개')


def check_fallback_rate() -> None:
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) FILTER (WHERE fallback_used) * 1.0 / NULLIF(COUNT(*), 0)
            FROM financials_pit
        """)
        row = cur.fetchone()
    rate = float(row[0]) if row and row[0] else 0.0
    if rate > 0.20:
        log.error(f'[FAIL] fallback_used 비율 {rate:.1%} > 20%')
    else:
        log.info(f'[OK] fallback_used 비율 {rate:.1%}')


# 필터가 실제로 소비하는 계정 — 이게 비면 규칙이 조용히 자동 통과된다
CRITICAL_ACCOUNTS = {
    '당기순이익':       'R6 adjROE',
    '영업활동현금흐름': 'R4·R5·R6',
    '재무활동현금흐름': 'R5',
    '자본총계':         'PBR 랭킹·R1·R2',
    '부채총계':         'R1',
    '매출액':           'R3',
}
COVERAGE_FLOOR = 0.90   # 보고서 종류별 최소 확보율
DIVERGENCE_MAX = 0.10   # FY 대비 중간보고서 확보율 허용 격차


def check_account_coverage() -> bool:
    """보고서 종류별 계정 확보율 — FY 대비 중간보고서 괴리를 감시한다.

    **이 검사가 없어서 10년을 놓쳤다.** DART 한글 계정명은 보고서 종류마다 다른데
    (사업 `당기순이익` / 반기 `반기순이익` / 분기 `분기순이익`) 별칭에 반기형이 없었다.
    FY 는 95~98% 를 유지하는 동안 H1 만 94.4%(2016) → 66.7%(2026) 로 흘러내렸고,
    TTM(FY−H1+H1)이 H1 값 두 개를 요구하는 탓에 R6 는 종목의 57% 에서만 계산됐다.
    계정이 없으면 stability_filter 의 조건문이 건너뛰어져 **탈락시켜야 할 종목이
    그냥 통과한다** — 조용하고, 한쪽으로 치우친 오류다.

    절대 확보율만 보면 늦다(천천히 내려가므로 어느 날도 '급변'이 아니다).
    **FY 대비 괴리**를 본다 — 같은 회사가 같은 해에 낸 두 보고서라 값이 벌어질
    이유가 없고, 벌어졌다면 표기 축에서 뭔가 빠진 것이다. 이번 사고의 지문이 정확히
    이 모양이었다.
    """
    ok = True
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT MAX(year) FROM financials WHERE report_type='FY'")
        row = cur.fetchone()
        if not row or row[0] is None:
            log.error('[FAIL] account_coverage: FY 데이터 없음')
            return False
        year = row[0]

        for acct, used_by in CRITICAL_ACCOUNTS.items():
            cur.execute("""
                SELECT f.report_type,
                       COUNT(DISTINCT f.ticker)                                      AS total,
                       COUNT(DISTINCT f.ticker) FILTER (WHERE a.ticker IS NOT NULL)  AS have
                FROM (SELECT DISTINCT ticker, report_type FROM financials WHERE year=%s) f
                LEFT JOIN (SELECT DISTINCT ticker, report_type FROM financials
                           WHERE year=%s AND account_nm=%s) a
                  ON a.ticker=f.ticker AND a.report_type=f.report_type
                GROUP BY f.report_type
            """, (year, year, acct))
            rates = {rt: (have / total if total else 0.0) for rt, total, have in cur.fetchall()}
            if not rates:
                continue
            fy_rate = rates.get('FY')
            for rt, rate in sorted(rates.items()):
                if rate < COVERAGE_FLOOR:
                    log.error(f'[FAIL] account_coverage {year} {rt} {acct}: '
                              f'{rate:.1%} < {COVERAGE_FLOOR:.0%} (소비처 {used_by})')
                    ok = False
                if fy_rate is not None and rt != 'FY' and (fy_rate - rate) > DIVERGENCE_MAX:
                    log.error(f'[FAIL] account_coverage {year} {rt} {acct}: '
                              f'FY {fy_rate:.1%} 대비 {rate:.1%} — 괴리 '
                              f'{(fy_rate-rate)*100:.1f}%p > {DIVERGENCE_MAX*100:.0f}%p. '
                              f'그 보고서 종류의 계정 표기를 놓치고 있을 가능성 '
                              f'(ALIAS_MISS_ISCF 로그 확인). 소비처 {used_by}')
                    ok = False
    if ok:
        log.info(f'[OK] account_coverage {year}: 임계 계정 {len(CRITICAL_ACCOUNTS)}종 정상')
    return ok


def main() -> None:
    today = date.today()
    log.info(f'=== healthcheck {today} ===')
    check_price_history(today)
    check_market_cap_history(today)
    check_ingest_status()
    check_fallback_rate()
    check_account_coverage()
    log.info('=== healthcheck 완료 ===')


if __name__ == '__main__':
    main()
