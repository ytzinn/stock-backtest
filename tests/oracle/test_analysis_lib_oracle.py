"""SPEC_11 — analysis_lib(스피어만·Jaccard) 손계산 oracle."""
from __future__ import annotations

import pytest

from scripts.analysis.analysis_lib import jaccard, spearman


def test_spearman_perfect_and_inverse():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_spearman_hand_calculated_with_tie():
    # x=[1,2,2,4] → 순위 [1, 2.5, 2.5, 4]; y=[10,20,30,40] → [1,2,3,4]
    # 피어슨(순위): cov=Σ(rx−2.5)(ry−2.5)=(−1.5)(−1.5)+0+0+(1.5)(1.5)=4.5... 손계산:
    # rx−m=[−1.5,0,0,1.5], ry−m=[−1.5,−0.5,0.5,1.5] → cov=2.25+0+0+2.25=4.5
    # sx=√(2.25+2.25)=√4.5, sy=√(2.25+0.25+0.25+2.25)=√5 → ρ=4.5/√22.5
    assert spearman([1, 2, 2, 4], [10, 20, 30, 40]) == pytest.approx(4.5 / (4.5 * 5) ** 0.5)


def test_spearman_degenerate_cases():
    assert spearman([1], [2]) == 0.0
    assert spearman([3, 3, 3], [1, 2, 3]) == 0.0   # 상수열
    with pytest.raises(ValueError):
        spearman([1, 2], [1])


def test_jaccard_hand_calculated():
    assert jaccard({'a', 'b'}, {'b', 'c'}) == pytest.approx(1 / 3)
    assert jaccard({'a'}, {'a'}) == 1.0
    assert jaccard(set(), set()) == 1.0
    assert jaccard({'a'}, set()) == 0.0
