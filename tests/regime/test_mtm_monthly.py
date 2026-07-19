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


def test_nav_path_delegates_to_daily_nav(monkeypatch):
    """
    _nav_path는 daily_nav.nav_path_db 위임 래퍼다 (SPEC_09 NAV 경로 SSOT 이관,
    2026-07-19). 고정수량·haircut 동결·재정규화 불변식의 단위 검증은
    tests/oracle/test_daily_nav_oracle.py로 이식됐다.
    """
    called = {}

    def fake_nav_path_db(conn, weights, rebal_date, obs_dates):
        called['args'] = (weights, rebal_date, obs_dates)
        return [1.23]

    monkeypatch.setattr(mtm, 'nav_path_db', fake_nav_path_db)
    navs = mtm._nav_path(conn=None, weights={'A': 1.0},
                         rebal_date=date(2020, 4, 3), obs_dates=[date(2020, 5, 31)])
    assert navs == [1.23]
    assert called['args'] == ({'A': 1.0}, date(2020, 4, 3), [date(2020, 5, 31)])


def test_check_replication_gate_raises_on_mismatch():
    period = {'holdings': [{'ret': 0.10}, {'ret': 0.20}]}   # 평균 15%
    with pytest.raises(RuntimeError, match='복제 게이트 실패'):
        mtm._check_replication_gate('D_rim_only', date(2020, 4, 3), period, port_nav=[1.0, 1.0])  # 0%


def test_check_replication_gate_passes_within_tolerance():
    period = {'holdings': [{'ret': 0.10}, {'ret': 0.20}]}   # 평균 15%
    mtm._check_replication_gate('D_rim_only', date(2020, 4, 3), period, port_nav=[1.10, 1.1501])
