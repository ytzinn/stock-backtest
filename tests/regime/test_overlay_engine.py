"""
SPEC_08 §9 공식 목록엔 없지만, overlay_engine.py가 이 스펙에서 가장 복잡하고(always_on 게이트,
지연 반영 재계산) 리스크가 큰 모듈이라 회귀 테스트를 둔다. DB 접속 없이 monkeypatch로 검증한다.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from backtest.regime import overlay_engine as oe


@pytest.fixture(autouse=True)
def _clear_overlay_caches():
    """run_combo()의 216 조합 재계산 캐시가 테스트 간에 새지 않도록 매 테스트 전 초기화."""
    oe.clear_caches()
    yield
    oe.clear_caches()


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *a, **k):
        pass

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)


def test_load_base_monthly_period_start_matches_timestamp_comparison():
    """
    ★ 핵심 회귀 — period_start가 datetime.date(object dtype)로 남아있으면
    `df['period_start'] == pd.Timestamp(rebal_date)`가 실제로 같은 날짜여도 전부 False가
    되어 run_combo()의 always_on 분기가 행을 0건만 저장하고도 에러 없이 조용히 넘어간다
    (실제 서버 실행에서 재현됨).
    """
    rows = [
        (date(2020, 4, 30), date(2020, 4, 3), date(2020, 8, 20), True, 0.01, 0.02, 0.02, 0.01),
    ]
    df = oe.load_base_monthly(_FakeConn(rows), mtm_run_id='mtm_v1', scenario='D_rim_only')
    matched = df[df['period_start'] == pd.Timestamp(date(2020, 4, 3))]
    assert len(matched) == 1


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
    """★ 반드시 rebal_date로 시작 — 첫 return interval의 s_t를 포트폴리오 형성 시점 신호로
    결정하기 위함(실제 서버에서 이게 빠져 첫 구간 수익이 통째로 누락되던 버그 수정)."""
    month_ends = [date(2020, 4, 30), date(2020, 5, 31), date(2020, 6, 30),
                  date(2020, 7, 31), date(2020, 8, 18)]
    monkeypatch.setattr(oe, 'month_end_dates', lambda conn, s, e: month_ends)

    rebal_date, next_date = date(2020, 4, 3), date(2020, 8, 18)
    assert oe._decision_dates_for_period(None, rebal_date, next_date, 'monthly') == \
        [rebal_date] + month_ends
    assert oe._decision_dates_for_period(None, rebal_date, next_date, 'quarterly') == \
        [rebal_date, date(2020, 4, 30), date(2020, 7, 31)]
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
    decision_dates는 rebal_date로 시작해야 하므로(첫 구간의 s_t는 포트폴리오 형성 시점
    신호로 결정) 3개 결정(rebal_date, 4월말, 5월말)이 나온다.
    """
    d0, d1, d2, d3 = date(2020, 4, 5), date(2020, 5, 4), date(2020, 6, 1), date(2020, 8, 19)
    monkeypatch.setattr(oe, 'month_end_dates',
                         lambda conn, s, e: [date(2020, 4, 30), date(2020, 5, 31)])
    monkeypatch.setattr(oe, 'next_trading_day',
                         lambda conn, d: {date(2020, 4, 3): d0, date(2020, 4, 30): d1,
                                           date(2020, 5, 31): d2, date(2020, 8, 20): d3}[d])
    monkeypatch.setattr(oe, 'load_period_holdings',
                         lambda tag, rebal: {'holdings': [{'ticker': 'A'}, {'ticker': 'B'}]})
    monkeypatch.setattr(oe, 'nav_path', lambda conn, weights, buy, obs: [1.02, 1.05, 1.10])
    monkeypatch.setattr(oe, 'build_largecap_sleeve', lambda conn, rebal: ({'X': 1.0}, {'X': 1.0}))

    value_spread_z = pd.Series(
        [0.0, 2.0, -2.0],
        index=pd.to_datetime([date(2020, 4, 3), date(2020, 4, 30), date(2020, 5, 31)]),
    )

    rows = oe._period_tilt_rows(
        conn=None, tag='D_rim_only', rebal_date=date(2020, 4, 3), next_date=date(2020, 8, 20),
        is_closed=True, overlay_freq='monthly', alt_sleeve='largecap_cw',
        value_spread_z=value_spread_z, size_mom_z=None, variant='D_v1',
        s_neutral=1.0, k=0.15, s_min=0.5, s_max=1.0,
    )

    assert len(rows) == 3
    # 첫 결정(rebal_date): z=0.0 -> s_t = clamp(1.0 + 0, ...) = 1.0, s_neutral에서 시작이라 turnover=0
    assert rows[0]['s_t'] == pytest.approx(1.0)
    assert rows[0]['overlay_turnover'] == pytest.approx(0.0)
    # 둘째 결정: z=2.0(양수) -> 옵션A 관례상 s_neutral=1.0에서 clamp돼 그대로 1.0
    assert rows[1]['s_t'] == pytest.approx(1.0)
    # 셋째 결정: z=-2.0 -> s_t = clamp(1.0 - 0.15*2.0, 0.5, 1.0) = 0.70
    assert rows[2]['s_t'] == pytest.approx(0.70)
    assert rows[2]['overlay_turnover'] == pytest.approx(abs(0.70 - 1.0))
    assert all(0.5 <= r['s_t'] <= 1.0 for r in rows)
    # port_return이 s_t에 실제로 의존하는지 확인 — s_t=1.0일 땐 순수 소형가치 수익률과 같아야 함
    assert rows[0]['port_return'] == pytest.approx(1.02 / 1.0 - 1)


def test_period_tilt_rows_skips_when_execution_date_unresolved(monkeypatch):
    """진행 중인 구간(#23류)은 next_date=오늘이라 그 이후 거래일이 없어 next_trading_day가
    None을 반환할 수 있다 — None이 nav_path에 섞여 0으로-나누기 사고로 번지기 전에 건너뛴다."""
    monkeypatch.setattr(oe, 'month_end_dates', lambda conn, s, e: [date(2026, 4, 30)])
    monkeypatch.setattr(oe, 'next_trading_day', lambda conn, d: None)   # 미래 거래일 없음

    rows = oe._period_tilt_rows(
        conn=None, tag='D_rim_only', rebal_date=date(2026, 4, 3), next_date=date(2026, 7, 10),
        is_closed=False, overlay_freq='monthly', alt_sleeve='largecap_cw',
        value_spread_z=pd.Series(dtype=float), size_mom_z=None, variant='D_v1',
        s_neutral=1.0, k=0.15, s_min=0.5, s_max=1.0,
    )
    assert rows == []


# ── grid.py 216 조합 재계산 캐시 회귀 테스트 ──────────────────────────────────
# (grid.py가 1분/조합 x 216 = 3~4시간 걸리던 걸 줄이려고 넣은 캐시 — 조합-불변 값을
# 실제로 재사용하는지, 그리고 다른 키에는 새로 계산하는지 둘 다 확인한다.)

def test_get_z_series_computes_once_per_normalization(monkeypatch):
    calls = []
    monkeypatch.setattr(oe, 'load_indicator_series', lambda conn, run_id, ind: pd.Series([1.0, 2.0]))
    monkeypatch.setattr(oe, 'compute_z', lambda series, norm, *a: calls.append(norm) or pd.Series([norm]))

    oe._get_z_series(None, 'value_spread', 'expanding_z')
    oe._get_z_series(None, 'value_spread', 'expanding_z')   # 캐시 히트 -> compute_z 재호출 안 됨
    oe._get_z_series(None, 'value_spread', 'rolling_pct_60m')   # 다른 정규화 -> 새로 계산

    assert calls == ['expanding_z', 'rolling_pct_60m']


def test_get_small_navs_computes_once_per_tag_rebal_dates(monkeypatch):
    calls = []
    monkeypatch.setattr(oe, 'load_period_holdings',
                         lambda tag, rebal: {'holdings': [{'ticker': 'A'}]})

    def fake_nav_path(conn, weights, buy, obs):
        calls.append((buy, tuple(obs)))
        return [1.1, 1.2]

    monkeypatch.setattr(oe, 'nav_path', fake_nav_path)

    exec_dates = [date(2020, 5, 4)]
    obs_dates = [date(2020, 6, 1), date(2020, 8, 19)]
    oe._get_small_navs(None, 'D_rim_only', date(2020, 4, 3), exec_dates, obs_dates)
    oe._get_small_navs(None, 'D_rim_only', date(2020, 4, 3), exec_dates, obs_dates)   # 캐시 히트
    oe._get_small_navs(None, 'F_momentum_rim', date(2020, 4, 3), exec_dates, obs_dates)  # tag 다름 -> 재계산

    assert len(calls) == 2


def test_get_alt_navs_shared_across_scenarios(monkeypatch):
    """alt_navs는 scenario조차 무관하므로 D/F가 같은 (alt_sleeve,rebal_date,dates)면 캐시를 공유해야 한다."""
    calls = []
    monkeypatch.setattr(oe, 'build_largecap_sleeve', lambda conn, rebal: ({'X': 1.0}, {'X': 1.0}))
    monkeypatch.setattr(oe, 'nav_path', lambda conn, weights, buy, obs: calls.append(1) or [1.05])

    exec_dates = [date(2020, 5, 4)]
    obs_dates = [date(2020, 6, 1)]
    oe._get_alt_navs(None, 'largecap_cw', date(2020, 4, 3), exec_dates, obs_dates)
    oe._get_alt_navs(None, 'largecap_cw', date(2020, 4, 3), exec_dates, obs_dates)

    assert len(calls) == 1


def test_clear_caches_forces_recomputation(monkeypatch):
    calls = []
    monkeypatch.setattr(oe, 'load_indicator_series', lambda conn, run_id, ind: pd.Series([1.0]))
    monkeypatch.setattr(oe, 'compute_z', lambda series, norm, *a: calls.append(norm) or pd.Series([norm]))

    oe._get_z_series(None, 'value_spread', 'expanding_z')
    oe.clear_caches()
    oe._get_z_series(None, 'value_spread', 'expanding_z')

    assert calls == ['expanding_z', 'expanding_z']
