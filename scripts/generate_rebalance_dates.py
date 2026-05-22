"""
리밸런싱 날짜 생성 스크립트.
Phase 2 시작 시 서버에서 1회만 실행. 출력을 backtest/configs/rebalance_dates.py에 하드코딩한다.

pykrx get_index_ohlcv_by_date KRX 2024 리뉴얼 이후 불작동 확인.
대안: 이미 DB에 수집된 price_history의 distinct date를 영업일 캘린더로 사용.
(KRX에서 수집한 데이터이므로 KRX 기반 기준 충족.)

실행:
    cd /opt/stock-backtest
    venv/bin/python scripts/generate_rebalance_dates.py
"""
from datetime import date, timedelta

from ingest.connection import db_conn


def nth_trading_day_after(cur, base: date, n: int) -> date:
    """
    base 날짜 이후 n번째 영업일 반환.
    price_history DISTINCT date를 영업일 캘린더로 사용.
    """
    start = base + timedelta(days=1)
    end   = base + timedelta(days=45)

    cur.execute(
        """
        SELECT DISTINCT date FROM price_history
        WHERE date > %s AND date <= %s
        ORDER BY date
        LIMIT %s
        """,
        (base, end, n),
    )
    rows = cur.fetchall()
    if len(rows) < n:
        raise ValueError(f'거래일 부족: base={base}, found={len(rows)}, need={n}')

    return rows[n - 1][0]


def main():
    with db_conn() as conn:
        cur = conn.cursor()
        dates = []
        for yr in range(2015, 2027):
            # 상반기: 3월 31일 + 3 영업일 (FY 사업보고서 법정 마감 후)
            dates.append(nth_trading_day_after(cur, date(yr, 3, 31), 3))
            # 하반기: 8월 14일 + 3 영업일 (H1 반기보고서 법정 마감 후)
            if yr < 2026:
                dates.append(nth_trading_day_after(cur, date(yr, 8, 14), 3))

    dates.sort()

    print('# 아래를 backtest/configs/rebalance_dates.py에 복사 붙여넣기')
    print('REBALANCE_DATES = [')
    for d in dates:
        print(f'    date({d.year}, {d.month}, {d.day}),  # {d.isoformat()}')
    print(']')
    print(f'\n# 총 {len(dates)}개 리밸런싱 날짜')


if __name__ == '__main__':
    main()
