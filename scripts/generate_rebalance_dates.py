"""
리밸런싱 날짜/스케줄 생성 스크립트.
출력을 backtest/configs/rebalance_dates.py(REBALANCE_DATES)·
backtest/configs/schedule.py(REBALANCE_SCHEDULE_A)에 하드코딩한다.

pykrx get_index_ohlcv_by_date KRX 2024 리뉴얼 이후 불작동 확인.
대안: 이미 DB에 수집된 price_history의 distinct date를 영업일 캘린더로 사용.
(KRX에서 수집한 데이터이므로 KRX 기반 기준 충족.)

SPEC_13 §7-3b. `--as-of` 필수(재현성) — CORR-ENGINE-003과 동일 원칙(date.today() 내부
호출 금지). 항상 먼저 반기 재현 오라클(23개 기존 날짜와 완전 일치)을 통과해야
quarterly 산출로 넘어간다.

실행:
    venv/bin/python scripts/generate_rebalance_dates.py --as-of 2026-07-28
    venv/bin/python scripts/generate_rebalance_dates.py --as-of 2026-07-28 --freq quarterly
    venv/bin/python scripts/generate_rebalance_dates.py --as-of 2026-07-28 --verify
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

from ingest.connection import db_conn

OFFSET = 3  # 법정마감 + 3영업일

# (월, 일, report_type)
DEADLINES = {
    'semiannual': [(3, 31, 'FY'), (8, 14, 'H1')],
    'quarterly':  [(3, 31, 'FY'), (5, 15, 'Q1'), (8, 14, 'H1'), (11, 14, 'Q3')],
}

# 오라클 기준 (backtest/configs/rebalance_dates.py 현행값) — 재생성 결과 대조용
KNOWN_SEMIANNUAL = [
    date(2015, 4, 3),  date(2015, 8, 19), date(2016, 4, 5),  date(2016, 8, 18),
    date(2017, 4, 5),  date(2017, 8, 18), date(2018, 4, 4),  date(2018, 8, 20),
    date(2019, 4, 3),  date(2019, 8, 20), date(2020, 4, 3),  date(2020, 8, 20),
    date(2021, 4, 5),  date(2021, 8, 19), date(2022, 4, 5),  date(2022, 8, 18),
    date(2023, 4, 5),  date(2023, 8, 18), date(2024, 4, 3),  date(2024, 8, 20),
    date(2025, 4, 3),  date(2025, 8, 20), date(2026, 4, 3),
]

CALENDAR_ID = {'semiannual': 'SEMIANNUAL', 'quarterly': 'A'}  # freq[:1].upper() 금지 — 버그였음


def nth_trading_day_after(cur, base: date, n: int) -> date:
    """base 날짜 이후 n번째 영업일 반환 (price_history DISTINCT date 기준)."""
    cur.execute(
        """
        SELECT DISTINCT date FROM price_history
        WHERE date > %s AND date <= %s
        ORDER BY date
        LIMIT %s
        """,
        (base, base + timedelta(days=45), n),
    )
    rows = cur.fetchall()
    if len(rows) < n:
        raise ValueError(f'거래일 부족: base={base}, found={len(rows)}, need={n}')
    return rows[n - 1][0]


def _fiscal_year(d: date, report_type: str) -> int:
    """앵커가 원하는 사업연도 — FY만 전년도, 나머지(Q1/H1/Q3)는 해당 연도(DEBT-3)."""
    return d.year - 1 if report_type == 'FY' else d.year


def _nominal_period_end(fiscal_year: int, report_type: str) -> date:
    month_day = {'FY': (12, 31), 'Q1': (3, 31), 'H1': (6, 30), 'Q3': (9, 30)}[report_type]
    return date(fiscal_year, *month_day)


def build(cur, freq: str, as_of: date, start_year: int = 2015, end_year: int = 2026) -> list[tuple[date, str]]:
    out = []
    for yr in range(start_year, end_year + 1):
        for mo, dy, rtype in DEADLINES[freq]:
            base = date(yr, mo, dy)
            if base > as_of:               # 마감일 자체가 미래 — 건너뜀
                continue
            try:
                d = nth_trading_day_after(cur, base, OFFSET)
            except ValueError:
                continue                   # 영업일 데이터가 as_of 시점에 아직 없음 — 건너뜀
            if d <= as_of:                  # date.today() 아님 — 주입된 as_of만 사용
                out.append((d, rtype))
    return sorted(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--as-of', required=True, type=date.fromisoformat,
                     help='YYYY-MM-DD (재현성 필수 — date.today() 사용 금지)')
    ap.add_argument('--freq', choices=['semiannual', 'quarterly'], default='semiannual')
    ap.add_argument('--verify', action='store_true', help='오라클 재현 검증만 하고 종료')
    args = ap.parse_args()

    with db_conn() as conn:
        cur = conn.cursor()

        # 어떤 모드든 항상 먼저 반기 재현 오라클
        semi = [d for d, _ in build(cur, 'semiannual', args.as_of)]
        if semi != KNOWN_SEMIANNUAL:
            diff = [(a, b) for a, b in zip(semi, KNOWN_SEMIANNUAL) if a != b]
            raise SystemExit(f'❌ 오라클 실패 — 반기 재현 불일치 {len(diff)}건: {diff[:5]}')
        print(f'✅ 오라클 통과 — 반기 {len(semi)}개 재현')
        if args.verify:
            return

        pts = build(cur, args.freq, args.as_of)
        if args.freq == 'quarterly':
            missing = set(KNOWN_SEMIANNUAL) - {d for d, _ in pts}
            if missing:
                raise SystemExit(f'❌ 상위집합 위반 — 누락 반기 앵커: {sorted(missing)}')
            print(f'✅ 상위집합 검증 통과 — 반기 앵커 {len(KNOWN_SEMIANNUAL)}개 포함')

    if args.freq == 'semiannual':
        print('\n# 아래를 backtest/configs/rebalance_dates.py의 REBALANCE_DATES에 복사')
        print('REBALANCE_DATES: list[date] = [')
        for d, _ in pts:
            print(f'    date({d.year}, {d.month}, {d.day}),  # {d.isoformat()}')
        print(']')
        print(f'# 총 {len(pts)}개')
        return

    print('\n# 아래를 backtest/configs/schedule.py의 REBALANCE_SCHEDULE_A에 복사')
    print(f'REBALANCE_SCHEDULE_A: tuple[RebalancePoint, ...] = (')
    for d, rtype in pts:
        fy = _fiscal_year(d, rtype)
        pe = _nominal_period_end(fy, rtype)
        print(f'    RebalancePoint(date({d.year},{d.month},{d.day}), "{rtype}", '
              f'{fy}, date({pe.year},{pe.month},{pe.day}), "{CALENDAR_ID[args.freq]}"),')
    print(')')
    print(f'# 총 {len(pts)}개')


if __name__ == '__main__':
    main()
