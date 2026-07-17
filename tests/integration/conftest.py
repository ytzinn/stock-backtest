"""
AUDIT Pass 0C 통합 테스트 공용 fixture — 합성 PostgreSQL (포트 5434+).

안전 가드:
  - 포트 5433(운영 backtest DB)·5432(stock-analysis DB) 접속을 하드 차단한다.
  - 실데이터 복사 금지 — 스키마는 ingest/schema.sql 부분집합 + v8 마이그레이션 컬럼을
    여기서 직접 생성하고, 데이터는 각 테스트가 손으로 만든다.
  - DB 미가용 시 실패가 아니라 skip.
"""
from __future__ import annotations

import os

import psycopg2
import pytest

FORBIDDEN_PORTS = {5432, 5433}   # 5433=운영 backtest DB, 5432=stock-analysis 전용

DEFAULT_DSN = 'host=127.0.0.1 port=5434 dbname=postgres user=postgres password=audit'

# ingest/schema.sql 부분집합 (백테스트가 읽는 테이블만) + v8_xbrl_original 컬럼 반영
SCHEMA_DDL = """
DROP TABLE IF EXISTS financials, financials_pit, disclosures, universe_gate_pit,
                     price_history, market_cap_history, stock_listing_events, stocks CASCADE;

CREATE TABLE stocks (
    ticker          TEXT PRIMARY KEY,
    corp_name       TEXT NOT NULL,
    is_excluded     BOOLEAN DEFAULT FALSE,
    listed_date     DATE
);

CREATE TABLE stock_listing_events (
    id            SERIAL PRIMARY KEY,
    ticker        TEXT NOT NULL,
    listed_date   DATE,
    delisted_date DATE,
    event_type    TEXT NOT NULL
);

CREATE TABLE financials_pit (
    id              SERIAL PRIMARY KEY,
    ticker          TEXT NOT NULL REFERENCES stocks(ticker),
    corp_code       TEXT NOT NULL DEFAULT '00000000',
    year            INTEGER NOT NULL,
    report_type     TEXT NOT NULL,
    fs_div          TEXT NOT NULL,
    account_nm      TEXT NOT NULL,
    amount          NUMERIC,
    available_from  DATE NOT NULL,
    source_rcept_no TEXT,
    fallback_used   BOOLEAN NOT NULL DEFAULT FALSE,
    original_amount NUMERIC,          -- v8_xbrl_original
    amendment_from  DATE,             -- v8_xbrl_original
    UNIQUE (ticker, year, report_type, fs_div, account_nm)
);

CREATE TABLE disclosures (
    rcept_no     TEXT PRIMARY KEY,
    ticker       TEXT NOT NULL REFERENCES stocks(ticker),
    rcept_dt     DATE,
    report_nm    TEXT,
    report_type  TEXT,
    year         INTEGER,
    is_amendment BOOLEAN DEFAULT FALSE   -- v8_xbrl_original
);

CREATE TABLE price_history (
    ticker        TEXT NOT NULL,
    date          DATE NOT NULL,
    close         NUMERIC,
    adj_close     NUMERIC,
    turnover      NUMERIC,
    is_suspended  BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE market_cap_history (
    ticker      TEXT NOT NULL,
    date        DATE NOT NULL,
    market_cap  NUMERIC,
    shares      BIGINT,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE universe_gate_pit (
    ticker         TEXT NOT NULL,
    year           INTEGER NOT NULL,
    report_type    TEXT NOT NULL,
    status         TEXT NOT NULL,
    status_amended TEXT,               -- CORR-GATE-003: 정정 반영값 기준 판정
    amendment_from DATE,               -- 게이트 계정 최초 정정 공시일 (NULL=정정 없음)
    reject_reasons JSONB DEFAULT '[]',
    flags          JSONB DEFAULT '[]',
    evaluated_at   TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (ticker, year, report_type)
);

CREATE TABLE financials (
    id            SERIAL PRIMARY KEY,
    ticker        TEXT NOT NULL REFERENCES stocks(ticker),
    corp_code     TEXT NOT NULL DEFAULT '00000000',
    year          INTEGER NOT NULL,
    report_type   TEXT NOT NULL,
    fs_div        TEXT NOT NULL,
    account_nm    TEXT NOT NULL,
    amount        NUMERIC,
    frmtrm_amount NUMERIC,
    original_amount NUMERIC,          -- v8_xbrl_original
    UNIQUE (ticker, year, report_type, fs_div, account_nm)
);
"""

DATA_TABLES = [
    'financials', 'financials_pit', 'disclosures', 'universe_gate_pit',
    'price_history', 'market_cap_history', 'stock_listing_events', 'stocks',
]


def _dsn() -> str:
    return os.getenv('AUDIT_PG_DSN', DEFAULT_DSN)


def _assert_port_allowed(dsn: str) -> None:
    for token in dsn.split():
        if token.startswith('port='):
            port = int(token.split('=', 1)[1])
            if port in FORBIDDEN_PORTS:
                pytest.exit(
                    f'포트 {port}는 운영/타 프로젝트 DB다 — 통합 테스트 접속 금지 '
                    f'(AUDIT MODE 규칙). 포트 5434 이상의 임시 컨테이너를 써라.',
                    returncode=3,
                )
            return
    pytest.exit('AUDIT_PG_DSN에 port= 가 명시돼야 한다 (포트 검증 불가).', returncode=3)


@pytest.fixture(scope='session')
def pg_conn():
    dsn = _dsn()
    _assert_port_allowed(dsn)
    try:
        conn = psycopg2.connect(dsn, connect_timeout=3)
    except psycopg2.OperationalError as e:
        pytest.skip(f'합성 PostgreSQL(5434+) 미가용 — tests/integration/README.md 참조: {e}')
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(SCHEMA_DDL)
    yield conn
    conn.close()


@pytest.fixture()
def conn(pg_conn):
    """테스트마다 데이터 초기화된 커넥션."""
    with pg_conn.cursor() as cur:
        cur.execute('TRUNCATE ' + ', '.join(DATA_TABLES) + ' RESTART IDENTITY CASCADE')
    return pg_conn


@pytest.fixture()
def make_stock(conn):
    def _make(ticker: str, is_excluded: bool = False):
        with conn.cursor() as cur:
            cur.execute(
                'INSERT INTO stocks (ticker, corp_name, is_excluded) VALUES (%s, %s, %s) '
                'ON CONFLICT (ticker) DO NOTHING',
                (ticker, f'합성{ticker}', is_excluded),
            )
    return _make
