"""
characterization baseline — 승인된 정답이 아니라 기존 구현의 동작 기록.
버그 수정 시 정당하게 깨진다. 깨졌다고 자동으로 되돌리지 마라. (tests/characterization/README.md 참조)

tests/baselines/selection/{tag}.json(원시 float)을 입력으로 backtest/engine.py의 현재 산술
경로(gross return, turnover)를 재현해 tests/baselines/aggregate/{tag}.json과 대조한다.
DB 접속 없음 — fast suite(pytest -m "not integration") 대상.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backtest.engine import _calc_turnover

BASELINE_DIR  = Path(__file__).resolve().parents[2] / 'tests' / 'baselines'
SELECTION_DIR = BASELINE_DIR / 'selection'
AGGREGATE_DIR = BASELINE_DIR / 'aggregate'

TAGS = sorted(p.stem for p in SELECTION_DIR.glob('*.json')) if SELECTION_DIR.exists() else []


def _recompute_gross_return(holdings: list[dict]) -> float:
    """
    backtest/engine.py::_calc_period_return()의 산술 경로를 그대로 재현한다.
    DB 접근 없이 selection tape의 원시 entry/exit price와 weight만 사용한다.
    이건 오라클이 아니다 — "지금 코드가 뭘 하는지"의 기록이다. _calc_period_return()이
    바뀌면 이 함수도 같이 갱신해야 대조 의미가 유지된다.

    2026-07 감사(CORR-ENGINE-001) 이후 계약: weight 가중합 + 유효 종목 weight 합 재정규화.
    등가중 1/N에서는 종전 단순평균과 동일값이다.
    """
    valid = [
        h for h in holdings
        if h['entry_price'] is not None and h['entry_price'] > 0 and h['exit_price'] is not None
    ]
    if not valid:
        return 0.0
    total_w = sum(h['weight'] for h in valid)
    if total_w <= 0:
        return 0.0
    return sum(
        (h['weight'] / total_w) * (h['exit_price'] / h['entry_price'] - 1) for h in valid
    )


def _load(tag: str) -> tuple[dict, dict]:
    selection = json.loads((SELECTION_DIR / f'{tag}.json').read_text(encoding='utf-8'))
    aggregate = json.loads((AGGREGATE_DIR / f'{tag}.json').read_text(encoding='utf-8'))
    return selection, aggregate


@pytest.mark.parametrize('tag', TAGS)
def test_selection_tape_cross_check_recorded_at_capture_time(tag):
    """캡처 스크립트가 자체 기록한 교차검증 결과(재계산 vs engine 실제값)가 비어 있는지."""
    selection, _ = _load(tag)
    assert selection['cross_check_mismatches'] == [], (
        f"{tag}: characterize_baseline.py 캡처 시점에 이미 selection tape 재계산이 "
        f"engine 실제값과 불일치했다 — {selection['cross_check_mismatches']}"
    )


@pytest.mark.parametrize('tag', TAGS)
def test_selection_tape_reproduces_aggregate_gross_return(tag):
    selection, aggregate = _load(tag)
    agg_by_date = {p['rebalance_date']: p for p in aggregate['periods']}

    for period in selection['periods']:
        agg_period = agg_by_date[period['rebalance_date']]
        recomputed = _recompute_gross_return(period['holdings'])
        assert recomputed == pytest.approx(agg_period['gross_return'], abs=1e-9), (
            f"{tag} {period['rebalance_date']}: 재계산({recomputed}) != "
            f"baseline({agg_period['gross_return']})"
        )


@pytest.mark.parametrize('tag', TAGS)
def test_turnover_formula_matches_recorded_baseline(tag):
    """
    현재 turnover 산식(backtest.engine._calc_turnover, sold / max(len(prev), len(curr), 1))이
    baseline과 같은 값을 내는지. 이 산식 자체가 옳은지는 여기서 판단하지 않는다
    (CORR-METRIC-001 — 올바른 정의는 tests/oracle/에서 별도로 다룬다).
    """
    selection, aggregate = _load(tag)
    agg_by_date = {p['rebalance_date']: p for p in aggregate['periods']}

    prev_portfolio: dict[str, float] = {}
    for period in selection['periods']:
        curr_portfolio = {h['ticker']: h['weight'] for h in period['holdings']}
        recomputed = _calc_turnover(prev_portfolio, curr_portfolio)
        agg_period = agg_by_date[period['rebalance_date']]
        assert recomputed == pytest.approx(agg_period['turnover'], abs=1e-12), (
            f"{tag} {period['rebalance_date']}: turnover 재계산({recomputed}) != "
            f"baseline({agg_period['turnover']})"
        )
        prev_portfolio = curr_portfolio


@pytest.mark.parametrize('tag', TAGS)
def test_closed_period_baseline_excludes_open_period(tag):
    """AUDIT_01 Pass 0B 지시: closed_period baseline은 열린 마지막 구간(#23)을 포함하면 안 된다."""
    selection, _ = _load(tag)
    open_periods = [p for p in selection['periods'] if p['is_open_period']]
    assert len(open_periods) == 1, (
        f"{tag}: 열린 구간은 정확히 1개(REBALANCE_DATES 마지막)여야 한다 — 실제 {len(open_periods)}개"
    )
