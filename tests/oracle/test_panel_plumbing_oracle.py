"""SPEC_15 `S-2` — 배관 게이트 L-1a / L-1b.

`tests/oracle/` 은 깨지면 수정이 틀린 것이다 (characterization 과 혼동 금지).

**이 파일이 SPEC_15 전체의 관문이다.** 패널이 엔진과 같은 것을 재현하지 못하면
`S-3` 이후는 전부 무의미하다. 특히 **상폐 종목이 `dropna()` 로 조용히 사라지는 것**이
최대 실패 모드이며(§3-2 D-1), L-1a 가 그것을 잡는 유일한 장치다.

둘로 **분리**한다 (SPEC_13 EQ-1/EQ-2 선례):
  L-1a  편입 종목 **집합**   — 허용오차 **없음**
  L-1b  구간 수익률          — `5e-7` (tape `ret` 이 `round(..., 6)`)
하나로 묶고 허용오차를 주면 **멤버십 오류가 반올림 뒤에 숨는다.**

산출물이 없으면 skip 한다 — 패널은 섀도우에서 생성해 회수해야 한다.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pytest

from backtest.pipeline import _rank_key, supplement_to_minimum
from backtest.portfolio import build_portfolio

ROOT = Path('experiments/_baselines/shadow_20260819/experiments')
TAG  = 'F_pbr_ma200_n13'
N_STOCKS = 13
TOL_RET = 5e-7          # tape `ret` = round(..., 6) 의 반올림 반폭

PANEL = ROOT / 'panel' / f'panel_{TAG}_L3R.parquet'
TAPE  = ROOT / 'ablation' / f'{TAG}_holdings.json'

pytestmark = pytest.mark.skipif(
    not (PANEL.exists() and TAPE.exists()),
    reason=f'패널·tape 미회수 — {PANEL} / {TAPE}')


@lru_cache(maxsize=1)
def _load():
    import pandas as pd
    panel = pd.read_parquet(PANEL)
    tape  = {p['rebalance_date']: p for p in json.loads(TAPE.read_text(encoding='utf-8'))}
    return panel, tape


def _select_from_panel(panel, rebal_date: str) -> list[str]:
    """패널 → 채택안 규칙대로 상위 13.

    **프로덕션 SSOT 를 탄다** — 정렬은 `pipeline._rank_key`, 절단은
    `portfolio.build_portfolio`, 보완은 `pipeline.supplement_to_minimum` 이다.
    `[검증된 사실]` PBR 경로는 `rejected` 가 비고 `MIN_PORTFOLIO_STOCKS = 5 < 13` 이라
    보완은 무동작이지만, 경로를 건너뛰지 않고 **그대로 호출**한다 — 무동작임을
    가정하지 않고 실행으로 확인하는 편이 낫다.
    """
    rows = panel[panel['rebalance_date'] == rebal_date]
    candidates = [{'ticker': r.ticker, 'upside_pct': float(r.inv_pbr)}
                  for r in rows.itertuples()]
    candidates = supplement_to_minimum(candidates, [], rebal_date)
    candidates = sorted(candidates, key=_rank_key)
    return list(build_portfolio(candidates, n_stocks=N_STOCKS))


def _comparable_periods() -> list[str]:
    panel, tape = _load()
    have = set(panel['rebalance_date'].unique())
    return sorted(d for d, p in tape.items() if p['n_portfolio'] > 0 and d in have)


# ── L-1a 멤버십 (허용오차 없음) ──────────────────────────────────────────────
def test_l1a_membership_matches_tape_exactly():
    panel, tape = _load()
    dates = _comparable_periods()
    assert dates, '대조 가능한 구간이 없다'

    mismatches = []
    for d in dates:
        got  = set(_select_from_panel(panel, d))
        want = {h['ticker'] for h in tape[d]['holdings']}
        if got != want:
            # 개수가 아니라 **종목 동일성**으로 센다 (CLOSEOUT §8 교훈 2 —
            # 교체 1건이 대칭차집합에서 2로 세어진 사례)
            mismatches.append({'date': d, 'panel_only': sorted(got - want),
                               'tape_only': sorted(want - got)})
    assert not mismatches, f'L-1a 멤버십 불일치 {len(mismatches)}구간: {mismatches[:3]}'


def test_l1a_delisted_survive_in_panel():
    """**D-1 준수의 직접 증거.** tape 에 상폐로 표시된 종목이 패널에도 있어야 한다.

    상폐 종목이 `dropna()` 로 사라지면 L-1a 가 깨지기 전에 이 테스트가 먼저 말해준다
    — 무엇이 사라졌는지까지.
    """
    panel, tape = _load()
    missing = []
    for d in _comparable_periods():
        delisted = {h['ticker'] for h in tape[d]['holdings'] if h.get('delisted')}
        if not delisted:
            continue
        have = set(panel[panel['rebalance_date'] == d]['ticker'])
        gone = delisted - have
        if gone:
            missing.append({'date': d, 'gone': sorted(gone)})
    assert not missing, f'상폐 종목이 패널에서 사라졌다 (D-1 위반): {missing}'


# ── L-1b 수치 (5e-7) ────────────────────────────────────────────────────────
def test_l1b_period_return_matches_tape():
    panel, tape = _load()
    worst, offenders = 0.0, []
    for d in _comparable_periods():
        sel = _select_from_panel(panel, d)
        rows = panel[(panel['rebalance_date'] == d) & (panel['ticker'].isin(sel))]
        got_vals = [v for v in rows['fwd_ret'].tolist() if v is not None and v == v]
        want_vals = [h['ret'] for h in tape[d]['holdings'] if h['ret'] is not None]
        got  = sum(got_vals) / len(got_vals)
        want = sum(want_vals) / len(want_vals)
        diff = abs(got - want)
        worst = max(worst, diff)
        if diff > TOL_RET:
            offenders.append({'date': d, 'panel': got, 'tape': want, 'diff': diff})
    assert not offenders, f'L-1b 수익률 불일치 (최대 {worst:.3e} > {TOL_RET:.0e}): {offenders[:3]}'


def test_every_tape_period_is_comparable():
    """대조 불가 구간이 있으면 **사유와 함께 드러나야 한다.** 조용히 건너뛰지 않는다."""
    panel, tape = _load()
    have = set(panel['rebalance_date'].unique())
    active = {d for d, p in tape.items() if p['n_portfolio'] > 0}
    assert not (active - have), f'tape 에는 있으나 패널에 없는 구간: {sorted(active - have)}'


# ── §2-2 메타 테스트 — 오라클이 프로덕션을 타는가 ───────────────────────────
#: (테스트 함수, 반드시 발화시켜야 할 프로덕션 심볼). 이 파일이 만드는 오라클 전부.
_META_TARGETS = [
    ('test_l1a_membership_matches_tape_exactly', 'backtest.portfolio.build_portfolio'),
    ('test_l1a_membership_matches_tape_exactly', 'backtest.pipeline._rank_key'),
    ('test_l1b_period_return_matches_tape',      'backtest.portfolio.build_portfolio'),
    ('test_l1b_period_return_matches_tape',      'backtest.pipeline._rank_key'),
]


@pytest.mark.parametrize('test_name,target', _META_TARGETS)
def test_meta_oracle_actually_exercises_production(test_name, target, monkeypatch):
    """대상 프로덕션 함수를 `raise` 로 바꾸면 그 오라클이 **반드시 실패해야** 한다.

    `[검증된 사실]` 직전 세션 1차 음성 대조에서 MC 테스트 3개가 발화하지 않았다 —
    산식을 테스트 안에 다시 적어 프로덕션을 타지 않는 구조였기 때문이다.
    통과 테스트만 봤으면 못 잡았을 결함이라, 개별 수정이 아니라 **불변식**으로 둔다.

    `[Claude 의견]` monkeypatch 가 이 저장소에서 두 번 실패한 것은 *상류에 방어선이
    하나 더 있었기* 때문이다. 여기서는 "그 함수가 호출되는가" 만 보므로 그 함정에
    걸리지 않는다 — 호출되지 않으면 예외가 안 나고, 그것이 곧 판정이다.
    """
    mod_name, attr = target.rsplit('.', 1)
    import importlib
    mod = importlib.import_module(mod_name)

    def _boom(*a, **k):
        raise AssertionError(f'META: {target} 가 호출됐다')

    monkeypatch.setattr(mod, attr, _boom)
    # 이 파일이 import 시점에 바인딩한 이름도 함께 갈아끼운다
    import sys
    here = sys.modules[__name__]
    if getattr(here, attr, None) is not None:
        monkeypatch.setattr(here, attr, _boom)

    _load.cache_clear()
    fn = globals()[test_name]
    with pytest.raises(Exception) as ei:
        fn()
    # **`META` 문자열을 반드시 요구한다.** `or isinstance(..., AssertionError)` 를 함께 두면
    # 패치와 무관한 이유로 실패해도 통과해 버린다 — 그러면 이 메타 테스트 자체가
    # "형식뿐인 검사" 가 되어 자기가 잡으려던 것과 같은 결함이 된다.
    assert f'META: {target}' in str(ei.value), (
        f'{test_name} 이 {target} 를 타지 않는다 — 오라클이 형식뿐이다. '
        f'실제 예외: {type(ei.value).__name__}: {ei.value}')
    _load.cache_clear()
