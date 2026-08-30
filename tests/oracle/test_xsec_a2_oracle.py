"""SPEC_15 A-2 / IC 오라클 — **옳음의 증명**.

`tests/oracle/` 은 깨지면 수정이 틀린 것이다 (characterization 과 혼동 금지).

여기서 고정하는 것:
  1. `rank_ic` 가 Spearman 정의와 일치 (동점 평균 순위 포함)
  2. permutation 귀무가 **구간 내**에서만 섞이고 0 에 중심을 둔다
  3. 두 SE 의 **차원**이 다르다 — 귀무는 이미 mean 의 sd, 실현은 계열의 sd (÷√T 필요).
     **이 구분이 A-2 의 핵심이고, 틀리면 MDE 가 √T 배 어긋난다**
  4. 검출률 예측이 **실제 표집분포**와 맞는다 (몬테카를로)
  5. §6-1 방화벽이 중첩·null 까지 잡는다
  6. §2-2 메타 — 위 오라클들이 프로덕션 함수를 실제로 타는가
"""
from __future__ import annotations

import importlib
import math
import sys

import numpy as np
import pytest

from scripts.xsec import ic as icmod
from scripts.xsec import power
from scripts.xsec.firewall import FirewallBreach, assert_clean, assert_whitelisted


# ── 1. rank IC 정의 ─────────────────────────────────────────────────────────
def test_rank_ic_matches_pearson_on_average_ranks():
    rng = np.random.default_rng(0)
    x, y = rng.normal(size=200), rng.normal(size=200)
    from scipy.stats import spearmanr          # 참조 구현 (프로덕션 아님)
    assert icmod.rank_ic(x, y) == pytest.approx(float(spearmanr(x, y).statistic), rel=1e-10)


def test_rank_ic_handles_ties_with_average_ranks():
    """동점을 평균 순위로 처리하지 않으면 **입력 순서가 IC 에 샌다** (CORR-SORT-001 유형)."""
    x = np.array([1.0, 1.0, 1.0, 2.0, 3.0])
    y = np.array([5.0, 4.0, 3.0, 2.0, 1.0])
    a = icmod.rank_ic(x, y)
    perm = [2, 0, 1, 3, 4]
    b = icmod.rank_ic(x[perm], y[perm])
    assert a == pytest.approx(b, rel=1e-12), '동점 처리에 입력 순서가 샌다'
    assert icmod.rankdata_average(x).tolist() == [2.0, 2.0, 2.0, 4.0, 5.0]


def test_rank_ic_is_one_when_signal_is_the_outcome():
    """P-1 이 검사하는 성질을 오라클로도 고정한다."""
    rng = np.random.default_rng(1)
    y = rng.normal(size=50)
    assert icmod.rank_ic(y, y) == pytest.approx(1.0, rel=1e-12)


# ── 2. permutation 귀무 ─────────────────────────────────────────────────────
def test_permutation_null_is_centered_and_within_period():
    rng = np.random.default_rng(2)
    periods = [(rng.normal(size=n), rng.normal(size=n)) for n in (40, 120, 300)]
    null = icmod.permutation_mean_ic(periods, b=2000, seed=7)
    assert abs(null.mean()) < 5 * null.std(ddof=1) / math.sqrt(2000)
    # 구간 내 치환이면 귀무 sd 는 구간 크기로 결정된다: sd(IC_t) ≈ 1/√(N_t−1)
    expect = math.sqrt(sum(1 / (n - 1) for n in (40, 120, 300))) / 3
    assert null.std(ddof=1) == pytest.approx(expect, rel=0.10)


def test_permutation_is_deterministic_for_a_seed():
    rng = np.random.default_rng(3)
    periods = [(rng.normal(size=50), rng.normal(size=50)) for _ in range(4)]
    a = icmod.permutation_mean_ic(periods, b=500, seed=11)
    b = icmod.permutation_mean_ic(periods, b=500, seed=11)
    assert np.array_equal(a, b)


# ── 3. 두 SE 의 차원 (A-2 의 핵심) ──────────────────────────────────────────
def test_null_se_is_already_the_se_of_the_mean():
    """permutation 귀무는 재표본마다 **mean(IC)** 을 낸다 → √T 로 다시 나누지 않는다."""
    assert power.se_mean_ic_from_null(0.0154) == pytest.approx(0.0154, rel=1e-15)


def test_series_se_divides_by_sqrt_T():
    assert power.se_mean_ic_from_series(0.14, 19) == pytest.approx(0.14 / math.sqrt(19), rel=1e-15)


def test_the_two_se_are_not_interchangeable():
    """둘을 바꿔 쓰면 MDE 가 √T 배 어긋난다 — 그 사실 자체를 고정한다."""
    sd, t = 0.14, 19
    wrong = power.se_mean_ic_from_null(sd)                 # 계열 sd 를 귀무처럼 쓴 경우
    right = power.se_mean_ic_from_series(sd, t)
    assert wrong / right == pytest.approx(math.sqrt(t), rel=1e-12)


# ── 4. 검출률 예측 ↔ 실제 표집분포 (몬테카를로) ─────────────────────────────
def test_predicted_detection_rate_matches_sampling_distribution():
    """예측 검출률이 **실제 표집분포**와 맞아야 한다. 산식 대 산식이 아니다."""
    se, design, m = 0.0154, 0.05, 200_000
    rng = np.random.default_rng(20260826)
    crit = se * __import__('statistics').NormalDist().inv_cdf(0.95)
    empirical = float((rng.normal(design, se, m) > crit).mean())
    assert power.predicted_detection_rate(design, se) == pytest.approx(empirical, abs=0.005)


# ── 5. §6-1 방화벽 ──────────────────────────────────────────────────────────
@pytest.mark.parametrize('payload,why', [
    ({'mean_ic': 0.01}, '최상위'),
    ({'period_set': {'diag': {'ic_series': [0.1]}}}, '중첩'),
    ({'t_stat': None}, 'null 값'),
    ({'a': [{'b': {'p_value': 0.04}}]}, '리스트 안'),
])
def test_firewall_catches_forbidden_keys(payload, why):
    with pytest.raises(FirewallBreach):
        assert_clean(payload, f'test:{why}')


def test_firewall_whitelist_rejects_unknown_top_level_key():
    ok = {'period_set': {}, 'T': 19, 'sd_null_ic': 0.1, 'sd_realized_ic': 0.1,
          'mde_perm': 0.1, 'mde_icir': 0.1, 'seed': 1, 'B': 1000}
    assert_whitelisted(ok, 'ok')                    # 통과해야 한다
    with pytest.raises(FirewallBreach):
        assert_whitelisted({**ok, 'extra_summary': 1}, 'bad')


def test_firewall_allows_a_clean_payload():
    """음성만 있으면 '항상 던지는 검사기' 를 못 거른다."""
    assert_clean({'sd_realized_ic': 0.14, 'nested': {'T': 19}}, 'clean')


# ── 6. §2-2 메타 — 오라클이 프로덕션을 타는가 ───────────────────────────────
_META_TARGETS = [
    ('test_rank_ic_matches_pearson_on_average_ranks',        'scripts.xsec.ic.rank_ic'),
    ('test_rank_ic_handles_ties_with_average_ranks',         'scripts.xsec.ic.rankdata_average'),
    ('test_permutation_null_is_centered_and_within_period',  'scripts.xsec.ic.permutation_mean_ic'),
    ('test_the_two_se_are_not_interchangeable',              'scripts.xsec.power.se_mean_ic_from_series'),
    ('test_predicted_detection_rate_matches_sampling_distribution',
     'scripts.xsec.power.predicted_detection_rate'),
]


@pytest.mark.parametrize('test_name,target', _META_TARGETS)
def test_meta_oracle_actually_exercises_production(test_name, target, monkeypatch):
    """대상 프로덕션 함수를 `raise` 로 바꾸면 그 오라클이 **반드시 실패해야** 한다.

    `[검증된 사실]` 이 장치가 없던 A-1 1차 음성 대조에서 몬테카를로 오라클 3개가
    발화하지 않았다 — 산식을 테스트 안에 다시 적어 프로덕션을 타지 않았기 때문이다.
    **`META` 문자열을 반드시 요구한다** — 다른 이유의 실패를 통과로 세면 이 메타 테스트
    자체가 자기가 잡으려던 결함이 된다.
    """
    mod_name, attr = target.rsplit('.', 1)
    mod = importlib.import_module(mod_name)

    def _boom(*a, **k):
        raise AssertionError(f'META: {target} 가 호출됐다')

    monkeypatch.setattr(mod, attr, _boom)
    here = sys.modules[__name__]
    if getattr(here, attr, None) is not None:          # 이 모듈이 직접 바인딩한 경우
        monkeypatch.setattr(here, attr, _boom)

    with pytest.raises(Exception) as ei:
        globals()[test_name]()
    assert f'META: {target}' in str(ei.value), (
        f'{test_name} 이 {target} 를 타지 않는다 — 오라클이 형식뿐이다. '
        f'실제 예외: {type(ei.value).__name__}: {ei.value}')
