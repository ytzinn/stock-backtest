from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable

import streamlit as st
from psycopg2.extras import RealDictCursor

from dashboard.config import CACHE_TTL_SEC, DB_STATEMENT_TIMEOUT_MS
from ingest.connection import get_connection

DB_BROWSER_TABLES = {
    "stocks",
    "stock_listing_events",
    "financials",
    "financials_pit",
    "disclosures",
    "price_history",
    "market_cap_history",
    "universe_gate_pit",
    "ingest_status",
    "validation_log",
    "backtest_runs",
    "reasoning_log",
    "classification_history",
    "rim_input_status",
}

VALIDATION_CHECKS = {
    "V01": {
        "name": "Balance equation",
        "description": "자산총계가 부채총계 + 자본총계와 맞는지 확인합니다.",
        "reject_rule": "오차 > 5%",
        "warn_rule": "오차 1~5%",
    },
    "V02": {
        "name": "Operating income <= revenue",
        "description": "영업이익이 매출액보다 큰 비정상 손익계층을 확인합니다.",
        "reject_rule": "",
        "warn_rule": "영업이익 > 매출액",
    },
    "V03": {
        "name": "NI-CFO gap",
        "description": "당기순이익과 영업활동현금흐름의 괴리가 자본 대비 과도한지 확인합니다.",
        "reject_rule": "",
        "warn_rule": "|당기순이익 - 영업CF| > 자본총계 30%",
    },
    "V04": {
        "name": "Revenue spike",
        "description": "매출액이 전년 대비 극단적으로 급변했는지 확인합니다.",
        "reject_rule": "",
        "warn_rule": "매출액 전년비 +/-500% 이상",
    },
    "V05": {
        "name": "Equity spike",
        "description": "자본총계가 전년 대비 극단적으로 급변했는지 확인합니다.",
        "reject_rule": "",
        "warn_rule": "자본총계 전년비 +/-300% 이상",
    },
    "V06": {
        "name": "Negative equity",
        "description": "자본총계가 음수인 자본잠식 후보를 확인합니다.",
        "reject_rule": "자본총계 < 0",
        "warn_rule": "",
    },
    "V07": {
        "name": "Negative assets",
        "description": "자산총계가 음수인 비정상 재무제표를 확인합니다.",
        "reject_rule": "자산총계 < 0",
        "warn_rule": "",
    },
    "V08": {
        "name": "Core account missing",
        "description": "매출액, 영업이익, 자본총계 중 핵심 계정 누락이 많은지 확인합니다.",
        "reject_rule": "핵심 계정 2개 이상 누락",
        "warn_rule": "",
    },
    "V09": {
        "name": "Negative liabilities",
        "description": "부채총계가 음수인 비정상 재무제표를 확인합니다.",
        "reject_rule": "부채총계 < 0",
        "warn_rule": "",
    },
    "V10": {
        "name": "P&L hierarchy",
        "description": "매출총이익, 매출액, 영업이익의 손익계산서 계층 순서가 맞는지 확인합니다.",
        "reject_rule": "",
        "warn_rule": "매출총이익 > 매출액 또는 영업이익 > 매출총이익",
    },
    "V11": {
        "name": "Controlling equity sanity",
        "description": "지배기업소유주지분이 자본총계를 초과하는 비정상 케이스를 확인합니다.",
        "reject_rule": "",
        "warn_rule": "지배기업소유주지분 > 자본총계 x 1.01",
    },
}

DQ_GATE_CHECKS = {
    "R01": {
        "kind": "reject",
        "name": "Permanent exclusion",
        "description": "리츠, 스팩, ETF 등 구조적으로 백테스트 대상에서 제외할 종목입니다.",
        "rule": "stocks.is_excluded = TRUE",
        "storage": "stocks",
    },
    "R02": {
        "kind": "reject",
        "name": "Negative equity",
        "description": "자본총계가 음수인 자본잠식 후보입니다.",
        "rule": "자본총계 < 0",
        "storage": "universe_gate_pit.reject_reasons",
    },
    "R03": {
        "kind": "reject",
        "name": "Negative assets",
        "description": "자산총계가 음수인 비정상 재무제표입니다.",
        "rule": "자산총계 < 0",
        "storage": "universe_gate_pit.reject_reasons",
    },
    "R04": {
        "kind": "reject",
        "name": "Core account missing",
        "description": "매출액, 영업이익, 자본총계 중 핵심 계정이 많이 누락된 기간입니다.",
        "rule": "핵심 계정 2개 이상 누락",
        "storage": "universe_gate_pit.reject_reasons",
    },
    "R05": {
        "kind": "reject",
        "name": "Consecutive FY missing",
        "description": "FY 재무 데이터가 연속으로 비어 있는 기간입니다.",
        "rule": "FY 핵심 계정이 현재/직전 연도 모두 누락",
        "storage": "universe_gate_pit.reject_reasons",
    },
    "R06": {
        "kind": "hard_filter",
        "name": "Audit opinion issue",
        "description": "감사의견 비적정/한정 등 공시 기반 위험입니다.",
        "rule": "미구현, Hard Filter로 이동 예정",
        "storage": "HardFilter",
    },
    "R07": {
        "kind": "hard_filter",
        "name": "Delisting event",
        "description": "리밸런싱 시점 기준 상장폐지 이력입니다.",
        "rule": "stock_listing_events 기준 상장 중 아님",
        "storage": "HardFilter",
    },
    "R08": {
        "kind": "hard_filter",
        "name": "Administrative issue",
        "description": "관리종목 지정 등 공시 기반 위험입니다.",
        "rule": "미구현, Hard Filter로 이동 예정",
        "storage": "HardFilter",
    },
    "R09": {
        "kind": "reject",
        "name": "Balance equation mismatch",
        "description": "자산총계가 부채총계 + 자본총계와 크게 맞지 않는 기간입니다.",
        "rule": "자산 = 부채 + 자본 오차 > 5%",
        "storage": "universe_gate_pit.reject_reasons",
    },
    "P01": {
        "kind": "flag",
        "name": "Revenue spike",
        "description": "매출액 전년비가 극단적으로 변한 기간입니다.",
        "rule": "매출액 전년비 +/-500% 초과",
        "storage": "universe_gate_pit.flags",
    },
    "P02": {
        "kind": "flag",
        "name": "Equity spike",
        "description": "자본총계 전년비가 극단적으로 변한 기간입니다.",
        "rule": "자본총계 전년비 +/-300% 초과",
        "storage": "universe_gate_pit.flags",
    },
    "P03": {
        "kind": "flag",
        "name": "Accrual alert",
        "description": "당기순이익과 영업활동현금흐름의 괴리가 큰 기간입니다.",
        "rule": "|당기순이익 - 영업CF| > 자본총계 30%",
        "storage": "universe_gate_pit.flags",
    },
    "P04": {
        "kind": "flag",
        "name": "CFO missing",
        "description": "RIM과 재무안정성 필터에 중요한 영업활동현금흐름이 누락된 기간입니다.",
        "rule": "영업활동현금흐름 없음",
        "storage": "universe_gate_pit.flags",
    },
    "P05": {
        "kind": "flag",
        "name": "Unit change suspect",
        "description": "계정 값이 전년 대비 100배 이상 급변해 단위 변경 또는 매핑 오류가 의심됩니다.",
        "rule": "매출액/자산총계/자본총계 전년비 > 100배 또는 < 0.01배",
        "storage": "universe_gate_pit.flags",
    },
}


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


def get_validation_nomenclature() -> list[dict[str, Any]]:
    return [
        {"check_id": check_id, **metadata}
        for check_id, metadata in VALIDATION_CHECKS.items()
    ]


def get_dq_gate_nomenclature() -> list[dict[str, Any]]:
    return [
        {"code": code, **metadata}
        for code, metadata in DQ_GATE_CHECKS.items()
    ]


def _dq_code(reason: str | None) -> str:
    if not reason:
        return ""
    text = str(reason)
    return text.split(":", 1)[0].strip()


def _dq_description(reason: str | None) -> str:
    code = _dq_code(reason)
    metadata = DQ_GATE_CHECKS.get(code, {})
    return metadata.get("description", "미등록 DQ 코드")


def _table_name(name: str) -> str:
    if name not in DB_BROWSER_TABLES:
        raise ValueError(f"Unsupported dashboard table: {name}")
    return name


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
        WITH dart_targets AS (
            SELECT ticker
            FROM stocks
            WHERE is_excluded = FALSE
              AND corp_code IS NOT NULL
        )
        SELECT
            COUNT(*) FILTER (WHERE i.status = 'done')::bigint AS done,
            COUNT(*) FILTER (WHERE i.status = 'error')::bigint AS error,
            COUNT(*) FILTER (WHERE i.status = 'pending' OR i.ticker IS NULL)::bigint AS pending,
            COUNT(*) FILTER (WHERE i.ticker IS NULL)::bigint AS missing_status,
            COUNT(*)::bigint AS total
        FROM dart_targets d
        LEFT JOIN ingest_status i ON i.ticker = d.ticker
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
    rows = _query(
        """
        SELECT reason, COUNT(*)::bigint AS cnt
        FROM universe_gate_pit,
             jsonb_array_elements_text(reject_reasons) AS reason
        GROUP BY reason
        ORDER BY cnt DESC
        LIMIT 10
        """
    )
    for row in rows:
        reason = str(row.get("reason") or "")
        row["code"] = _dq_code(reason)
        row["description"] = _dq_description(reason)
    return rows


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_dq_gate_top_flags() -> list[dict[str, Any]]:
    rows = _query(
        """
        SELECT flag, COUNT(*)::bigint AS cnt
        FROM universe_gate_pit,
             jsonb_array_elements_text(flags) AS flag
        GROUP BY flag
        ORDER BY cnt DESC
        LIMIT 10
        """
    )
    for row in rows:
        flag = str(row.get("flag") or "")
        row["code"] = _dq_code(flag)
        row["description"] = _dq_description(flag)
    return rows


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_validation_summary() -> list[dict[str, Any]]:
    rows = _query(
        """
        SELECT
            check_id,
            COUNT(*) FILTER (WHERE severity = 'REJECT')::bigint AS reject_count,
            COUNT(*) FILTER (WHERE severity = 'WARN')::bigint AS warn_count,
            COUNT(*)::bigint AS total_count
        FROM validation_log
        GROUP BY check_id
        ORDER BY check_id
        """
    )
    for row in rows:
        metadata = VALIDATION_CHECKS.get(str(row.get("check_id")), {})
        row["description"] = metadata.get("description", "미등록 검사")
    return rows


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


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_stock_browser_catalog(search: str = "", limit: int = 300) -> list[dict[str, Any]]:
    pattern = f"%{search.strip()}%" if search.strip() else "%"
    return _query(
        """
        WITH price AS (
            SELECT ticker, COUNT(*)::bigint AS price_rows, MAX(date) AS latest_price_date
            FROM price_history
            GROUP BY ticker
        ),
        market_cap AS (
            SELECT ticker, COUNT(*)::bigint AS market_cap_rows, MAX(date) AS latest_market_cap_date
            FROM market_cap_history
            GROUP BY ticker
        ),
        fin AS (
            SELECT ticker, COUNT(*)::bigint AS financial_rows, MAX(year) AS latest_financial_year
            FROM financials
            GROUP BY ticker
        ),
        pit AS (
            SELECT ticker, COUNT(*)::bigint AS pit_rows, MAX(available_from) AS latest_pit_available_from
            FROM financials_pit
            GROUP BY ticker
        ),
        disc AS (
            SELECT ticker, COUNT(*)::bigint AS disclosure_rows, MAX(rcept_dt) AS latest_disclosure_date
            FROM disclosures
            GROUP BY ticker
        )
        SELECT
            s.ticker,
            s.corp_name,
            s.market,
            s.sector_name,
            s.is_excluded,
            COALESCE(p.price_rows, 0)::bigint AS price_rows,
            p.latest_price_date,
            COALESCE(m.market_cap_rows, 0)::bigint AS market_cap_rows,
            m.latest_market_cap_date,
            COALESCE(f.financial_rows, 0)::bigint AS financial_rows,
            f.latest_financial_year,
            COALESCE(pit.pit_rows, 0)::bigint AS pit_rows,
            pit.latest_pit_available_from,
            COALESCE(d.disclosure_rows, 0)::bigint AS disclosure_rows,
            d.latest_disclosure_date,
            COALESCE(i.status, 'missing') AS ingest_status,
            i.last_attempt AS ingest_last_attempt
        FROM stocks s
        LEFT JOIN price p ON p.ticker = s.ticker
        LEFT JOIN market_cap m ON m.ticker = s.ticker
        LEFT JOIN fin f ON f.ticker = s.ticker
        LEFT JOIN pit ON pit.ticker = s.ticker
        LEFT JOIN disc d ON d.ticker = s.ticker
        LEFT JOIN ingest_status i ON i.ticker = s.ticker
        WHERE s.ticker ILIKE %s
           OR s.corp_name ILIKE %s
           OR COALESCE(s.corp_code, '') ILIKE %s
        ORDER BY s.is_excluded, s.market NULLS LAST, s.ticker
        LIMIT %s
        """,
        (pattern, pattern, pattern, int(limit)),
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_stock_browser_overview(ticker: str) -> list[dict[str, Any]]:
    return _query(
        """
        SELECT
            s.ticker,
            s.corp_name,
            s.corp_code,
            s.market,
            s.sector,
            s.sector_name,
            s.is_financial,
            s.is_excluded,
            s.exclude_reason,
            s.listed_date,
            i.status AS ingest_status,
            i.last_attempt,
            i.call_count,
            i.error_msg
        FROM stocks s
        LEFT JOIN ingest_status i ON i.ticker = s.ticker
        WHERE s.ticker = %s
        """,
        (ticker,),
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_stock_browser_price_history(ticker: str, limit: int = 250) -> list[dict[str, Any]]:
    return _query(
        """
        SELECT date, open, high, low, close, adj_close, volume, turnover, is_suspended
        FROM price_history
        WHERE ticker = %s
        ORDER BY date DESC
        LIMIT %s
        """,
        (ticker, int(limit)),
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_stock_browser_market_cap_history(ticker: str, limit: int = 250) -> list[dict[str, Any]]:
    return _query(
        """
        SELECT date, market_cap, shares, source
        FROM market_cap_history
        WHERE ticker = %s
        ORDER BY date DESC
        LIMIT %s
        """,
        (ticker, int(limit)),
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_stock_browser_financials(ticker: str, limit: int = 500) -> list[dict[str, Any]]:
    return _query(
        """
        SELECT year, report_type, fs_div, account_nm, amount
        FROM financials
        WHERE ticker = %s
        ORDER BY year DESC, report_type, fs_div, account_nm
        LIMIT %s
        """,
        (ticker, int(limit)),
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_stock_browser_financials_pit(ticker: str, limit: int = 500) -> list[dict[str, Any]]:
    return _query(
        """
        SELECT year, report_type, fs_div, account_nm, amount, available_from, source_rcept_no, fallback_used
        FROM financials_pit
        WHERE ticker = %s
        ORDER BY available_from DESC, year DESC, report_type, fs_div, account_nm
        LIMIT %s
        """,
        (ticker, int(limit)),
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_stock_browser_disclosures(ticker: str, limit: int = 200) -> list[dict[str, Any]]:
    return _query(
        """
        SELECT rcept_no, rcept_dt, report_nm, report_type, year
        FROM disclosures
        WHERE ticker = %s
        ORDER BY rcept_dt DESC NULLS LAST, rcept_no DESC
        LIMIT %s
        """,
        (ticker, int(limit)),
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_stock_browser_quality(ticker: str, limit: int = 200) -> list[dict[str, Any]]:
    return _query(
        """
        SELECT 'validation_log' AS source, year, report_type, check_id AS item, severity AS status,
               message AS detail, evaluated_at
        FROM validation_log
        WHERE ticker = %s
        UNION ALL
        SELECT 'universe_gate_pit' AS source, year, report_type, NULL AS item, status,
               concat('reject_reasons=', reject_reasons::text, '; flags=', flags::text) AS detail,
               evaluated_at
        FROM universe_gate_pit
        WHERE ticker = %s
        ORDER BY evaluated_at DESC NULLS LAST, year DESC, report_type
        LIMIT %s
        """,
        (ticker, ticker, int(limit)),
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_stock_browser_listing_events(ticker: str, limit: int = 100) -> list[dict[str, Any]]:
    return _query(
        """
        SELECT event_type, corp_code, corp_name, market, listed_date, delisted_date, source, source_note
        FROM stock_listing_events
        WHERE ticker = %s
        ORDER BY COALESCE(listed_date, delisted_date) DESC NULLS LAST, id DESC
        LIMIT %s
        """,
        (ticker, int(limit)),
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_stock_browser_rim_input_status(ticker: str, limit: int = 200) -> list[dict[str, Any]]:
    return _query(
        """
        SELECT year, report_type, field_name, status, note, evaluated_at
        FROM rim_input_status
        WHERE ticker = %s
        ORDER BY evaluated_at DESC NULLS LAST, year DESC, report_type, field_name
        LIMIT %s
        """,
        (ticker, int(limit)),
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_db_browser_tables() -> list[dict[str, Any]]:
    return _query(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
          AND table_name = ANY(%s)
        ORDER BY table_name
        """,
        (sorted(DB_BROWSER_TABLES),),
    )


@st.cache_data(ttl=CACHE_TTL_SEC)
def get_db_browser_table_preview(table_name: str, limit: int = 200) -> list[dict[str, Any]]:
    table = _table_name(table_name)
    return _query(f"SELECT * FROM {table} LIMIT %s", (int(limit),))
