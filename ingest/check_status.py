#!/usr/bin/env python3
"""
수집 현황 및 데이터 품질 빠른 확인.

실행:
    python -m ingest.check_status
"""
from datetime import date

from ingest.connection import db_conn


def _q(cur, sql, *params):
    cur.execute(sql, params or None)
    return cur.fetchall()


def main():
    with db_conn() as conn:
        _run(conn)


def _run(conn):
    cur = conn.cursor()

    print("=" * 65)
    print(f"  수집 현황 ({date.today()})")
    print("=" * 65)

    # [1] 테이블 행 수
    print("\n[1] 테이블 행 수")
    rows = _q(cur, """
        SELECT relname, n_live_tup
        FROM pg_stat_user_tables
        WHERE relname IN (
            'stocks','stock_listing_events','financials','financials_pit',
            'disclosures','price_history','market_cap_history',
            'universe_gate_pit','validation_log','ingest_status','backtest_runs'
        )
        ORDER BY relname
    """)
    for t, n in rows:
        print(f"  {t:<35} {n:>12,}")

    # [2] 종목 현황
    print("\n[2] 종목 현황")
    rows = _q(cur, """
        SELECT
            COUNT(*)                                          AS total,
            COUNT(*) FILTER (WHERE is_excluded = FALSE)       AS active,
            COUNT(*) FILTER (WHERE corp_code IS NOT NULL
                               AND is_excluded = FALSE)       AS with_corp_code,
            COUNT(*) FILTER (WHERE is_financial = TRUE)       AS financial
        FROM stocks
    """)
    total, active, corp, fin = rows[0]
    print(f"  전체: {total:,}  활성: {active:,}  corp_code 있음: {corp:,}  금융업: {fin:,}")

    # [3] financials 연도별 종목 수 (최근 5년)
    print("\n[3] financials 연도별 (FY, 최근 5년)")
    rows = _q(cur, """
        SELECT year, COUNT(DISTINCT ticker) AS tickers
        FROM financials
        WHERE report_type = 'FY'
        GROUP BY year ORDER BY year DESC LIMIT 5
    """)
    for yr, t in rows:
        print(f"  {yr}년  {t:>4,}종목")

    # [4] 핵심 계정별 수집률 (2024 FY 기준)
    print("\n[4] 핵심 계정 수집률 — 2024년 FY (활성 종목 대비 %)")
    rows = _q(cur, """
        WITH base AS (
            SELECT COUNT(*) AS n FROM stocks WHERE is_excluded = FALSE
        )
        SELECT f.account_nm,
               COUNT(DISTINCT f.ticker)                            AS tickers,
               ROUND(COUNT(DISTINCT f.ticker) * 100.0 / b.n, 1)   AS pct
        FROM financials f, base b
        WHERE f.year = 2024 AND f.report_type = 'FY'
          AND f.account_nm IN (
              '매출액','영업이익','당기순이익','자본총계','자산총계',
              '부채총계','영업활동현금흐름','지배기업소유주지분'
          )
        GROUP BY f.account_nm, b.n
        ORDER BY f.account_nm
    """)
    for nm, t, pct in rows:
        bar = "█" * int(pct // 5)
        flag = "  ⚠️" if pct < 50 else ""
        print(f"  {nm:<18} {t:>5,}종목  {pct:>5.1f}%  {bar}{flag}")

    # [5] financials_pit 요약
    print("\n[5] financials_pit")
    rows = _q(cur, """
        SELECT
            COUNT(*)                                           AS total,
            COUNT(*) FILTER (WHERE fallback_used = TRUE)       AS fallback,
            ROUND(COUNT(*) FILTER (WHERE fallback_used = TRUE)
                  * 100.0 / NULLIF(COUNT(*), 0), 1)            AS fallback_pct,
            MIN(available_from)                                AS min_avail,
            MAX(available_from)                                AS max_avail
        FROM financials_pit
    """)
    total, fb, fb_pct, mn, mx = rows[0]
    print(f"  총 {total:,}건  fallback: {fb:,}건 ({fb_pct}%)  기간: {mn} ~ {mx}")

    # [6] price_history
    print("\n[6] price_history")
    rows = _q(cur, """
        SELECT COUNT(DISTINCT ticker), MIN(date), MAX(date), COUNT(*)
        FROM price_history
    """)
    t, mn, mx, r = rows[0]
    print(f"  {t:,}종목  {mn} ~ {mx}  총 {r:,}행")

    # [7] market_cap_history
    print("\n[7] market_cap_history")
    rows = _q(cur, """
        SELECT COUNT(DISTINCT ticker), MIN(date), MAX(date), COUNT(*)
        FROM market_cap_history
    """)
    t, mn, mx, r = rows[0]
    print(f"  {t:,}종목  {mn} ~ {mx}  총 {r:,}행")

    # [8] universe_gate_pit
    print("\n[8] universe_gate_pit (DQ Gate)")
    rows = _q(cur, """
        SELECT status, COUNT(*) FROM universe_gate_pit GROUP BY status ORDER BY status
    """)
    for status, cnt in rows:
        print(f"  {status}: {cnt:,}")

    rows = _q(cur, """
        SELECT flags->>0 AS flag, COUNT(*) AS cnt
        FROM universe_gate_pit, jsonb_array_elements_text(flags) flag_elem
        CROSS JOIN LATERAL (SELECT flag_elem) AS t(dummy)
        WHERE jsonb_array_length(flags) > 0
        GROUP BY flags->>0
        ORDER BY cnt DESC LIMIT 5
    """)
    if rows:
        print("  상위 플래그:")
        for flag_raw in _q(cur, """
            SELECT flag_val, COUNT(*) AS cnt
            FROM universe_gate_pit,
                 jsonb_array_elements_text(flags) AS flag_val
            GROUP BY flag_val ORDER BY cnt DESC LIMIT 5
        """):
            print(f"    {flag_raw[0]:<35} {flag_raw[1]:>6,}")

    # [9] validation_log 최근 REJECT 건수
    print("\n[9] validation_log REJECT (check_id별)")
    rows = _q(cur, """
        SELECT check_id, COUNT(*) AS cnt
        FROM validation_log
        WHERE severity = 'REJECT'
        GROUP BY check_id ORDER BY cnt DESC
    """)
    if rows:
        for check_id, cnt in rows:
            print(f"  {check_id:<8} {cnt:>6,}건")
    else:
        print("  (REJECT 없음)")

    print("\n" + "=" * 65)


if __name__ == "__main__":
    main()
