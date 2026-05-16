"""
재무 이상치 검사 (V01~V11, V04).
financials 테이블 대상. CFS 기준, 없으면 OFS fallback.

실행:
    python -m ingest.validator --ticker 005930
    python -m ingest.validator --all
"""
import argparse
import logging

from ingest.connection import db_conn

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)


# H1 frmtrm 교차검증 시 IS/CF로 분류되는 계정 (나머지는 BS 잔액)
_IS_CF_ACCOUNTS = frozenset([
    '매출액', '매출총이익', '영업이익', '당기순이익', '지배기업소유주지분순이익',
    '영업활동현금흐름', '투자활동현금흐름', '재무활동현금흐름', '배당금지급', '법인세비용차감전순이익',
])


def _get_amount(accounts: dict, name: str) -> float | None:
    val = accounts.get(name)
    return float(val) if val is not None else None


def _pct_change(a, b) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return (a - b) / abs(b)


def validate_period(ticker: str, year: int, report_type: str,
                    accounts: dict) -> tuple[list[str], list[str]]:
    """
    단일 기간 재무 검사.
    반환: (reject_flags, warning_flags)
    """
    rejects  = []
    warnings = []

    assets      = _get_amount(accounts, '자산총계')
    liabilities = _get_amount(accounts, '부채총계')
    equity      = _get_amount(accounts, '자본총계')
    revenue     = _get_amount(accounts, '매출액')
    op_income   = _get_amount(accounts, '영업이익')
    net_income  = _get_amount(accounts, '당기순이익')
    cfo         = _get_amount(accounts, '영업활동현금흐름')
    gross       = _get_amount(accounts, '매출총이익')
    ctrl_equity = _get_amount(accounts, '지배기업소유주지분')

    # V01: 자산 = 부채 + 자본 (허용 오차)
    if assets is not None and liabilities is not None and equity is not None:
        total = liabilities + equity
        if total != 0:
            err = abs(assets - total) / abs(total)
            if err > 0.05:
                rejects.append(f'V01: 자산≠부채+자본 오차 {err:.1%}')
            elif err > 0.01:
                warnings.append(f'V01: 자산≠부채+자본 오차 {err:.1%} (경고)')

    # V02: 영업이익 <= 매출액
    if op_income is not None and revenue is not None and revenue > 0:
        if op_income > revenue:
            warnings.append('V02: 영업이익 > 매출액')

    # V03: |NI - CFO| > 자본총계 30%
    if net_income is not None and cfo is not None and equity is not None and equity != 0:
        if abs(net_income - cfo) > abs(equity) * 0.30:
            warnings.append('V03: |NI-CFO| > 자본총계 30%')

    # V06: 자본총계 < 0 (자본잠식)
    if equity is not None and equity < 0:
        rejects.append('V06: 자본총계 < 0 (자본잠식)')

    # V07: 자산총계 < 0
    if assets is not None and assets < 0:
        rejects.append('V07: 자산총계 < 0')

    # V08: 핵심 계정 2개 이상 누락 (매출액, 영업이익, 자본총계 기준)
    core = {'매출액': revenue, '영업이익': op_income, '자본총계': equity}
    missing = [k for k, v in core.items() if v is None]
    if len(missing) >= 2:
        rejects.append(f'V08: 핵심 계정 누락 {missing}')

    # V09: 부채총계 < 0
    if liabilities is not None and liabilities < 0:
        rejects.append('V09: 부채총계 < 0')

    # V10: P&L 계층 순서 위반
    # 매출총이익은 매출에서 원가를 뺀 것 → 매출총이익 ≤ 매출액이어야 함
    if gross is not None and revenue is not None and revenue > 0:
        if gross > revenue:
            warnings.append('V10: 매출총이익 > 매출액')
    # 영업이익은 매출총이익에서 판관비를 뺀 것 → 영업이익 ≤ 매출총이익이어야 함
    if op_income is not None and gross is not None:
        if op_income > gross:
            warnings.append('V10: 영업이익 > 매출총이익')

    # V11: 지배기업소유주지분 > 자본총계 (비지배지분은 음수 불가)
    if ctrl_equity is not None and equity is not None and equity > 0:
        if ctrl_equity > equity * 1.01:
            warnings.append('V11: 지배기업소유주지분 > 자본총계')

    return rejects, warnings


def _has_correction_report(cur, ticker: str, year: int, report_type: str) -> bool:
    """해당 기간 정정보고서가 disclosures에 있으면 True (재작성으로 인한 frmtrm 불일치 정상 처리)."""
    cur.execute(
        """
        SELECT 1 FROM disclosures
        WHERE ticker = %s AND year = %s AND report_type = %s
          AND report_nm LIKE '%%정정%%'
        LIMIT 1
        """,
        (ticker, year, report_type),
    )
    return cur.fetchone() is not None


def _check_frmtrm(cur, ticker: str, year: int, fs_div: str, fscl_month: int) -> list[str]:
    """
    H1 frmtrm 교차검증 (V04).

    DART bsns_year 관행:
      fscl_month <= 6: H1 bsns_year=N은 FY N 이후 반기 → frmtrm 기준 = FY 동년(N)
      fscl_month >= 7: H1 bsns_year=N은 FY N 이전 반기 → frmtrm 기준 = FY 전년(N-1)
    IS/CF 계정: 항상 전년 H1 비교 (누계 개념)
    참조 기간에 정정보고서가 있으면 skip.
    임계값 >10% → WARN (restatement 정상 범위 수용)
    """
    cur.execute(
        """
        SELECT account_nm, frmtrm_amount FROM financials
        WHERE ticker = %s AND year = %s AND report_type = 'H1' AND fs_div = %s
          AND frmtrm_amount IS NOT NULL
        """,
        (ticker, year, fs_div),
    )
    h1_rows = cur.fetchall()
    if not h1_rows:
        return []

    bs_ref_year = year if (fscl_month or 12) <= 6 else year - 1
    correction_cache: dict[tuple, bool] = {}
    warnings = []

    for acct_nm, frmtrm_amt in h1_rows:
        if acct_nm in _IS_CF_ACCOUNTS:
            ref_year, ref_rpt = year - 1, 'H1'
        else:
            ref_year, ref_rpt = bs_ref_year, 'FY'

        cache_key = (ref_year, ref_rpt)
        if cache_key not in correction_cache:
            correction_cache[cache_key] = _has_correction_report(cur, ticker, ref_year, ref_rpt)
        if correction_cache[cache_key]:
            continue

        cur.execute(
            """
            SELECT amount FROM financials
            WHERE ticker = %s AND year = %s AND report_type = %s
              AND fs_div = %s AND account_nm = %s AND amount IS NOT NULL
            """,
            (ticker, ref_year, ref_rpt, fs_div, acct_nm),
        )
        row = cur.fetchone()
        if row is None or row[0] == 0:
            continue
        frmtrm_v = float(frmtrm_amt)
        actual_v = float(row[0])
        # 배당금지급은 부호 기준이 연도별로 달라지는 경우가 있어 절대값 비교
        if acct_nm == '배당금지급':
            diff = abs(abs(frmtrm_v) - abs(actual_v)) / abs(actual_v)
        else:
            diff = abs(frmtrm_v - actual_v) / abs(actual_v)
        if diff > 0.10:
            warnings.append(
                f'V04: frmtrm 불일치 {acct_nm} {diff:.1%}'
                f' (H1{year} vs {ref_rpt}{ref_year})'
            )

    return warnings


def _check_frmtrm_fy(cur, ticker: str, year: int, fs_div: str) -> list[str]:
    """
    FY frmtrm 교차검증 (V04).
    FY.frmtrm_amount(year N) == FY.amount(year N-1) 여야 함.
    year N-1에 정정보고서가 있으면 skip (합법적 재작성).
    임계값 >10% → WARN
    """
    cur.execute(
        """
        SELECT f.account_nm, f.frmtrm_amount, prev.amount
        FROM financials f
        JOIN financials prev
            ON prev.ticker = f.ticker
            AND prev.year = f.year - 1
            AND prev.report_type = 'FY'
            AND prev.fs_div = f.fs_div
            AND prev.account_nm = f.account_nm
            AND prev.amount IS NOT NULL
        WHERE f.ticker = %s AND f.year = %s AND f.report_type = 'FY' AND f.fs_div = %s
          AND f.frmtrm_amount IS NOT NULL
          AND prev.amount != 0
        """,
        (ticker, year, fs_div),
    )
    rows = cur.fetchall()
    if not rows:
        return []

    if _has_correction_report(cur, ticker, year - 1, 'FY'):
        return []

    warnings = []
    for acct_nm, frmtrm_amt, prev_amt in rows:
        frmtrm_v = float(frmtrm_amt)
        actual_v = float(prev_amt)
        if acct_nm == '배당금지급':
            diff = abs(abs(frmtrm_v) - abs(actual_v)) / abs(actual_v)
        else:
            diff = abs(frmtrm_v - actual_v) / abs(actual_v)
        if diff > 0.10:
            warnings.append(
                f'V04: frmtrm 불일치 {acct_nm} {diff:.1%}'
                f' (FY{year}.frmtrm vs FY{year - 1})'
            )

    return warnings


def validate_ticker(ticker: str) -> dict:
    """종목 전체 기간 검사 + validation_log upsert. 반환: {(year, report_type): (rejects, warnings)}"""
    results = {}
    with db_conn() as conn:
        cur = conn.cursor()

        cur.execute("SELECT fscl_month FROM stocks WHERE ticker = %s", (ticker,))
        row = cur.fetchone()
        fscl_month = row[0] if row else None

        cur.execute(
            """
            SELECT DISTINCT year, report_type, fs_div
            FROM financials WHERE ticker = %s
            ORDER BY year, report_type
            """,
            (ticker,),
        )
        periods = cur.fetchall()

        for year, report_type, fs_div in periods:
            cur.execute(
                """
                SELECT account_nm, amount FROM financials
                WHERE ticker = %s AND year = %s AND report_type = %s AND fs_div = %s
                """,
                (ticker, year, report_type, fs_div),
            )
            accounts = {row[0]: row[1] for row in cur.fetchall()}
            rejects, warnings = validate_period(ticker, year, report_type, accounts)

            if report_type == 'H1':
                warnings += _check_frmtrm(cur, ticker, year, fs_div, fscl_month)
            elif report_type == 'FY':
                warnings += _check_frmtrm_fy(cur, ticker, year, fs_div)
            results[(year, report_type)] = (rejects, warnings)

            rows = [
                (ticker, year, report_type, msg.split(':')[0].strip(), 'REJECT', msg)
                for msg in rejects
            ] + [
                (ticker, year, report_type, msg.split(':')[0].strip(), 'WARN', msg)
                for msg in warnings
            ]
            if rows:
                cur.executemany(
                    """
                    INSERT INTO validation_log
                        (ticker, year, report_type, check_id, severity, message)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticker, year, report_type, check_id) DO UPDATE SET
                        severity     = EXCLUDED.severity,
                        message      = EXCLUDED.message,
                        evaluated_at = now()
                    """,
                    rows,
                )

    return results


def validate_all() -> None:
    """전종목 검사 후 reject 있는 종목/기간 로그 출력."""
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT ticker FROM stocks WHERE is_excluded = FALSE ORDER BY ticker")
        tickers = [r[0] for r in cur.fetchall()]

    for ticker in tickers:
        results = validate_ticker(ticker)
        for (year, rtype), (rejects, _) in results.items():
            if rejects:
                log.warning(f'{ticker} {year} {rtype}: {rejects}')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--ticker', help='단일 종목 검사')
    parser.add_argument('--all',    action='store_true')
    args = parser.parse_args()

    if args.ticker:
        results = validate_ticker(args.ticker)
        for (year, rtype), (rejects, warnings) in results.items():
            if rejects or warnings:
                print(f'{year} {rtype}: REJECT={rejects} WARN={warnings}')
    elif args.all:
        validate_all()


if __name__ == '__main__':
    main()
