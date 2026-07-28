"""
승법 net 정의 오라클 — SPEC_13 §9-1 / §9-1a EQ-1.

QG1~3의 metric SSOT는 "일별 NAV와 동일한 승법 거래비용 정의"다. `run_random_pool`의
fast-path는 일별 경로를 만들지 않고 terminal NAV만 승법으로 누적하는데, 그 누적이
`daily_nav.stitch_periods()`의 net 정의와 **정확히 같아야** 한다(EQ-1 게이트가 실행
시점에 확인하는 것을 여기서 단위 수준으로 고정).

DB 미접속 — fast suite 대상.
"""
from __future__ import annotations

import pandas as pd

from backtest.daily_nav import stitch_periods
from scripts.robustness.run_random_pool import _net_cagr_from_growth

# (gross, tc) 구간 시퀀스 — 상승·하락·무변동·고비용 혼합
PERIODS = [(0.10, 0.005), (-0.20, 0.009), (0.00, 0.0), (0.35, 0.0103)]


def _fast_path_growth(periods) -> float:
    """run_random_pool.run_draws의 승법 누적과 동일 산식."""
    g = 1.0
    for gross, tc in periods:
        g *= (1.0 + ((1.0 - tc) * (1.0 + gross) - 1.0))
    return g


def test_multiplicative_net_matches_stitch_periods_definition():
    """EQ-1: fast-path 승법 누적 == stitch_periods의 net NAV 종점 (tol 1e-12).

    stitch_periods는 리밸런싱일에 `n *= (1−tc)` 차감 후 구간 경로 배수를 곱한다.
    구간 경로의 종점 배수가 (1+gross)이면 두 정의가 같아야 한다.
    """
    stitched = stitch_periods([
        {
            'rebalance_date':   pd.Timestamp(f'2020-01-{i + 1:02d}').date(),
            'obs_dates':        [pd.Timestamp(f'2020-02-{i + 1:02d}').date()],
            'nav_path':         [1.0 + gross],
            'transaction_cost': tc,
        }
        for i, (gross, tc) in enumerate(PERIODS)
    ])
    assert abs(_fast_path_growth(PERIODS) - float(stitched['nav_net'].iloc[-1])) < 1e-12


def test_multiplicative_net_differs_from_arithmetic_definition():
    """산술 net=gross−tc와는 **달라야** 한다 — 교차항 gross×tc 만큼.

    두 정의가 우연히 같아지면 §9-1의 정의 통일이 무의미해지므로 차이를 명시 고정한다.
    """
    arithmetic = 1.0
    for gross, tc in PERIODS:
        arithmetic *= (1.0 + (gross - tc))
    assert abs(_fast_path_growth(PERIODS) - arithmetic) > 1e-9


def test_zero_cost_reduces_to_pure_gross_compounding():
    free = [(g, 0.0) for g, _ in PERIODS]
    expected = 1.0
    for g, _ in free:
        expected *= (1.0 + g)
    assert abs(_fast_path_growth(free) - expected) < 1e-15


def test_net_cagr_from_growth_uses_calendar_days():
    """연수는 실제 캘린더 경과일/365.25 (CORR-METRIC-002, compute_cagr과 동일 정의)."""
    import datetime as dt
    span = {'start_date': dt.date(2016, 4, 5), 'end_date': dt.date(2026, 4, 3)}
    years = (span['end_date'] - span['start_date']).days / 365.25
    growth = 2.0
    assert abs(_net_cagr_from_growth(growth, span) - (growth ** (1 / years) - 1)) < 1e-15


def test_net_cagr_from_growth_without_span_returns_zero():
    """유효 구간이 하나도 없으면(span=None) 0.0 — 임의 값 날조 금지."""
    assert _net_cagr_from_growth(1.5, None) == 0.0
