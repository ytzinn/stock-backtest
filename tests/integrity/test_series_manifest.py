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


# ── 산출물이 다른 데 있는 태그 (`elsewhere`) ────────────────────────────────

def test_elsewhere_tags_are_real_configs():
    """`elsewhere` 가 실재하는 태그를 가리키고, 같은 축이 두 번 갖지 않는가.

    오타면 태그 매트릭스가 그 태그를 계속 `미배정` 이라 부르는데, 축에는 배정했다고
    적혀 있어서 아무도 다시 안 본다 — 조용히 틀리는 통로다.
    """
    for s in SERIES:
        claimed = set(s.tags)
        for e in s.elsewhere:
            assert e.tag in ABLATION_CONFIGS, \
                f'{s.id}: `{e.tag}` 은 ABLATION_CONFIGS 에 없다'
            assert e.tag not in claimed, (
                f'{s.id}: `{e.tag}` 이 tags 와 elsewhere 양쪽에 있다 — 산출물이 '
                f'있으면 멤버, 없으면 elsewhere 다. 둘 다일 수 없다')
            assert e.where, f'{s.id}/{e.tag}: 값이 어디 있는지 안 적혀 있다'
            assert e.why.strip(), (
                f'{s.id}/{e.tag}: 왜 ablation 산출물이 없는지 안 적혀 있다 — '
                f'그 이유가 없으면 다음 사람은 유실로 읽는다')


def test_elsewhere_really_has_no_artifact(catalog):
    """`elsewhere` 로 적힌 태그에 산출물이 **생기면** 검사가 깨져야 한다.

    `run_ablation` 을 태우면 그날부터 비교표의 멤버가 될 수 있다. 그런데 등록 대장이
    계속 "다른 데 있다"고 적고 있으면, 화면은 있는 그래프를 안 그리면서 "산출물이
    없다"는 낡은 설명을 띄운다. 되돌리기 쉬운 쪽으로 깨뜨린다.
    """
    stale = [(s.id, e.tag) for s in SERIES for e in s.elsewhere if e.tag in catalog]
    assert not stale, (
        f'이제 산출물이 있는데 elsewhere 로 남아 있다: {stale} — '
        f'해당 항목을 지우고 `tags` 로 옮겨라')


def test_elsewhere_is_not_reported_as_missing(catalog):
    """"다른 데 있다"가 "없다"로 뜨면 안 된다.

    `missing` 은 화면에서 빨간 오류다. 정상 상태가 영구 오류로 뜨면 진짜 유실이 났을 때
    아무도 그 줄을 안 읽는다 — 늑대소년이 되는 고전적 경로다.
    """
    for s in SERIES:
        if not s.elsewhere:
            continue
        got = resolve(s, catalog)
        overlap = {e.tag for e in s.elsewhere} & set(got.missing)
        assert not overlap, f'{s.id}: elsewhere 태그가 missing 으로도 잡힌다: {overlap}'


def test_elsewhere_paths_point_at_something(catalog):
    """값이 있다고 적은 경로 중 **최소 하나**는 이 PC 에 실재해야 한다.

    전부 요구하지는 않는다 — 산출물 상당수가 git 미추적이라 "서버엔 있고 개발 PC 엔
    없다"가 정상이고, 화면도 그 비율을 그대로 띄운다. 다만 하나도 안 맞으면 경로가
    죽은 것이고, 그건 "근거가 있다"는 인상만 주고 확인은 막는다.
    """
    dead = [f'{s.id}/{e.tag}: {e.where}' for s in SERIES for e in s.elsewhere
            if not any((ROOT / p).exists() for p in e.where)]
    assert not dead, '값이 있다고 적힌 경로가 하나도 실재하지 않는다:\n  ' + '\n  '.join(dead)


def test_no_new_unreachable_artifact_family(catalog):
    """등록 대장에서 화면으로 **도달할 수 없는** 산출물이 새로 생기면 깨진다.

    `unassigned()` 는 `experiments/ablation/` 만 훑는다. 그래서 나머지 다섯 계열은
    구조적으로 안 세어졌고, 2026-08-16 에 처음 재 보니 223개 중 86개가 도달 불가였다 —
    그중에 **G5 판정의 SSOT 인 일별 NAV 36개**와 **캘린더 A/C 관문 산출물 6개**가
    들어 있었다. 매트릭스가 설정만 세던 것과 같은 모양의 사각지대다.

    한 번에 다 배정할 수 없으므로 **래칫**으로 관리한다: 알고 있는 계열은
    `UNCOVERED` 에 사유·날짜와 함께 적고, 그 밖에서 새 파일이 나오면 여기서 걸린다.
    """
    import fnmatch as _fn

    from dashboard.series import UNCOVERED, unreachable_files

    left = [p for p in unreachable_files()
            if not any(_fn.fnmatch(p, pat) for pat in UNCOVERED)]
    assert not left, (
        '등록 대장에서 도달할 수 없는 새 산출물:\n  ' + '\n  '.join(left) +
        '\n축에 배정하거나(`paths`·`elsewhere`), 지금 못 하면 '
        '`series.UNCOVERED` 에 사유와 날짜를 적어라')


def test_uncovered_patterns_expire_when_covered():
    """**아무 파일도 안 걸리는 `UNCOVERED` 패턴은 지워야 한다.**

    자기만료가 없으면 억제 목록이 되고, 억제 목록은 시간이 지나면 아무도 안 보는
    사각지대가 된다 — 이 저장소가 이미 두 번 겪은 실패다.
    """
    import fnmatch as _fn

    from dashboard.series import UNCOVERED, unreachable_files

    left = unreachable_files()
    stale = [pat for pat in UNCOVERED
             if not any(_fn.fnmatch(p, pat) for p in left)]
    assert not stale, (
        f'해소된 계열이 `UNCOVERED` 에 남아 있다: {stale} — 지워라. '
        f'남겨 두면 같은 자리에 새 사각지대가 생겨도 조용히 통과한다')


def test_uncovered_entries_record_why_and_when():
    """사유·날짜가 없으면 다음 사람이 지워도 되는지 알 수 없다."""
    from dashboard.series import UNCOVERED

    for pat, reason in UNCOVERED.items():
        assert len(reason) > 30, f'{pat}: 사유가 사실상 비었다'
        assert '2026' in reason, f'{pat}: 언제 내린 판단인지 없다'


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


def test_why_map_next_axes_point_at_real_axes():
    """"다음 질문을 받은 축"이 실재해야 한다.

    오타면 화면에서 그 줄이 조용히 사라져, 축들이 다시 서로 모르는 섬이 된다.
    자기 자신을 가리키는 것도 막는다 — 이어지는 흐름이 아니라 순환이 된다.
    """
    for s in SERIES:
        if s.why is None:
            continue
        for nxt in s.why.next_axes:
            assert nxt in SERIES_BY_ID, f'{s.id}: 존재하지 않는 축을 가리킨다 — {nxt}'
            assert nxt != s.id, f'{s.id}: 자기 자신을 다음 축으로 가리킨다'
        if s.why.next_axes:
            assert s.why.next_step, f'{s.id}: 이어받는 축만 있고 설명이 없다'


def test_rim_ranking_gate_still_fails(catalog):
    """랭킹 축 왜-지도의 1차 근거 — `D >= C_p95` 미달 — 이 아직 사실인가.

    이 축의 결론("RIM 랭킹 근거 상실")은 순위표가 아니라 **이 관문 하나**에서 나온다.
    모멘텀 경로에서는 RIM 이 근소하게 앞서므로, 관문이 뒤집히면 결론 전체를 다시 봐야
    한다. 재발행으로 그렇게 되면 이 검사가 깨져서 왜-지도를 고치게 만든다.

    2026-07-30 재발행 기준: D 9.5408% vs C_p95 12.1345% (−2.59%p).
    """
    d = catalog.require('D_rim_only').metrics['cagr']
    c_p95 = catalog.require('C_stability_random').metrics['p95_cagr']
    assert d < c_p95, (
        f'RIM 랭킹(D {d:.4%})이 이제 랜덤 p95({c_p95:.4%})를 넘는다 — '
        f'"근거 상실" 판정의 1차 관문이 뒤집혔다. 왜-지도와 축 status 를 다시 보라')


def test_ranking_signal_direction_flips_between_paths(catalog):
    """모멘텀 유무로 RIM↔1/PBR 우열이 **뒤집힌다**는 서술이 아직 맞는가.

    왜-지도가 이 뒤집힘을 `[검증된 사실]` 로 적고, "모멘텀 경로 한 줄만 뽑아 인용하지
    마라"는 경고의 근거로 쓴다. 뒤집힘이 사라지면 그 경고도 다시 써야 한다.
    """
    def cagr(key):
        return catalog.require(key).metrics['cagr']

    # **R6 를 양쪽 다 끈 짝**으로 잰다. R6 켠 쪽과 끈 쪽을 빼면 랭킹·R6 가 함께
    # 달라진 값이 나온다 (SPEC_13 §9-9 도 이 짝을 썼다).
    no_mom = cagr('D_pbr_only') - cagr('D_no_r6')        # 1/PBR − RIM
    with_mom = cagr('F_pbr_only') - cagr('F_no_r6')

    assert no_mom > 0, (
        f'모멘텀 없는 경로에서 1/PBR 이 더 이상 앞서지 않는다 ({no_mom:+.2%}) — '
        f'왜-지도 서술을 고쳐라')
    assert with_mom < 0, (
        f'모멘텀 경로에서 RIM 이 더 이상 앞서지 않는다 ({with_mom:+.2%}) — '
        f'"여기서만 RIM 이 앞선다"는 서술이 무효다')
    assert abs(with_mom) < abs(no_mom), (
        f'모멘텀 경로의 격차({with_mom:+.2%})가 모멘텀 없는 경로({no_mom:+.2%})보다 커졌다 '
        f'— "근소 우위"라는 표현을 재검토하라')


def test_pbr_ranking_turns_over_less(catalog):
    """"1/PBR 은 회전율이 낮아 net 에서 격차가 더 벌어진다"가 사실인가."""
    rim, pbr = catalog.require('D_no_r6'), catalog.require('D_pbr_only')
    assert pbr.metrics['avg_turnover'] < rim.metrics['avg_turnover'], \
        '1/PBR 의 회전율이 더 이상 낮지 않다 — 왜-지도의 비용 서술을 고쳐라'
    gross_gap = pbr.metrics['cagr'] - rim.metrics['cagr']
    net_gap = pbr.metrics['net_cagr'] - rim.metrics['net_cagr']
    assert net_gap > gross_gap, (
        f'net 격차({net_gap:+.2%})가 gross({gross_gap:+.2%})보다 크지 않다 — '
        f'"비용을 빼면 더 벌어진다"는 서술이 무효다')


# ── PBR 룰 조합 축의 왜-지도가 주장하는 사실들 ──────────────────────────────
#
# 이 축의 왜-지도는 채택 근거를 담는다. 재발행으로 사실이 바뀌면 **검사가 깨져서**
# 문구를 고치게 만들어야 한다 — 안 그러면 `[검증된 사실]` 이라 적힌 거짓이 남는다.
# 수치는 여기 재선언하지 않는다. 산출물이 기록한 값을 읽어 **관계**만 확인한다.

_ADOPTED_RULES = 'F_pbr_no_r3r4'          # {R1,R2,R5,R6} — 이 축의 baseline


def _rule_cagr(catalog, key: str) -> float:
    return catalog.require(key).metrics['cagr']


def test_pbr_rules_adopted_set_is_not_the_cagr_leader(catalog):
    """"채택안은 이 표의 1등이 아니다"가 아직 사실인가.

    이 문장이 왜-지도의 첫 줄이고, 그래서 "그럼 왜 안 바꿨나"(1구간·1종목 + max 선택
    금지)라는 설명이 뒤따른다. 순위가 다시 뒤집히면 그 설명 전체가 필요 없어진다.
    """
    adopted = _rule_cagr(catalog, _ADOPTED_RULES)
    minus_r2 = _rule_cagr(catalog, 'F_pbr_no_r2r3r4')
    assert minus_r2 > adopted, (
        f'R2 를 뺀 조합({minus_r2:.4%})이 더 이상 채택안({adopted:.4%})을 앞서지 않는다 — '
        f'왜-지도의 첫 줄과 "그런데도 교체하지 않았다"는 서술을 다시 써라')
    gap = (minus_r2 - adopted) * 100
    assert gap < 0.4, (
        f'격차가 {gap:+.2f}%p 로 커졌다 — 왜-지도는 이것을 2026-07-16 눈금'
        f'(0.4~2%p 는 구간 의존) **아래**라고 적었다. 그 근거가 무효다')


def test_pbr_rules_adopted_set_has_the_shallowest_drawdown(catalog):
    """"낙폭에서는 채택안이 유일한 최저"가 아직 사실인가.

    CAGR 1등이 아닌데도 이 조합을 쓰는 이유의 절반이 여기 있다. MDD 는 음수이므로
    **가장 큰 값**이 가장 얕은 낙폭이다.
    """
    axis = resolve(SERIES_BY_ID['pbr_rules'], catalog)
    mdds = {m.artifact_key: catalog.require(m.artifact_key).metrics['mdd']
            for m in axis.members}
    adopted = mdds[_ADOPTED_RULES]
    worse = {k: v for k, v in mdds.items() if v > adopted}
    assert not worse, f'채택안보다 얕은 낙폭의 조합이 생겼다: {worse}'
    # 쌍둥이(R2 제거)만 동률이어야 한다. 셋 이상이 동률이면 "유일한 최저"가 아니다.
    tied = {k for k, v in mdds.items() if v == adopted} - {_ADOPTED_RULES}
    assert tied == {'F_pbr_no_r2r3r4'}, (
        f'동률 조합이 바뀌었다: {tied} — 왜-지도의 "R2 를 뺀 쌍둥이와 동률" 을 고쳐라')


def test_rule_contributions_are_not_additive(catalog):
    """"룰의 기여는 더할 수 없다" — R6 의 부호가 바탕에 따라 뒤집히는가.

    아무것도 없는 위에 R6 만 켜면 해롭고, `{R1,R2,R5}` 위에 얹으면 도움이 된다.
    이 뒤집힘이 사라지면 LOO 값을 합산해도 된다는 뜻이 되어, 왜-지도의 경고가 무효다.
    """
    alone = _rule_cagr(catalog, 'F_pbr_r6only') - _rule_cagr(catalog, 'F_pbr_nostab')
    on_top = _rule_cagr(catalog, _ADOPTED_RULES) - _rule_cagr(catalog, 'F_pbr_no_r3r4r6')
    assert alone < 0, (
        f'R6 를 혼자 켰을 때 더 이상 해롭지 않다 ({alone:+.2%}) — '
        f'"룰 기여는 덧셈이 아니다"의 근거 절반이 사라졌다')
    assert on_top > 0, (
        f'R6 를 {{R1,R2,R5}} 위에 얹었을 때 더 이상 도움이 안 된다 ({on_top:+.2%})')


def test_r1_and_r2_are_partial_substitutes(catalog):
    """"R1 이 있으면 R2 는 무해, 없으면 강한 기여" — 부호가 아직 갈리는가."""
    with_r1 = _rule_cagr(catalog, 'F_pbr_no_r2r3r4') - _rule_cagr(catalog, _ADOPTED_RULES)
    without_r1 = (_rule_cagr(catalog, 'F_pbr_no_r1r2r3r4')
                  - _rule_cagr(catalog, 'F_pbr_no_r1r3r4'))
    assert with_r1 > 0 > without_r1, (
        f'R2 제거의 부호가 더 이상 갈리지 않는다 (R1 있음 {with_r1:+.2%} · '
        f'R1 없음 {without_r1:+.2%}) — "부분 대체재" 서술을 고쳐라')
    assert abs(without_r1) > abs(with_r1), (
        f'R1 이 없을 때의 R2 기여({without_r1:+.2%})가 있을 때({with_r1:+.2%})보다 '
        f'크지 않다 — "R1 이 없으면 R2 가 강하게 일한다"가 무효다')


def test_stability_layer_is_net_positive_on_the_pbr_path(catalog):
    """"PBR 경로에서 안정성 레이어는 순기여" — RIM 경로의 순감 반전과 반대인가.

    이 대비가 왜-지도의 한 줄이고, RIM 경로 축들(`stability_all`)의 결론을 이 축으로
    끌어오지 말라는 근거다.
    """
    off, on = catalog.require('F_pbr_nostab'), catalog.require(_ADOPTED_RULES)
    assert on.metrics['cagr'] > off.metrics['cagr'], \
        'PBR 경로에서 안정성을 끄는 쪽이 더 낫다 — 왜-지도 서술을 고쳐라'
    assert on.metrics['mdd'] > off.metrics['mdd'], \
        '안정성을 꺼도 낙폭이 나빠지지 않는다 — "MDD −40.2% 로 최악" 서술이 무효다'


def test_r5_has_no_single_axis_cell_in_this_axis(catalog):
    """"R5 칸이 이 표에 없다"가 아직 사실인가.

    있으면 왜-지도가 없다고 거짓말하는 것이고, 동시에 `elsewhere` 항목도 낡은 것이다.
    R1·R2·R6 는 반대로 **있어야** 한다 — 없으면 "넷 중 R5 만" 이 틀린다.
    """
    keys = {m.artifact_key for m in resolve(SERIES_BY_ID['pbr_rules'], catalog).members}
    assert 'F_pbr_no_r3r4r5' not in keys, \
        'R5 단독 대조 산출물이 생겼다 — 왜-지도의 "R5 칸이 없다"와 elsewhere 를 지워라'
    for tag in ('F_pbr_no_r1r3r4', 'F_pbr_no_r2r3r4', 'F_pbr_no_r3r4r6'):
        assert tag in keys, f'{tag}: 단독 대조가 사라졌다 — "넷 중 R5 만" 서술이 틀린다'


def test_why_map_deltas_stay_inside_one_set(catalog):
    """증분표가 **세트를 가로질러** 빼지 않는가.

    세트는 "이것만 빼고 나머지가 같은" 묶음이다. 세트를 넘어 두 행을 빼면 여러 조건이
    한꺼번에 달라진 값이 나오는데, 화면에는 "무엇을 바꿨나" 한 줄로 뜨므로 사람은 그
    하나의 효과로 읽는다. R6 축의 "세트를 가로질러 비교하지 마라" 경고와 같은 규칙이다.
    """
    for s in SERIES:
        if s.why is None or not s.why.deltas or not s.groups:
            continue
        where = {k: i for i, g in enumerate(s.groups) for k in g}
        for d in s.why.deltas:
            a, b = where.get(d.base), where.get(d.variant)
            if a is None or b is None:
                continue
            if d.crosses_sets:
                assert a != b, (
                    f'{s.id}: 증분 "{d.label}" 이 crosses_sets 인데 실제로는 같은 '
                    f'세트다 — 플래그가 낡았다')
                assert d.note, f'{s.id}: 세트를 넘는 증분에는 이유를 적어야 한다'
                continue
            assert a == b, (
                f'{s.id}: 증분 "{d.label}" 이 세트 {a} 와 {b} 를 가로질러 뺀다 — '
                f'여러 조건이 함께 달라진 값이 단일 효과로 읽힌다. 의도한 것이라면 '
                f'crosses_sets=True 로 명시하고 이유를 note 에 적어라')


def test_config_matrix_reads_the_real_pipeline(catalog):
    """설정표가 조립된 파이프라인에서 나오는가, 그리고 다축을 드러내는가.

    `D_rim_only` 와 `D_pbr_only` 는 이름만 보면 랭킹만 다른 것 같지만 실제로는
    **랭킹·R6·밸류에이션 컷 셋**이 다르다. 설정표를 만들자마자 그게 드러났고, 그래서
    증분표의 짝을 고쳤다. 이 검사는 그 사실이 화면에 계속 드러나는지 지킨다.
    """
    from dashboard.series_view import config_matrix, pipeline_facts, varying_columns

    rows = config_matrix(resolve(SERIES_BY_ID['ranking_signal'], catalog))
    assert rows, '설정표가 비었다'
    assert len(varying_columns(rows)) >= 2, (
        '랭킹 축에서 달라지는 조건이 1개 이하로 잡혔다 — 다축이라는 사실이 '
        '화면에서 사라졌다')

    rim, pbr = pipeline_facts('D_rim_only'), pipeline_facts('D_pbr_only')
    assert rim['랭킹 신호'] != pbr['랭킹 신호']
    assert rim['안정성 룰'] != pbr['안정성 룰'], \
        'R6 차이가 사라졌다 — 증분표의 짝(D_no_r6)을 다시 검토하라'
    assert rim['밸류에이션 컷'] != pbr['밸류에이션 컷'], \
        '밸류에이션 컷 차이가 사라졌다 — "본질적으로 다축"이라는 서술을 고쳐라'


def test_tag_matrix_is_current():
    """`docs/TAG_MATRIX.md` 가 코드와 맞는가.

    이 문서는 **코드의 순수 함수**다 (조건·소속 축·짝 대조군만 담고 성과 수치는 안 담는다).
    그래서 낡았는지 기계가 판정할 수 있고, 낡으면 실패해야 한다 — 앞선 사례가
    `tests/baselines/SCENARIO_REGISTRY.json` 이다. 2026-07-12 에 33개로 만들어졌고
    지금 태그는 72개인데, 신선도 검사가 없어서 **아무도 모른 채 39개가 빠져 있었다.**
    """
    import subprocess
    import sys

    r = subprocess.run([sys.executable, '-m', 'scripts.make_tag_matrix', '--check'],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, (
        'TAG_MATRIX.md 가 낡았다 — `python -m scripts.make_tag_matrix` 로 재생성하라\n'
        + (r.stderr or r.stdout))


def test_canonical_class_tracks_the_live_setting():
    """`CANONICAL` 분류가 **현행 운영 태그 하나**에만 붙는가.

    손으로 적어 뒀더니 채택안이 두 번 바뀌는 동안(RIM → 1/PBR, MA 20/60 → MA200,
    n 20 → 13) 아무도 못 고쳤다. 매트릭스가 폐기된 RIM 태그 `F_no_r2r3` 를 현행
    채택안이라고 띄우고, 진짜 채택안 `F_pbr_ma200` 은 분류가 비어 있었다
    (2026-08-15 사용자 발견). 이제 `scripts/live/freeze_rebalance.py` 에서 읽는다.
    """
    from backtest.canonical_state import _freeze_constants
    from dashboard.tags import TAG_NOTES, adopted_tag, class_of

    live = _freeze_constants()[0]
    assert adopted_tag() == live
    assert live in ABLATION_CONFIGS, f'운영 태그 `{live}` 가 ABLATION_CONFIGS 에 없다'
    assert class_of(live) == 'CANONICAL'

    marked = [t for t in ABLATION_CONFIGS if class_of(t) == 'CANONICAL']
    assert marked == [live], f'CANONICAL 이 운영 태그 하나가 아니다: {marked}'
    hand = [t for t, (cls, _) in TAG_NOTES.items() if cls == 'CANONICAL']
    assert not hand, (
        f'분류를 손으로 `CANONICAL` 이라 적은 태그가 있다: {hand} — '
        f'채택안이 바뀌면 낡는다. 운영 설정에서 파생되게 두라')


def test_matrix_axis_membership_survives_key_suffixes():
    """소속 축이 **산출물 키 접미사를 되돌려** 세는가.

    등록 대장은 키로 배정하고(`F_pbr_ma200_n13`) 매트릭스는 태그 단위다. 문자열로만
    맞추면 현행 채택안의 소속 축이 `momentum_grid` 하나로 뜬다 — 정작 관문(`benchmarks`)
    과 종목 수 축에서 쓰이는데 그 사실이 채택안 행에서 사라진다 (2026-08-15 사용자 발견).
    """
    from dashboard.tags import adopted_tag
    from scripts.make_tag_matrix import _axes_of, _base_tag

    assert _base_tag('F_pbr_ma200_n13') == 'F_pbr_ma200'
    assert _base_tag('F_pbr_no_r3r4_A') == 'F_pbr_no_r3r4'
    assert _base_tag('F_pbr_ma5_20') == 'F_pbr_ma5_20', \
        '`_n` 없는 숫자 접미사를 종목 수로 오해했다'

    axes = set(_axes_of(adopted_tag()))
    for must in ('benchmarks', 'n_stocks'):
        assert must in axes, (
            f'채택안의 소속 축에 `{must}` 이 없다 — 등록 대장은 `_n13` 키로 배정하는데 '
            f'매트릭스가 그걸 못 알아본다. 현재: {sorted(axes)}')


def test_derived_keys_have_their_own_rows(catalog):
    """종목 수·캘린더를 독립변수로 쓸어 본 실행이 **표에 행으로 있는가.**

    처음엔 설명 절만 두고 행은 안 만들었다. 사용자가 두 번 물었고 — 표를 훑는 사람은
    보이는 행이 전부라고 읽는다. 설명은 표를 대신하지 못한다 (2026-08-16).
    """
    from scripts.make_tag_matrix import all_rows

    rows = set(all_rows())
    derived = {k for k in catalog.keys() if k not in ABLATION_CONFIGS}
    missing = sorted(derived - rows)
    assert not missing, f'산출물은 있는데 표에 행이 없다: {missing}'
    assert 'F_pbr_ma200_n13' in rows, '현행 채택 산출물의 행이 없다'


def test_derived_rows_are_distinguishable_from_their_parent(catalog):
    """파생 행이 부모와 **적어도 한 열에서** 달라야 한다.

    안 그러면 표에서 구별이 안 되고, 구별 안 되는 행은 짝 대조군도 같은 답을 받는다.
    캘린더 열이 없던 동안 `F_pbr_no_r3r4_A` 와 `_C` 가 정확히 그 상태였다.
    """
    from scripts.make_tag_matrix import _base_tag, _row_facts

    for key in catalog.keys():
        if key in ABLATION_CONFIGS:
            continue
        parent = _base_tag(key)
        if parent not in ABLATION_CONFIGS:
            continue
        child, base = _row_facts(key), _row_facts(parent)
        diff = [c for c in child if child[c] != base.get(c)]
        assert diff, (
            f'{key} 가 부모 `{parent}` 와 모든 열이 같다 — 표에서 구별되지 않는다. '
            f'달라지는 축을 담을 열이 없다는 뜻이다')


def test_identical_conditions_imply_identical_performance(catalog):
    """조건이 같은 행끼리 **성적도 같은가.** 다르면 열이 하나 모자란 것이다.

    이 검사가 다른 것들과 다른 점: 설정이 아니라 **결과**를 본다. 돌연변이 탐침은
    설정 키만 훑으므로 "설정에는 없는데 결과를 가르는 것"(실행 인자·코드 경로)을 못
    본다. 여기서는 조건표가 같다고 말한 두 행의 산출물을 실제로 대조하므로, 표가
    놓친 차원이 **성적 차이로** 드러난다.

    지금 같은 묶음 둘은 진짜로 같다 — `F_pbr_ma200`(기본 20) vs `_n20`, 그리고
    `F_pbr_ma_double_adapter` vs `F_pbr_no_r3r4`(같은 산식을 다른 배관으로 부른다).
    부동소수 끝자리까지 일치한다.
    """
    from scripts.make_tag_matrix import twin_groups

    for group in twin_groups():
        present = [k for k in group if k in catalog]
        if len(present) < 2:
            continue
        head = catalog.require(present[0]).metrics
        for other in present[1:]:
            got = catalog.require(other).metrics
            for m in ('cagr', 'net_cagr', 'mdd', 'sharpe', 'avg_turnover'):
                a, b = head.get(m), got.get(m)
                if a is None or b is None:
                    continue
                assert abs(a - b) < 1e-12, (
                    f'`{present[0]}` 와 `{other}` 는 조건이 같은데 {m} 이 다르다 '
                    f'({a} vs {b}) — 조건표가 못 보는 차원이 성적을 가르고 있다. '
                    f'열을 추가하라')


def test_no_artifact_is_silently_missing_from_the_matrix(catalog):
    """산출물이 매트릭스에 **행이 없다면 그 이유가 설명돼야** 한다.

    행은 `ABLATION_CONFIGS` 키다. 실행 파라미터로 파생된 키(`_n{K}` 종목 수,
    `_A`/`_C` 캘린더)는 설정이 아니라 행이 없는데, 그걸 문서가 말하지 않으면 73행을
    보고 "이게 전부"라고 읽는다 — 실제로 종목 수 스윕 4개가 그렇게 안 보였다
    (2026-08-15 사용자 발견).

    새로운 종류의 파생 키가 생기면 여기서 걸려서, 문서에 설명을 추가하게 만든다.
    """
    from scripts.make_tag_matrix import _base_tag

    unexplained = []
    for key in catalog.keys():
        if key in ABLATION_CONFIGS:          # 행이 있다
            continue
        parent = _base_tag(key)
        if parent != key and parent in ABLATION_CONFIGS:   # 알려진 파생 키
            continue
        unexplained.append(key)
    assert not unexplained, (
        f'매트릭스에 행도 없고 알려진 파생 키도 아닌 산출물: {sorted(unexplained)} — '
        f'`이 표에 행이 없는 것` 절에 설명을 추가하거나 ABLATION_CONFIGS 에 등록하라')


def test_matrix_shows_derived_keys_and_says_why(catalog):
    """파생 키가 **본문 표에** 있고, 왜 싣는지도 적혀 있는가.

    2026-08-16 이전에는 설명 절만 두고 행을 안 만들었다. 사용자가 두 번 물어서
    바꿨다 — 표를 훑는 사람은 보이는 행이 전부라고 읽으므로, 설명은 표를 대신하지
    못한다. 되돌아가면 이 검사가 깨진다.
    """
    from scripts.make_tag_matrix import render

    body = render()
    assert '파생 키도 행으로 싣는다' in body, '파생 키 설명 절이 사라졌다'
    for must in ('--n-stocks', '캘린더', '조건이 완전히 같은 행'):
        assert must in body, f'`{must}` 설명이 없다'
    for key in ('F_pbr_ma200_n13', 'F_pbr_no_r3r4_A'):
        if key in catalog:
            assert f'| `{key}` |' in body, f'`{key}` 가 본문 표에 행으로 없다'


def test_every_tag_note_points_at_a_real_tag():
    """태그 설명이 실재하는 태그를 가리키는가. 오타면 영영 안 뜬다."""
    from dashboard.tags import CLASSES, TAG_NOTES

    stray = sorted(set(TAG_NOTES) - set(ABLATION_CONFIGS))
    assert not stray, f'존재하지 않는 태그에 설명이 달려 있다: {stray}'
    for tag, (cls, note) in TAG_NOTES.items():
        assert cls in CLASSES, f'{tag}: 알 수 없는 분류 {cls}'
        # 길이를 요구하지 않는다. "R1 단독 leave-one-out." 처럼 한 줄로 끝나는 것이
        # 옳은 설명인 태그가 많다 — 비어 있지만 않으면 된다.
        assert note.strip(), f'{tag}: 설명이 비었다'


def test_frozen_audit_registry_is_marked_as_superseded():
    """동결된 감사 스냅샷이 **현행으로 오해되지 않게** 표시돼 있는가.

    이 파일은 2026-07-12 시점 33개의 기록이고 갱신하지 않는다. 표시가 없으면 다음
    사람이 "태그 목록"으로 열어 보고 39개가 빠진 걸 현행으로 읽는다 — 실제로 그
    상태로 한 달을 지냈다.
    """
    import json

    path = ROOT / 'tests/baselines/SCENARIO_REGISTRY.json'
    if not path.exists():
        pytest.skip('감사 레지스트리가 없다 (지웠다면 감사 문서 참조도 정리했는지 확인).')
    meta = json.loads(path.read_text(encoding='utf-8'))['_meta']
    assert 'TAG_MATRIX' in meta.get('superseded_by', ''), \
        '동결 기록에 후속 문서 표시가 없다'
    assert '동결' in meta.get('status', ''), '동결 상태 표시가 없다'
