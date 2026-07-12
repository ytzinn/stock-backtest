"""
[O-2][O-3] _calc_period_return() 오라클 — 가중 수익률 소비 + 상폐 3시나리오 순서 독립성.

DB 미접속: backtest.engine 네임스페이스에 바인딩된 get_close_price / is_delisted_at /
_last_known_price 를 monkeypatch로 대체한다 (engine.py는 `from backtest.data_access
import ...`로 이름을 자기 네임스페이스에 복사하므로 engine 쪽을 패치해야 한다).

⚠ 이 파일의 일부 테스트는 **의도적으로 실패 상태**다 (tests/oracle/README.md 참조).
  - test_weighted_return_consumes_portfolio_weights   → CORR-ENGINE-001 증거
  - test_delisting_adjustments_are_order_independent  → CORR-ENGINE-002 증거 (재현 = P0-A 승격)
  xfail 처리하지 마라. Pass 3에서 프로덕션 코드를 고치면 저절로 통과한다.
"""
from __future__ import annotations

from datetime import date

import pytest

import backtest.engine as engine
from backtest.engine import DELISTING_HAIRCUT, _calc_period_return

START = date(2024, 4, 3)
END   = date(2024, 8, 20)


def _patch_market(monkeypatch, start_prices: dict, end_prices: dict,
                  delisted: set = frozenset(), last_prices: dict | None = None):
    """가격·상폐 조회를 dict 기반 fake로 대체."""
    def fake_close(conn, ticker, as_of):
        return start_prices.get(ticker) if as_of == START else end_prices.get(ticker)

    def fake_delisted(conn, ticker, as_of):
        return ticker in delisted

    def fake_last(conn, ticker, before):
        return (last_prices or {}).get(ticker, 0.0)

    monkeypatch.setattr(engine, 'get_close_price', fake_close)
    monkeypatch.setattr(engine, 'is_delisted_at', fake_delisted)
    monkeypatch.setattr(engine, '_last_known_price', fake_last)


# ── O-2: build_portfolio()가 반환한 weight가 실제로 소비되는가 ──────────────────

def test_weighted_return_consumes_portfolio_weights(monkeypatch):
    """
    비등가중 포트폴리오 {A:0.5, B:0.3, C:0.2}, 수익률 A+10% B+20% C−10%.
    올바른 가중 수익률 = 0.5×0.10 + 0.3×0.20 + 0.2×(−0.10) = 0.09.

    ⚠ 현재 구현은 weight를 무시하고 sum/len 단순평균(≈0.0667)을 반환한다.
      이 실패가 CORR-ENGINE-001의 증거다. 고치지 말고 그대로 둘 것.
    """
    _patch_market(
        monkeypatch,
        start_prices={'A': 100.0, 'B': 100.0, 'C': 100.0},
        end_prices={'A': 110.0, 'B': 120.0, 'C': 90.0},
    )
    gross, _, _ = _calc_period_return(None, {'A': 0.5, 'B': 0.3, 'C': 0.2}, START, END)
    assert gross == pytest.approx(0.09, abs=1e-12)


def test_equal_weight_case_weighted_and_simple_average_agree(monkeypatch):
    """등가중(1/N)에서는 가중평균 == 단순평균 — 현행 구현도 이 케이스는 옳다 (통과 유지)."""
    _patch_market(
        monkeypatch,
        start_prices={'A': 100.0, 'B': 100.0},
        end_prices={'A': 110.0, 'B': 90.0},
    )
    gross, _, _ = _calc_period_return(None, {'A': 0.5, 'B': 0.5}, START, END)
    assert gross == pytest.approx(0.0, abs=1e-12)


# ── O-3: 상폐 3시나리오 (base / optimistic / conservative) ──────────────────────

def test_single_delisted_stock_three_scenarios_invariants(monkeypatch):
    """
    상폐 종목 1개 100% 포트폴리오, 마지막 가격 80 (진입 100).
    계약 (engine.py DELISTING_HAIRCUT 주석 + SPEC_05):
      base           = last×H/start − 1                (haircut 청산)
      base + opt_adj = last/start − 1                  (haircut 없이 마지막 가격 전액 회수)
      base + cons_adj = −1.0                           (전액 손실)
    H = DELISTING_HAIRCUT (SSOT import — 값을 하드코딩하지 않는다).
    """
    _patch_market(
        monkeypatch,
        start_prices={'D': 100.0},
        end_prices={},
        delisted={'D'},
        last_prices={'D': 80.0},
    )
    gross, opt_adj, cons_adj = _calc_period_return(None, {'D': 1.0}, START, END)

    assert gross == pytest.approx(80.0 * DELISTING_HAIRCUT / 100.0 - 1.0, abs=1e-12)
    assert gross + opt_adj == pytest.approx(80.0 / 100.0 - 1.0, abs=1e-12)
    assert gross + cons_adj == pytest.approx(-1.0, abs=1e-12)


def test_delisting_adjustments_are_order_independent(monkeypatch):
    """
    ★ 핵심: 가격결측 종목(M)과 상폐 종목(D)이 동시에 존재할 때,
      두 종목의 순회 순서를 바꿔도 (gross, opt_adj, cons_adj)가 동일해야 한다.

    구성: M(진입가 결측), D(상폐, 진입 100, 마지막 80), N(정상 100→110).
    순회 순서 = dict 삽입 순서 = RIM 상승여력 정렬 순서이므로,
    이 값이 순서에 의존하면 "정렬 tie-break만 바꿔도 편입종목이 같은데 숫자가 바뀐다".

    ⚠ 현재 구현은 n(분모)을 순회 중에 감소시키므로 M이 D보다 앞에 오면 w=1/2,
      뒤에 오면 w=1/3로 opt/cons가 달라진다. 이 실패가 CORR-ENGINE-002의
      **재현 증거**다 (AUDIT_00 §5: 재현 시 P0-A). 고치지 말고 그대로 둘 것.
    """
    _patch_market(
        monkeypatch,
        start_prices={'D': 100.0, 'N': 100.0},   # M은 진입가 없음
        end_prices={'N': 110.0},
        delisted={'D'},
        last_prices={'D': 80.0},
    )
    w = 1.0 / 3.0
    result_m_first = _calc_period_return(None, {'M': w, 'D': w, 'N': w}, START, END)
    result_d_first = _calc_period_return(None, {'D': w, 'M': w, 'N': w}, START, END)

    assert result_m_first == pytest.approx(result_d_first, abs=1e-12)


def test_gross_return_itself_is_order_independent(monkeypatch):
    """gross(기준 수익률)만큼은 순서 무관해야 하고, 현행 구현도 그렇다 (통과 유지)."""
    _patch_market(
        monkeypatch,
        start_prices={'D': 100.0, 'N': 100.0},
        end_prices={'N': 110.0},
        delisted={'D'},
        last_prices={'D': 80.0},
    )
    w = 1.0 / 3.0
    gross_1, _, _ = _calc_period_return(None, {'M': w, 'D': w, 'N': w}, START, END)
    gross_2, _, _ = _calc_period_return(None, {'D': w, 'M': w, 'N': w}, START, END)
    assert gross_1 == pytest.approx(gross_2, abs=1e-12)


def test_empty_portfolio_returns_zero(monkeypatch):
    _patch_market(monkeypatch, start_prices={}, end_prices={})
    assert _calc_period_return(None, {}, START, END) == (0.0, 0.0, 0.0)
