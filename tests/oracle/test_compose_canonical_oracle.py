"""성적 표 조립 오라클 — **옳음의 증명**.

`tests/oracle/` 은 깨지면 수정이 틀린 것이다 (characterization 과 혼동 금지).

여기서 고정하는 것:
  1. fail-closed **5종이 실제로 발화한다** — 일부러 어긋난 입력을 주고 거부를 확인
  2. 조립기가 **값을 계산하지 않는다** — 출력의 모든 수가 등재 산출물에 그대로 있다
  3. 성적·게이트가 **같은 구간 집합**이다
  4. 메타 — 오라클이 프로덕션을 실제로 타는가 (조인 단언)

`[검증된 사실]` 1번이 이 파일의 존재 이유다. 이 저장소에서 검사기가 **자기가 잡으려던
결함을 자기가 갖고 있던** 사고가 두 번 있었다 (메타 테스트의 `or isinstance`,
`N-2b-1` 의 구조적 공허). 검사가 있다는 것과 검사가 발화한다는 것은 다른 사실이다.
"""
from __future__ import annotations

import ast
import copy
import importlib
import json
import sys
from pathlib import Path

import pytest

from scripts.analysis.baseline_registry import _load as _load_registry
from scripts.robustness import compose_canonical as cc
from scripts.robustness import compose_gate_results as cg


def _srcs():
    if not (cc.TRUNC_SRC.exists() and cc.G52_SRC.exists()):
        pytest.skip('등재 산출물 없음')
    return (json.loads(cc.TRUNC_SRC.read_bytes().decode('utf-8')),
            json.loads(cc.G52_SRC.read_bytes().decode('utf-8')))


def _write(tmp: Path, name: str, obj) -> Path:
    p = tmp / name
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    return p


def _registry_for(path: Path, sha: str | None = None) -> dict:
    """`path` 를 등재부에 올린 사본. sha 를 주면 그 값으로 등재한다."""
    from scripts.analysis.baseline_registry import file_digest
    reg = copy.deepcopy(_load_registry())
    reg['shared_inputs'][path.as_posix()] = {
        'sha256': sha if sha is not None else file_digest(path),
        'compatible_baselines': ['shadow_20260819'],
    }
    return reg


# ── 0. 정상 경로 ────────────────────────────────────────────────────────────
def test_compose_succeeds_on_registered_artifacts():
    r = cc.compose()
    assert r['period_set']['id'] == 'SPEC15_T18_g1compat'
    assert r['ablation_tag']['n_periods'] == 18


def test_composed_values_all_come_from_sources():
    """**조립기는 계산하지 않는다.** 출력의 모든 수가 출처에 그대로 있어야 한다."""
    t, g52 = _srcs()
    abl = cc.compose()['ablation_tag']
    m = t['cut18']['metrics']
    for k in ('n_periods', 'start', 'end', 'years', 'cagr', 'net_cagr', 'sharpe',
              'net_sharpe', 'mdd', 'robustness', 'benchmark_cagr', 'kosdaq_cagr',
              'alpha', 'alpha_kosdaq', 'avg_turnover'):
        assert abl[k] == m[k], f'{k}: 출처와 다르다 — 계산했을 수 있다'

    nav = cc.compose()['nav_tag']
    # 판정 층 — af529ff 의 G5 판정 블록 그대로여야 한다.
    jt, j = nav['judgment_t18'], g52['G5']['judgment']
    assert jt['net']['daily_mdd'] == j['daily_mdd_net']
    assert jt['net']['daily_sharpe'] == j['net_sharpe']
    assert jt['net']['n_days'] == j['n_days']
    # 판정 구간 일별 net CAGR 은 출처에 없다 — **메워 넣지 않았는지** 확인한다.
    assert jt['net_cagr'] is None
    # 전 구간 층 — 등재 섀도우 산출물 그대로여야 한다. 두 층이 섞이지 않았는가.
    full = json.loads(cc.NAV_SRC.read_bytes().decode('utf-8'))['tags'][cc.TAG]
    for k in ('net_cagr', 'gross_cagr', 'daily_mdd_gross', 'endpoint_mdd_gross'):
        assert nav[k] == full[k], f'{k}: 전 구간 층이 출처와 다르다'
    assert nav['net'] == full['net']
    assert jt['net']['daily_sharpe'] != nav['net']['daily_sharpe'],         '판정 층과 전 구간 층이 같은 값이다 — 층이 뭉개졌을 수 있다'


def test_performance_and_gate_share_one_period_set():
    r = cc.compose()
    assert r['period_set']['sha256'] == r['gate']['period_set']['sha256']
    assert r['nav_tag']['judgment_t18']['period_set'] == r['ablation_tag']['period_set']


# ── 1. 음성 대조 — 5종이 **발화하는가** ─────────────────────────────────────
def test_nc1_unregistered_input_is_refused(tmp_path):
    """1 — 등재부에 없는 파일이면 거부."""
    t, _ = _srcs()
    p = _write(tmp_path, 'not_registered.json', t)
    with pytest.raises(SystemExit) as ei:
        cc.compose(trunc_src=p)
    assert '등재부' in str(ei.value)


def test_nc1_sha_mismatch_is_refused(tmp_path):
    """1 — 등재는 됐지만 내용이 바뀌었으면 거부."""
    t, _ = _srcs()
    p = _write(tmp_path, 'tampered.json', t)
    reg = _registry_for(p, sha='0' * 64)
    with pytest.raises(SystemExit) as ei:
        cc.compose(trunc_src=p, registry=reg)
    assert 'sha256 불일치' in str(ei.value)


def test_nc1b_poisoned_input_is_refused(tmp_path):
    """1-b — 출처가 폐기 세대 요약 JSON 을 물고 있으면 거부."""
    t, _ = _srcs()
    bad = copy.deepcopy(t)
    bad['provenance']['inputs']['experiments/ablation/summary.json'] = {'sha256': 'x'}
    p = _write(tmp_path, 'poisoned_input.json', bad)
    with pytest.raises(SystemExit) as ei:
        cc.compose(trunc_src=p, registry=_registry_for(p))
    assert '_poisoned_summaries' in str(ei.value)


def test_nc2_missing_period_set_is_refused(tmp_path):
    """2 — period_set 블록이 없으면 거부 (역산으로 눈감아 주지 않는다)."""
    t, _ = _srcs()
    bad = copy.deepcopy(t)
    bad['cut18'].pop('period_set')
    p = _write(tmp_path, 'no_ps.json', bad)
    with pytest.raises(SystemExit) as ei:
        cc.compose(trunc_src=p, registry=_registry_for(p))
    assert 'period_set 블록이 없다' in str(ei.value)


def test_nc2_self_inconsistent_sha_is_refused(tmp_path):
    """2 — period_set 의 sha256 이 자기 dates 와 안 맞으면 거부."""
    t, _ = _srcs()
    bad = copy.deepcopy(t)
    bad['cut18']['period_set']['sha256'] = 'f' * 64
    p = _write(tmp_path, 'ps_corrupt.json', bad)
    with pytest.raises(SystemExit) as ei:
        cc.compose(trunc_src=p, registry=_registry_for(p))
    assert '자기 dates 와 불일치' in str(ei.value)


def test_nc2_name_content_disagreement_is_refused(tmp_path):
    """2 — **이름이 내용과 다르면 거부.** 이름을 믿지 않는다는 것의 실증."""
    t, _ = _srcs()
    bad = copy.deepcopy(t)
    bad['cut18']['period_set']['id'] = 'SPEC15_T19_primary'   # 내용은 T18 인데 이름만 T19
    p = _write(tmp_path, 'ps_mislabeled.json', bad)
    with pytest.raises(SystemExit) as ei:
        cc.compose(trunc_src=p, registry=_registry_for(p))
    assert '이름이 내용과 어긋난다' in str(ei.value)


def test_nc2_unregistered_period_set_is_refused(tmp_path):
    """2 — A-0 에 없는 집합이면 이름을 지어내지 않고 거부."""
    from scripts.analysis.period_set_lib import period_set_sha
    t, _ = _srcs()
    bad = copy.deepcopy(t)
    dates = bad['cut18']['period_set']['dates'][:-1]
    bad['cut18']['period_set'] = {'id': None, 'n': len(dates), 'dates': dates,
                                  'sha256': period_set_sha(dates)}
    p = _write(tmp_path, 'ps_unknown.json', bad)
    with pytest.raises(SystemExit) as ei:
        cc.compose(trunc_src=p, registry=_registry_for(p))
    assert 'A-0 등재부에 없다' in str(ei.value)


def test_nc3_period_count_mismatch_is_refused(tmp_path):
    """3 — 지표의 구간 수가 집합과 다르면 거부."""
    t, _ = _srcs()
    bad = copy.deepcopy(t)
    bad['cut18']['metrics']['n_periods'] = 19
    p = _write(tmp_path, 'n_mismatch.json', bad)
    with pytest.raises(SystemExit) as ei:
        cc.compose(trunc_src=p, registry=_registry_for(p))
    assert '구간 수 불일치' in str(ei.value)


def test_nc3_boundary_mismatch_is_refused(tmp_path):
    """3 — 시작 경계가 집합의 최초 앵커와 다르면 거부."""
    t, _ = _srcs()
    bad = copy.deepcopy(t)
    bad['cut18']['metrics']['start'] = '2016-04-05'
    p = _write(tmp_path, 'start_mismatch.json', bad)
    with pytest.raises(SystemExit) as ei:
        cc.compose(trunc_src=p, registry=_registry_for(p))
    assert '시작 경계 불일치' in str(ei.value)


def test_nc4_cross_value_mismatch_is_refused(tmp_path):
    """4 — 성적측과 게이트측의 F net CAGR 이 다르면 거부."""
    t, _ = _srcs()
    bad = copy.deepcopy(t)
    bad['cut18']['metrics']['net_cagr'] += 1e-9
    p = _write(tmp_path, 'value_drift.json', bad)
    with pytest.raises(SystemExit) as ei:
        cc.compose(trunc_src=p, registry=_registry_for(p))
    assert 'F net CAGR 불일치' in str(ei.value)


def test_nc5_different_period_set_is_refused():
    """5 (신설) — 성적과 게이트의 구간 집합이 다르면 거부.

    게이트측 sha 만 바꾼 사본을 주입한다. **한 문서에 두 세대**가 실리는 유일한 경로다.
    """
    gate = copy.deepcopy(cg.compose())
    gate['period_set'] = {**gate['period_set'], 'sha256': 'a' * 64}
    with pytest.raises(SystemExit) as ei:
        cc.compose(gate=gate)
    assert '구간 집합이 다르다' in str(ei.value)


# ── 2. §3-3 이름 기반 판정 금지 ─────────────────────────────────────────────
_NAME_BASED = {'endswith', 'startswith', 'fnmatch', 'fnmatchcase', 'glob', 'rglob'}
_COMPOSERS = ['scripts/robustness/compose_canonical.py',
              'scripts/robustness/compose_gate_results.py']


@pytest.mark.parametrize('path', _COMPOSERS)
def test_type_detection_is_not_name_based(path):
    """접미사·패턴 매칭으로 파일 종류를 판정하지 않는다 (§3-3 규칙).

    2026-08-29 에 `endswith('n13.json')` 이 추첨 풀 스냅샷을 요약 JSON 으로 오인했다.
    이 저장소에서 같은 유형이 **세 번째**였다 (H1 결측 서명 오독, `amount` 변동을
    정정으로 오독, 파일명 접미사로 계열 오독) — 세 번 다 내용 기반 판별자로 고쳐졌다.

    검사 자체가 문자열 매칭이면 주석·docstring 을 오탐한다. **AST 로 호출을 본다** —
    이 테스트가 강제하려는 그 원칙을 이 테스트가 먼저 지킨다.
    """
    tree = ast.parse(Path(path).read_text(encoding='utf-8'))
    hits = [f'{path}:{n.lineno} .{n.func.attr}()'
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr in _NAME_BASED]
    assert not hits, f'이름 기반 판별 호출: {hits}'


# ── 3. 메타 — 오라클이 프로덕션을 타는가 ────────────────────────────────────
_META_TARGETS = [
    ('test_compose_succeeds_on_registered_artifacts',
     'scripts.robustness.compose_canonical._check_period_set'),
    ('test_composed_values_all_come_from_sources',
     'scripts.robustness.compose_canonical._check_registered'),
    ('test_performance_and_gate_share_one_period_set',
     'scripts.robustness.compose_canonical._check_same_period_set'),
]


@pytest.mark.parametrize('test_name,target', _META_TARGETS)
def test_meta_oracle_actually_exercises_production(test_name, target, monkeypatch):
    """대상 프로덕션 함수를 `raise` 로 바꾸면 그 오라클이 **반드시 실패해야** 한다.

    **`META` 문자열을 반드시 요구한다** — 다른 이유의 실패를 통과로 세면 이 메타 테스트
    자체가 자기가 잡으려던 결함이 된다 (2026-08-26 세션에 실제로 그랬다).
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
