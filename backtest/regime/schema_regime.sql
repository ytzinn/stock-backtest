-- SPEC_07 §6 — 레짐 진단 전용 신규 테이블. 기존 스키마 불변.
-- 적용: venv/bin/python -m backtest.regime.schema_regime (psycopg2 스크립트로 실행 — 서버 PATH에 psql 없음)

CREATE TABLE IF NOT EXISTS regime_indicators (
    run_id       TEXT,               -- 지표 계산 run 식별자
    config_hash  TEXT,               -- config_regime.py 파라미터 해시 (민감도 구분)
    date         DATE,
    indicator    TEXT,               -- value_spread | size_val_gap | illiq_discount
                                      --  | size_mom_6m | breadth_ma200 | mega_cap_concentration
    value        DOUBLE PRECISION,
    universe_n   INTEGER,
    dropped_pct  DOUBLE PRECISION,   -- 결측 제외 비율 (생존편향 로그)
    PRIMARY KEY (run_id, date, indicator)
);

CREATE TABLE IF NOT EXISTS strategy_returns_monthly (
    source_run_id      TEXT,          -- MTM run 식별자
    holdings_source    TEXT,          -- 홀딩스 출처(JSON 경로)
    delisting_scenario TEXT,          -- 'base_70pct' 등 (기존 백테스트 청산 가정)
    largecap_rule      TEXT,          -- 'top_decile' 등
    scenario           TEXT,          -- D_rim_only 등
    period_start       DATE,
    period_end         DATE,          -- 실제 next_rebalance_date (월말 아님). 마지막 구간은 열린 stub
    is_closed_period   BOOLEAN,       -- FALSE면 진행 중인 구간(#23) — 게이트 판정 제외, 참고 표시만
    return_start       DATE,          -- 이번 관측치 수익 시작
    return_end         DATE,          -- 이번 관측치 수익 종료 (월말 또는 period_end stub)
    date               DATE,          -- return_end 와 동일(정렬 편의)
    port_return        DOUBLE PRECISION,
    largecap_cw_return DOUBLE PRECISION,   -- 주 벤치마크
    largecap_ew_return DOUBLE PRECISION,
    kospi_return       DOUBLE PRECISION,
    rel_vs_large       DOUBLE PRECISION,   -- port - largecap_cw (핵심)
    rel_vs_large_ew    DOUBLE PRECISION,
    rel_vs_kospi       DOUBLE PRECISION,
    n_holdings         INTEGER,
    PRIMARY KEY (source_run_id, scenario, date)
);

CREATE INDEX IF NOT EXISTS idx_regime_indicators_date ON regime_indicators (date);
CREATE INDEX IF NOT EXISTS idx_strategy_returns_monthly_date ON strategy_returns_monthly (date);
