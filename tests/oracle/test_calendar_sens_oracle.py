"""
SPEC_14 오라클 — calsens_lib 순수 함수와 §7·§8 판정 규칙의 **옳음의 증명**.

`tests/oracle/` 은 깨지면 수정이 틀린 것이다 (characterization 과 혼동 금지).
여기서 고정하는 것:
  - `g(·)` 가 `compute_nav_cagr` 와 정확히 대응한다 (`CAGR = exp(g) − 1`)
  - bootstrap block index 가 circular·길이 N·전역 seed 결정론이다 (§10)
  - 부호 3분류·contrast 3분류·Q1/Q2-D/Q2-M 판정이 사전등록 문턱대로 동작한다
  - contrast 레지스트리 불변식 (단일축 5개, C_RANK 다축 강등)
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from backtest.metrics import compute_nav_cagr
from scripts.calendar_sens import calsens_lib as lib
from scripts.calendar_sens.stage_b import _judge_q1, _judge_q2d, _judge_q2m


# ── g(·) ↔ compute_nav_cagr 대응 ─────────────────────────────────────────────

def test_g_matches_compute_nav_cagr():
    """g = Σlog/years 이면 exp(g)−1 이 compute_nav_cagr 와 **정확히** 같아야 한다."""
    s, e = date(2016, 5, 18), date(2026, 4, 3)
    idx = pd.bdate_range(s, e)
    assert idx[0].date() == s and idx[-1].date() == e
    rng = np.random.default_rng(0)
    nav = pd.Series(np.exp(np.cumsum(rng.normal(3e-4, 1e-2, len(idx)))), index=idx)
    nav.iloc[0] = 1.0

    years = lib.common_period_years(s, e)
    g = lib.annualized_log_return(lib.log_returns(nav, s, e).to_numpy(), years)

    expected = compute_nav_cagr(nav, initial_capital=float(nav.iloc[0]))
    assert lib.cagr_from_g(g) == pytest.approx(expected, rel=1e-12)


def test_log_returns_length_is_obs_minus_one():
    idx = pd.bdate_range('2020-01-01', periods=50)
    nav = pd.Series(np.linspace(1.0, 1.5, 50), index=idx)
    lr = lib.log_returns(nav, idx[0].date(), idx[-1].date())
    assert len(lr) == 49
    assert lr.index[0] == idx[1]          # (S, E] — 시작일은 수익률에 없다


# ── §10 bootstrap block index ────────────────────────────────────────────────

def test_expand_starts_is_circular_and_truncated():
    n, block = 10, 4
    starts = np.array([[0, 7, 9]], dtype=np.int32)     # n_blocks=3 → 12개 → 10 절단
    idx = lib.expand_starts(starts, block, n)
    assert idx.shape == (1, n)
    # 첫 블록 0..3, 둘째 7,8,9,0 (wrap), 셋째 9,0 까지만 (절단)
    assert list(idx[0]) == [0, 1, 2, 3, 7, 8, 9, 0, 9, 0]
    assert idx.max() < n and idx.min() >= 0


def test_block_starts_deterministic_and_global():
    """같은 seed → 같은 행렬. 이 행렬 하나를 전 셀에 써야 §10 셀 간 동조가 성립한다."""
    a = lib.block_starts(2423, 21, 50)
    b = lib.block_starts(2423, 21, 50)
    assert np.array_equal(a, b)
    assert a.shape == (50, -(-2423 // 21))
    # seed 가 다르면 달라야 한다 (v0.2 의 contrast별 seed 가 왜 결함인지의 근거)
    c = lib.block_starts(2423, 21, 50, seed='SPEC14:OTHER')
    assert not np.array_equal(a, c)


def test_bootstrap_g_uses_same_index_for_all_series():
    """동일 시리즈를 두 행에 넣으면 두 행의 g 가 전 반복에서 같아야 한다 (paired 보존)."""
    rng = np.random.default_rng(1)
    row = rng.normal(0, 0.01, 300)
    mat = np.vstack([row, row, rng.normal(0, 0.01, 300)])
    idx = lib.expand_starts(lib.block_starts(300, 21, 20), 21, 300)
    g = lib.bootstrap_g(mat, idx, years=1.2)
    assert np.allclose(g[0], g[1])
    assert not np.allclose(g[0], g[2])


def test_bootstrap_g_resample_length_equals_original():
    """재표본 길이가 원표본과 같아야 동일 `years` 분모가 정당하다 (§10 'N일에서 절단')."""
    mat = np.ones((1, 100)) * 0.001
    idx = lib.expand_starts(lib.block_starts(100, 21, 5), 21, 100)
    g = lib.bootstrap_g(mat, idx, years=1.0)
    assert np.allclose(g, 0.1)            # 100 × 0.001 / 1.0


def test_index_digest_is_stable():
    idx = lib.expand_starts(lib.block_starts(300, 21, 10), 21, 300)
    assert lib.index_digest(idx) == lib.index_digest(idx.copy())


# ── §7-2 부호 3분류 ──────────────────────────────────────────────────────────

def test_classify_sign_thresholds():
    eps, n = lib.EPSILON, 1000
    clearly_pos = np.full(n, 0.02)
    clearly_neg = np.full(n, -0.02)
    tiny        = np.full(n, 0.001)       # |e| < ε → 중립
    assert lib.classify_sign(clearly_pos)[0] == lib.SIGN_POSITIVE
    assert lib.classify_sign(clearly_neg)[0] == lib.SIGN_NEGATIVE
    assert lib.classify_sign(tiny)[0] == lib.SIGN_NEUTRAL
    # 확률 0.90 문턱 — 89%만 ε 초과면 중립
    mixed = np.concatenate([np.full(890, eps + 0.01), np.full(110, 0.0)])
    assert lib.classify_sign(mixed)[0] == lib.SIGN_NEUTRAL
    mixed2 = np.concatenate([np.full(900, eps + 0.01), np.full(100, 0.0)])
    assert lib.classify_sign(mixed2)[0] == lib.SIGN_POSITIVE


def test_classify_contrast_truth_table():
    P, N, X = lib.SIGN_POSITIVE, lib.SIGN_NEGATIVE, lib.SIGN_NEUTRAL
    assert lib.classify_contrast(P, P) == lib.DIR_HELD
    assert lib.classify_contrast(N, N) == lib.DIR_HELD
    assert lib.classify_contrast(P, N) == lib.DIR_REVERSED
    assert lib.classify_contrast(N, P) == lib.DIR_REVERSED
    for pair in ((P, X), (X, P), (N, X), (X, X)):
        assert lib.classify_contrast(*pair) == lib.DIR_NEUTRAL


# ── §8 판정 규칙 ─────────────────────────────────────────────────────────────

def test_q1_large_requires_both_size_and_significance():
    """v0.2 결함 — 점추정만 크고 CI 가 0 을 포함하면 '큼'이 아니다."""
    assert _judge_q1(0.012, (0.002, 0.022), (0.004, 0.020)) == 'Q1_LARGE'
    assert _judge_q1(0.012, (-0.005, 0.029), (-0.002, 0.026)) == 'Q1_INCONCLUSIVE'
    assert _judge_q1(0.002, (-0.02, 0.024), (-0.018, 0.022)) == 'Q1_INCONCLUSIVE'


def test_q1_small_requires_equivalence_not_just_small_point():
    """점추정 0.2%p 여도 90% CI 가 넓으면 '작음'이 아니라 '불확정'이다."""
    assert _judge_q1(0.002, (-0.004, 0.008), (-0.003, 0.004)) == 'Q1_SMALL'
    assert _judge_q1(0.002, (-0.02, 0.024), (-0.015, 0.019)) == 'Q1_INCONCLUSIVE'


def test_q2d_uses_reversal_count_only():
    assert _judge_q2d(n_reversal=2, n_neutral=0) == 'Q2D_LARGE'
    assert _judge_q2d(n_reversal=3, n_neutral=2) == 'Q2D_LARGE'
    assert _judge_q2d(n_reversal=0, n_neutral=2) == 'Q2D_SMALL'
    assert _judge_q2d(n_reversal=0, n_neutral=3) == 'Q2D_INCONCLUSIVE'
    assert _judge_q2d(n_reversal=1, n_neutral=0) == 'Q2D_INCONCLUSIVE'


def test_q2m_uses_delta_ci():
    big   = [{'delta_point': 0.015, 'delta_ci95': [0.005, 0.025]}]
    small = [{'delta_point': 0.002, 'delta_ci95': [-0.006, 0.008]}]
    wide  = [{'delta_point': 0.004, 'delta_ci95': [-0.03, 0.038]}]
    assert _judge_q2m(big) == 'Q2M_LARGE'
    assert _judge_q2m(small) == 'Q2M_SMALL'
    assert _judge_q2m(wide) == 'Q2M_INCONCLUSIVE'
    # 크기는 크지만 CI 가 0 을 포함 → 큼 아님
    assert _judge_q2m([{'delta_point': 0.015, 'delta_ci95': [-0.002, 0.032]}]) \
        == 'Q2M_INCONCLUSIVE'


def test_interval_helpers():
    assert lib.excludes_zero((0.001, 0.02)) and lib.excludes_zero((-0.02, -0.001))
    assert not lib.excludes_zero((-0.001, 0.02))
    assert lib.within((-0.004, 0.004), 0.005)
    assert not lib.within((-0.006, 0.004), 0.005)


# ── contrast 레지스트리 불변식 (§6-3 + 구현 정정) ────────────────────────────

def test_contrast_registry_invariants():
    assert len(lib.JUDGMENT_CONTRASTS) == 8
    assert len(lib.SINGLE_AXIS_CONTRASTS) == 5
    ids = {c.contrast_id for c in lib.SINGLE_AXIS_CONTRASTS}
    assert ids == {'C_R1', 'C_R2', 'C_R5', 'C_R6', 'C_MOM'}
    # 인컴번트 자신은 판정 contrast 에 없다 (자기비교 = 항상 0, §6-3)
    assert lib.INCUMBENT_TAG not in {c.variant_tag for c in lib.JUDGMENT_CONTRASTS}


def test_c_rank_is_multi_axis():
    """`F_no_r3r4` 는 RIM 랭킹 + 밸류에이션 컷이 함께 바뀐다 — 단일축 아님."""
    c = next(c for c in lib.JUDGMENT_CONTRASTS if c.contrast_id == 'C_RANK')
    assert c.single_axis is False and c.n_axes > 1


def test_new_c_r5_tag_exists_in_ablation_configs():
    from backtest.ablation import ABLATION_CONFIGS
    cfg = ABLATION_CONFIGS['F_pbr_no_r3r4r5']
    assert cfg['stability_rules'] == {'R1', 'R2', 'R6'}
    inc = ABLATION_CONFIGS[lib.INCUMBENT_TAG]
    diff = [k for k in set(cfg) | set(inc) if cfg.get(k) != inc.get(k)]
    assert diff == ['stability_rules'], f'인컴번트와 stability_rules 외 축이 다르다: {diff}'
