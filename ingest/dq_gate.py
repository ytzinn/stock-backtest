"""
Data Quality Gate — universe_gate_pit 판정.

규칙 접두사:
  R (Reject) — 조건 충족 시 status='REJECT', 백테스트 유니버스에서 제외
  P (Pass flag) — 제외하지 않고 flags 컬럼에 기록, 추가 검토용 이상 징후

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
                accounts_cur: dict, accounts_prev: dict | None = None,
                conn=None) -> None:
    """
    단일 (ticker, year, report_type) DQ Gate 판정 → universe_gate_pit upsert.

    accounts_cur:  해당 기간 계정 딕셔너리 {account_nm: amount}
                   ※ PIT 계약: _load_accounts()가 주는 **최초 공시값** 기준이어야 한다
                   (CORR-GATE-002 — 정정 반영값으로 판정하면 정정 이전 리밸런싱일에
                   미래 정보가 새어든다).
    accounts_prev: 직전 기간 (V09 연속 누락 판정용, 없으면 None)
    conn: 주입 시 그 커넥션으로 upsert (테스트·배치 재사용). None이면 자체 db_conn.
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

    # P04: 영업활동현금흐름 누락 — RIM 모델 필수값, 경고 전용 (REJECT 아님)
    if cfo is None:
        flags.append('P04:cfo_missing')

    # P05: 계정 단위 급변 (전년비 100배 이상 — 단위 변경 의심)
    # P01(±500%)은 사업 급성장도 포함. 100배(9,900%)는 단위 오류 외 설명 어려운 수준.
    if accounts_prev:
        for acct in ['매출액', '자산총계', '자본총계']:
            cur_val  = accounts_cur.get(acct)
            prev_val = accounts_prev.get(acct)
            if cur_val and prev_val and prev_val != 0:
                ratio = abs(cur_val / prev_val)
                if ratio > 100 or ratio < 0.01:
                    flags.append(f'P05:unit_change_suspect:{acct}')

    status = 'REJECT' if reject_reasons else 'PASS'

    if conn is not None:
        _upsert_gate(conn.cursor(), ticker, year, report_type, status, reject_reasons, flags)
    else:
        with db_conn() as own_conn:
            _upsert_gate(own_conn.cursor(), ticker, year, report_type, status,
                         reject_reasons, flags)


def _upsert_gate(cur, ticker: str, year: int, report_type: str,
                 status: str, reject_reasons: list, flags: list) -> None:
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
    """
    게이트 판정 입력 로드. 두 가지 계약:

    1. PIT (CORR-GATE-002): COALESCE(original_amount, amount) — **최초 공시값** 기준.
       financials.amount는 정정공시로 덮어써지므로, 그걸 쓰면 정정 이전 리밸런싱일의
       게이트 판정에 미래 정보가 새어든다 (정정으로 자본총계 부호가 뒤집힌 실측 145행).
    2. 결정성 (CORR-GATE-001): 동일 account_nm에 CFS/OFS가 공존하면 **CFS 우선** —
       load_pit_series와 동일 규칙. ORDER BY로 OFS를 먼저 읽고 CFS가 덮어쓰게 해
       스캔 순서 비의존을 보장한다 (종전엔 ORDER BY 없이 dict 덮어쓰기 = 비결정적).
    """
    cur.execute(
        """
        SELECT account_nm, COALESCE(original_amount, amount) AS first_disclosed
        FROM financials
        WHERE ticker = %s AND year = %s AND report_type = %s
        ORDER BY CASE fs_div WHEN 'OFS' THEN 1 ELSE 2 END
        """,
        (ticker, year, report_type),
    )
    return {row[0]: (float(row[1]) if row[1] is not None else None)
            for row in cur.fetchall()}


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
                        accounts_prev=prev_accounts if prev_year == year - 1 else None,
                        conn=conn)
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
