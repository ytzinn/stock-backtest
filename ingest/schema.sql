-- schema version: v1.0 (v4.8 설계서 기준)
-- 적용: psql -h localhost -p 5433 -U postgres -d backtest -f ingest/schema.sql

-- 1. 종목 마스터 (현재 상장 + 상장폐지 포함)
CREATE TABLE IF NOT EXISTS stocks (
    ticker          TEXT        PRIMARY KEY,
    corp_name       TEXT        NOT NULL,
    corp_code       TEXT,
    market          TEXT,
    sector          TEXT,
    sector_name     TEXT,
    fscl_month      INTEGER,    -- 결산월 (1~12, NULL=미확인). DART company.json fscl_month
    is_financial    BOOLEAN     DEFAULT FALSE,
    is_excluded     BOOLEAN     DEFAULT FALSE,
    exclude_reason  TEXT,
    listed_date     DATE,
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- 2. 상장 이벤트 이력 (생존편향 해소 + 시점별 유니버스 복원)
-- stock_listing_history(ticker PK 단일행) 폐기 → 이벤트 이력 구조로 교체.
CREATE TABLE IF NOT EXISTS stock_listing_events (
    id            SERIAL      PRIMARY KEY,
    ticker        TEXT        NOT NULL,
    corp_code     TEXT,
    corp_name     TEXT,
    market        TEXT,
    listed_date   DATE,
    delisted_date DATE,
    event_type    TEXT        NOT NULL,
    -- 'listed' | 'delisted' | 'market_transfer' | 'spac_merge' | 'split' | 'merger'
    source        TEXT,       -- 'fdr' | 'pykrx' | 'dart_manual'
    source_note   TEXT
);

CREATE INDEX IF NOT EXISTS idx_listing_events_ticker
    ON stock_listing_events (ticker);
CREATE INDEX IF NOT EXISTS idx_listing_events_listed_date
    ON stock_listing_events (listed_date);

-- 3. 재무제표 원시 수치 (DART → 계정명 표준화 후 저장)
CREATE TABLE IF NOT EXISTS financials (
    id            SERIAL  PRIMARY KEY,
    ticker        TEXT    NOT NULL REFERENCES stocks(ticker),
    corp_code     TEXT    NOT NULL,
    year          INTEGER NOT NULL,
    report_type   TEXT    NOT NULL,   -- 'FY' | 'H1' | 'Q1' | 'Q3'
    fs_div        TEXT    NOT NULL,   -- 'CFS' | 'OFS'
    account_nm    TEXT    NOT NULL,   -- 표준화된 계정명
    amount        NUMERIC,            -- 당기(thstrm)
    frmtrm_amount NUMERIC,            -- 전기(frmtrm) — 시계열 교차검증용
    UNIQUE (ticker, year, report_type, fs_div, account_nm)
);

CREATE INDEX IF NOT EXISTS idx_financials_ticker_year
    ON financials (ticker, year, report_type);

-- 4. Point-in-Time 재무 데이터
CREATE TABLE IF NOT EXISTS financials_pit (
    id              SERIAL  PRIMARY KEY,
    ticker          TEXT    NOT NULL REFERENCES stocks(ticker),
    corp_code       TEXT    NOT NULL,
    year            INTEGER NOT NULL,
    report_type     TEXT    NOT NULL,
    fs_div          TEXT    NOT NULL,
    account_nm      TEXT    NOT NULL,
    amount          NUMERIC,
    available_from  DATE    NOT NULL,
    source_rcept_no TEXT,
    fallback_used   BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (ticker, year, report_type, fs_div, account_nm)
);

CREATE INDEX IF NOT EXISTS idx_financials_pit_avail_ticker
    ON financials_pit (available_from, ticker);
CREATE INDEX IF NOT EXISTS idx_financials_pit_ticker_year
    ON financials_pit (ticker, year, report_type);

-- 5. 공시 목록 (available_from 결정용)
CREATE TABLE IF NOT EXISTS disclosures (
    rcept_no    TEXT    PRIMARY KEY,
    ticker      TEXT    NOT NULL REFERENCES stocks(ticker),
    rcept_dt    DATE,
    report_nm   TEXT,
    report_type TEXT,
    year        INTEGER
);

CREATE INDEX IF NOT EXISTS idx_disclosures_ticker_year
    ON disclosures (ticker, year, report_type);

-- 6. 일별 주가 OHLCV + 수정주가
CREATE TABLE IF NOT EXISTS price_history (
    ticker        TEXT    NOT NULL,
    date          DATE    NOT NULL,
    open          NUMERIC,
    high          NUMERIC,
    low           NUMERIC,
    close         NUMERIC,
    adj_close     NUMERIC,
    volume        BIGINT,
    turnover      NUMERIC,
    is_suspended  BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_price_history_ticker_date
    ON price_history (ticker, date);
CREATE INDEX IF NOT EXISTS idx_price_history_date
    ON price_history (date);

-- 7. 일별 시가총액·상장주식수
CREATE TABLE IF NOT EXISTS market_cap_history (
    ticker      TEXT    NOT NULL,
    date        DATE    NOT NULL,
    market_cap  NUMERIC,
    shares      BIGINT,
    source      TEXT DEFAULT 'pykrx',
    PRIMARY KEY (ticker, date)
);

-- 8. DQ Gate 판정 결과 — 시점별
CREATE TABLE IF NOT EXISTS universe_gate_pit (
    ticker         TEXT    NOT NULL,
    year           INTEGER NOT NULL,
    report_type    TEXT    NOT NULL,
    status         TEXT    NOT NULL,  -- 'PASS' | 'REJECT'
    reject_reasons JSONB   DEFAULT '[]',
    flags          JSONB   DEFAULT '[]',
    evaluated_at   TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (ticker, year, report_type)
);

CREATE INDEX IF NOT EXISTS idx_universe_gate_pit_ticker_year
    ON universe_gate_pit (ticker, year, report_type);

-- 9. 수집 상태 추적
CREATE TABLE IF NOT EXISTS ingest_status (
    ticker       TEXT        PRIMARY KEY,
    status       TEXT        NOT NULL DEFAULT 'pending',
    last_attempt TIMESTAMPTZ,
    error_msg    TEXT,
    call_count   INTEGER     DEFAULT 0
);

-- 10. 백테스트 실험 실행 로그
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id            SERIAL      PRIMARY KEY,
    run_name          TEXT,
    phase             TEXT,
    params            JSONB,
    fitness           NUMERIC,
    metrics           JSONB,
    ablation_tag      TEXT,
    git_commit        TEXT,
    param_hash        TEXT,
    data_cutoff_date  DATE,
    db_schema_version TEXT,
    started_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ,
    status            TEXT,
    error_msg         TEXT,
    created_at        TIMESTAMPTZ DEFAULT now()
);

-- 11. Reasoning Log (XAI)
CREATE TABLE IF NOT EXISTS reasoning_log (
    id          SERIAL  PRIMARY KEY,
    run_id      INTEGER REFERENCES backtest_runs(run_id),
    change_desc TEXT,
    reason      TEXT,
    confidence  TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- 12. 분류 이력 추적
CREATE TABLE IF NOT EXISTS classification_history (
    id             SERIAL  PRIMARY KEY,
    ticker         TEXT    NOT NULL REFERENCES stocks(ticker),
    rebalance_date DATE    NOT NULL,
    prev_type      TEXT,
    curr_type      TEXT    NOT NULL,
    changed        BOOLEAN NOT NULL DEFAULT FALSE,
    change_reason  TEXT,
    soft_label     JSONB,
    UNIQUE (ticker, rebalance_date)
);

-- 13. 재무 이상치 검사 로그 (validator.py → V01~V09)
CREATE TABLE IF NOT EXISTS validation_log (
    id           SERIAL      PRIMARY KEY,
    ticker       TEXT        NOT NULL,
    year         INTEGER     NOT NULL,
    report_type  TEXT        NOT NULL,
    check_id     TEXT        NOT NULL,  -- 'V01', 'V02', ...
    severity     TEXT        NOT NULL,  -- 'REJECT' | 'WARN'
    message      TEXT,
    evaluated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (ticker, year, report_type, check_id)
);
CREATE INDEX IF NOT EXISTS idx_validation_log_ticker
    ON validation_log (ticker, year, report_type);

-- 14. RIM 입력값 상태 메타 (Phase 2 — dividend_status 3분류 등)
CREATE TABLE IF NOT EXISTS rim_input_status (
    ticker       TEXT    NOT NULL,
    year         INTEGER NOT NULL,
    report_type  TEXT    NOT NULL,
    field_name   TEXT    NOT NULL,  -- 'dividend', 'cfo', ...
    status       TEXT    NOT NULL,  -- 'missing' | 'reported_positive' | 'confirmed_zero'
    note         TEXT,
    evaluated_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (ticker, year, report_type, field_name)
);

-- 15. KRX 리밸런싱 시점 상장 스냅샷
-- backtest.configs.rebalance_dates.REBALANCE_DATES 기준 KOSPI+KOSDAQ 종목 스냅샷
-- 수집: python -m ingest.krx_listing_ingest
CREATE TABLE IF NOT EXISTS krx_listing_snapshots (
    snapshot_date DATE    NOT NULL,
    ticker        CHAR(6) NOT NULL,
    company_name  VARCHAR(200),
    market        VARCHAR(20),   -- 'KOSPI' | 'KOSDAQ'
    shares        BIGINT,
    close_price   INTEGER,
    PRIMARY KEY (snapshot_date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_krx_listing_snapshots_ticker
    ON krx_listing_snapshots (ticker);
