"""캘린더 축 메타데이터 정합 (설계메모 v3 §8 검사 6번).

## 왜 이 검사가 지금까지 없었나

"안 A 는 분기 캘린더다"라는 사실이 **태그 이름 `_A` 에만** 있었다. 산출물 JSON 17개
필드 어디에도 캘린더·빈도가 없었다 — `n_stocks` 와 똑같은 병이다(2026-08-12). 대조할
내용이 없으니 검사를 만들 수가 없었고, 이름이 틀려도 잡을 방법이 없었다.

`run_ablation.calendar_metadata()` 가 이제 실행이 **실제로 쓴 앵커**에서 캘린더를 파생해
기록한다. 라벨(`--calendar`)이 아니라 앵커에서 뽑는 이유: 라벨은 사람이 넘기는 값이라
틀릴 수 있지만 앵커는 엔진이 순회한 것이다.

## 기록 이전 산출물은 어떻게 하나

전부(72개) 기록 이전이다. 그래도 **내용으로 할 수 있는 교차 검증이 하나 있다**:
분기 캘린더(안 A)는 반기보다 구간이 많아야 하고, 위상만 옮긴 안 C 는 구간 수가 같아야
한다. 이름이 주장하는 바를 구간 수가 뒷받침하는지 보는 것이라, 필드가 없어도 성립한다.
"""
from __future__ import annotations

import json
import warnings

import pytest

from backtest.configs.schedule import CALENDAR_CHOICES, get_schedule
from dashboard.artifacts import build_catalog
from dashboard.series import CALENDAR_VARIANTS
from scripts.run_ablation import calendar_metadata

#: 캘린더별로 앵커에 **반드시 나타나야 하는** report_type (내용 기반 식별자).
REQUIRED_TYPES = {
    'SEMIANNUAL': {'FY', 'H1'},
    'A':          {'FY', 'H1', 'Q1', 'Q3'},
    'C':          {'Q1', 'Q3'},
}
#: 나타나면 안 되는 것 — 반기 캘린더에 분기 보고서가 섞이면 그 자체가 사고다.
FORBIDDEN_TYPES = {
    'SEMIANNUAL': {'Q1', 'Q3'},
    'A':          set(),
    'C':          {'FY', 'H1'},
}


# ── 1. 파생기 자체 (산출물 없이도 항상 돈다) ────────────────────────────────

@pytest.mark.parametrize('calendar', list(CALENDAR_CHOICES))
def test_metadata_identifies_each_calendar_by_content(calendar):
    """이름이 아니라 **앵커 구성**으로 캘린더가 구별돼야 한다."""
    points = list(get_schedule(calendar))
    meta = calendar_metadata(points)

    assert meta['id'] == calendar
    assert meta['n_anchors'] == len(points)
    types = set(meta['report_types'])
    assert REQUIRED_TYPES[calendar] <= types, (
        f'{calendar}: {sorted(REQUIRED_TYPES[calendar] - types)} 앵커가 없다')
    assert not (types & FORBIDDEN_TYPES[calendar]), (
        f'{calendar}: {sorted(types & FORBIDDEN_TYPES[calendar])} 가 섞였다')


def test_metadata_reveals_mixed_calendars():
    """캘린더가 섞인 실행은 **숨기지 말고 드러내야** 한다.

    섞인 앵커로 돌린 결과는 어느 캘린더의 성적도 아니다. `id` 를 하나로 뭉개면
    그 사고가 산출물에 정상처럼 기록된다.
    """
    mixed = list(get_schedule('SEMIANNUAL'))[:2] + list(get_schedule('C'))[:2]
    meta = calendar_metadata(mixed)
    assert '+' in meta['id'], f'섞인 캘린더가 단일 id 로 뭉개졌다: {meta["id"]}'
    assert meta['n_anchors'] == 4


def test_semiannual_and_quarterly_are_not_confusable():
    """반기와 분기가 구간 수만으로 구별되는 게 아니라 **보고서 종류**로 구별된다.

    구간 수는 데이터 사정으로 달라질 수 있지만 보고서 종류는 캘린더의 정의다.
    """
    semi = calendar_metadata(list(get_schedule('SEMIANNUAL')))
    quarterly = calendar_metadata(list(get_schedule('A')))
    assert set(semi['report_types']) != set(quarterly['report_types'])
    assert quarterly['n_anchors'] > semi['n_anchors']


# ── 2. 산출물 대조 ──────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def catalog():
    c = build_catalog()
    if not len(c):
        pytest.skip('experiments/ablation 산출물이 없다 (git 미추적).')
    return c


def _expected_calendar(key: str) -> str:
    for v in CALENDAR_VARIANTS:
        if key.endswith(f'_{v}'):
            return v
    return 'SEMIANNUAL'


def test_recorded_calendar_matches_the_name(catalog):
    """`calendar` 를 기록한 산출물은 이름이 주장하는 캘린더와 같아야 한다.

    기록분이 하나도 없으면 **조용히 통과시키지 않는다** — 그게 `n_stocks` 검사가
    68/72 에서 공허하게 통과하던 방식이다. 없으면 없다고 경고한다.
    """
    recorded = [(a.key, a.metrics['calendar']) for a in catalog
                if isinstance(a.metrics.get('calendar'), dict)]
    if not recorded:
        warnings.warn(
            f'`calendar` 를 기록한 산출물이 하나도 없다 (총 {len(catalog)}개). '
            f'run_ablation 은 이제 기록하지만 기존 산출물은 재실행 전까지 비어 있다 — '
            f'그때까지 이 검사는 아무것도 대조하지 못한다.', UserWarning, stacklevel=2)
        return

    wrong = []
    for key, meta in recorded:
        want = _expected_calendar(key)
        if meta.get('id') != want:
            wrong.append(f'{key}: 이름은 {want} 인데 내용은 {meta.get("id")}')
        types = set(meta.get('report_types') or {})
        if types & FORBIDDEN_TYPES.get(want, set()):
            wrong.append(f'{key}: {want} 인데 {sorted(types & FORBIDDEN_TYPES[want])} 앵커가 섞였다')
        if meta.get('n_anchors') is not None and meta['n_anchors'] < (
                catalog.require(key).n_periods or 0):
            wrong.append(f'{key}: 앵커 {meta["n_anchors"]}개보다 구간이 많다')
    assert not wrong, '기록된 캘린더가 이름과 다르다:\n  ' + '\n  '.join(wrong)


def test_legacy_calendar_variants_are_cross_checked_by_period_count(catalog):
    """기록 이전 산출물도 **구간 수로** 이름의 주장을 교차 검증한다.

    안 A(분기)는 반기보다 구간이 많아야 하고, 안 C(위상만 이동)는 같아야 한다.
    필드가 없어도 성립하는 검사라, 재실행 전까지 이게 유일한 방어선이다.
    """
    problems = []
    for a in catalog:
        variant = _expected_calendar(a.key)
        if variant == 'SEMIANNUAL':
            continue
        parent_key = a.key[:-2]
        parent = catalog.get(parent_key)
        if parent is None or parent.n_periods is None or a.n_periods is None:
            continue
        if variant == 'A' and not a.n_periods > parent.n_periods:
            problems.append(f'{a.key}: 분기 캘린더인데 구간이 {a.n_periods} 로 '
                            f'반기({parent.n_periods}) 이하다')
        if variant == 'C' and a.n_periods != parent.n_periods:
            problems.append(f'{a.key}: 위상만 옮긴 캘린더인데 구간 수가 '
                            f'{a.n_periods} ≠ {parent.n_periods} 다')
    assert not problems, '캘린더 변형의 구간 수가 이름의 주장과 어긋난다:\n  ' + \
                         '\n  '.join(problems)


def test_summary_files_carry_their_calendar_in_the_filename(catalog):
    """`summary_A.json`·`summary_C.json` 이 실제로 캘린더별로 갈려 있는가.

    한 파일에 두 캘린더가 섞이면 "안 A 결과"를 반기 결과로 읽게 된다.
    """
    from backtest.canonical_state import ABL_DIR
    for suffix, variant in (('_A', 'A'), ('_C', 'C')):
        path = ABL_DIR / f'summary{suffix}.json'
        if not path.exists():
            continue
        keys = json.loads(path.read_text(encoding='utf-8')).get('scenarios', {})
        # summary 안의 키는 캘린더 접미사를 갖지 않는다 (파일이 이미 갈려 있으므로).
        stray = [k for k in keys if k.endswith(('_A', '_C'))]
        assert not stray, (
            f'summary{suffix}.json 안에 캘린더 접미사가 붙은 키가 있다: {stray}. '
            f'파일이 이미 {variant} 전용인데 키에도 접미사가 있으면 이중 표기다.')
