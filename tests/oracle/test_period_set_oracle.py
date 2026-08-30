"""`period_set` 도입 오라클 — **옳음의 증명**.

`tests/oracle/` 은 깨지면 수정이 틀린 것이다 (characterization 과 혼동 금지).

여기서 고정하는 것:
  1. sha256 규약이 **직전 세션들과 동일**하다 — 규약이 갈리면 산출물 간 자동 대조가 깨진다
  2. `gate_analysis` 가 `--period-set` 없이는 **실행되지 않는다** (fail-closed)
  3. `period_set = full` 이 절단 없음과 **정확히** 같다 (회귀 안전)
  4. §직전 세션 규약의 메타 테스트 — 오라클이 프로덕션을 실제로 타는가

`[검증된 사실]` 2번이 이 파일의 존재 이유다. `gate_analysis` 는 전 구간에서 G1 을
스스로 다시 계산하므로, 인자 없이 돌리면 사전등록된 n=18 판정이 조용히 덮인다.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.analysis import period_set_lib as ga

A0 = Path('experiments/analysis/2026.08.25._xsec_prelim/a0_period_set.json')

#: 직전 세션들이 산출물에 박아 둔 값. **여기 적어 두면 규약 이탈이 즉시 드러난다.**
KNOWN = {
    'SPEC15_T19_primary':  '7b1d7489522f8747',
    'SPEC15_T18_g1compat': 'd318d475e89c438d',
}


# ── 1. sha256 규약 ──────────────────────────────────────────────────────────
def test_period_set_sha_matches_prior_sessions():
    """A-0 산출물·G5G2 산출물이 쓴 규약과 같아야 한다."""
    if not A0.exists():
        pytest.skip('A-0 산출물 없음')
    a0 = json.loads(A0.read_text(encoding='utf-8'))
    for key in ('period_set', 'period_set_t18'):
        blk = a0[key]
        assert ga.period_set_sha(blk['dates']) == blk['sha256'], (
            f"{blk['id']}: 규약 이탈 — 산출물의 sha256 과 다르다")
        assert blk['sha256'][:16] == KNOWN[blk['id']]


def test_sha_convention_is_sorted_lf_joined():
    """규약 자체를 고정한다: 정렬된 날짜 목록을 LF 로 join 한 것의 sha256."""
    dates = ['2020-01-02', '2019-05-05', '2021-12-31']
    expect = hashlib.sha256('\n'.join(sorted(dates)).encode()).hexdigest()
    assert ga.period_set_sha(dates) == expect
    # 정렬 불변 — 입력 순서가 해시에 새지 않는다
    assert ga.period_set_sha(list(reversed(dates))) == expect


def test_sha_detects_a_single_date_change():
    """음성 대조 — 하루만 달라도 해시가 갈려야 한다."""
    a = ['2020-01-02', '2020-07-02']
    b = ['2020-01-02', '2020-07-03']
    assert ga.period_set_sha(a) != ga.period_set_sha(b)


# ── 2. fail-closed ──────────────────────────────────────────────────────────
def test_gate_analysis_refuses_without_period_set():
    """**인자 없이 돌면 사전등록 판정이 덮인다.** argparse 가 거부해야 한다."""
    r = subprocess.run([sys.executable, '-m', 'scripts.robustness.gate_analysis',
                        '--f-tag', 'F_pbr_ma200_n13'],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    assert r.returncode != 0
    assert '--period-set' in (r.stderr + r.stdout)


def test_resolve_rejects_unknown_id():
    with pytest.raises(SystemExit):
        ga.resolve_period_set('NOT_A_REAL_ID')


# ── 3. full = 절단 없음 ─────────────────────────────────────────────────────
def test_full_means_no_truncation():
    ident, keep = ga.resolve_period_set('full')
    assert ident == 'full' and keep is None


def test_truncated_set_is_a_strict_subset_of_full():
    if not A0.exists():
        pytest.skip('A-0 산출물 없음')
    _i, keep19 = ga.resolve_period_set('SPEC15_T19_primary')
    _j, keep18 = ga.resolve_period_set('SPEC15_T18_g1compat')
    assert keep18 < keep19, 'T18 은 T19 의 진부분집합이어야 한다'
    assert keep19 - keep18 == {'2026-04-05'} or len(keep19 - keep18) == 1


# ── 4. 메타 — 오라클이 프로덕션을 타는가 ────────────────────────────────────
_META_TARGETS = [
    ('test_period_set_sha_matches_prior_sessions',
     'scripts.analysis.period_set_lib.period_set_sha'),
    ('test_sha_convention_is_sorted_lf_joined',
     'scripts.analysis.period_set_lib.period_set_sha'),
    ('test_full_means_no_truncation',
     'scripts.analysis.period_set_lib.resolve_period_set'),
    ('test_truncated_set_is_a_strict_subset_of_full',
     'scripts.analysis.period_set_lib.resolve_period_set'),
]


@pytest.mark.parametrize('test_name,target', _META_TARGETS)
def test_meta_oracle_actually_exercises_production(test_name, target, monkeypatch):
    """대상 프로덕션 함수를 `raise` 로 바꾸면 그 오라클이 **반드시 실패해야** 한다.

    **`META` 문자열을 반드시 요구한다** — 다른 이유의 실패를 통과로 세면 이 메타 테스트
    자체가 자기가 잡으려던 결함이 된다 (직전 세션에 실제로 그랬다).
    """
    mod_name, attr = target.rsplit('.', 1)
    mod = importlib.import_module(mod_name)

    def _boom(*a, **k):
        raise AssertionError(f'META: {target} 가 호출됐다')

    monkeypatch.setattr(mod, attr, _boom)
    here = sys.modules[__name__]
    if getattr(here, attr, None) is not None:
        monkeypatch.setattr(here, attr, _boom)

    with pytest.raises(Exception) as ei:
        globals()[test_name]()
    assert f'META: {target}' in str(ei.value), (
        f'{test_name} 이 {target} 를 타지 않는다 — 오라클이 형식뿐이다. '
        f'실제 예외: {type(ei.value).__name__}: {ei.value}')
