"""
Data Quality Gate — universe_gate_pit 판정 (R01~R09).

실행:
    python -m ingest.dq_gate                   # 전종목 전기간 판정
    python -m ingest.dq_gate --ticker 005930   # 단일 종목
    python -m ingest.dq_gate --report          # PASS 통계 출력
"""
import argparse
import json
import logging

from ingest.connection import db_conn
from ingest.logging_config import configure_logging
from ingest.validator import validate_period

configure_logging('dq_gate.log')
log = logging.getLogger(__name__)


def run_dq_gate(ticker: str, year: int, report_type: str,
                accounts_cur: dict, accounts_prev: dict | None = None) -> None:
    """
    단일 (ticker, year, report_type) DQ Gate 판정 → universe_gate_pit upsert.

    accounts_cur:  해당 기간 계정 딕셔너리 {account_nm: amount}
    accounts_prev: 직전 기간 (V09 연속 누락 판정용, 없으면 None)
    """
    reject_reasons: list[str] = []
    flags: list[str] = []

    equity  = accounts_cur.get('자본총계')
    assets  = accounts_cur.get('자산총계')
    revenue = accounts_cur.get('매출액')
    op_inc  = accounts_cur.get('영업이익')

    # R02: 자본총계 < 0 (자본잠식)
    if equity is not None and equity < 0:
        reject_reasons.append('R02: 자본총계 < 0')

    # R03: 자산총계 < 0
    if assets is not None and assets < 0:
        reject_reasons.append('R03: 자산총계 < 0')

    # R04: 핵심 계정 2개 이상 누락 (매출액, 영업이익, 자본총계)
    core_missing = [k for k, v in {
        '매출액': revenue, '영업이익': op_inc, '자본총계': equity
    }.items() if v is None]
    if len(core_missing) >= 2:
        reject_reasons.append(f'R04: 핵심계정누락 {core_missing}')

    # R05: FY 재무 연속 2년 이상 누락 — 해당 기간 자체가 비어있고 직전도 비어있으면
    if report_type == 'FY' and accounts_prev is not None:
        cur_missing  = revenue is None and op_inc is None and equity is None
        prev_missing = (accounts_prev.get('매출액') is None
                        and accounts_prev.get('영업이익') is None
                        and accounts_prev.get('자본총계') is None)
        if cur_missing and prev_missing:
            reject_reasons.append('R05: FY 재무 연속 2년 누락')

    # R09: 자산 = 부채 + 자본 오차 > 5%
    liabilities = accounts_cur.get('부채총계')
    if assets and liabilities is not None and equity is not None:
        total = liabilities + equity
        if total != 0 and abs(assets - total) / abs(total) > 0.05:
            reject_reasons.append('R09: 자산≠부채+자본 오차>5%')

    # 자동 PASS 플래그 (P01~P03)
    if revenue is not None and abs(revenue) > 0:
        # P01: 매출액 급변 — 전기 대비 ±500% (accounts_prev 있을 때만)
        if accounts_prev:
            prev_rev = accounts_prev.get('매출액')
            if prev_rev and prev_rev != 0:
                chg = abs(revenue - prev_rev) / abs(prev_rev)
                if chg > 5.0:
                    flags.append('P01:revenue_spike')

    if equity is not None and accounts_prev:
        prev_eq = accounts_prev.get('자본총계')
        if prev_eq and prev_eq != 0:
            if abs(equity - prev_eq) / abs(prev_eq) > 3.0:
                flags.append('P02:equity_spike')

    net_income = accounts_cur.get('당기순이익')
    cfo        = accounts_cur.get('영업활동현금흐름')
    if net_income is not None and cfo is not None and equity and equity != 0:
        if abs(net_income - cfo) > abs(equity) * 0.30:
            flags.append('P03:accrual_alert')

    status = 'REJECT' if reject_reasons else 'PASS'

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO universe_gate_pit
                (ticker, year, report_type, status, reject_reasons, flags)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb)
            ON CONFLICT (ticker, year, report_type) DO UPDATE SET
                status         = EXCLUDED.status,
                reject_reasons = EXCLUDED.reject_reasons,
                flags          = EXCLUDED.flags,
                evaluated_at   = now()
            """,
            (ticker, year, report_type, status,
             json.dumps(reject_reasons, ensure_ascii=False),
             json.dumps(flags, ensure_ascii=False)),
        )


def _load_accounts(cur, ticker: str, year: int,
                    report_type: str) -> dict:
    cur.execute(
        """
        SELECT account_nm, amount FROM financials
        WHERE ticker = %s AND year = %s AND report_type = %s
        """,
        (ticker, year, report_type),
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def evaluate_ticker(ticker: str) -> None:
    """단일 종목 전기간 DQ Gate 판정."""
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT year, report_type FROM financials WHERE ticker = %s ORDER BY year",
            (ticker,),
        )
        periods = cur.fetchall()

        prev_accounts: dict | None = None
        prev_year = None
        for year, report_type in periods:
            accounts = _load_accounts(cur, ticker, year, report_type)
            run_dq_gate(ticker, year, report_type, accounts,
                        accounts_prev=prev_accounts if prev_year == year - 1 else None)
            if report_type == 'FY':
                prev_accounts = accounts
                prev_year = year


def evaluate_all() -> None:
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT ticker FROM stocks WHERE is_excluded = FALSE ORDER BY ticker")
        tickers = [r[0] for r in cur.fetchall()]

    log.info(f'DQ Gate 전종목 판정: {len(tickers)}개')
    for i, ticker in enumerate(tickers, 1):
        evaluate_ticker(ticker)
        if i % 200 == 0:
            log.info(f'  진행: {i}/{len(tickers)}')
    log.info('DQ Gate 완료')


def print_report() -> None:
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT status, COUNT(*) FROM universe_gate_pit GROUP BY status
        """)
        for status, cnt in cur.fetchall():
            log.info(f'  {status}: {cnt}개')
        cur.execute("""
            SELECT report_type, COUNT(*) FILTER (WHERE status='PASS') * 1.0 / COUNT(*)
            FROM universe_gate_pit
            GROUP BY report_type
        """)
        for rtype, rate in cur.fetchall():
            log.info(f'  {rtype} PASS 비율: {rate:.1%}')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--ticker', help='단일 종목')
    parser.add_argument('--report', action='store_true')
    args = parser.parse_args()

    if args.ticker:
        evaluate_ticker(args.ticker)
        log.info(f'{args.ticker} DQ Gate 완료')
    elif args.report:
        print_report()
    else:
        evaluate_all()


if __name__ == '__main__':
    main()
