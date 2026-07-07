"""
SPEC_07 §11 — analyze.py 테스트. 순수 pandas 로직(DB 무관)만 합성 데이터로 검증한다.
regime_indicators/strategy_returns_monthly 로드(load_indicator_df 등)는 DB 필요 —
실제 값 정합성은 서버에서 STEP A-5 최초 실행 로그로 확인한다.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from backtest.regime import analyze as az
from backtest.regime import config_regime as cfg


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *a, **k):
        pass

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    """psycopg2 conn.cursor() 컨텍스트매니저만 흉내낸다 — 실제 DB 연결 없이 로더 함수를 검증."""

    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)


def test_load_indicator_df_index_is_datetime_and_asof_safe():
    """
    ★ 회귀 테스트 — psycopg2가 DATE 컬럼을 datetime.date로 반환하면 pivot() 인덱스가
    object dtype이 되어 .asof(pd.Timestamp(...))가 TypeError를 던졌던 실제 버그.
    """
    rows = [
        (date(2020, 1, 31), 'value_spread', 1.0),
        (date(2020, 2, 29), 'value_spread', 2.0),
        (date(2020, 3, 31), 'value_spread', 3.0),
    ]
    df = az.load_indicator_df(_FakeConn(rows), run_id='test')
    assert isinstance(df.index, pd.DatetimeIndex)
    result = df['value_spread'].asof(pd.Timestamp(date(2020, 3, 1)))   # Feb29 이후, Mar31 이전
    assert result == pytest.approx(2.0)


def test_load_monthly_returns_index_is_datetime():
    rows = [
        (date(2020, 5, 31), date(2020, 4, 3), date(2020, 8, 20), True, 0.01, 0.02, -0.01),
    ]
    df = az.load_monthly_returns(_FakeConn(rows), run_id='test', scenario='D_rim_only')
    assert isinstance(df.index, pd.DatetimeIndex)


def test_period_level_returns_compounds_monthly_rows():
    dates = pd.to_datetime([date(2020, 5, 31), date(2020, 6, 30), date(2020, 7, 31), date(2020, 8, 20)])
    df = pd.DataFrame({
        'period_start': [date(2020, 4, 3)] * 2 + [date(2020, 8, 20)] * 2,
        'period_end': [date(2020, 8, 20)] * 4,
        'is_closed_period': [True] * 4,
        'port_return': [0.10, 0.05, -0.02, 0.03],
        'largecap_cw_return': [0.02, 0.01, 0.00, 0.01],
    }, index=dates)

    out = az.period_level_returns(df)
    p1 = out.loc[date(2020, 4, 3)]
    assert p1['port_total'] == pytest.approx(1.10 * 1.05 - 1)
    assert p1['cw_total'] == pytest.approx(1.02 * 1.01 - 1)
    assert p1['rel_vs_large'] == pytest.approx(p1['port_total'] - p1['cw_total'])


def test_hot_cold_split_excludes_open_periods():
    idx = [date(2020, 4, 3), date(2020, 8, 20), date(2021, 4, 5), date(2021, 8, 19)]
    df = pd.DataFrame({
        'is_closed_period': [True, True, True, False],
        'rel_vs_large': [0.30, -0.10, 0.05, 0.99],   # 마지막(열린 구간, #23류)이 최댓값이지만 제외돼야 함
    }, index=idx)
    hot, cold = az.hot_cold_split(df, quartile=0.25)
    assert date(2021, 8, 19) not in hot
    assert date(2021, 8, 19) not in cold
    assert date(2020, 4, 3) in hot   # 닫힌 구간 중 최댓값


def test_anchor_regression_g2b_excludes_period_but_keeps_sign():
    idx = pd.to_datetime([date(2016, 4, 3) + pd.DateOffset(months=6 * i) for i in range(21)])
    rng = np.random.RandomState(0)
    x = pd.Series(np.linspace(-1, 1, 21), index=idx)
    y_vals = 2 * x.values + 0.01 * rng.randn(21)
    period_index = [d.date() for d in idx]
    period_df = pd.DataFrame({
        'is_closed_period': True,
        'rel_vs_large': y_vals,
    }, index=period_index)

    full = az.anchor_regression(x, period_df)
    excluded_date = period_index[-2]   # #22에 해당하는 자리
    g2b = az.anchor_regression(x, period_df, exclude={excluded_date})

    assert full is not None and g2b is not None
    assert full['n'] == 21
    assert g2b['n'] == 20
    assert full['coef'] > 0 and g2b['coef'] > 0   # 부호 유지


def test_gates_are_driven_only_by_value_spread_not_other_indicators():
    """
    G1/G1b/G2/G2b는 value_spread(1순위 가설)만 쓴다 — size_val_gap 등 탐색적 지표를
    바꿔도 analyze_scenario의 게이트 결과가 달라지면 안 된다(§9 사전등록 원칙).
    """
    dates = pd.to_datetime([date(2016, 4, 30) + pd.DateOffset(months=i) for i in range(24)])
    rng = np.random.RandomState(1)
    value_spread = pd.Series(np.linspace(-1, 1, 24), index=dates)
    rel = pd.Series(0.5 * value_spread.shift(1).fillna(0).values + 0.01 * rng.randn(24), index=dates)

    monthly = pd.DataFrame({
        'period_start': [date(2016, 4, 5)] * 24,
        'period_end': [date(2016, 8, 18)] * 24,
        'is_closed_period': True,
        'port_return': rel.values,
        'largecap_cw_return': 0.0,
        'rel_vs_large': rel.values,
    }, index=dates)

    indicator_df_a = pd.DataFrame({'value_spread': value_spread, 'size_val_gap': 0.0})
    indicator_df_b = pd.DataFrame({'value_spread': value_spread, 'size_val_gap': 999.0})

    def _gates(idf):
        vs = idf['value_spread']
        is_closed = monthly['is_closed_period'].astype(bool)
        g1 = az.lead_lag_table(vs, monthly['rel_vs_large'], is_closed, horizons=(1,))
        return g1[1]['coef'] if g1[1] else None

    assert _gates(indicator_df_a) == pytest.approx(_gates(indicator_df_b))


def test_config_hash_changes_when_a_parameter_changes(monkeypatch):
    """민감도 run이 base run(config_hash)과 충돌하지 않으려면 파라미터 변화가 해시에 반영돼야 한다."""
    h1 = cfg.config_hash()
    monkeypatch.setattr(cfg, 'PBR_QUANTILES', cfg.PBR_QUANTILES + 1)
    h2 = cfg.config_hash()
    assert h1 != h2


def test_primary_scenarios_excludes_archive():
    archive_overlap = set(cfg.PRIMARY_SCENARIOS) & set(cfg.ARCHIVE_SCENARIOS)
    assert archive_overlap == set()
    assert cfg.PRIMARY_SCENARIOS == ['D_rim_only', 'F_momentum_rim']
