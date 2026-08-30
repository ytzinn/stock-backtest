"""SPEC_15 A-1 오라클 — 연율화·SE 승수의 **옳음의 증명**.

`tests/oracle/` 은 깨지면 수정이 틀린 것이다 (characterization 과 혼동 금지).

여기서 고정하는 것:
  1. `g = Σlog(1+r)/years` 의 `CAGR = exp(g) − 1` 이 `compute_cagr` 와 정확히 대응
     (선례: `test_calendar_sens_oracle.py::test_g_matches_compute_nav_cagr`)
  2. 벡터화 `g_vec` 가 SSOT `annualized_log_return` 과 동치
  3. **SE 승수** — 산식 대 산식이 아니라 **산식 대 추정량의 실제 표집분포**로 고정한다

`[검증된 사실]` 3번이 이 파일의 존재 이유다. SPEC_15 v0.1 이 SE 승수를 `√2` 로 적었고
(합의 변동성 승수이지 평균의 것이 아니다), **오라클이 없어서** 그 식이 검증 없이
스크립트로 복사돼 SE 를 √2배 과소추정했다. 산식을 산식과 비교하는 테스트였다면
같은 오기를 그대로 통과시켰을 것이다. 그래서 몬테카를로로 잰다.
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from backtest.metrics import compute_cagr
from scripts.calendar_sens.calsens_lib import (
    annualized_log_return,
    cagr_from_g,
    common_period_years,
)
from scripts.xsec import power


# ── 1. g ↔ compute_cagr 대응 (유도의 닻) ────────────────────────────────────
def test_g_matches_compute_cagr_on_period_returns():
    """구간 수익률에서 만든 g 의 exp(g)−1 이 `compute_cagr` 와 **정확히** 같아야 한다.

    이것이 A-1 전체의 닻이다 — `years` 를 캘린더 경과일수로 잡는 규약까지 함께 고정된다.
    """
    s, e = date(2017, 4, 5), date(2026, 4, 3)
    rng = np.random.default_rng(11)
    r = pd.Series(rng.normal(0.03, 0.20, 18))

    years = common_period_years(s, e)
    g = annualized_log_return(np.log1p(r.to_numpy()), years)

    expected = compute_cagr(r, start_date=s, end_date=e)
    assert cagr_from_g(g) == pytest.approx(expected, rel=1e-12)


def test_years_is_not_exactly_T_over_2():
    """반기 캘린더라도 `years != T/2` 다 — 그래서 승수 상수 2 를 쓰면 안 된다.

    설계서가 `2σ/√T` 로 적은 것은 `years = T/2` 근사에 기댄 식이다.
    """
    s, e = date(2017, 4, 5), date(2026, 4, 3)
    years = common_period_years(s, e)
    assert years != pytest.approx(18 / 2, abs=1e-9)
    assert years == pytest.approx(9.0, abs=0.05)      # 근사로는 맞다


# ── 2. 벡터화 동치 ──────────────────────────────────────────────────────────
def test_g_vec_matches_ssot_annualized_log_return():
    rng = np.random.default_rng(3)
    m = rng.normal(0.01, 0.2, size=(50, 18))
    years = 9.0
    got = power.g_vec(m, years)
    want = np.array([annualized_log_return(row, years) for row in m])
    assert np.allclose(got, want, rtol=0, atol=0)


# ── 3. SE 승수 — 몬테카를로가 판정한다 (이 파일의 핵심) ─────────────────────
@pytest.mark.parametrize('T,years,sigma', [(18, 8.9966, 0.18), (19, 9.9966, 0.25), (24, 12.0, 0.1)])
def test_se_g_analytic_matches_sampling_distribution(T, years, sigma):
    """`SE(g) = σ√T/years` 가 **g 의 실제 표집분포 sd** 와 일치해야 한다.

    독립 표본을 M개 만들어 각각 g 를 구하고, 그 sd 를 해석적 값과 견준다.
    M=200,000 에서 sd 추정의 상대오차는 ~1/√(2M) ≈ 0.16% 이므로 1% 허용이면 넉넉하다.
    **오기(√2 승수)는 41% 어긋나므로 반드시 걸린다.**
    """
    m = 200_000
    rng = np.random.default_rng(20260825)
    samples = rng.normal(0.0, sigma, size=(m, T))
    gs = power.g_vec(samples, years)

    empirical = float(gs.std(ddof=1))
    analytic = sigma * math.sqrt(T) / years        # 모집단 σ 를 아는 상황
    assert empirical == pytest.approx(analytic, rel=0.01)

    # **프로덕션 함수를 직접 태운다.** 위 두 줄만 두면 산식을 테스트 안에 다시 적은 셈이라
    # `power.se_g_analytic` 에 같은 오기가 들어가도 이 테스트는 통과한다 —
    # 실제로 √2 주입 음성 대조에서 이 테스트가 발화하지 않았다.
    # 표본 sd 대비 **배율**로 보면 s 의 편의(E[s] = c4·σ)가 상쇄돼 정확 비교가 된다.
    sub = samples[:4000]
    got = np.array([power.se_g_analytic(row, years) for row in sub])
    assert np.allclose(got / sub.std(axis=1, ddof=1), math.sqrt(T) / years, rtol=1e-12)


def test_oracle_rejects_the_v0_1_sqrt2_multiplier():
    """**음성 대조** — v0.1 의 `√2` 승수를 넣으면 위 대조가 깨져야 한다.

    오라클이 형식뿐이 아님을 이 테스트가 증명한다. 통과 테스트만 있으면
    "항상 통과하는 오라클" 을 걸러낼 수 없다.
    """
    T, years, sigma, m = 18, 8.9966, 0.18, 200_000
    rng = np.random.default_rng(20260825)
    gs = power.g_vec(rng.normal(0.0, sigma, size=(m, T)), years)
    empirical = float(gs.std(ddof=1))

    correct = sigma * math.sqrt(T) / years
    v0_1_wrong = sigma / math.sqrt(T) * math.sqrt(2)      # 설계서 v0.1 의 식

    assert empirical == pytest.approx(correct, rel=0.01)
    assert empirical != pytest.approx(v0_1_wrong, rel=0.01)

    # 과소추정 배율을 **정확히** 고정한다.
    #   correct / wrong = (σ√T/years) / (σ√2/√T) = T / (years·√2)
    # `[정정]` 최초 작성 때 이것을 √2 로 적었다가 이 테스트가 걸렸다. 검출기가 아니라
    # **기대치 산술이 틀린 것**이었다 (CLOSEOUT §8 교훈 2). √2 가 되려면 years == T/2
    # 여야 하는데, 바로 위 test_years_is_not_exactly_T_over_2 가 그렇지 않음을 고정한다.
    assert correct / v0_1_wrong == pytest.approx(T / (years * math.sqrt(2)), rel=1e-12)
    # 그리고 그 값은 √2 에 **가깝다** — 설계서가 상수 2 근사로 미끄러진 지점이다
    assert correct / v0_1_wrong == pytest.approx(math.sqrt(2), rel=1e-3)


def test_se_g_analytic_uses_sample_sd_ddof1():
    """해석적 SE 는 표본 sd(ddof=1). 부트스트랩과의 √((T−1)/T) 차이의 출처다."""
    x = np.array([0.1, -0.2, 0.05, 0.3, -0.15])
    years = 2.5
    assert power.se_g_analytic(x, years) == pytest.approx(
        float(np.std(x, ddof=1)) * math.sqrt(len(x)) / years, rel=1e-15)


# ── 4. MDE / 재현성 ─────────────────────────────────────────────────────────
def test_mde_is_one_sided_and_not_two_sided():
    """단측 α=0.05·검정력 0.8. **양측 승수(≈2.802)와 혼재하면 안 된다.**"""
    se = 0.1
    got = power.mde_one_sided(se) / se
    assert got == pytest.approx(1.6448536269514722 + 0.8416212335729143, rel=1e-12)
    assert got != pytest.approx(2.8020, rel=1e-3)     # 양측 값이 아니다


def test_bootstrap_is_deterministic_for_a_seed():
    rng = np.random.default_rng(5)
    x = np.log1p(rng.normal(0.02, 0.2, 18))
    a, _ = power.se_g_bootstrap(x, 9.0, b=20_000, seed=7)
    b, _ = power.se_g_bootstrap(x, 9.0, b=20_000, seed=7)
    assert a == b


def test_bootstrap_approaches_analytic_from_below():
    """부트스트랩은 모집단 sd(n 분모)에 가까워 해석적(ddof=1)보다 √((T−1)/T) 배 작다."""
    rng = np.random.default_rng(9)
    x = np.log1p(rng.normal(0.02, 0.2, 18))
    an = power.se_g_analytic(x, 9.0)
    bs, _ = power.se_g_bootstrap(x, 9.0, b=200_000, seed=1)
    ratio = bs / an
    assert math.sqrt(17 / 18) == pytest.approx(ratio, rel=0.02)
    assert ratio < 1.0


# ── 5. 가드 ─────────────────────────────────────────────────────────────────
def test_power_module_does_not_touch_db():
    """A-1 은 산출물 파일만 읽는다. DB 접속 모듈이 딸려오면 단언이 깨진다."""
    from scripts.xsec._guard import GuardFailed, assert_no_db_imported
    try:
        assert_no_db_imported('test')
    except GuardFailed as e:            # 다른 테스트가 먼저 psycopg2 를 끌어온 경우
        pytest.skip(f'세션에 DB 모듈이 이미 로드됨: {e}')
