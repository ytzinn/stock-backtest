"""
[O-6] 최소 편입 종목 수 — 정책 결정 항목 (CONTRACT-PF-001).

portfolio.py 계약 충돌:
  docstring (portfolio.py:28): "후보가 MIN_PORTFOLIO_STOCKS 미만이면 빈 dict
                                → engine이 period_return=0으로 처리"
  구현     (portfolio.py:35): n == 0 일 때만 빈 dict — 1~4개면 그대로 전액 투자

어느 쪽이 정답인지 **감사자가 임의로 정하지 않는다** (AUDIT_01 O-6 지시).
docstring 쪽 계약을 xfail(strict=False)로 걸어두고 TECH_DEBT.md CONTRACT-PF-001에
정책 결정 항목으로 올린다. 선택지:
  (a) 5종목 미만 → 현금 100%   (b) 그대로 전액 투자(현행 동작)
  (c) 부족분만 현금             (d) 차선 종목으로 보완

관련 동작: pipeline.score_and_rank()는 RIM 컷 통과가 MIN_PORTFOLIO_STOCKS 미만이면
고평가 종목을 upside 순으로 **보완**한다 (pipeline.py:111-118) — 선택지 (d)가 랭킹
단계에 이미 부분 구현돼 있는 셈이라, 정책 결정 시 두 단계의 상호작용을 함께 정해야 한다.
"""
from __future__ import annotations

import pytest

from backtest.portfolio import MIN_PORTFOLIO_STOCKS, build_portfolio


def _candidates(n: int) -> list[dict]:
    return [{'ticker': f'T{i:03d}', 'upside_pct': 100.0 - i, 'model': 'RIM',
             'fair_value': 1000.0, 'price': 500.0} for i in range(n)]


@pytest.mark.xfail(
    strict=False,
    reason='CONTRACT-PF-001: docstring은 "MIN_PORTFOLIO_STOCKS 미만이면 빈 dict"이나 '
           '구현은 n==0일 때만 빈 dict. 정책 결정 전까지 xfail(strict=False) 유지 — '
           'TECH_DEBT.md 참조. 임의 수정 금지.',
)
def test_docstring_contract_below_min_stocks_returns_empty():
    """docstring 계약: 후보 3개 (< MIN_PORTFOLIO_STOCKS=5) → 빈 포트폴리오."""
    assert MIN_PORTFOLIO_STOCKS == 5   # 상수가 바뀌면 이 테스트 전제도 재검토
    portfolio = build_portfolio(_candidates(3), n_stocks=20)
    assert portfolio == {}


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
