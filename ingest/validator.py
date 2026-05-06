"""
재무 이상치 검사 (V01~V09).
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


def _get_amount(accounts: dict, name: str) -> float | None:
    return accounts.get(name)


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

    return rejects, warnings


def validate_ticker(ticker: str) -> dict:
    """종목 전체 기간 검사. 반환: {(year, report_type): (rejects, warnings)}"""
    results = {}
    with db_conn() as conn:
        cur = conn.cursor()

        # 사용 가능한 기간 목록
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
            results[(year, report_type)] = (rejects, warnings)

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
