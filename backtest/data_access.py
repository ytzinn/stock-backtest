"""
백테스트 DB 조회 헬퍼. 모든 필터/파이프라인에서 이 모듈만 import한다.
커넥션은 엔진에서 열어 conn 인자로 주입한다 (ingest/connection.py 팩토리 재사용).

단위: 모든 금액은 KRW(원), 주식수는 실제 주식 수.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date

import pandas as pd

log = logging.getLogger(__name__)


class PriceDataUnavailable(RuntimeError):
    """price_history에 해당 종목의 행이 아예 없음 (미수집 또는 미상장).

    '데이터 없음'과 '거래 없음(정지·무거래)'은 다른 상태다 (CORR-DA-001) —
    조용한 0/False 반환 대신 이 예외로 구분한다. 호출자가 '제외'로 처리하려면
    명시적으로 잡아서 사유를 남겨라 (hard_filter가 그렇게 한다).
    """


def _has_any_price_row(cur, ticker: str, as_of: date) -> bool:
    cur.execute(
        "SELECT 1 FROM price_history WHERE ticker = %s AND date <= %s LIMIT 1",
        (ticker, as_of),
    )
    return cur.fetchone() is not None


# TTM 계산 대상 계정 (IS + CF: 기간 누적값)
_IS_CF_ACCOUNTS = frozenset({
    '매출액', '매출총이익', '영업이익', '당기순이익',
    '영업활동현금흐름', '투자활동현금흐름', '재무활동현금흐름', '배당금지급',
})


# ── 가격 / 거래대금 ─────────────────────────────────────────────────────────────

def get_avg_turnover(conn, ticker: str, as_of: date, window: int = 20,
                     max_lookback_days: int = 90) -> float:
    """최근 window 영업일 평균 거래대금(KRW).

    계약 (CORR-DA-001):
      - price_history에 이 종목의 행이 **아예 없으면** PriceDataUnavailable을 던진다
        (미수집/미상장 — '무거래'와 다른 상태. 조용한 0 반환 금지).
      - 행은 있으나 거래정지·turnover NULL 등으로 유효 거래가 없으면 0.0
        (실제 '거래 없음' — 유동성 부족 제외 사유로 정당).

    max_lookback_days: 이 기간(캘린더 일) 밖의 데이터는 사용하지 않는다.
    거래정지 후 오래된 거래량이 현재 유동성인 것처럼 계산되는 것을 방지.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT AVG(turnover), COUNT(*)
            FROM (
                SELECT turnover
                FROM price_history
                WHERE ticker = %s AND date <= %s
                  AND date >= %s - INTERVAL '1 day' * %s
                  AND is_suspended = FALSE
                  AND turnover IS NOT NULL
                ORDER BY date DESC
                LIMIT %s
            ) sub
            """,
            (ticker, as_of, as_of, max_lookback_days, window),
        )
        avg, cnt = cur.fetchone()
        if cnt == 0 and not _has_any_price_row(cur, ticker, as_of):
            raise PriceDataUnavailable(f'{ticker}: price_history에 {as_of} 이전 행 없음')
    return float(avg) if avg is not None else 0.0


def has_recent_trade(conn, ticker: str, as_of: date, window: int = 5) -> bool:
    """최근 window 영업일(KRX 거래일 기준) 중 거래가 한 건이라도 있으면 True.

    계약 (CORR-DA-001): price_history에 이 종목의 행이 아예 없으면
    PriceDataUnavailable을 던진다 — '거래정지'(False)와 '데이터 미수집'은 다른 상태다.

    price_history에서 as_of 이전 최근 window 거래일을 조회해 is_suspended=FALSE인
    날이 하나라도 있는지 확인한다. 없으면 거래정지 상태로 간주.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT is_suspended
                FROM price_history
                WHERE ticker = %s AND date <= %s AND adj_close IS NOT NULL
                ORDER BY date DESC
                LIMIT %s
            ) sub
            WHERE is_suspended = FALSE
            """,
            (ticker, as_of, window),
        )
        row = cur.fetchone()
        if (not row or row[0] == 0) and not _has_any_price_row(cur, ticker, as_of):
            raise PriceDataUnavailable(f'{ticker}: price_history에 {as_of} 이전 행 없음')
    return (row[0] > 0) if row else False


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


def get_max_price_date(conn) -> date | None:
    """price_history 전체의 최신 거래일. 데이터 신선도 검증용 (CORR-FRESH-001). 없으면 None."""
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(date) FROM price_history")
        row = cur.fetchone()
    return row[0] if row else None


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
    """stocks.listed_date 반환. 없으면 None (운영 DB의 92%가 NULL — CORR-HARD-001,
    백필 전까지 호출자는 get_first_price_date 프록시로 보완해야 한다)."""
    with conn.cursor() as cur:
        cur.execute("SELECT listed_date FROM stocks WHERE ticker = %s", (ticker,))
        row = cur.fetchone()
    return row[0] if row else None


def get_first_price_date(conn, ticker: str) -> date | None:
    """price_history 최초 거래일. 상장일 프록시 (실제 상장일보다 늦을 수 없는 하한이 아니라
    수집 시작일(2014-01)로 절단된 값 — 2014년 이전 상장 종목은 2014년으로 나온다.
    '최근 상장' 판정(상장 N개월 미만)에는 보수적으로 안전: 프록시가 실제보다 늦으면
    더 오래 제외될 뿐 조기 편입은 없다). 없으면 None."""
    with conn.cursor() as cur:
        cur.execute("SELECT MIN(date) FROM price_history WHERE ticker = %s", (ticker,))
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

def load_gate_passed_tickers(
    conn,
    rebalance_date: date,
    report_type: str = 'FY',
) -> list[str]:
    """
    리밸런싱 기준일에 투자 가능한 종목 목록.

    조건:
      1. stocks.is_excluded = FALSE
      2. universe_gate_pit.status = 'PASS' (rebalance_date 기준 최신 report_type)
      3. rebalance_date 이전에 상장폐지된 종목 제외 (stock_listing_events 기준)

    report_type: 'FY' (4월 리밸런싱) 또는 'H1' (8월 리밸런싱)
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH latest_report AS (
                SELECT DISTINCT ON (ticker) ticker, year, report_type
                FROM financials_pit
                WHERE available_from <= %s AND report_type = %s
                ORDER BY ticker, year DESC
            ),
            gate_pass AS (
                SELECT lr.ticker
                FROM latest_report lr
                JOIN universe_gate_pit ugp
                  ON lr.ticker = ugp.ticker
                 AND lr.year = ugp.year
                 AND lr.report_type = ugp.report_type
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
            (rebalance_date, report_type, rebalance_date),
        )
        return [row[0] for row in cur.fetchall()]


def load_pit_series(
    conn,
    rebalance_date: date,
    n_years: int = 3,
    report_type: str = 'FY',
) -> dict[str, list[dict]]:
    """
    universe_gate_pit PASS 종목 전체에 대해 rebalance_date 기준
    최신 n_years 개 PIT 데이터를 로드.

    반환: {ticker: [현재dict, t-1dict, t-2dict]}
      - available_from <= rebalance_date 조건 (룩어헤드 방지)
      - report_type: 'FY' (4월 리밸런싱) 또는 'H1' (8월 리밸런싱)
      - CFS(연결) 우선, OFS(별도) fallback
      - 각 dict는 {account_nm: amount} flat dict
      - 연도가 부족한 종목은 리스트 길이가 짧아짐
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH avail AS (
                SELECT DISTINCT ON (ticker, year)
                    ticker, year, report_type
                FROM financials_pit
                WHERE available_from <= %s AND report_type = %s
                ORDER BY ticker, year DESC, available_from ASC
            ),
            top_n AS (
                SELECT ticker, year, report_type,
                       ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY year DESC) AS rn
                FROM avail
            ),
            selected AS (
                SELECT ticker, year, report_type FROM top_n WHERE rn <= %s
            ),
            -- CFS 우선, OFS fallback (동일 account_nm에서 CFS 선택)
            prioritized AS (
                SELECT f.ticker, f.year, f.account_nm,
                       CASE
                           WHEN f.amendment_from IS NOT NULL AND f.amendment_from <= %s
                           THEN f.amount           -- 정정 공개됨 → 정정값
                           WHEN f.amendment_from IS NOT NULL AND f.original_amount IS NULL
                           THEN NULL               -- 정정 미공개 + 원본 미상 → 사용 불가
                                                   -- (정정값을 쓰면 룩어헤드 — PIT-AMEND-001.
                                                   --  NULL은 파이썬 쪽에서 계정 제외로 처리)
                           WHEN f.original_amount IS NOT NULL
                           THEN f.original_amount  -- 정정 미공개 → 원본값
                           ELSE f.amount           -- 정정 없음
                       END AS effective_amount,
                       ROW_NUMBER() OVER (
                           PARTITION BY f.ticker, f.year, f.account_nm
                           ORDER BY CASE f.fs_div WHEN 'CFS' THEN 1 ELSE 2 END
                       ) AS div_rank
                FROM financials_pit f
                JOIN selected s
                  ON f.ticker = s.ticker AND f.year = s.year AND f.report_type = s.report_type
                WHERE f.available_from <= %s
            )
            SELECT ticker, year, account_nm, effective_amount
            FROM prioritized
            WHERE div_rank = 1
            ORDER BY ticker, year DESC, account_nm
            """,
            (rebalance_date, report_type, n_years, rebalance_date, rebalance_date),
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


def load_pit_series_ttm(
    conn,
    rebalance_date: date,
    report_type: str = 'FY',
) -> dict[str, list[dict]]:
    """
    TTM(Trailing Twelve Months) 적용 PIT 시계열 로드.

    FY 리밸런싱(4월): load_pit_series(n_years=3) 그대로 반환.
    H1 리밸런싱(8월): TTM = FY_prev − H1_prev + H1_curr 공식 적용.
      반환 리스트: [ttm_curr, ttm_prev, ttm_pp] — 3개 (stability_filter series[2] 사용)
      BS 계정(자산·부채·자본)은 H1_curr 그대로 사용.
    """
    if report_type == 'FY':
        return load_pit_series(conn, rebalance_date, n_years=3, report_type='FY')

    # H1: TTM 3개 생성하려면 h1 4개, fy 3개 필요
    h1_series = load_pit_series(conn, rebalance_date, n_years=4, report_type='H1')
    fy_series = load_pit_series(conn, rebalance_date, n_years=3, report_type='FY')

    result: dict[str, list[dict]] = {}
    for ticker, h1_list in h1_series.items():
        h1c   = h1_list[0] if len(h1_list) > 0 else {}
        h1p   = h1_list[1] if len(h1_list) > 1 else {}
        h1pp  = h1_list[2] if len(h1_list) > 2 else {}
        h1ppp = h1_list[3] if len(h1_list) > 3 else {}
        fy    = fy_series.get(ticker, [])
        fyp   = fy[0] if len(fy) > 0 else {}
        fypp  = fy[1] if len(fy) > 1 else {}
        fyppp = fy[2] if len(fy) > 2 else {}

        result[ticker] = [
            _make_ttm(fyp,   h1c,  h1p,   ticker),
            _make_ttm(fypp,  h1p,  h1pp,  ticker),
            _make_ttm(fyppp, h1pp, h1ppp, ticker),
        ]

    return result


def _make_ttm(fy_d: dict, h1_curr: dict, h1_prev: dict, ticker: str) -> dict:
    """
    TTM = FY_prev − H1_prev + H1_curr (IS/CF 계정만).
    BS 계정은 h1_curr 그대로.
    FY 없으면 H1×2 fallback.
    """
    result = dict(h1_curr)
    for acct in _IS_CF_ACCOUNTS:
        fy_val  = fy_d.get(acct)
        h1c_val = h1_curr.get(acct)
        h1p_val = h1_prev.get(acct)
        if fy_val is not None:
            if h1c_val is not None and h1p_val is not None:
                result[acct] = fy_val - h1p_val + h1c_val
            else:
                result.pop(acct, None)
        elif h1c_val is not None:
            log.debug('%s %s: FY 없어 H1×2 fallback', ticker, acct)
            result[acct] = h1c_val * 2
        else:
            result.pop(acct, None)
    return result
