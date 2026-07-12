"""
[O-6] 최소 편입 종목 수 — 정책 확정 (CONTRACT-PF-001, 2026-07-12).

**확정된 계약**: 후보가 1개라도 있으면 그 수만큼 전액 투자(동일가중 1/n).
빈 dict는 후보 0개일 때만. (선택지 (a)현금100% / (c)부족분 현금 / (d)차선 보완은
비채택 — 실측상 5종목 미만 구간 0건, 5~7종목 구간 2건뿐이라 결과 영향 미미.
사용자 결정 이력은 TECH_DEBT.md CONTRACT-PF-001 참조.)

관련 동작: pipeline.score_and_rank()는 RIM 컷 통과가 MIN_PORTFOLIO_STOCKS 미만이면
고평가 종목을 upside 순으로 5개까지 보완한다 (pipeline.py) — 랭킹 단계의 보완과
구성 단계의 전액 투자가 한 세트의 확정 정책이다.
"""
from __future__ import annotations

import pytest

from backtest.portfolio import MIN_PORTFOLIO_STOCKS, build_portfolio


def _candidates(n: int) -> list[dict]:
    return [{'ticker': f'T{i:03d}', 'upside_pct': 100.0 - i, 'model': 'RIM',
             'fair_value': 1000.0, 'price': 500.0} for i in range(n)]


def test_below_min_stocks_invests_fully_in_available_candidates():
    """확정 계약: 후보 3개 (< MIN_PORTFOLIO_STOCKS=5) → 3종목 전액 투자 (각 1/3)."""
    assert MIN_PORTFOLIO_STOCKS == 5   # 상수가 바뀌면 이 테스트 전제도 재검토
    portfolio = build_portfolio(_candidates(3), n_stocks=20)
    assert len(portfolio) == 3
    assert all(w == pytest.approx(1 / 3, abs=1e-12) for w in portfolio.values())
    assert sum(portfolio.values()) == pytest.approx(1.0, abs=1e-12)


def test_zero_candidates_returns_empty():
    """양쪽 계약이 일치하는 부분: 후보 0개 → 빈 dict (통과 유지)."""
    assert build_portfolio([], n_stocks=20) == {}


def test_equal_weights_sum_to_one():
    """동일가중 1/N, 합계 1.0 — 논쟁 없는 산술 계약."""
    portfolio = build_portfolio(_candidates(25), n_stocks=20)
    assert len(portfolio) == 20
    assert all(w == pytest.approx(0.05, abs=1e-15) for w in portfolio.values())
    assert sum(portfolio.values()) == pytest.approx(1.0, abs=1e-12)


def test_top_n_selection_preserves_ranking_order():
    """상위 n_stocks만 편입 — score_and_rank 정렬(upside 내림차순)을 신뢰하는 계약."""
    cands = _candidates(25)
    portfolio = build_portfolio(cands, n_stocks=20)
    expected_tickers = {c['ticker'] for c in cands[:20]}
    assert set(portfolio) == expected_tickers
