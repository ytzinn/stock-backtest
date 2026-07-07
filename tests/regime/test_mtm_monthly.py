"""
SPEC_07 §11 — mtm_monthly.py 테스트.

DB 접속 없이 검증 가능한 로직(구간 정의, 고정수량 NAV, 상폐 haircut 동결)만 다룬다.
실제 DB 대조 복제 게이트(STEP A-2, JSON 'ret' vs MTM 누적수익 일치)는 서버에서
mtm_monthly.run() 최초 실행 시 '[A-2 게이트 통과]' 로그 여부로 확인한다 — 그 자체가
_check_replication_gate()의 실측 회귀 테스트다.
"""
from __future__ import annotations

from datetime import date

import pytest

from backtest.regime import mtm_monthly as mtm


def test_periods_start_at_2016_04_05_and_total_count():
    periods = mtm._periods()
    assert len(periods) == 21
    assert periods[0][0] == date(2016, 4, 5)


def test_last_period_is_open_and_uses_today_as_next_date():
    periods = mtm._periods()
    rebal, next_d, is_closed = periods[-1]
    assert rebal == date(2026, 4, 3)
    assert is_closed is False
    assert next_d == date.today()


def test_all_but_last_period_are_closed():
    periods = mtm._periods()
    assert all(is_closed for *_, is_closed in periods[:-1])


def test_obs_dates_appends_stub_when_period_end_not_a_month_end(monkeypatch):
    monkeypatch.setattr(mtm, 'month_end_dates',
                         lambda conn, s, e: [date(2020, 5, 31), date(2020, 6, 30)])
    obs = mtm._obs_dates(conn=None, rebal_date=date(2020, 4, 3), next_date=date(2020, 8, 20))
    assert obs == [date(2020, 5, 31), date(2020, 6, 30), date(2020, 8, 20)]


def test_obs_dates_no_duplicate_when_period_end_already_last_month_end(monkeypatch):
    monkeypatch.setattr(mtm, 'month_end_dates',
                         lambda conn, s, e: [date(2020, 5, 31), date(2020, 8, 20)])
    obs = mtm._obs_dates(conn=None, rebal_date=date(2020, 4, 3), next_date=date(2020, 8, 20))
    assert obs == [date(2020, 5, 31), date(2020, 8, 20)]


def test_nav_path_is_fixed_shares_not_monthly_rebalanced(monkeypatch):
    """
    ★ 핵심 회귀 — '초기 수량 고정' 검증 (SPEC_07 §7-2).
    A: 100 -> 200 -> 100 (+100%, -50%), B: 100(고정, 무변동).
    고정수량 50/50이면 두 자산 모두 시작가로 복귀 → 총수익 0%.
    월별 리밸런싱이었다면 1.5 * (1 + 0.5*(-0.5)) = 1.125 (+12.5%)로 달랐을 것 —
    이 테스트는 두 값이 다르다는 사실 자체로 '전략 불변' 불변식을 검증한다.
    """
    d0, d1, d2 = date(2020, 4, 3), date(2020, 5, 31), date(2020, 8, 20)
    prices = {
        d0: {'A': 100.0, 'B': 100.0},
        d1: {'A': 200.0, 'B': 100.0},
        d2: {'A': 100.0, 'B': 100.0},
    }
    monkeypatch.setattr(mtm, 'latest_close_batch',
                         lambda conn, tickers, d: {t: prices[d][t] for t in tickers})
    monkeypatch.setattr(mtm, 'is_delisted_at', lambda conn, ticker, d: False)

    navs = mtm._nav_path(conn=None, weights={'A': 0.5, 'B': 0.5}, rebal_date=d0, obs_dates=[d1, d2])

    assert navs[0] == pytest.approx(1.5)                       # 0.005*200 + 0.005*100
    assert navs[1] == pytest.approx(1.0)                       # 원금 복귀
    total_return = navs[-1] - 1.0
    assert total_return == pytest.approx(0.0, abs=1e-9)
    monthly_rebalanced_would_be = 1.5 * (1 + 0.5 * (-0.5))     # = 1.125, 참고용 반례
    assert total_return != pytest.approx(monthly_rebalanced_would_be - 1.0)


def test_delisted_ticker_haircut_applied_once_and_frozen(monkeypatch):
    """상폐는 최초 감지월에 1회만 haircut, 이후 관측월엔 그 값에 동결(반복 청산 금지)."""
    d0, d1, d2 = date(2022, 4, 5), date(2022, 5, 31), date(2022, 8, 18)
    calls = []

    def fake_last_known_price(conn, ticker, before_date):
        calls.append((ticker, before_date))
        return 69.0

    monkeypatch.setattr(mtm, 'latest_close_batch',
                         lambda conn, tickers, d: {t: 69.0 for t in tickers} if d == d0 else {})
    monkeypatch.setattr(mtm, 'is_delisted_at', lambda conn, ticker, d: d >= d1)
    monkeypatch.setattr(mtm, '_last_known_price', fake_last_known_price)

    navs = mtm._nav_path(conn=None, weights={'X': 1.0}, rebal_date=d0, obs_dates=[d1, d2])

    shares = 1.0 / 69.0
    expected = shares * (69.0 * mtm.DELISTING_HAIRCUT)
    assert navs[0] == pytest.approx(expected)
    assert navs[1] == pytest.approx(expected)      # 동결 — d2에서 재청산되지 않음
    assert len(calls) == 1                          # 최초 감지월 1회만 조회


def test_delisted_haircut_matches_066110_real_case(monkeypatch):
    """2026.07.07 재생성 홀딩스 JSON 실측치(066110, 한프): 진입 69 → 청산 48, -30.00%."""
    d0, d1 = date(2022, 4, 5), date(2022, 8, 18)
    monkeypatch.setattr(mtm, 'latest_close_batch', lambda conn, tickers, d: {t: 69.0 for t in tickers})
    monkeypatch.setattr(mtm, 'is_delisted_at', lambda conn, ticker, d: True)
    monkeypatch.setattr(mtm, '_last_known_price', lambda conn, ticker, d: 69.0)

    navs = mtm._nav_path(conn=None, weights={'066110': 1.0}, rebal_date=d0, obs_dates=[d1])

    ret = navs[-1] - 1.0
    assert ret == pytest.approx(-0.30, abs=1e-6)


def test_check_replication_gate_raises_on_mismatch():
    period = {'holdings': [{'ret': 0.10}, {'ret': 0.20}]}   # 평균 15%
    with pytest.raises(RuntimeError, match='복제 게이트 실패'):
        mtm._check_replication_gate('D_rim_only', date(2020, 4, 3), period, port_nav=[1.0, 1.0])  # 0%


def test_check_replication_gate_passes_within_tolerance():
    period = {'holdings': [{'ret': 0.10}, {'ret': 0.20}]}   # 평균 15%
    mtm._check_replication_gate('D_rim_only', date(2020, 4, 3), period, port_nav=[1.10, 1.1501])
