#!/usr/bin/env python3
"""자료 수집 현황 빠른 확인 스크립트."""

import psycopg2
from datetime import date

DB = dict(host="localhost", port=5433, dbname="backtest", user="postgres", password="backtest2026!")


def run(conn, sql):
    cur = conn.cursor()
    cur.execute(sql)
    return cur.fetchall()


def main():
    conn = psycopg2.connect(**DB)

    print("=" * 60)
    print(f"  자료 수집 현황  ({date.today()})")
    print("=" * 60)

    # 1. 테이블별 행 수
    print("\n[1] 테이블 행 수")
    rows = run(conn, """
        SELECT relname, n_live_tup
        FROM pg_stat_user_tables
        ORDER BY relname
    """)
    for t, n in rows:
        print(f"  {t:<35} {n:>10,}")

    # 2. 종목 수
    print("\n[2] 종목 현황")
    rows = run(conn, """
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE financial_flag = true) as fin_flag,
            COUNT(*) FILTER (WHERE is_active = true) as active
        FROM stocks
    """)
    total, fin, active = rows[0]
    print(f"  전체 종목: {total:,}  |  financial_flag=T: {fin:,}  |  active: {active:,}")

    # 3. financials_raw 연도별
    print("\n[3] financials_raw 연도별 건수")
    rows = run(conn, """
        SELECT fiscal_year, COUNT(DISTINCT ticker) as tickers, COUNT(*) as rows
        FROM financials_raw
        GROUP BY fiscal_year ORDER BY fiscal_year DESC
        LIMIT 10
    """)
    for yr, t, r in rows:
        print(f"  {yr}년  종목 {t:>4,}개  행 {r:>6,}개")

    # 4. financials_pit 커버리지
    print("\n[4] financials_pit")
    rows = run(conn, """
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE fallback_used = true) as fallback,
            MIN(available_from) as min_date,
            MAX(available_from) as max_date
        FROM financials_pit
    """)
    total, fb, mn, mx = rows[0]
    print(f"  총 {total:,}건  |  fallback: {fb:,}건  |  기간: {mn} ~ {mx}")

    # 5. price_daily 최신일
    print("\n[5] price_daily")
    rows = run(conn, """
        SELECT
            COUNT(DISTINCT ticker) as tickers,
            MIN(date) as min_date,
            MAX(date) as max_date,
            COUNT(*) as rows
        FROM price_daily
    """)
    t, mn, mx, r = rows[0]
    print(f"  종목 {t:,}개  |  {mn} ~ {mx}  |  총 {r:,}행")

    # 6. market_cap_daily
    print("\n[6] market_cap_daily")
    rows = run(conn, """
        SELECT
            COUNT(DISTINCT ticker) as tickers,
            MIN(date) as min_date,
            MAX(date) as max_date,
            COUNT(*) as rows
        FROM market_cap_daily
    """)
    t, mn, mx, r = rows[0]
    print(f"  종목 {t:,}개  |  {mn} ~ {mx}  |  총 {r:,}행")

    # 7. universe_gate_pit
    print("\n[7] universe_gate_pit (DQ Gate)")
    rows = run(conn, """
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE gate_pass = true) as pass,
            COUNT(*) FILTER (WHERE gate_pass = false) as fail
        FROM universe_gate_pit
    """)
    total, p, f = rows[0]
    print(f"  총 {total:,}건  |  PASS: {p:,}  |  FAIL: {f:,}")

    # 8. validation_log 최근 실행
    print("\n[8] validation_log 최근 5건")
    try:
        rows = run(conn, """
            SELECT run_at, step, status, message
            FROM validation_log
            ORDER BY run_at DESC LIMIT 5
        """)
        for run_at, step, status, msg in rows:
            print(f"  [{status}] {run_at:%Y-%m-%d %H:%M}  {step}  {msg or ''}")
    except Exception as e:
        print(f"  (조회 불가: {e})")

    print("\n" + "=" * 60)
    conn.close()


if __name__ == "__main__":
    main()
