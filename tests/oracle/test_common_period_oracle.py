"""
공통 기간 절단 + paired block bootstrap 오라클 — SPEC_13 §9-2b / §9-3a.

절단·정규화 규약이 어긋나면 캘린더 간 비교가 통째로 무효가 되는데(각 전략이 서로 다른
구간을 보게 됨) 그건 조용히 일어난다. 손계산 가능한 합성 NAV로 고정한다. DB 미접속.
"""
from __future__ import annotations

import pandas as pd
import pytest

from backtest.metrics import compute_daily_metrics, compute_nav_cagr, slice_common_period
from scripts.robustness.robustness_lib import paired_block_bootstrap_cagr_diff


def _nav(dates: list[str], values: list[float]) -> pd.Series:
    return pd.Series(values, index=pd.to_datetime(dates))


# ── slice_common_period (§9-2b) ─────────────────────────────────────────────

def test_slice_keeps_only_common_window_inclusive():
    nav = _nav(['2016-01-04', '2016-05-18', '2020-06-01', '2026-04-03', '2026-05-20'],
               [1.0, 1.5, 2.0, 3.0, 3.3])
    out = slice_common_period(nav, '2016-05-18', '2026-04-03')
    assert out.index[0] == pd.Timestamp('2016-05-18')
    assert out.index[-1] == pd.Timestamp('2026-04-03')
    assert len(out) == 3


def test_cagr_after_slice_equals_normalized_definition():
    """§9-2b: NAV(S)=1.0 정규화 후 CAGR == (NAV(E)/NAV(S))^(1/years)−1.

    시리즈를 다시 쓰지 않고 initial_capital만 넘겨도 동일함을 고정 —
    이 등식이 깨지면 §9-2b의 "정규화만, 강제 매수 금지" 규약이 무너진다.
    """
    nav = _nav(['2016-01-04', '2016-05-18', '2026-04-03'], [1.0, 1.5, 3.0])
    sliced = slice_common_period(nav, '2016-05-18', '2026-04-03')

    got = compute_nav_cagr(sliced, initial_capital=float(sliced.iloc[0]))

    years = (pd.Timestamp('2026-04-03') - pd.Timestamp('2016-05-18')).days / 365.25
    expected = (3.0 / 1.5) ** (1 / years) - 1
    assert abs(got - expected) < 1e-15

    # 명시적으로 정규화한 시리즈와도 동일해야 한다
    normalized = sliced / float(sliced.iloc[0])
    assert abs(compute_nav_cagr(normalized, initial_capital=1.0) - got) < 1e-15


def test_daily_metrics_are_scale_invariant_so_slicing_suffices():
    """MDD·Sharpe는 비율 기반 — 정규화해도 값이 같아야 절단만으로 충분하다."""
    dates = pd.bdate_range('2020-01-01', periods=60).strftime('%Y-%m-%d').tolist()
    vals = [1.0 + 0.01 * ((i % 7) - 3) + 0.002 * i for i in range(60)]
    nav = _nav(dates, vals)
    sliced = slice_common_period(nav, dates[10], dates[-1])

    a = compute_daily_metrics(sliced)
    b = compute_daily_metrics(sliced / float(sliced.iloc[0]))
    assert abs(a['daily_mdd'] - b['daily_mdd']) < 1e-15
    assert abs(a['daily_sharpe'] - b['daily_sharpe']) < 1e-12


def test_slice_raises_when_start_not_an_observation():
    """조용히 근처 날짜로 대체하면 전략마다 다른 구간을 비교하게 된다 — 예외여야 한다."""
    nav = _nav(['2016-05-18', '2026-04-03'], [1.0, 2.0])
    with pytest.raises(ValueError, match='공통 시작일'):
        slice_common_period(nav, '2016-05-17', '2026-04-03')


# ── paired block bootstrap (§9-3a) ──────────────────────────────────────────

_A = [0.02, -0.01, 0.03, 0.00, 0.01, 0.02, -0.02, 0.015, 0.005, 0.01, -0.005, 0.02] * 3
_B = [0.01, -0.01, 0.02, 0.01, 0.00, 0.01, -0.01, 0.010, 0.000, 0.01, -0.010, 0.01] * 3


def test_bootstrap_is_deterministic_for_fixed_seed():
    """seed 고정이면 재현 가능해야 한다 — 사전등록 수치가 재현 불가하면 의미가 없다."""
    r1 = paired_block_bootstrap_cagr_diff(_A, _B, n_resamples=200, seed=13)
    r2 = paired_block_bootstrap_cagr_diff(_A, _B, n_resamples=200, seed=13)
    assert r1 == r2


def test_bootstrap_point_estimate_matches_direct_cagr_difference():
    import numpy as np
    r = paired_block_bootstrap_cagr_diff(_A, _B, n_resamples=50, seed=13)
    n = len(_A)
    ca = float(np.prod([1 + x for x in _A]) ** (12.0 / n) - 1)
    cb = float(np.prod([1 + x for x in _B]) ** (12.0 / n) - 1)
    assert abs(r['delta_cagr_point'] - (ca - cb)) < 1e-15


def test_bootstrap_reports_prereg_parameters():
    r = paired_block_bootstrap_cagr_diff(_A, _B, n_resamples=100)
    assert r['block_months'] == 12 and r['seed'] == 13 and r['n_months'] == len(_A)
    assert r['ci_low'] <= r['ci_high']
    assert 0.0 <= r['p_gt_0'] <= 1.0 and 0.0 <= r['p_gt_delta'] <= 1.0


def test_bootstrap_rejects_unpaired_lengths():
    """길이가 다르면 paired 전제가 깨진다 — 조용히 자르면 안 된다."""
    with pytest.raises(ValueError, match='월 수 불일치'):
        paired_block_bootstrap_cagr_diff(_A, _B[:-1], n_resamples=10)


def test_bootstrap_identical_series_gives_zero_difference():
    """같은 시리즈끼리는 어떤 리샘플에서도 차이가 0 — paired 추출이 깨지면 0이 안 나온다."""
    r = paired_block_bootstrap_cagr_diff(_A, _A, n_resamples=200, seed=13)
    assert abs(r['delta_cagr_point']) < 1e-15
    assert r['p_gt_0'] == 0.0
    assert abs(r['ci_low']) < 1e-12 and abs(r['ci_high']) < 1e-12
