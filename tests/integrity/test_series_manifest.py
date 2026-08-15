"""시리즈 매니페스트와 산출물의 정합성 (설계메모 v3 §8 검사 1·2·4·5·6·9).

매니페스트는 "이 축으로 무엇을 비교하나"를 적은 지도다. 지도가 가리키는 곳에 땅이
없거나, 땅은 있는데 지도 어디에도 안 적혀 있으면 화면이 조용히 거짓말한다.

여기서 잡는 것:

1. 매니페스트가 가리키는 태그가 실재하는가 (`missing` 이 비어야 한다)
2. `base_tag` 가 `ABLATION_CONFIGS` 에 실재하는가 — 오타난 태그가 조용히 무시되지 않게
4. 어디에도 배정 안 된 산출물이 있는가 (실패가 아니라 **warning** — 만들어 놓고 잊은 것)
5. 한 태그가 여러 축에 들어가도 되는가 (다대다 모델이 실제로 쓰이는지 증명)
6. 캘린더 A/C 변형이 `params` 로 분리됐는가
9. 근거 문서 경로가 살아 있는가 — 죽은 링크는 "근거가 있다"는 인상만 주고 확인은 막는다
   (이 저장소는 실제로 죽은 근거 경로를 커밋한 적이 두 번 있다)
"""
from __future__ import annotations

import glob
import warnings

import pytest

from backtest.ablation import ABLATION_CONFIGS
from backtest.canonical_state import ROOT
from dashboard.artifacts import build_catalog
from dashboard.series import (
    CALENDAR_VARIANTS,
    KIND_MEANING,
    SERIES,
    STATUS_MEANING,
    resolve,
    resolve_all,
    unassigned,
)

VALID_CODES = {'CLOSED_PASS', 'CLOSED_FAIL', 'EXPLORING', 'ADOPTED', 'ARCHIVED'}


@pytest.fixture(scope='module')
def catalog():
    c = build_catalog()
    if not len(c):
        pytest.skip('experiments/ablation 산출물이 없다 (git 미추적). 서버에서 판정할 것.')
    return c


# ── 매니페스트 자체의 형태 (산출물 없이도 항상 돈다) ─────────────────────────

def test_series_ids_are_unique():
    ids = [s.id for s in SERIES]
    assert len(ids) == len(set(ids)), f'시리즈 id 중복: {ids}'


def test_series_shape_is_valid():
    for s in SERIES:
        assert s.kind in ('A', 'B'), f'{s.id}: kind 는 A|B 만'
        assert s.status.code in VALID_CODES, f'{s.id}: 알 수 없는 status code {s.status.code}'
        assert s.status.label and s.status.as_of, f'{s.id}: status 라벨·일자 누락'
        if s.kind == 'B':
            assert s.paths, f'{s.id}: B형인데 원본 경로가 없다 — 화면에 띄울 게 없다'


def test_every_code_on_screen_has_a_meaning():
    """화면이 `A`·`ARCHIVED` 같은 코드만 띄우지 않게, 뜻이 매니페스트에 있어야 한다.

    코드만 보이면 처음 보는 사람은 뜻을 물어볼 데가 없고, 대개 틀린 쪽으로 짐작한다
    (`ARCHIVED` 를 "실험이 틀렸다"로 읽는 것이 대표적이다 — 실제로는 전제가 교체된 것).
    """
    assert set(STATUS_MEANING) == VALID_CODES, (
        f'상태 코드와 뜻 목록이 어긋난다: 뜻 없음 {VALID_CODES - set(STATUS_MEANING)} · '
        f'쓰이지 않는 뜻 {set(STATUS_MEANING) - VALID_CODES}')
    for s in SERIES:
        assert s.status.meaning, f'{s.id}: 상태 `{s.status.code}` 의 뜻이 비었다'
        assert s.kind in KIND_MEANING, f'{s.id}: 유형 `{s.kind}` 의 뜻이 없다'
    for code, meaning in STATUS_MEANING.items():
        assert len(meaning) > 10, f'{code}: 뜻이 사실상 비었다'


def test_glossary_status_table_is_generated_from_the_manifest():
    """용어사전의 상태 표가 매니페스트 정의에서 생성되는가.

    같은 5개 코드를 두 곳에 손으로 적으면 한쪽만 고쳐진다. 용어사전은
    `STATUS_MEANING` 을 읽어 표를 만들므로, 여기서 그 연결이 살아 있는지 확인한다.
    """
    from dashboard.glossary import GLOSSARY_BY_ID

    body = GLOSSARY_BY_ID['status_codes'].body
    for code, meaning in STATUS_MEANING.items():
        assert f'`{code}`' in body, f'용어사전에 `{code}` 가 없다'
        assert meaning in body, f'용어사전의 `{code}` 설명이 매니페스트와 다르다'


def test_evidence_documents_exist():
    """근거 문서가 실재하는가. 파일명을 손으로 적으면 반드시 틀린다.

    이 검사를 만들면서 실제로 4건이 틀려 있었다 (SPEC_05_ablation.md 는 존재하지 않고
    SPEC_05_backtest.md 다 등). 죽은 링크는 근거가 아니라 근거인 척이다.
    """
    dead = [f'{s.id}: {s.status.source}' for s in SERIES
            if not (ROOT / s.status.source).exists()]
    assert not dead, '근거 문서 경로가 없다:\n  ' + '\n  '.join(dead)


# ── 매니페스트 ↔ 산출물 ──────────────────────────────────────────────────────

def test_every_manifest_tag_exists(catalog):
    """매니페스트가 가리키는 태그가 실재해야 한다. 없으면 화면에 빈 칸이 뜬다."""
    dangling = {s.spec.id: list(s.missing) for s in resolve_all(catalog) if s.missing}
    assert not dangling, f'매니페스트에 있는데 산출물이 없는 태그: {dangling}'


def test_every_base_tag_is_a_real_config(catalog):
    """`base_tag` 는 `ABLATION_CONFIGS` 의 키여야 한다.

    캘린더 변형(`_A`/`_C`)은 런타임 파생이라 config 에 없다 — 그래서 `params` 로
    분리한다. 분리 후에도 남는 건 오타이거나 미등록 설정이다.
    """
    unknown = {(s.spec.id, m.artifact_key, m.base_tag)
               for s in resolve_all(catalog) for m in s.members
               if m.base_tag not in ABLATION_CONFIGS}
    assert not unknown, f'ABLATION_CONFIGS 에 없는 base_tag: {sorted(unknown)}'


def test_calendar_variants_are_split_into_params(catalog):
    """`_A`/`_C` 는 base_tag 에 붙어 있으면 안 된다 — 부모와 같은 설정으로 취급돼야."""
    for s in resolve_all(catalog):
        for m in s.members:
            for v in CALENDAR_VARIANTS:
                assert not m.base_tag.endswith(f'_{v}'), (
                    f'{m.artifact_key}: 캘린더 접미사가 base_tag 에 남아 있다 — '
                    f'부모 설정과 다른 전략으로 오인된다.')
            if m.artifact_key != m.base_tag and 'calendar' in m.params:
                assert m.params['calendar'] in CALENDAR_VARIANTS


def test_a_series_compare_at_least_two_members(catalog):
    """A형은 비교표다. 멤버가 하나면 비교가 아니다."""
    thin = {s.spec.id: len(s.members) for s in resolve_all(catalog)
            if s.spec.kind == 'A' and len(s.members) < 2}
    assert not thin, f'A형인데 멤버가 2개 미만: {thin}'


def test_many_to_many_membership_is_exercised(catalog):
    """한 태그가 여러 축에 들어가는 구조가 **실제로 쓰이는지** 확인한다.

    1:1 로도 통과하는 검사만 있으면, 어느 날 누가 "한 태그는 한 시리즈"로 바꿔도
    아무도 모른다. `D_rim_only` 는 레이어·LOO·룰개별·전체on/off·랭킹분해에 함께 든다.
    """
    from collections import Counter
    counts = Counter(m.artifact_key for s in resolve_all(catalog) for m in s.members)
    shared = {k: v for k, v in counts.items() if v > 1}
    assert shared, '어떤 태그도 두 축 이상에 속하지 않는다 — 다대다 모델이 사실상 죽었다.'


def test_b_series_paths_resolve(catalog):
    """B형 원본 glob 이 최소 하나의 실제 파일로 해석돼야 한다."""
    dead = []
    for s in SERIES:
        if s.kind != 'B':
            continue
        for pattern in s.paths:
            if not glob.glob(str(ROOT / pattern)):
                dead.append(f'{s.id}: {pattern}')
    assert not dead, 'B형 원본 경로가 아무 파일도 가리키지 않는다:\n  ' + '\n  '.join(dead)


def test_unassigned_artifacts_are_reported(catalog):
    """어느 축에도 없는 산출물은 **경고로 드러낸다.** 실패는 아니다.

    새 실험을 돌리면 잠깐 미배정 상태가 되는 게 정상이라 실패시키면 늑대소년이 된다.
    다만 조용히 사라지면 "만들어 놓고 잊은" 산출물이 쌓인다.
    """
    left = unassigned(catalog)
    if left:
        warnings.warn(f'어느 시리즈에도 배정되지 않은 산출물 {len(left)}개: '
                      f'{", ".join(left[:10])}' + (' …' if len(left) > 10 else ''),
                      UserWarning, stacklevel=2)


def test_exclude_prevents_double_ownership(catalog):
    """종목 수 축과 모멘텀 그리드가 `F_pbr_ma200_n*` 을 함께 갖지 않는다.

    둘 다 가지면 모멘텀 그리드 표에 같은 전략이 종목 수만 다른 4행으로 끼어들어,
    "MA 창을 바꿨을 때의 차이"를 읽는 축이 오염된다.
    """
    grid = {m.artifact_key for m in resolve(
        next(s for s in SERIES if s.id == 'momentum_grid'), catalog).members}
    n_axis = {m.artifact_key for m in resolve(
        next(s for s in SERIES if s.id == 'n_stocks'), catalog).members}
    assert not (grid & n_axis), f'두 축이 같은 키를 소유한다: {sorted(grid & n_axis)}'


def test_scenario_ref_is_hashable():
    """`ScenarioRef` 는 해시 가능해야 한다 — Streamlit 위젯이 옵션을 해싱한다.

    frozen dataclass 는 __hash__ 를 자동 생성하는데 `params` 가 dict 라 그대로 두면
    해싱 시점에 TypeError 로 터진다. 데이터 계층에서는 아무 문제가 없어 보이다가
    **화면에서만** 죽는 종류라, 여기서 못 박는다 (2026-08-14 실제 발생).
    """
    from dashboard.artifacts import ScenarioRef
    a = ScenarioRef.of('F_pbr_ma200', n_stocks=13)
    b = ScenarioRef.of('F_pbr_ma200', n_stocks=13)
    assert hash(a) == hash(b) and a == b
    assert len({a, b}) == 1                      # 집합·dict 키로 쓸 수 있어야 한다
    assert a.params == {'n_stocks': 13}          # 해시에서 뺐어도 값은 살아 있어야 한다
    assert len({r for s in resolve_all() for r in s.members}) > 0
