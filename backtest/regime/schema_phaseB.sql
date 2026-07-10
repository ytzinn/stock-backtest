-- SPEC_08 §4 — Phase B(Signal → Tilt) 전용 신규 테이블. Phase A 스키마 불변.
-- 적용: venv/bin/python -m backtest.regime.schema_phaseB (psycopg2 스크립트로 실행 — 서버 PATH에 psql 없음)

CREATE TABLE IF NOT EXISTS overlay_returns (
    run_id           TEXT,
    config_hash      TEXT,
    scenario         TEXT,     -- D_rim_only | F_momentum_rim (PRIMARY만)
    variant          TEXT,     -- 'D_v1'(vs단독) | 'D_v2'(vs+size_mom) | 'F_v1'(vs단독)
    tilt_option      TEXT,     -- 'A_defensive' | 'B_two_sided'
    mode             TEXT,     -- 'always_on' | 'tilt' | 'tilt_conservative'
    normalization    TEXT,     -- 'expanding_z' | 'rolling_pct_60m'
    overlay_freq     TEXT,     -- 'monthly' | 'quarterly' | 'semiannual'
    alt_sleeve       TEXT,     -- 'largecap_cw' | 'kospi'
    signal_date      DATE,
    execution_date   DATE,     -- signal_date != execution_date (lag, §3-1)
    period_start     DATE,
    period_end       DATE,
    date             DATE,
    s_t              DOUBLE PRECISION,
    z_t              DOUBLE PRECISION,
    size_mom_z       DOUBLE PRECISION,   -- D_v2 실험용
    port_return      DOUBLE PRECISION,   -- overlay 적용(gross)
    base_return      DOUBLE PRECISION,   -- always-on(비교군)
    alt_return       DOUBLE PRECISION,
    overlay_turnover DOUBLE PRECISION,   -- |Δs_t| (sleeve 이동)
    overlay_cost     DOUBLE PRECISION,   -- 2*|Δs_t|*leg_bps (비대칭)
    net_port_return  DOUBLE PRECISION,   -- 비용 차감
    net_base_return  DOUBLE PRECISION,
    is_oos           BOOLEAN,
    episode_tag      TEXT,               -- 'normal' | 'period22' | 'live_forward'
    -- SPEC_08 원안의 PK(run_id, scenario, variant, tilt_option, mode, date)는 grid.py가
    -- normalization/overlay_freq/alt_sleeve/K를 바꿔가며 도는 조합들을 구분하지 못해
    -- 서로 다른 조합이 같은 행을 조용히 덮어쓸 수 있었다(Phase A의 config_hash 도입 취지와
    -- 동일한 함정). config_hash가 조합축(K·normalization·overlay_freq·alt_sleeve)까지
    -- 포함해 계산되도록 하고 PK에 추가해 막는다(config_phaseB.py::config_hash 참고).
    PRIMARY KEY (run_id, config_hash, scenario, variant, tilt_option, mode, date)
);

CREATE INDEX IF NOT EXISTS idx_overlay_returns_date ON overlay_returns (date);
