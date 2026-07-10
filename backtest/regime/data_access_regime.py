"""
레짐 진단 전용 배치 DB 조회 헬퍼.

backtest/data_access.py는 종목 단위 조회 위주라 유니버스 전체(~2000종목) ×
126개월 스캔에는 비효율적 — 월말마다 배치(전체 유니버스 한 번에) 쿼리로 묶는다.
기존 backtest/data_access.py, backtest/engine.py는 import만 하고 수정하지 않는다.

Look-ahead 금지: 모든 조회는 as_of(<=) 조건을 건다.
"""
from __future__ import annotations

import logging
from datetime import date
from itertools import groupby

import pandas as pd

log = logging.getLogger(__name__)

# rim.py와 동일한 equity 우선순위 (backtest/models/rim.py:48-50)
_EQUITY_KEYS = ('지배기업소유주지분', '지배기업소유주지분_1', '자본총계')

_KOSPI_CACHE: pd.Series | None = None


# ── 유니버스 ─────────────────────────────────────────────────────────────────

def list_universe_tickers(conn, as_of: date) -> list[str]:
    """
    SPEC_07 §4-1 공통 유니버스: 상장 중 · 금융 제외 · is_excluded 제외.
    universe_gate_pit(투자가능판정)는 의도적으로 적용하지 않는다(v0.3 확정) —
    RIM 전략의 게이트가 섞이면 지표/벤치마크가 "우리가 걸러낸 유니버스 안"으로
    왜곡된다.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.ticker
            FROM stocks s
            WHERE s.is_financial = FALSE
              AND s.is_excluded = FALSE
              AND (s.listed_date IS NULL OR s.listed_date <= %s)
              AND NOT EXISTS (
                SELECT 1 FROM stock_listing_events sle
                WHERE sle.ticker = s.ticker
                  AND sle.delisted_date IS NOT NULL
                  AND sle.delisted_date <= %s
              )
            ORDER BY s.ticker
            """,
            (as_of, as_of),
        )
        return [r[0] for r in cur.fetchall()]


# ── 배치 시총 / 유동성 ───────────────────────────────────────────────────────

def market_cap_batch(conn, tickers: list[str], as_of: date) -> dict[str, float]:
    """as_of 기준 가장 가까운 시총. 티커별 1건(DISTINCT ON)."""
    if not tickers:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (ticker) ticker, market_cap
            FROM market_cap_history
            WHERE ticker = ANY(%s) AND date <= %s AND market_cap IS NOT NULL
            ORDER BY ticker, date DESC
            """,
            (tickers, as_of),
        )
        return {r[0]: float(r[1]) for r in cur.fetchall()}


def turnover_batch(conn, tickers: list[str], as_of: date,
                    window: int = 20, max_lookback_days: int = 90) -> dict[str, float]:
    """티커별 최근 window 영업일 평균 거래대금. get_avg_turnover()의 배치 버전."""
    if not tickers:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ticker, AVG(turnover)
            FROM (
                SELECT ticker, turnover,
                       ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn
                FROM price_history
                WHERE ticker = ANY(%s) AND date <= %s
                  AND date >= %s::date - (%s || ' days')::interval
                  AND is_suspended = FALSE AND turnover IS NOT NULL
            ) sub
            WHERE rn <= %s
            GROUP BY ticker
            """,
            (tickers, as_of, as_of, max_lookback_days, window),
        )
        return {r[0]: float(r[1]) for r in cur.fetchall() if r[1] is not None}


def price_series_batch(conn, tickers: list[str], start_date: date, end_date: date) -> pd.DataFrame:
    """[start_date, end_date] 구간 전체 adj_close. 컬럼: date, ticker, adj_close."""
    if not tickers:
        return pd.DataFrame(columns=['date', 'ticker', 'adj_close'])
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT date, ticker, adj_close
            FROM price_history
            WHERE ticker = ANY(%s) AND date BETWEEN %s AND %s AND adj_close IS NOT NULL
            ORDER BY ticker, date
            """,
            (tickers, start_date, end_date),
        )
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=['date', 'ticker', 'adj_close'])


def latest_close_batch(conn, tickers: list[str], as_of: date) -> dict[str, float]:
    """as_of 기준 가장 가까운 adj_close. get_close_price()의 배치 버전."""
    if not tickers:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (ticker) ticker, adj_close
            FROM price_history
            WHERE ticker = ANY(%s) AND date <= %s AND adj_close IS NOT NULL
            ORDER BY ticker, date DESC
            """,
            (tickers, as_of),
        )
        return {r[0]: float(r[1]) for r in cur.fetchall()}


# ── 배치 book equity (PIT) ───────────────────────────────────────────────────

def book_equity_batch(conn, tickers: list[str], as_of: date) -> dict[str, float]:
    """
    §4-2 PBR 분모. 티커별 as_of 이하 최신 (year, report_type) 보고서에서
    CFS 우선 · equity 키 우선순위(지배기업소유주지분 > _1 > 자본총계)로 산출.
    financials_pit 갱신은 반기 단위 → 보고서 사이 구간은 자동으로 직전 값 유지된다
    (as_of 조건으로 그 시점 최신값만 뽑으므로).

    DART 정정공시 룩어헤드 가드 — load_pit_series()(backtest/data_access.py)와 동일 규칙:
    amendment_from(정정 공시일)이 as_of 이후면 아직 시장에 공개되지 않은 정정값이므로
    original_amount(정정 전 원본값)를 대신 쓴다.
    """
    if not tickers:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH latest AS (
                SELECT DISTINCT ON (ticker) ticker, year, report_type
                FROM financials_pit
                WHERE ticker = ANY(%s) AND available_from <= %s
                ORDER BY ticker, available_from DESC
            ),
            prioritized AS (
                SELECT f.ticker, f.account_nm,
                       CASE
                           WHEN f.amendment_from IS NOT NULL AND f.amendment_from <= %s
                           THEN f.amount            -- 정정 공개됨 → 정정값
                           WHEN f.original_amount IS NOT NULL
                           THEN f.original_amount   -- 정정 미공개 → 원본값
                           ELSE f.amount             -- 정정 없음
                       END AS effective_amount,
                       ROW_NUMBER() OVER (
                           PARTITION BY f.ticker, f.account_nm
                           ORDER BY CASE f.fs_div WHEN 'CFS' THEN 1 ELSE 2 END
                       ) AS div_rank
                FROM financials_pit f
                JOIN latest l
                  ON f.ticker = l.ticker AND f.year = l.year AND f.report_type = l.report_type
                WHERE f.available_from <= %s
                  AND f.account_nm = ANY(%s)
            )
            SELECT ticker, account_nm, effective_amount FROM prioritized WHERE div_rank = 1
            """,
            (tickers, as_of, as_of, as_of, list(_EQUITY_KEYS)),
        )
        rows = cur.fetchall()

    by_ticker: dict[str, dict[str, float]] = {}
    for ticker, account_nm, amount in rows:
        if amount is not None:
            by_ticker.setdefault(ticker, {})[account_nm] = float(amount)

    result: dict[str, float] = {}
    for ticker, accts in by_ticker.items():
        equity = accts.get(_EQUITY_KEYS[0]) or accts.get(_EQUITY_KEYS[1]) or accts.get(_EQUITY_KEYS[2])
        if equity is not None:
            result[ticker] = equity
    return result


# ── 월말 캘린더 ──────────────────────────────────────────────────────────────

def month_end_dates(conn, start_date: date, end_date: date) -> list[date]:
    """
    (start_date, end_date] 구간의 월별 마지막 거래일. price_history DISTINCT date 기반
    (pykrx get_index_ohlcv_by_date()가 KRX 2024 리뉴얼 이후 불작동이라 CLAUDE.md 관례 따름).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT date FROM price_history
            WHERE date > %s AND date <= %s
            ORDER BY date
            """,
            (start_date, end_date),
        )
        dates = [r[0] for r in cur.fetchall()]
    result = []
    for _, grp in groupby(dates, key=lambda d: (d.year, d.month)):
        *_, last = grp
        result.append(last)
    return result


def next_trading_day(conn, after_date: date) -> date:
    """
    after_date보다 뒤(>)인 첫 거래일. SPEC_08 §3-1 — signal_date(월말)와 execution_date를
    분리해 "같은 종가로 신호→체결"하는 룩어헤드를 막기 위한 lag 계산용(Phase B 신규).
    price_history에 after_date 이후 데이터가 없으면(가장 최근 월말 등) None.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MIN(date) FROM price_history WHERE date > %s",
            (after_date,),
        )
        row = cur.fetchone()
    return row[0] if row and row[0] is not None else None


# ── KOSPI 벤치마크 ───────────────────────────────────────────────────────────

def kospi_return(start_date: date, end_date: date) -> float:
    """KS11(Naver Finance, fdr) 구간 수익률. 실패 시 0.0 (engine.py 관례와 동일)."""
    global _KOSPI_CACHE
    try:
        if _KOSPI_CACHE is None:
            import FinanceDataReader as fdr
            _KOSPI_CACHE = fdr.DataReader('KS11', '2015-01-01')['Close']
        s = _KOSPI_CACHE
        s1 = s[s.index <= pd.Timestamp(start_date)]
        s2 = s[s.index <= pd.Timestamp(end_date)]
        if s1.empty or s2.empty:
            return 0.0
        return float(s2.iloc[-1] / s1.iloc[-1] - 1)
    except Exception:
        log.exception('KOSPI 수익률 조회 실패 (%s~%s)', start_date, end_date)
        return 0.0
