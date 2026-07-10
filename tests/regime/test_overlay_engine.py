"""
SPEC_08 §9 공식 목록엔 없지만, overlay_engine.py가 이 스펙에서 가장 복잡하고(always_on 게이트,
지연 반영 재계산) 리스크가 큰 모듈이라 회귀 테스트를 둔다. DB 접속 없이 monkeypatch로 검증한다.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from backtest.regime import overlay_engine as oe


def _fake_monthly_df():
    dates = pd.to_datetime([date(2020, 4, 30), date(2020, 5, 31), date(2020, 8, 20)])
    return pd.DataFrame({
        'period_start': [date(2020, 4, 3)] * 2 + [date(2020, 8, 20)],
        'period_end': [date(2020, 8, 20)] * 3,
        'is_closed_period': [True, True, True],
        'port_return': [0.05, 0.02, 0.03],
        'largecap_cw_return': [0.01, 0.00, 0.02],
        'largecap_ew_return': [0.01, 0.01, 0.02],
        'kospi_return': [0.005, 0.00, 0.01],
    }, index=dates)


def test_compute_always_on_series_matches_port_return_when_s_neutral_is_one():
    """always_on(s_neutral=1.0) 정의상 port_return과 완전히 동일해야 한다 — §3-14 게이트 전제."""
    df = _fake_monthly_df()
    always_on = oe.compute_always_on_series(df, 'largecap_cw', s_neutral=1.0)
    pd.testing.assert_series_equal(always_on, df['port_return'], check_names=False)


def test_compute_always_on_series_blends_when_s_neutral_below_one():
    df = _fake_monthly_df()
    always_on = oe.compute_always_on_series(df, 'largecap_cw', s_neutral=0.8)
    expected = 0.8 * df['port_return'] + 0.2 * df['largecap_cw_return']
    pd.testing.assert_series_equal(always_on, expected, check_names=False)


def test_check_always_on_gate_passes_silently_when_consistent():
    df = _fake_monthly_df()
    oe.check_always_on_gate(df, 'largecap_cw')   # 예외 없으면 통과


def test_check_always_on_gate_raises_if_formula_broken(monkeypatch):
    """★ 핵심 회귀 — 블렌딩 공식이 깨지면(부호/컬럼 오류) 반드시 여기서 잡혀야 한다."""
    df = _fake_monthly_df()
    monkeypatch.setattr(oe, 'compute_always_on_series', lambda *a, **k: df['port_return'] + 1.0)
    with pytest.raises(RuntimeError, match='always_on 게이트 실패'):
        oe.check_always_on_gate(df, 'largecap_cw')


def test_decision_dates_for_period_monthly_quarterly_semiannual(monkeypatch):
    month_ends = [date(2020, 4, 30), date(2020, 5, 31), date(2020, 6, 30),
                  date(2020, 7, 31), date(2020, 8, 18)]
    monkeypatch.setattr(oe, 'month_end_dates', lambda conn, s, e: month_ends)

    rebal_date, next_date = date(2020, 4, 3), date(2020, 8, 18)
    assert oe._decision_dates_for_period(None, rebal_date, next_date, 'monthly') == month_ends
    assert oe._decision_dates_for_period(None, rebal_date, next_date, 'quarterly') == \
        [date(2020, 4, 30), date(2020, 7, 31)]
    assert oe._decision_dates_for_period(None, rebal_date, next_date, 'semiannual') == [rebal_date]


def test_kospi_nav_path_compounds_returns(monkeypatch):
    fake_rets = {(date(2020, 1, 1), date(2020, 2, 1)): 0.10, (date(2020, 2, 1), date(2020, 3, 1)): -0.05}
    monkeypatch.setattr(oe, 'kospi_return', lambda s, e: fake_rets[(s, e)])
    navs = oe._kospi_nav_path(date(2020, 1, 1), [date(2020, 2, 1), date(2020, 3, 1)])
    assert navs[0] == pytest.approx(1.10)
    assert navs[1] == pytest.approx(1.10 * 0.95)


def test_period_tilt_rows_turnover_and_bounds(monkeypatch):
    """
    ★ 핵심 회귀 — s_t가 실제로 turnover/cost/port_return 계산에 쓰이는지 확인
    (MAX_STOCK_WEIGHT 폐기 사례처럼 '계산은 되는데 안 쓰이는' 죽은 파라미터 방지).
    """
    d0, d1, d2, d3 = date(2020, 4, 5), date(2020, 5, 4), date(2020, 6, 1), date(2020, 8, 19)
    monkeypatch.setattr(oe, 'month_end_dates',
                         lambda conn, s, e: [date(2020, 4, 30), date(2020, 5, 31)])
    monkeypatch.setattr(oe, 'next_trading_day',
                         lambda conn, d: {date(2020, 4, 3): d0, date(2020, 4, 30): d1,
                                           date(2020, 5, 31): d2, date(2020, 8, 20): d3}[d])
    monkeypatch.setattr(oe, 'load_period_holdings',
                         lambda tag, rebal: {'holdings': [{'ticker': 'A'}, {'ticker': 'B'}]})
    monkeypatch.setattr(oe, 'nav_path', lambda conn, weights, buy, obs: [1.05, 1.10])
    monkeypatch.setattr(oe, 'build_largecap_sleeve', lambda conn, rebal: ({'X': 1.0}, {'X': 1.0}))

    value_spread_z = pd.Series([2.0, -2.0], index=pd.to_datetime([date(2020, 4, 30), date(2020, 5, 31)]))

    rows = oe._period_tilt_rows(
        conn=None, tag='D_rim_only', rebal_date=date(2020, 4, 3), next_date=date(2020, 8, 20),
        is_closed=True, overlay_freq='monthly', alt_sleeve='largecap_cw',
        value_spread_z=value_spread_z, size_mom_z=None, variant='D_v1',
        s_neutral=1.0, k=0.15, s_min=0.5, s_max=1.0,
    )

    assert len(rows) == 2
    # 첫 결정: z=2.0(양수) -> 옵션A 관례상 s_neutral=1.0에서 clamp돼 그대로 1.0
    assert rows[0]['s_t'] == pytest.approx(1.0)
    assert rows[0]['overlay_turnover'] == pytest.approx(0.0)   # s_neutral(1.0)에서 시작 -> 변화 없음
    # 두번째 결정: z=-2.0 -> s_t = clamp(1.0 - 0.15*2.0, 0.5, 1.0) = 0.70
    assert rows[1]['s_t'] == pytest.approx(0.70)
    assert rows[1]['overlay_turnover'] == pytest.approx(abs(0.70 - 1.0))
    assert all(0.5 <= r['s_t'] <= 1.0 for r in rows)
    # port_return이 s_t에 실제로 의존하는지 확인 — s_t=1.0일 땐 순수 소형가치 수익률과 같아야 함
    assert rows[0]['port_return'] == pytest.approx(1.05 / 1.0 - 1)
