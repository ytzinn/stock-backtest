"""
SPEC_08 §9 — tilt.py 테스트. DB 접속 없이 검증 가능한 로직만 다룬다
(next_trading_day는 monkeypatch로 대체).
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from backtest.regime import tilt


def test_expanding_z_never_uses_future_data():
    """★ 핵심 회귀 — t 시점 z가 t 이후 값에 의존하면 안 된다(룩어헤드 금지, R4)."""
    dates = pd.date_range('2020-01-31', periods=48, freq='ME')
    values = pd.Series(np.linspace(0, 1, 48), index=dates)

    z_before = tilt.expanding_z(values, warmup_m=6, z_cap=2.0)

    # values[40:] 이후를 극단값으로 바꿔도 t<=39 시점의 z는 변하지 않아야 한다
    values_altered = values.copy()
    values_altered.iloc[40:] = 999.0
    z_after = tilt.expanding_z(values_altered, warmup_m=6, z_cap=2.0)

    pd.testing.assert_series_equal(z_before.iloc[:40], z_after.iloc[:40])


def test_expanding_z_inactive_during_warmup():
    dates = pd.date_range('2020-01-31', periods=12, freq='ME')
    values = pd.Series(np.arange(12, dtype=float), index=dates)
    z = tilt.expanding_z(values, warmup_m=6, z_cap=2.0)
    assert z.iloc[:6].isna().all()
    assert z.iloc[6:].notna().all()


def test_expanding_z_is_clamped_to_z_cap():
    dates = pd.date_range('2020-01-31', periods=20, freq='ME')
    values = pd.Series([1.0] * 10 + [1000.0] * 10, index=dates)   # 급격한 이상치
    z = tilt.expanding_z(values, warmup_m=3, z_cap=2.0)
    assert z.dropna().between(-2.0, 2.0).all()


def test_rolling_pct_z_never_uses_future_data():
    dates = pd.date_range('2020-01-31', periods=80, freq='ME')
    values = pd.Series(np.linspace(0, 1, 80), index=dates)

    z_before = tilt.rolling_pct_z(values, window_m=60, z_cap=2.0)
    values_altered = values.copy()
    values_altered.iloc[70:] = -999.0
    z_after = tilt.rolling_pct_z(values_altered, window_m=60, z_cap=2.0)

    pd.testing.assert_series_equal(z_before.iloc[:70], z_after.iloc[:70])


def test_signal_execution_dates_lag_after_signal(monkeypatch):
    """signal_date < execution_date — 같은 종가로 신호→체결하는 룩어헤드 금지(§3-1)."""
    fake_next = {
        date(2020, 4, 30): date(2020, 5, 4),
        date(2020, 5, 29): date(2020, 6, 1),
    }
    monkeypatch.setattr(tilt, 'next_trading_day', lambda conn, d: fake_next[d])

    pairs = tilt.signal_execution_dates(conn=None, signal_dates=list(fake_next.keys()))

    assert pairs == [(date(2020, 4, 30), date(2020, 5, 4)), (date(2020, 5, 29), date(2020, 6, 1))]
    assert all(sig < exe for sig, exe in pairs)


def test_share_from_z_is_bounded_and_smax_capped_at_one():
    """R1·R7: s_t는 [S_MIN,S_MAX] 유계, S_MAX<=1.0(레버리지 금지)."""
    s = tilt.share_from_z(z_t=100.0, s_neutral=1.0, k=0.15, s_min=0.5, s_max=1.0)
    assert s == 1.0
    s = tilt.share_from_z(z_t=-100.0, s_neutral=1.0, k=0.15, s_min=0.5, s_max=1.0)
    assert s == 0.5


def test_share_from_z_effective_floor_matches_1_minus_k_times_zcap():
    """§3-3: K별 실효 하한(옵션 A) = 1 − K·Z_CAP. K=0.075/0.15/0.25 → 0.85/0.70/0.50."""
    z_cap = 2.0
    for k, expected_floor in [(0.075, 0.85), (0.15, 0.70), (0.25, 0.50)]:
        s = tilt.share_from_z(z_t=-z_cap, s_neutral=1.0, k=k, s_min=0.5, s_max=1.0)
        assert s == pytest.approx(expected_floor, abs=1e-9)


def test_share_from_z_returns_neutral_when_z_is_none_or_nan():
    assert tilt.share_from_z(None, s_neutral=1.0, k=0.15, s_min=0.5, s_max=1.0) == 1.0
    assert tilt.share_from_z(float('nan'), s_neutral=0.8, k=0.15, s_min=0.5, s_max=1.0) == 0.8


def test_effective_k_forces_conservative_in_conservative_mode():
    """R2: WALKFORWARD 미통과 보수 모드에서는 K가 무조건 CONSERVATIVE_K."""
    assert tilt.effective_k('tilt_conservative', k_requested=0.25, conservative_k=0.075) == 0.075
    assert tilt.effective_k('tilt', k_requested=0.25, conservative_k=0.075) == 0.25


def test_effective_tilt_option_disables_option_b_in_conservative_mode():
    """R2: 보수 모드에서 옵션 B(양방향) 비활성 — A(방어형)로 강제."""
    assert tilt.effective_tilt_option('tilt_conservative', 'B_two_sided') == 'A_defensive'
    assert tilt.effective_tilt_option('tilt_conservative', 'A_defensive') == 'A_defensive'
    assert tilt.effective_tilt_option('tilt', 'B_two_sided') == 'B_two_sided'
