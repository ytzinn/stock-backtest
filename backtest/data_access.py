"""
백테스트 DB 조회 헬퍼. 모든 필터/파이프라인에서 이 모듈만 import한다.
커넥션은 엔진에서 열어 conn 인자로 주입한다 (ingest/connection.py 팩토리 재사용).

단위: 모든 금액은 KRW(원), 주식수는 실제 주식 수.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

import pandas as pd


# ── 가격 / 거래대금 ─────────────────────────────────────────────────────────────

def get_avg_turnover(conn, ticker: str, as_of: date, window: int = 20) -> float:
    """최근 window 영업일 평균 거래대금(KRW). 데이터 없거나 is_suspended이면 0으로 처리."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(AVG(turnover), 0)
            FROM (
                SELECT turnover
                FROM price_history
                WHERE ticker = %s AND date <= %s
                  AND is_suspended = FALSE
                  AND turnover IS NOT NULL
                ORDER BY date DESC
                LIMIT %s
            ) sub
            """,
            (ticker, as_of, window),
        )
        row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0


def get_adj_close_range(conn, ticker: str, as_of: date, lookback: int) -> pd.Series:
    """as_of 이전 lookback 영업일 adj_close 시계열 (오름차순 날짜 인덱스). 없으면 빈 Series."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT date, adj_close
            FROM price_history
            WHERE ticker = %s AND date <= %s AND adj_close IS NOT NULL
            ORDER BY date DESC
            LIMIT %s
            """,
            (ticker, as_of, lookback),
        )
        rows = cur.fetchall()
    if not rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(rows, columns=['date', 'adj_close'])
    return df.set_index('date')['adj_close'].sort_index()


def get_close_price(conn, ticker: str, as_of: date) -> float | None:
    """as_of 기준 가장 가까운 adj_close. 없으면 None."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT adj_close FROM price_history
            WHERE ticker = %s AND date <= %s AND adj_close IS NOT NULL
            ORDER BY date DESC LIMIT 1
            """,
            (ticker, as_of),
        )
        row = cur.fetchone()
    return float(row[0]) if row else None


# ── 시가총액 / 주식수 ────────────────────────────────────────────────────────────

def get_market_cap(conn, ticker: str, as_of: date) -> float | None:
    """as_of 기준 가장 가까운 시가총액(KRW). 없으면 None."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT market_cap FROM market_cap_history
            WHERE ticker = %s AND date <= %s AND market_cap IS NOT NULL
            ORDER BY date DESC LIMIT 1
            """,
            (ticker, as_of),
        )
        row = cur.fetchone()
    return float(row[0]) if row else None


def get_shares_outstanding(conn, ticker: str, as_of: date) -> int | None:
    """as_of 기준 가장 가까운 상장주식수. 없으면 None."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT shares FROM market_cap_history
            WHERE ticker = %s AND date <= %s AND shares IS NOT NULL
            ORDER BY date DESC LIMIT 1
            """,
            (ticker, as_of),
        )
        row = cur.fetchone()
    return int(row[0]) if row else None


# ── 종목 메타 ───────────────────────────────────────────────────────────────────

def get_listed_date(conn, ticker: str) -> date | None:
    """stocks.listed_date 반환. 없으면 None."""
    with conn.cursor() as cur:
        cur.execute("SELECT listed_date FROM stocks WHERE ticker = %s", (ticker,))
        row = cur.fetchone()
    return row[0] if row else None


def is_delisted_at(conn, ticker: str, as_of: date) -> bool:
    """as_of 시점에 상장폐지 여부. stock_listing_events 기준."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM stock_listing_events
            WHERE ticker = %s
              AND delisted_date IS NOT NULL
              AND delisted_date <= %s
            LIMIT 1
            """,
            (ticker, as_of),
        )
        return cur.fetchone() is not None


# ── PIT 데이터 ──────────────────────────────────────────────────────────────────

def load_gate_passed_tickers(conn, rebalance_date: date) -> list[str]:
    """
    리밸런싱 기준일에 투자 가능한 종목 목록.

    조건:
      1. stocks.is_excluded = FALSE
      2. universe_gate_pit.status = 'PASS' (rebalance_date 기준 최신 FY)
      3. rebalance_date 이전에 상장폐지된 종목 제외 (stock_listing_events 기준)

    상장 여부 확인은 HardFilter R07(is_delisted_at)에서 재확인한다.
    stock_listing_events.listed_date가 전체 NULL인 수집 한계로 인해
    상장 중 종목 조인 대신 상장폐지 확정 종목만 제외하는 방식 채택.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH latest_fy AS (
                SELECT DISTINCT ON (ticker) ticker, year, report_type
                FROM financials_pit
                WHERE available_from <= %s AND report_type = 'FY'
                ORDER BY ticker, year DESC
            ),
            gate_pass AS (
                SELECT lf.ticker
                FROM latest_fy lf
                JOIN universe_gate_pit ugp
                  ON lf.ticker = ugp.ticker
                 AND lf.year = ugp.year
                 AND lf.report_type = ugp.report_type
                WHERE ugp.status = 'PASS'
            )
            SELECT s.ticker
            FROM stocks s
            JOIN gate_pass gp ON s.ticker = gp.ticker
            WHERE s.is_excluded = FALSE
              AND NOT EXISTS (
                SELECT 1 FROM stock_listing_events sle
                WHERE sle.ticker = s.ticker
                  AND sle.delisted_date IS NOT NULL
                  AND sle.delisted_date <= %s
              )
            ORDER BY s.ticker
            """,
            (rebalance_date, rebalance_date),
        )
        return [row[0] for row in cur.fetchall()]


def load_pit_series(
    conn,
    rebalance_date: date,
    n_years: int = 3,
) -> dict[str, list[dict]]:
    """
    universe_gate_pit PASS 종목 전체에 대해 rebalance_date 기준
    최신 n_years 개 FY PIT 데이터를 로드.

    반환: {ticker: [FY현재dict, FY(t-1)dict, FY(t-2)dict]}
      - available_from <= rebalance_date 조건 (룩어헤드 방지)
      - FY(연간) 보고서만 사용 (report_type='FY')
      - CFS(연결) 우선, OFS(별도) fallback
      - 각 dict는 {account_nm: amount} flat dict
      - 연도가 부족한 종목은 리스트 길이가 짧아짐
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH fy_avail AS (
                -- 각 (ticker, year)별 가장 이른 available_from (공시 완료 기준)
                SELECT DISTINCT ON (ticker, year)
                    ticker, year, report_type
                FROM financials_pit
                WHERE available_from <= %s AND report_type = 'FY'
                ORDER BY ticker, year DESC, available_from ASC
            ),
            top_n AS (
                SELECT ticker, year, report_type,
                       ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY year DESC) AS rn
                FROM fy_avail
            ),
            selected AS (
                SELECT ticker, year, report_type FROM top_n WHERE rn <= %s
            ),
            -- CFS 우선, OFS fallback (동일 account_nm에서 CFS 선택)
            prioritized AS (
                SELECT f.ticker, f.year, f.account_nm, f.amount,
                       ROW_NUMBER() OVER (
                           PARTITION BY f.ticker, f.year, f.account_nm
                           ORDER BY CASE f.fs_div WHEN 'CFS' THEN 1 ELSE 2 END
                       ) AS div_rank
                FROM financials_pit f
                JOIN selected s
                  ON f.ticker = s.ticker AND f.year = s.year AND f.report_type = s.report_type
                WHERE f.available_from <= %s
            )
            SELECT ticker, year, account_nm, amount
            FROM prioritized
            WHERE div_rank = 1
            ORDER BY ticker, year DESC, account_nm
            """,
            (rebalance_date, n_years, rebalance_date),
        )
        rows = cur.fetchall()

    # {ticker: {year: {account_nm: amount}}}
    raw: dict[str, dict[int, dict]] = defaultdict(lambda: defaultdict(dict))
    for ticker, year, account_nm, amount in rows:
        if amount is not None:
            raw[ticker][year][account_nm] = float(amount)

    result: dict[str, list[dict]] = {}
    for ticker, year_dict in raw.items():
        sorted_years = sorted(year_dict.keys(), reverse=True)[:n_years]
        result[ticker] = [year_dict[yr] for yr in sorted_years]

    return result
