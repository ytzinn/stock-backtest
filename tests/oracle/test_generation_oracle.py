"""세대 판별자 오라클 — **옳음의 증명**.

`tests/oracle/` 은 깨지면 수정이 틀린 것이다 (characterization 과 혼동 금지).

여기서 고정하는 것:
  1. **재오염을 잡는다** — 같은 경로에 `run_ablation` 산출이 앉으면 차단
  2. **거짓 양성을 안 낸다** — 등재 섀도우본은 폐기본과 경로 문자열이 같아도 통과
  3. **미분류는 막는다** (fail-closed) — "모르면 깨끗함" 금지
  4. 판별자 D1~D4 가 **각각** 자기 대조를 잡고 있다
  5. **검사기 자신이 규칙을 지킨다** — 경로 문자열로 판정하지 않는다 (AST)
  6. 메타 — 오라클이 프로덕션을 실제로 타는가 (조인 단언)

`[검증된 사실]` 5번이 이 파일에 있는 이유: 직전 세션에서 **이름 기반 판정 금지 검사기가
이름 기반으로 구현**됐다(문자열 매칭이 docstring 인용을 오탐). 이 저장소의 자기지시
결함 세 번째였다. **원칙을 강제하는 도구는 그 원칙의 첫 번째 적용 대상이다.**
"""
from __future__ import annotations

import ast
import copy
import importlib
import json
import sys
from pathlib import Path

import pytest

from scripts.analysis import generation as G
from scripts.analysis.baseline_registry import _load, file_digest

SCRATCH = Path('experiments/analysis/2026.08.29._plumbing')
COMPOSED = Path('experiments/ablation/F_pbr_ma200_n13.json')
REGISTERED_CSV = Path('experiments/ablation/F_pbr_ma200_n13_periods.csv')
SHADOW_NAV = Path('experiments/_baselines/shadow_20260819/'
                  'experiments/daily_nav/summary.json')


def _reg():
    return _load()


def _classify(path: Path, registry=None):
    return G.classify(Path(path), registry=registry or _reg(), digest_fn=file_digest)


def _copy(src: Path, tmp: Path, name: str) -> Path:
    if not src.exists():
        pytest.skip(f'{src} 없음')
    p = tmp / name
    p.write_bytes(src.read_bytes())
    return p


# ── 1. 재오염을 잡는가 (이 전환의 본 대조) ──────────────────────────────────
def test_reinfection_of_a_composed_path_is_caught(tmp_path):
    """조립분 경로에 `run_ablation` 산출이 앉으면 차단해야 한다.

    **경로는 그대로다.** 경로 기반 판별자는 두 상태를 구분하지 못한다 — 그것이
    이 전환의 이유 전부다.
    """
    good = _copy(COMPOSED, tmp_path, 'same_path.json')
    assert _classify(good).verdict == 'composed'

    bad = _copy(SCRATCH / 'scratch_F_pbr_ma200_n13.json', tmp_path, 'same_path.json')
    v = _classify(bad)
    assert not v.clean, '재오염이 통과했다 — 판별자가 무력하다'
    assert v.verdict == 'unregistered' and 'D3' in v.by


def test_reinfection_of_a_registered_input_is_caught(tmp_path):
    """등재 입력이 재실행 산출로 덮이면 차단해야 한다.

    `run_ablation` 은 `{tag}_periods.csv` 를 **항상 함께** 덮어쓴다. 등록부가 막으려고
    만들어진 바로 그 사고이고, 경로 기반 판별자는 이 경로를 목록에 갖고 있지 않아
    **통과시켰다.**
    """
    good = _copy(REGISTERED_CSV, tmp_path, 'p.csv')
    assert _classify(good).verdict == 'registered'

    bad = _copy(SCRATCH / 'scratch_F_pbr_ma200_n13_periods.csv', tmp_path, 'p.csv')
    assert not _classify(bad).clean, '등재본 덮어쓰기가 통과했다'


# ── 2. 거짓 양성을 안 내는가 ────────────────────────────────────────────────
def test_registered_shadow_copy_passes_despite_sharing_the_poisoned_path():
    """등재 섀도우본은 폐기본과 **상대경로 문자열이 같다.** 내용으로 갈라야 한다."""
    if not SHADOW_NAV.exists():
        pytest.skip('등재 섀도우본 없음')
    rel = 'experiments/daily_nav/summary.json'
    from scripts.analysis.baseline_registry import poisoned_summaries
    assert rel in poisoned_summaries(), '전제가 깨졌다 — 이 경로가 폐기 목록에 없다'
    assert _classify(SHADOW_NAV).verdict == 'registered'


def test_composed_and_registered_artifacts_are_clean():
    for p in (COMPOSED, REGISTERED_CSV,
              Path('experiments/robustness/gate_results_F_pbr_ma200_n13.json'),
              Path('experiments/analysis/2026.08.24._period_truncation/'
                   'truncation_results_shadow_20260819.json')):
        if p.exists():
            assert _classify(p).clean, f'{p} 가 오분류됐다'


def test_binary_registered_inputs_are_recognised():
    """이진 등재 입력(`.parquet`)도 D1 이 잡아야 한다.

    `[검증된 사실]` `file_digest` 가 확장자로 텍스트·이진을 갈랐을 때, 이 파일들은
    CRLF 정규화를 맞아 등재값을 **재현하지 못했다**. 판별자 도입 중 드러났다.
    """
    reg = _reg()
    root = Path(reg['baselines']['shadow_20260819']['local_root'])
    found = [root / rel for rel in reg['baselines']['shadow_20260819']['inputs']
             if (root / rel).exists() and b'\x00' in (root / rel).read_bytes()]
    if not found:
        pytest.skip('이진 등재 입력이 회수돼 있지 않다')
    for p in found:
        assert _classify(p).verdict == 'registered', f'{p} 가 등재본으로 인식되지 않는다'


# ── 3. fail-closed — 미분류는 막는가 ────────────────────────────────────────
def test_unknown_is_not_clean(tmp_path):
    p = tmp_path / 'nothing.json'
    p.write_text('{"hello": 1}', encoding='utf-8')
    v = _classify(p)
    assert v.verdict == 'unknown' and not v.clean


def test_unreadable_is_not_clean(tmp_path):
    p = tmp_path / 'binary.bin'
    p.write_bytes(b'\x00\x01\x02not json')
    assert not _classify(p).clean


def test_missing_file_is_not_clean(tmp_path):
    assert not _classify(tmp_path / 'absent.json').clean


def test_container_with_one_composed_entry_is_mixed(tmp_path):
    """한 파일 안에 세대가 섞이면 `mixed` 이고, `mixed` 는 깨끗하지 않다."""
    p = tmp_path / 'c.json'
    p.write_text(json.dumps({'tags': {
        'a': {'composed_from': 'x', 'method': 'y'},
        'b': {'run_at': '2026-08-15T00:00:00'},
    }}), encoding='utf-8')
    v = _classify(p)
    assert v.verdict == 'mixed' and not v.clean
    # 그래도 **항목 하나**는 깨끗할 수 있다 — 소비처가 그 키만 읽으면 안전하다.
    assert G.entry_classify(p, 'a', registry=_reg(), digest_fn=file_digest).clean
    assert not G.entry_classify(p, 'b', registry=_reg(), digest_fn=file_digest).clean


def test_composed_stamp_requires_both_fields(tmp_path):
    """지문 하나만으로는 조립분으로 인정하지 않는다."""
    p = tmp_path / 'half.json'
    p.write_text(json.dumps({'composed_from': 'x'}), encoding='utf-8')
    assert not _classify(p).clean


def test_declared_provenance_alone_is_not_enough(tmp_path):
    """`provenance` 선언만으로는 안 된다 — **기록된 입력 sha 가 등재값과 맞아야** 한다.

    선언은 베낄 수 있어도 sha 는 못 베낀다.
    """
    p = tmp_path / 'fake_prov.json'
    p.write_text(json.dumps({'provenance': {
        'baseline': 'shadow_20260819',
        'inputs': {'experiments/ablation/F_pbr_ma200_n13_periods.csv':
                   {'sha256': '0' * 64}}}}), encoding='utf-8')
    assert not _classify(p).clean


# ── 4. 판별자별 1:1 대응 — 무엇이 무엇을 잡고 있는가 ────────────────────────
def test_d2_positively_identifies_a_recorded_poisoned_digest():
    """기록된 폐기 sha256 은 **양성 식별**된다 (경로와 무관하게)."""
    reg = _reg()
    dig = reg['_poisoned_summaries']['digests']
    hits = [p for p in (Path(r) for r in reg['_poisoned_summaries']['paths'])
            if p.exists() and file_digest(p) in dig]
    assert hits, '기록된 폐기본이 하나도 남아 있지 않다 — 대조가 공허하다'
    for p in hits:
        v = _classify(p)
        assert v.verdict == 'poisoned' and 'D2' in v.by


def test_the_live_mixed_container_is_reported_as_mixed():
    """실제 `daily_nav/summary.json` 은 **섞여 있다** — 그렇게 보고돼야 한다.

    현행 채택안 태그만 조립분이고 나머지는 2026-08-15 세대다. `composed` 로 뭉개면
    그 사실이 사라지고, `poisoned` 로 뭉개면 조립분까지 못 쓰게 된다.
    """
    p = Path('experiments/daily_nav/summary.json')
    if not p.exists():
        pytest.skip('없음')
    v = _classify(p)
    assert v.verdict == 'mixed' and not v.clean
    assert G.entry_classify(p, 'F_pbr_ma200_n13',
                            registry=_reg(), digest_fn=file_digest).clean


def test_a_poisoned_file_that_only_d2_can_name(tmp_path):
    """D2 만이 이름 붙일 수 있는 폐기본이 있다 — 그것이 D2 의 존재 이유다.

    `[검증된 사실]` 컨테이너 폐기본(`ablation/summary.json`)은 D3 도 식별한다. 그래서
    그 파일로 D2 의 커버리지를 재면 **아무것도 안 잡는 것처럼 보인다** — 2026-08-29
    무력화 실험 2차에서 실제로 그랬다. 겹치지 않는 파일로 재야 한다.
    """
    p = Path('experiments/robustness/random_summary_C_pbr_ma200_random_n13.json')
    if not p.exists():
        pytest.skip('없음')
    v = _classify(p)
    assert v.verdict == 'poisoned' and 'D2' in v.by

    # D2 를 끄면 이 파일은 **이름을 잃는다** (막히기는 하지만 진단이 사라진다).
    reg = copy.deepcopy(_reg())
    reg['_poisoned_summaries']['digests'] = {}
    v2 = _classify(p, reg)
    assert v2.verdict == 'unknown' and not v2.clean


def test_d1_wins_over_d2_when_a_digest_is_both():
    """등재 sha 는 폐기 sha 보다 먼저 본다 — 순서가 뒤집히면 등재본이 막힌다."""
    reg = copy.deepcopy(_reg())
    sha = file_digest(COMPOSED) if COMPOSED.exists() else None
    if sha is None:
        pytest.skip('조립분 없음')
    reg['shared_inputs']['x'] = {'sha256': sha, 'compatible_baselines': []}
    reg['_poisoned_summaries']['digests'][sha] = {'generation': 'test',
                                                 'recorded_as': 'x'}
    assert _classify(COMPOSED, reg).verdict == 'registered'


# ── 5. 검사기 자신이 규칙을 지키는가 ────────────────────────────────────────
_NAME_BASED = {'endswith', 'startswith', 'fnmatch', 'fnmatchcase', 'glob', 'rglob'}
_ENFORCERS = ['scripts/analysis/generation.py',
              'scripts/analysis/baseline_registry.py']


@pytest.mark.parametrize('path', _ENFORCERS)
def test_the_enforcer_obeys_its_own_rule(path):
    """**원칙을 강제하는 도구는 그 원칙의 첫 번째 적용 대상이다.**

    세대 판별자가 경로 문자열로 종류를 판정하면, 그것이 고치려던 결함을 그대로
    갖게 된다. 이 저장소의 자기지시 결함이 세 번 있었고 셋 다 이 한 줄이면 잡혔다.

    AST 로 본다 — 문자열 매칭으로 검사하면 주석·docstring 을 오탐한다(직전 세션).
    """
    tree = ast.parse(Path(path).read_text(encoding='utf-8'))
    hits = [f'{path}:{n.lineno} .{n.func.attr}()'
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr in _NAME_BASED]
    assert not hits, f'이름 기반 판별 호출: {hits}'


@pytest.mark.parametrize('path', _ENFORCERS)
def test_the_enforcer_does_not_branch_on_path_suffix(path):
    """`path.suffix` 로 갈라 종류를 정하지 않는다.

    `[검증된 사실]` `file_digest` 가 `path.suffix != '.gz'` 로 텍스트·이진을 갈랐고,
    그래서 이진 `.parquet` 5종이 등재 sha 를 재현하지 못했다. 확장자 목록으로 하는
    판별은 확장자가 늘 때마다 뒤처진다.
    """
    tree = ast.parse(Path(path).read_text(encoding='utf-8'))
    hits = [f'{path}:{n.lineno} .suffix' for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and n.attr in ('suffix', 'suffixes')]
    assert not hits, f'확장자 기반 분기: {hits}'


# ── 6. 메타 — 오라클이 프로덕션을 타는가 ────────────────────────────────────
_META_TARGETS = [
    ('test_reinfection_of_a_composed_path_is_caught',
     'scripts.analysis.generation.classify'),
    ('test_registered_shadow_copy_passes_despite_sharing_the_poisoned_path',
     'scripts.analysis.generation.classify'),
    ('test_d2_positively_identifies_a_recorded_poisoned_digest',
     'scripts.analysis.baseline_registry.file_digest'),
]


@pytest.mark.parametrize('test_name,target', _META_TARGETS)
def test_meta_oracle_actually_exercises_production(test_name, target, monkeypatch,
                                                   tmp_path):
    """대상 프로덕션 함수를 `raise` 로 바꾸면 그 오라클이 **반드시 실패해야** 한다.

    **`META` 문자열을 반드시 요구한다** — 다른 이유의 실패를 통과로 세면 이 메타
    테스트 자체가 자기가 잡으려던 결함이 된다 (2026-08-26 세션에 실제로 그랬다).
    """
    import inspect

    mod_name, attr = target.rsplit('.', 1)
    mod = importlib.import_module(mod_name)

    def _boom(*a, **k):
        raise AssertionError(f'META: {target} 가 호출됐다')

    monkeypatch.setattr(mod, attr, _boom)
    here = sys.modules[__name__]
    if getattr(here, attr, None) is not None:
        monkeypatch.setattr(here, attr, _boom)

    fn = globals()[test_name]
    with pytest.raises(Exception) as ei:
        if 'tmp_path' in inspect.signature(fn).parameters:
            fn(tmp_path)
        else:
            fn()
    assert f'META: {target}' in str(ei.value), (
        f'{test_name} 이 {target} 를 타지 않는다 — 오라클이 형식뿐이다. '
        f'실제 예외: {type(ei.value).__name__}: {ei.value}')
