"""시리즈 등록 대장과 산출물의 정합성 (설계메모 v3 §8 검사 1·2·4·5·6·9).

> **파일 이름의 `manifest` 는 옛 이름이다.** 2026-08-15 에 이 개념을 "매니페스트"에서
> **"등록 대장"**으로 바꿨다 — 이 저장소에서 매니페스트는 이미 다른 두 파일
> (`experiments/live/dryrun/manifest.yaml`, `experiments/ARTIFACTS_MANIFEST.json`)을
> 가리키고 있어 셋이 겹쳤다. 파일명·식별자는 git 이력이 끊기지 않게 그대로 뒀다.

등록 대장은 "이 축으로 무엇을 비교하나"를 적은 지도다. 지도가 가리키는 곳에 땅이
없거나, 땅은 있는데 지도 어디에도 안 적혀 있으면 화면이 조용히 거짓말한다.

여기서 잡는 것:

1. 등록 대장이 가리키는 태그가 실재하는가 (`missing` 이 비어야 한다)
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
    CONFIDENCE,
    KIND_MEANING,
    SERIES,
    SERIES_BY_ID,
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


# ── 등록 대장 자체의 형태 (산출물 없이도 항상 돈다) ─────────────────────────

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
    """화면이 `A`·`ARCHIVED` 같은 코드만 띄우지 않게, 뜻이 등록 대장에 있어야 한다.

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
    """용어사전의 상태 표가 등록 대장 정의에서 생성되는가.

    같은 5개 코드를 두 곳에 손으로 적으면 한쪽만 고쳐진다. 용어사전은
    `STATUS_MEANING` 을 읽어 표를 만들므로, 여기서 그 연결이 살아 있는지 확인한다.
    """
    from dashboard.glossary import GLOSSARY_BY_ID

    body = GLOSSARY_BY_ID['status_codes'].body
    for code, meaning in STATUS_MEANING.items():
        assert f'`{code}`' in body, f'용어사전에 `{code}` 가 없다'
        assert meaning in body, f'용어사전의 `{code}` 설명이 등록 대장과 다르다'


def test_groups_only_name_tags_the_axis_actually_owns():
    """세트에 적은 키는 그 축의 태그여야 한다. 오타면 그 줄이 조용히 안 그려진다."""
    for s in SERIES:
        listed = {k for g in s.groups for k in g}
        stray = listed - set(s.tags)
        assert not stray, f'{s.id}: 세트에 이 축의 태그가 아닌 키가 있다: {sorted(stray)}'
        flat = [k for g in s.groups for k in g]
        assert len(flat) == len(set(flat)), f'{s.id}: 세트에 같은 키가 두 번 들어갔다'


def test_grouped_axis_keeps_pairs_together(catalog):
    """세트가 있으면 **세트 순서**로 정렬된다 — baseline 을 위로 올리지 않는다.

    올리면 `D_rim_only` 가 짝(`D_no_r6`)에서 떨어져, 이 축의 요점인 R6 on/off 대조가
    그림에서 사라진다. 실제로 그렇게 떠 있었다 (2026-08-15).
    """
    for s in SERIES:
        if not s.groups:
            continue
        got = [m.artifact_key for m in resolve(s, catalog).members]
        want = [k for g in s.groups for k in g if k in got]
        assert got[:len(want)] == want, (
            f'{s.id}: 세트 순서가 지켜지지 않았다\n  실제 {got}\n  기대 {want}')
        if s.baseline:
            assert got[0] != s.baseline or want[0] == s.baseline, \
                f'{s.id}: baseline 이 세트 순서를 무시하고 맨 앞으로 올라갔다'


def test_evidence_documents_exist():
    """근거 문서가 실재하는가. 파일명을 손으로 적으면 반드시 틀린다.

    이 검사를 만들면서 실제로 4건이 틀려 있었다 (SPEC_05_ablation.md 는 존재하지 않고
    SPEC_05_backtest.md 다 등). 죽은 링크는 근거가 아니라 근거인 척이다.
    """
    dead = [f'{s.id}: {s.status.source}' for s in SERIES
            if not (ROOT / s.status.source).exists()]
    assert not dead, '근거 문서 경로가 없다:\n  ' + '\n  '.join(dead)


# ── 등록 대장 ↔ 산출물 ──────────────────────────────────────────────────────

def test_every_manifest_tag_exists(catalog):
    """등록 대장이 가리키는 태그가 실재해야 한다. 없으면 화면에 빈 칸이 뜬다."""
    dangling = {s.spec.id: list(s.missing) for s in resolve_all(catalog) if s.missing}
    assert not dangling, f'등록 대장에 있는데 산출물이 없는 태그: {dangling}'


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


# ── 왜-지도 (sub1) ───────────────────────────────────────────────────────────

def test_why_maps_are_well_formed():
    """왜-지도의 라벨과 필수 칸. `history` 가 비면 "결정 이력"이 아니다."""
    for s in SERIES:
        if s.why is None:
            continue
        w = s.why
        assert w.variable and w.question and w.failure_mode, f'{s.id}: 왜-지도 빈 칸'
        assert w.history, f'{s.id}: 먹여준 결정이 비었다 — 결론만 남으면 같은 실험을 다시 한다'
        for detail, label in w.understanding:
            assert label in CONFIDENCE, (
                f'{s.id}: 알 수 없는 확신 라벨 `{label}` (허용 {CONFIDENCE})')
            assert detail.strip(), f'{s.id}: 내 이해 항목이 비었다'


def test_why_map_sources_exist():
    """왜-지도가 인용하는 문서가 실재해야 한다.

    계보를 적으면서 근거 경로를 틀리게 적는 것이 이 저장소의 단골 실수다
    (시리즈 근거 문서에서 4건, 미해결 과제에서 2건).
    """
    dead = [f'{s.id}: {src}' for s in SERIES if s.why
            for src in s.why.sources if not (ROOT / src).exists()]
    assert not dead, '왜-지도 근거 문서가 없다:\n  ' + '\n  '.join(dead)


def test_r6_sign_flip_claim_still_holds(catalog):
    """왜-지도가 "E·G 에서 R6 의 부호가 뒤집힌다"고 적었다. 산출물이 아직 그런가.

    이 문장은 화면에서 `[검증된 사실]` 로 표시되므로, 재발행으로 사실이 바뀌면
    **검사가 깨져서** 문구를 고치게 만들어야 한다. 안 그러면 검증됐다고 적힌 거짓이
    남는다 — 왜-지도에서 가장 위험한 실패 모드다.
    """
    from dashboard.series_view import comparison_rows

    series = resolve(SERIES_BY_ID['r6_loo'], catalog)
    rows = {m.artifact_key: r for m, r in zip(series.members, comparison_rows(series, catalog))}

    def gap(on: str, off: str) -> float | None:
        a, b = rows.get(on), rows.get(off)
        if not a or not b or a['CAGR'] is None or b['CAGR'] is None:
            return None
        return a['CAGR'] - b['CAGR']          # R6 켬 − 끔

    helps = {k: gap(*v) for k, v in {
        'C': ('C_stability_random', 'C_no_r6'),
        'D': ('D_rim_only', 'D_no_r6'),
        'F': ('F_momentum_rim', 'F_no_r6')}.items()}
    hurts = {k: gap(*v) for k, v in {
        'E': ('E_screener_rim', 'E_no_r6'),
        'G': ('G_full', 'G_no_r6')}.items()}

    for name, d in helps.items():
        if d is not None:
            assert d > 0, (f'{name} 세트에서 R6 이 더 이상 도움이 안 된다 ({d:+.2f}%p) — '
                           f'왜-지도의 "C·D·F 는 켜는 쪽이 낫다"를 고쳐라')
    for name, d in hurts.items():
        if d is not None:
            assert d < 0, (f'{name} 세트에서 R6 이 더 이상 해롭지 않다 ({d:+.2f}%p) — '
                           f'왜-지도의 "E·G 는 끄는 쪽이 낫다"를 고쳐라')


def test_screener_is_what_separates_the_flipped_sets():
    """부호가 뒤집힌 두 세트(E·G)가 정말 **스크리너가 켜진** 세트인가.

    왜-지도가 그 상호작용을 `[Claude 의견]` 으로 적었다. 해석은 의견이어도 되지만,
    그 의견이 딛고 선 **설정 사실**은 참이어야 한다.
    """
    flipped = {'E_screener_rim', 'G_full'}
    kept = {'C_stability_random', 'D_rim_only', 'F_momentum_rim'}
    for tag in flipped:
        assert ABLATION_CONFIGS[tag].get('use_screener') is True, \
            f'{tag}: 스크리너가 꺼져 있다 — 왜-지도의 상호작용 서술 근거가 사라졌다'
    for tag in kept - {'C_stability_random'}:      # C 는 랜덤 추첨 계열
        assert not ABLATION_CONFIGS[tag].get('use_screener'), \
            f'{tag}: 스크리너가 켜져 있다 — 두 덩어리를 가르는 축이 스크리너가 아니다'
