"""
`engine._calc_transaction_cost()` 오라클 — SPEC_13 §8 QG0 DEBT-4 비용 오라클.

CORR-COST-001(매수/매도 분리 + 시장별 매도요율 + 첫 진입 buy-only)이 구현됐지만
지금까지 이 함수를 직접 검증하는 오라클이 없었다(daily_nav 오라클은 stitch_periods가
주어진 transaction_cost를 승법으로 적용하는지만 검증 — tc 계산 자체는 미검증).
DB 미접속 — `get_markets`를 monkeypatch로 대체.

Q-G에서 산식부가 순수 함수 `_transaction_cost_from_markets()`로 분리됐다
(run_random_pool이 1,000회 추첨마다 DB를 치지 않도록). 아래 기존 5건은 리팩터링
회귀 게이트를 겸한다 — `_calc_transaction_cost` 호출 계약은 불변이어야 한다.
"""
from __future__ import annotations

from datetime import date

import backtest.engine as engine_mod
from backtest.configs.constants import BUY_COST, SELL_COST_KOSDAQ, SELL_COST_KOSPI
from backtest.engine import _transaction_cost_from_markets

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


# ── _transaction_cost_from_markets (Q-G 순수 함수 분리) ──────────────────────

def test_pure_function_matches_db_path_given_same_markets(monkeypatch):
    """순수 함수와 DB 경유 함수가 **같은 시장 정보에서 같은 값**을 내야 한다.

    run_random_pool이 날짜당 1회 prefetch한 시장으로 순수 함수만 호출하는 근거 —
    두 경로가 갈리면 대조군 거래비용이 후보와 다른 산식이 된다.
    """
    markets = {'005930': 'KOSPI', '250000': 'KOSDAQ'}
    monkeypatch.setattr(engine_mod, 'get_markets', lambda conn, tickers, as_of: markets)

    prev = {'005930': 0.5, '250000': 0.5}
    curr = {'005930': 0.2, '250000': 0.3, '000660': 0.5}
    via_db   = engine_mod._calc_transaction_cost(conn=None, prev=prev, curr=curr, as_of=AS_OF)
    via_pure = _transaction_cost_from_markets(prev, curr, markets)
    assert via_db == via_pure


def test_pure_function_first_entry_buy_only():
    """prev 없음 → 매도비용 0. 시장 정보가 있어도 매수비용만."""
    tc = _transaction_cost_from_markets({}, {'005930': 0.5, '000660': 0.5},
                                        {'005930': 'KOSPI', '000660': 'KOSDAQ'})
    assert tc == BUY_COST


def test_pure_function_missing_market_key_defaults_to_kospi():
    """prefetch 딕셔너리에 키가 없어도 예외 없이 KOSPI 상한으로 보수 처리."""
    tc = _transaction_cost_from_markets({'999999': 1.0}, {}, {})
    assert abs(tc - SELL_COST_KOSPI) < 1e-12
