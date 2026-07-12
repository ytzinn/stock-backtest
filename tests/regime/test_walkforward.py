"""
SPEC_08 §9 — walkforward.py 테스트. DB 접속 없이 검증 가능한 로직만 다룬다.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from backtest.regime import walkforward as wf


def _fake_oos_df(alpha_seq: list[float], period22_idx: int | None = None) -> pd.DataFrame:
    n = len(alpha_seq)
    dates = [date(2020, 1, 31) + timedelta(days=30 * i) for i in range(n)]
    return pd.DataFrame({
        'date': dates,
        'is_oos': [True] * n,
        'episode_tag': ['period22' if i == period22_idx else 'normal' for i in range(n)],
        'net_port_return': [0.01 + a for a in alpha_seq],
        'net_base_return': [0.01] * n,
    })


def test_check_c1_ex22_alpha_true_when_positive():
    assert wf.check_c1_ex22_alpha({'ex22_alpha': 0.01}) is True
    assert wf.check_c1_ex22_alpha({'ex22_alpha': -0.01}) is False
    assert wf.check_c1_ex22_alpha({}) is False   # 값 없으면 안전하게 실패 처리


def test_check_c2_period22_share_true_when_below_half():
    assert wf.check_c2_period22_share({'period22_share': 0.3}) is True
    assert wf.check_c2_period22_share({'period22_share': 0.6}) is False
    assert wf.check_c2_period22_share({'period22_share': np.nan}) is False


def test_check_c3_sign_stability_true_when_both_halves_same_sign():
    df = _fake_oos_df([0.01] * 20)   # 전부 양의 alpha -> 전/후 반기 둘 다 양수
    assert wf.check_c3_sign_stability(df) is True


def test_check_c3_sign_stability_false_when_halves_disagree():
    df = _fake_oos_df([0.05] * 10 + [-0.05] * 10)   # 전반 양수, 후반 음수
    assert wf.check_c3_sign_stability(df) is False


def test_check_c3_sign_stability_false_when_too_few_observations():
    df = _fake_oos_df([0.01, 0.01])
    assert wf.check_c3_sign_stability(df) is False


def test_check_c4_economic_passes_on_strong_cagr_improvement():
    assert wf.check_c4_economic({'cagr_improve_vs_always_on': 0.01}) is True


def test_check_c4_economic_passes_on_small_cagr_loss_with_mdd_improvement():
    assert wf.check_c4_economic({'cagr_improve_vs_always_on': -0.001, 'mdd_improve_vs_always_on': 0.03}) is True


def test_check_c4_economic_fails_on_large_cagr_loss_without_offsetting_mdd():
    stats = {'cagr_improve_vs_always_on': -0.05, 'mdd_improve_vs_always_on': 0.0,
             'net_sharpe': -1.0, 'net_calmar': -1.0, 'ex22_alpha': -0.01}
    assert wf.check_c4_economic(stats) is False


def test_evaluate_candidate_is_candidate_only_when_all_four_pass():
    """★ ex22_alpha<=0(#22 제외 시 뒤집힘)이면 나머지가 좋아도 후보 불가(=C1)."""
    good_df = _fake_oos_df([0.01] * 20, period22_idx=19)
    result = wf.evaluate_candidate(good_df)
    assert result['C1'] is True
    assert result['is_candidate'] == (result['C1'] and result['C2'] and result['C3'] and result['C4'])

    bad_df = _fake_oos_df([-0.01] * 19 + [0.30], period22_idx=19)   # 알파 전부 #22 하나에서 나옴
    bad_result = wf.evaluate_candidate(bad_df)
    assert bad_result['C1'] is False   # ex22_alpha <= 0
    assert bad_result['is_candidate'] is False


def test_expanding_folds_never_reference_future_periods():
    """★ R3 핵심 회귀 — fold의 '과거' 목록에 test_period 이후 구간이 절대 섞이면 안 된다."""
    period_starts = [date(2016, 4, 5) + timedelta(days=180 * i) for i in range(21)]
    folds = wf.expanding_folds(period_starts, warmup_end_idx=6)

    assert len(folds) == 21 - 6 - 1
    for past, test_period in folds:
        assert all(p < test_period for p in past)
        assert test_period not in past


def test_select_k_from_past_picks_best_utility():
    stats_by_k = {
        0.075: {'net_cagr': 0.05, 'net_mdd': -0.10},
        0.15:  {'net_cagr': 0.08, 'net_mdd': -0.10},   # utility 최댓값
        0.25:  {'net_cagr': 0.09, 'net_mdd': -0.40},   # CAGR은 높지만 MDD 페널티로 밀림
    }
    assert wf.select_k_from_past(stats_by_k) == 0.15


def test_select_k_from_past_ignores_nan_stats():
    stats_by_k = {
        0.075: {'net_cagr': np.nan, 'net_mdd': np.nan},
        0.15:  {'net_cagr': 0.03, 'net_mdd': -0.05},
    }
    assert wf.select_k_from_past(stats_by_k) == 0.15
