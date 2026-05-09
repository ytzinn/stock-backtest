from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable

import streamlit as st
from psycopg2.extras import RealDictCursor

from dashboard.config import CACHE_TTL_SEC, DB_STATEMENT_TIMEOUT_MS
from ingest.connection import get_connection


def _coerce(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _rows(cur) -> list[dict[str, Any]]:
    return [{key: _coerce(value) for key, value in row.items()} for row in cur.fetchall()]


def _query(sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"SET statement_timeout = {int(DB_STATEMENT_TIMEOUT_MS)}")
            cur.execute(sql, params or ())
            return _rows(cur)
    finally:
        conn.close()


def safe_call(fn: Callable[[], list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], str | None]:
    try:
        return fn(), None
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_row_counts() -> list[dict[str, Any]]:
    return _query(
        """
        SELECT 'stocks' AS tbl, COUNT(*)::bigint AS rows FROM stocks UNION ALL
        SELECT 'stock_listing_events', COUNT(*)::bigint FROM stock_listing_events UNION ALL
        SELECT 'financials', COUNT(*)::bigint FROM financials UNION ALL
        SELECT 'financials_pit', COUNT(*)::bigint FROM financials_pit UNION ALL
        SELECT 'disclosures', COUNT(*)::bigint FROM disclosures UNION ALL
        SELECT 'price_history', COUNT(*)::bigint FROM price_history UNION ALL
        SELECT 'market_cap_history', COUNT(*)::bigint FROM market_cap_history UNION ALL
        SELECT 'universe_gate_pit', COUNT(*)::bigint FROM universe_gate_pit UNION ALL
        SELECT 'ingest_status', COUNT(*)::bigint FROM ingest_status UNION ALL
        SELECT 'validation_log', COUNT(*)::bigint FROM validation_log UNION ALL
        SELECT 'rim_input_status', COUNT(*)::bigint FROM rim_input_status UNION ALL
        SELECT 'backtest_runs', COUNT(*)::bigint FROM backtest_runs
        ORDER BY tbl
        """
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_ingest_progress() -> list[dict[str, Any]]:
    return _query(
        """
        SELECT
            COUNT(*) FILTER (WHERE status = 'done')::bigint AS done,
            COUNT(*) FILTER (WHERE status = 'error')::bigint AS error,
            COUNT(*) FILTER (WHERE status = 'pending')::bigint AS pending,
            COUNT(*)::bigint AS total
        FROM ingest_status
        """
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_ingest_status_summary() -> list[dict[str, Any]]:
    return _query(
        """
        SELECT status, COUNT(*)::bigint AS count, MAX(last_attempt) AS latest,
               COALESCE(SUM(call_count), 0)::bigint AS total_calls
        FROM ingest_status
        GROUP BY status
        ORDER BY count DESC
        """
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_ingest_errors() -> list[dict[str, Any]]:
    return _query(
        """
        SELECT ticker, status, last_attempt, error_msg
        FROM ingest_status
        WHERE status <> 'done' OR error_msg IS NOT NULL
        ORDER BY last_attempt DESC NULLS LAST
        LIMIT 20
        """
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_price_freshness() -> list[dict[str, Any]]:
    return _query(
        """
        WITH latest AS (SELECT MAX(date) AS latest_date FROM price_history)
        SELECT latest.latest_date,
               COUNT(DISTINCT p.ticker)::bigint AS ticker_count
        FROM latest
        LEFT JOIN price_history p ON p.date = latest.latest_date
        GROUP BY latest.latest_date
        """
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_market_cap_freshness() -> list[dict[str, Any]]:
    return _query(
        """
        WITH latest AS (SELECT MAX(date) AS latest_date FROM market_cap_history)
        SELECT latest.latest_date,
               COUNT(DISTINCT m.ticker)::bigint AS ticker_count
        FROM latest
        LEFT JOIN market_cap_history m ON m.date = latest.latest_date
        GROUP BY latest.latest_date
        """
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_recent_price_coverage() -> list[dict[str, Any]]:
    return _query(
        """
        SELECT date, COUNT(DISTINCT ticker)::bigint AS ticker_count
        FROM price_history
        GROUP BY date
        ORDER BY date DESC
        LIMIT 10
        """
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_recent_market_cap_coverage() -> list[dict[str, Any]]:
    return _query(
        """
        SELECT date, COUNT(DISTINCT ticker)::bigint AS ticker_count
        FROM market_cap_history
        GROUP BY date
        ORDER BY date DESC
        LIMIT 10
        """
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_dq_gate_summary() -> list[dict[str, Any]]:
    return _query(
        """
        SELECT status, report_type, COUNT(*)::bigint AS cnt,
               ROUND(COUNT(*) * 100.0 / NULLIF(SUM(COUNT(*)) OVER (PARTITION BY report_type), 0), 2) AS pct
        FROM universe_gate_pit
        GROUP BY status, report_type
        ORDER BY report_type, status
        """
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_dq_gate_top_rejects() -> list[dict[str, Any]]:
    return _query(
        """
        SELECT reason, COUNT(*)::bigint AS cnt
        FROM universe_gate_pit,
             jsonb_array_elements_text(reject_reasons) AS reason
        GROUP BY reason
        ORDER BY cnt DESC
        LIMIT 10
        """
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_validation_summary() -> list[dict[str, Any]]:
    return _query(
        """
        SELECT check_id, severity, COUNT(*)::bigint AS count
        FROM validation_log
        GROUP BY check_id, severity
        ORDER BY check_id, severity
        """
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_validation_top_tickers() -> list[dict[str, Any]]:
    return _query(
        """
        SELECT ticker,
               COUNT(*) FILTER (WHERE severity = 'REJECT')::bigint AS reject_count,
               COUNT(*) FILTER (WHERE severity = 'WARN')::bigint AS warn_count,
               COUNT(*)::bigint AS total_count,
               MAX(evaluated_at) AS latest
        FROM validation_log
        GROUP BY ticker
        ORDER BY total_count DESC, reject_count DESC
        LIMIT 20
        """
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_pit_fallback_rate() -> list[dict[str, Any]]:
    return _query(
        """
        SELECT COUNT(*) FILTER (WHERE fallback_used)::bigint AS fallback_cnt,
               COUNT(*)::bigint AS total_cnt,
               ROUND(COUNT(*) FILTER (WHERE fallback_used) * 100.0 / NULLIF(COUNT(*), 0), 2) AS fallback_pct
        FROM financials_pit
        """
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_pit_available_from_anomalies() -> list[dict[str, Any]]:
    return _query(
        """
        SELECT ticker, year, report_type, available_from, fallback_used
        FROM financials_pit
        WHERE available_from > CURRENT_DATE
           OR available_from < DATE '2010-01-01'
        ORDER BY available_from DESC
        LIMIT 50
        """
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_stocks_stats() -> list[dict[str, Any]]:
    return _query(
        """
        SELECT COALESCE(market, '(unknown)') AS market,
               COUNT(*)::bigint AS total,
               COUNT(*) FILTER (WHERE is_excluded = FALSE)::bigint AS active,
               COUNT(*) FILTER (WHERE is_financial = TRUE)::bigint AS financial,
               COUNT(*) FILTER (WHERE is_excluded = TRUE)::bigint AS excluded
        FROM stocks
        GROUP BY COALESCE(market, '(unknown)')
        ORDER BY market
        """
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_orphan_checks() -> list[dict[str, Any]]:
    return _query(
        """
        SELECT 'price_history_without_stocks' AS check_id, COUNT(*)::bigint AS cnt
        FROM price_history p LEFT JOIN stocks s ON s.ticker = p.ticker WHERE s.ticker IS NULL
        UNION ALL
        SELECT 'market_cap_without_stocks', COUNT(*)::bigint
        FROM market_cap_history m LEFT JOIN stocks s ON s.ticker = m.ticker WHERE s.ticker IS NULL
        UNION ALL
        SELECT 'validation_without_stocks', COUNT(*)::bigint
        FROM validation_log v LEFT JOIN stocks s ON s.ticker = v.ticker WHERE s.ticker IS NULL
        """
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_backtest_runs() -> list[dict[str, Any]]:
    return _query(
        """
        SELECT run_id, run_name, phase, ablation_tag, git_commit, param_hash,
               data_cutoff_date, db_schema_version, started_at, finished_at,
               status, error_msg, created_at
        FROM backtest_runs
        ORDER BY created_at DESC
        LIMIT 20
        """
    )

