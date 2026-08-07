"""
SPEC_14 §14-1 — RIM 밸류에이션 컷을 랭킹과 분리한 스위치의 오라클.

핵심은 **기존 태그가 하나도 안 바뀌는 것**이다. `rim_cut` 키가 없으면 각 경로는
종전 동작을 그대로 쓴다 — RIM 경로는 컷 0.05, PBR 경로는 컷 없음.
"""
from __future__ import annotations

from datetime import date

import pytest

from backtest.ablation import (
    ABLATION_CONFIGS,
    _PBRRankPipeline,
    build_ablation_pipeline,
)
from backtest.pipeline import BacktestPipeline, passes_rim_cut, supplement_to_minimum
from backtest.portfolio import MIN_PORTFOLIO_STOCKS


# ── 컷 판정 규칙 (SSOT) ──────────────────────────────────────────────────────

def test_passes_rim_cut_threshold_semantics():
    # FV 100억, 컷 5% → 시총 105억까지 통과
    assert passes_rim_cut(100.0, 105.0, 0.05)
    assert not passes_rim_cut(100.0, 105.001, 0.05)


def test_passes_rim_cut_none_means_no_cut():
    """None 은 '컷 없음'이다 — 어떤 시총이어도 통과."""
    assert passes_rim_cut(100.0, 10_000.0, None)


def test_passes_rim_cut_none_handles_zero_fv():
    """FV=0 종목도 컷 없음이면 통과해야 한다.

    `fv * (1 + inf)` 우회로를 썼다면 `0 × inf = nan` → 비교 False → 조용히 탈락한다.
    이 테스트가 그 우회로를 막는다.
    """
    assert passes_rim_cut(0.0, 1_000.0, None)
    assert not passes_rim_cut(0.0, 1_000.0, 0.05)


# ── 보완 로직 ────────────────────────────────────────────────────────────────

def _item(t: str, upside: float) -> dict:
    return {'ticker': t, 'upside_pct': upside}


def test_supplement_fires_only_below_minimum():
    passed = [_item(f'{i:06d}', 10.0) for i in range(MIN_PORTFOLIO_STOCKS)]
    rejected = [_item('999999', 5.0)]
    assert supplement_to_minimum(passed, rejected, date(2020, 4, 1)) == passed


def test_supplement_picks_by_rank_key():
    passed = [_item('000001', 50.0)]
    rejected = [_item('000010', 1.0), _item('000020', 9.0), _item('000030', 9.0),
                _item('000040', 4.0), _item('000050', 3.0), _item('000060', 2.0)]
    out = supplement_to_minimum(passed, rejected, date(2020, 4, 1))
    assert len(out) == MIN_PORTFOLIO_STOCKS
    # upside 내림차순, 동률은 ticker 오름차순 (_rank_key). 최하위 000010 은 안 뽑힌다
    assert [x['ticker'] for x in out] == ['000001', '000020', '000030', '000040', '000050']


def test_supplement_takes_all_when_rejected_is_short():
    """보완 후보가 모자라면 있는 만큼만 채운다 — 예외를 던지지 않는다."""
    passed = [_item('000001', 50.0)]
    out = supplement_to_minimum(passed, [_item('000010', 1.0)], date(2020, 4, 1))
    assert [x['ticker'] for x in out] == ['000001', '000010']


def test_supplement_noop_when_no_rejected():
    """컷이 꺼져 있으면 rejected 가 비므로 보완이 절대 발화하지 않는다."""
    passed = [_item('000001', 50.0)]
    assert supplement_to_minimum(passed, [], date(2020, 4, 1)) == passed


# ── 기존 태그 불변 (회귀 방지) ───────────────────────────────────────────────

@pytest.mark.parametrize('tag', ['F_pbr_no_r3r4', 'F_pbr_r6', 'F_pbr_only',
                                 'F_pbr_no_r3r4_parent', 'D_pbr_only'])
def test_existing_pbr_tags_keep_no_cut(tag):
    p = build_ablation_pipeline(tag, ABLATION_CONFIGS[tag])
    assert isinstance(p, _PBRRankPipeline)
    assert p.rim_threshold is None, f'{tag}: PBR 경로에 없던 컷이 켜졌다'


@pytest.mark.parametrize('tag', ['F_momentum_rim', 'F_no_r3r4', 'D_rim_only', 'G_full'])
def test_existing_rim_tags_keep_default_cut(tag):
    p = build_ablation_pipeline(tag, ABLATION_CONFIGS[tag])
    assert type(p) is BacktestPipeline
    assert p.rim_threshold == 0.05, f'{tag}: RIM 컷 문턱이 바뀌었다'


# ── 신규 2×2 셀 ─────────────────────────────────────────────────────────────

def test_rimrank_cell_has_rim_ranking_without_cut():
    p = build_ablation_pipeline('F_rimrank_no_r3r4',
                                ABLATION_CONFIGS['F_rimrank_no_r3r4'])
    assert type(p) is BacktestPipeline      # RIM upside 랭킹 경로
    assert p.rim_threshold is None          # 컷 없음 → 인컴번트와 스코어 함수만 다르다


def test_pbr_rimcut_cell_has_pbr_ranking_with_cut():
    p = build_ablation_pipeline('F_pbr_no_r3r4_rimcut',
                                ABLATION_CONFIGS['F_pbr_no_r3r4_rimcut'])
    assert isinstance(p, _PBRRankPipeline)  # 랭킹은 1/PBR 그대로
    assert p.rim_threshold == 0.05          # 컷만 켬


def test_2x2_shares_filter_stack_with_incumbent():
    """네 칸이 같은 필터 스택(HARD·Stability{R1,R2,R5,R6}·모멘텀)을 써야 2×2 가 성립한다."""
    tags = ['F_pbr_no_r3r4', 'F_rimrank_no_r3r4', 'F_pbr_no_r3r4_rimcut', 'F_no_r3r4']
    stacks = []
    for t in tags:
        p = build_ablation_pipeline(t, ABLATION_CONFIGS[t])
        stacks.append([type(f).__name__ for f in p.filters])
    assert all(s == stacks[0] for s in stacks), dict(zip(tags, stacks))
    rules = [ABLATION_CONFIGS[t].get('stability_rules') for t in tags]
    assert all(r == {'R1', 'R2', 'R5', 'R6'} for r in rules), rules
