"""
`engine._calc_transaction_cost()` 오라클 — SPEC_13 §8 QG0 DEBT-4 비용 오라클.

CORR-COST-001(매수/매도 분리 + 시장별 매도요율 + 첫 진입 buy-only)이 구현됐지만
지금까지 이 함수를 직접 검증하는 오라클이 없었다(daily_nav 오라클은 stitch_periods가
주어진 transaction_cost를 승법으로 적용하는지만 검증 — tc 계산 자체는 미검증).
DB 미접속 — `get_markets`를 monkeypatch로 대체.
"""
from __future__ import annotations

from datetime import date

import backtest.engine as engine_mod
from backtest.configs.constants import BUY_COST, SELL_COST_KOSDAQ, SELL_COST_KOSPI

AS_OF = date(2024, 4, 5)


def test_first_entry_is_buy_only_no_sell_cost(monkeypatch):
    """DEBT-4: prev={}(첫 진입)는 팔 종목이 없으므로 매도비용 0, 매수비용만."""
    def _fail_get_markets(*_a, **_k):
        raise AssertionError('첫 진입은 sell_deltas가 비어 get_markets를 호출하면 안 됨')
    monkeypatch.setattr(engine_mod, 'get_markets', _fail_get_markets)

    curr = {'005930': 0.5, '000660': 0.5}
    tc = engine_mod._calc_transaction_cost(conn=None, prev={}, curr=curr, as_of=AS_OF)
    assert tc == BUY_COST  # buy_turnover = 1.0, sell_turnover = 0


def test_full_replacement_splits_buy_and_sell_by_market(monkeypatch):
    """전량 교체(겹침 없음) — 매도는 시장별 요율, 매수는 공통 요율."""
    monkeypatch.setattr(engine_mod, 'get_markets',
                        lambda conn, tickers, as_of: {'005930': 'KOSPI', '250000': 'KOSDAQ'})

    prev = {'005930': 0.5, '250000': 0.5}
    curr = {'000660': 0.5, '035420': 0.5}
    tc = engine_mod._calc_transaction_cost(conn=None, prev=prev, curr=curr, as_of=AS_OF)

    expected = 1.0 * BUY_COST + 0.5 * SELL_COST_KOSPI + 0.5 * SELL_COST_KOSDAQ
    assert abs(tc - expected) < 1e-12


def test_partial_rebalance_only_decreasing_weights_charged_sell_cost(monkeypatch):
    """일부만 비중 축소 — 늘어난 종목은 매도비용 0, 줄어든 종목만 매도요율 적용."""
    calls = []

    def _fake_get_markets(conn, tickers, as_of):
        calls.append(set(tickers))
        return {'005930': 'KOSPI'}

    monkeypatch.setattr(engine_mod, 'get_markets', _fake_get_markets)

    prev = {'005930': 0.6, '000660': 0.4}
    curr = {'005930': 0.3, '000660': 0.7}   # 005930만 축소(-0.3), 000660은 확대(+0.3)
    tc = engine_mod._calc_transaction_cost(conn=None, prev=prev, curr=curr, as_of=AS_OF)

    assert calls == [{'005930'}], '축소된 종목만 get_markets 조회 대상'
    expected = 0.3 * BUY_COST + 0.3 * SELL_COST_KOSPI
    assert abs(tc - expected) < 1e-12


def test_unknown_market_defaults_to_kospi_sell_cost(monkeypatch):
    """시장 미상 종목은 KOSPI 요율(상한)로 보수 처리 — get_markets 계약."""
    monkeypatch.setattr(engine_mod, 'get_markets', lambda conn, tickers, as_of: {})

    prev = {'999999': 1.0}
    curr = {}
    tc = engine_mod._calc_transaction_cost(conn=None, prev=prev, curr=curr, as_of=AS_OF)
    assert abs(tc - 1.0 * SELL_COST_KOSPI) < 1e-12


def test_unchanged_weights_yield_zero_cost(monkeypatch):
    def _fail_get_markets(*_a, **_k):
        raise AssertionError('비중 불변이면 sell_deltas가 비어 get_markets 호출 안 함')
    monkeypatch.setattr(engine_mod, 'get_markets', _fail_get_markets)

    w = {'005930': 0.5, '000660': 0.5}
    tc = engine_mod._calc_transaction_cost(conn=None, prev=w, curr=w, as_of=AS_OF)
    assert tc == 0.0
