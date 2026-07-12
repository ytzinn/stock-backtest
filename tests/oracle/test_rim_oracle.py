"""
[O-1] RIMModel.fair_value() 오라클 — 수학적으로 옳은 값 검증. 깨지면 수정이 틀린 것이다.

계약 (MASTER.md §3-1, CLAUDE.md 주요 상수):
    adjROE       = (0.5×NI + 0.5×CFO) / equity
    r            = RF + β×(RK−RF) + beta_adj    (β=1.0 고정 → r = RK + beta_adj)
    V/B          = 1 + (adjROE − r) / (1 + r − ω),  clamp [0, VB_CAP]
    FV_total     = equity × V/B
    FV_per_share = FV_total / shares            ← 반환값은 총액이 아니라 **주당 적정가**

상수는 backtest/configs/constants.py 에서 import (SSOT). 하드코딩 금지.
"""
from __future__ import annotations

import pytest

from backtest.configs.constants import OMEGA, RF, RK, VB_CAP
from backtest.models.rim import RIMModel

EQUITY = 1_000_000_000.0
SHARES = 1_000_000

# β=1.0, beta_adj=0 → r = RF + (RK − RF) = RK
R = RK


def _pit(ni: float, cfo: float, equity: float = EQUITY, equity_key: str = '자본총계') -> dict:
    return {'당기순이익': ni, '영업활동현금흐름': cfo, equity_key: equity}


def _expected_per_share(adj_roe: float, equity: float = EQUITY, shares: int = SHARES) -> float:
    """계약 산식으로 독립 계산한 기대 주당 적정가 (구현과 다른 코드 경로)."""
    vb = 1.0 + (adj_roe - R) / (1.0 + R - OMEGA)
    vb = max(0.0, min(vb, VB_CAP))
    return equity * vb / shares


def test_adjroe_equals_r_gives_exact_book_value_per_share():
    """adjROE == r → 초과이익 0 → V/B == 1 → 주당 적정가 == 주당 장부가."""
    ni = cfo = R * EQUITY  # adjROE = (0.5+0.5)×R×equity/equity = R
    fv = RIMModel().fair_value('T', _pit(ni, cfo), SHARES)
    assert fv == pytest.approx(EQUITY / SHARES, rel=1e-12)


def test_adjroe_above_r_gives_premium_above_book():
    adj_roe = R + 0.05
    ni = cfo = adj_roe * EQUITY
    fv = RIMModel().fair_value('T', _pit(ni, cfo), SHARES)
    assert fv == pytest.approx(_expected_per_share(adj_roe), rel=1e-12)
    assert fv > EQUITY / SHARES


def test_adjroe_below_r_gives_discount_below_book():
    adj_roe = R - 0.03
    ni = cfo = adj_roe * EQUITY
    fv = RIMModel().fair_value('T', _pit(ni, cfo), SHARES)
    assert fv == pytest.approx(_expected_per_share(adj_roe), rel=1e-12)
    assert 0 < fv < EQUITY / SHARES


def test_vb_upper_clamp_at_vb_cap():
    """premium이 VB_CAP−1을 넘으면 V/B == VB_CAP 에서 캡."""
    # adjROE를 캡 초과 수준으로: premium = (adjROE−r)/(1+r−ω) > VB_CAP−1
    adj_roe = R + (VB_CAP - 1.0) * (1.0 + R - OMEGA) * 2.0   # 캡 지점의 2배
    ni = cfo = adj_roe * EQUITY
    fv = RIMModel().fair_value('T', _pit(ni, cfo), SHARES)
    assert fv == pytest.approx(EQUITY * VB_CAP / SHARES, rel=1e-12)


def test_vb_exactly_at_cap_boundary():
    """premium == VB_CAP−1 정확히 → V/B == VB_CAP (캡과 무캡의 경계에서 같은 값)."""
    adj_roe = R + (VB_CAP - 1.0) * (1.0 + R - OMEGA)
    ni = cfo = adj_roe * EQUITY
    fv = RIMModel().fair_value('T', _pit(ni, cfo), SHARES)
    assert fv == pytest.approx(EQUITY * VB_CAP / SHARES, rel=1e-9)


def test_vb_lower_clamp_zero_returns_none():
    """
    adjROE가 충분히 낮으면 1+premium ≤ 0 → V/B가 0으로 클램프 → fv_total == 0
    → fv_total <= 0 방어 경로에서 None. (0원 주식이 아니라 '평가 불가'가 계약.)
    """
    adj_roe = R - 1.0 * (1.0 + R - OMEGA) * 1.5   # premium = −1.5 → 1+premium < 0
    ni = cfo = adj_roe * EQUITY
    assert RIMModel().fair_value('T', _pit(ni, cfo), SHARES) is None


def test_per_share_division_is_applied():
    """
    반환 계약은 총액이 아니라 주당 적정가다.
    shares를 2배로 하면 주당 적정가는 정확히 절반 — 나눗셈 단계가 누락되면 실패한다.
    """
    ni = cfo = (R + 0.02) * EQUITY
    fv_1x = RIMModel().fair_value('T', _pit(ni, cfo), SHARES)
    fv_2x = RIMModel().fair_value('T', _pit(ni, cfo), SHARES * 2)
    assert fv_2x == pytest.approx(fv_1x / 2.0, rel=1e-12)
    # 총액을 그대로 반환하면 equity×V/B ≈ 10억 스케일 — 주당 스케일(1천 원대)인지 확인
    assert fv_1x < EQUITY  # 나눗셈 누락 시 fv_1x == equity×vb ≥ equity×0 스케일로 실패


def test_equity_priority_지배기업소유주지분_over_자본총계():
    """equity 우선순위: 지배기업소유주지분 > 지배기업소유주지분_1 > 자본총계."""
    ni = cfo = R * 500_000_000.0  # adjROE = R×(5e8/실제선택된 equity)
    pit = {
        '당기순이익': ni, '영업활동현금흐름': cfo,
        '지배기업소유주지분': 500_000_000.0,
        '자본총계': 999_999_999.0,
    }
    fv = RIMModel().fair_value('T', pit, SHARES)
    # 지배기업소유주지분(5e8)이 선택되면 adjROE == R → V/B == 1 → 5e8/shares
    assert fv == pytest.approx(500_000_000.0 / SHARES, rel=1e-12)


def test_equity_priority_suffix1_over_자본총계():
    pit = {
        '당기순이익': R * 500_000_000.0, '영업활동현금흐름': R * 500_000_000.0,
        '지배기업소유주지분_1': 500_000_000.0,
        '자본총계': 999_999_999.0,
    }
    fv = RIMModel().fair_value('T', pit, SHARES)
    assert fv == pytest.approx(500_000_000.0 / SHARES, rel=1e-12)


@pytest.mark.parametrize('missing', ['당기순이익', '영업활동현금흐름'])
def test_missing_ni_or_cfo_returns_none(missing):
    pit = _pit(50_000_000.0, 60_000_000.0)
    del pit[missing]
    assert RIMModel().fair_value('T', pit, SHARES) is None


def test_nonpositive_equity_returns_none():
    assert RIMModel().fair_value('T', _pit(1.0, 1.0, equity=0.0), SHARES) is None
    assert RIMModel().fair_value('T', _pit(1.0, 1.0, equity=-1e9), SHARES) is None


def test_zero_or_none_shares_returns_none():
    pit = _pit(50_000_000.0, 60_000_000.0)
    assert RIMModel().fair_value('T', pit, 0) is None
    assert RIMModel().fair_value('T', pit, None) is None


def test_beta_adj_shifts_required_return():
    """beta_adj > 0 → r 상승 → 같은 adjROE에서 V/B 하락 → 주당 적정가 하락."""
    ni = cfo = (R + 0.03) * EQUITY
    fv_base = RIMModel(beta_adj=0.0).fair_value('T', _pit(ni, cfo), SHARES)
    fv_up   = RIMModel(beta_adj=0.02).fair_value('T', _pit(ni, cfo), SHARES)
    assert fv_up < fv_base
