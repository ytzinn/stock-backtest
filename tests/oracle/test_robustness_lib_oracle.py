"""
SPEC_10 §5-2 — robustness_lib 손계산 oracle (LOO·top-k 제거·부호검정·백분위).

전략과 랜덤 추첨 양쪽에 동일 적용되는 공유 함수의 옳음 증명 — 깨지면 수정이 틀린 것.
"""
from __future__ import annotations

import pytest

from scripts.robustness.robustness_lib import (
    compound,
    loo_reversal_count,
    margin,
    percentile_below,
    remove_stocks_period_returns,
    sign_test,
    top_contributors,
    topk_removal_margin,
    total_contributions,
)


def test_compound_and_margin_hand_calculated():
    assert compound([0.1, -0.2]) == pytest.approx(1.1 * 0.8)
    assert margin([0.1, -0.2], [0.0, 0.0]) == pytest.approx(1.1 * 0.8 - 1.0)


def test_margin_rejects_length_mismatch():
    with pytest.raises(ValueError, match='구간 수 불일치'):
        margin([0.1], [0.1, 0.2])


def test_loo_reversal_hand_calculated():
    # base = 1.1×0.8 − 1 = −0.12 (<0). i=0 제외: −0.2 < 0 유지. i=1 제외: +0.1 > 0 반전.
    count, idx = loo_reversal_count([0.1, -0.2], [0.0, 0.0])
    assert count == 1
    assert idx == [1]


def test_loo_no_reversal_when_dominant():
    count, idx = loo_reversal_count([0.1, 0.2, 0.3], [0.0, 0.0, 0.0])
    assert count == 0
    assert idx == []


def test_total_contributions_and_topk():
    ps = {
        'p1': [('A', 0.5, 0.20), ('B', 0.5, -0.10)],
        'p2': [('A', 0.5, 0.10), ('B', 0.5, 0.30)],
    }
    contrib = total_contributions(ps)
    assert contrib['A'] == pytest.approx(0.5 * 0.20 + 0.5 * 0.10)   # 0.15
    assert contrib['B'] == pytest.approx(0.5 * -0.10 + 0.5 * 0.30)  # 0.10
    assert top_contributors(ps, 1) == ['A']
    assert top_contributors(ps, 2) == ['A', 'B']


def test_topk_tiebreak_is_ticker_ascending():
    ps = {'p1': [('Z', 0.5, 0.10), ('A', 0.5, 0.10)]}
    assert top_contributors(ps, 1) == ['A']   # 동률 → ticker 오름차순


def test_remove_stocks_renormalizes():
    ps = {'p1': [('A', 0.5, 0.20), ('B', 0.25, -0.10), ('C', 0.25, 0.40)]}
    # A 제거 → B·C 재정규화 (0.5/0.5씩): 0.5×(−0.1) + 0.5×0.4 = 0.15
    assert remove_stocks_period_returns(ps, {'A'}) == [pytest.approx(0.15)]
    # 전 종목 제거 → 0.0 (현금 가정)
    assert remove_stocks_period_returns(ps, {'A', 'B', 'C'}) == [0.0]


def test_topk_removal_margin_hand_calculated():
    a = {'p1': [('A', 0.5, 0.40), ('B', 0.5, 0.10)]}   # top1=A → 제거 후 [0.10]
    b = {'p1': [('X', 0.5, 0.20), ('Y', 0.5, 0.00)]}   # top1=X → 제거 후 [0.00]
    assert topk_removal_margin(a, b, 1) == pytest.approx(1.10 - 1.00)


def test_sign_test_hand_calculated():
    # pos=3, n=4 → 양측 p = 2×(C(4,0)+C(4,1))/2^4 = 10/16 = 0.625
    pos, n, p = sign_test([0.1, 0.2, 0.3, -0.1])
    assert (pos, n) == (3, 4)
    assert p == pytest.approx(0.625)
    # 0은 유효 표본에서 제외
    assert sign_test([0.0, 0.0])[1] == 0


def test_percentile_below_with_ties():
    assert percentile_below(5.0, [1.0, 5.0, 10.0]) == pytest.approx((1 + 0.5) / 3)
    assert percentile_below(0.0, [1.0, 2.0]) == 0.0
    assert percentile_below(3.0, [1.0, 2.0]) == 1.0
