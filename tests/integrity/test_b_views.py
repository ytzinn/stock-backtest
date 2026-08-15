"""B형 전용 뷰가 산출물을 옳게 대변하는가 (설계메모 v3 §4-6 renderer 키).

## 전용 뷰의 위험

A형 비교표는 숫자를 그대로 옮긴다. B형 전용 뷰는 **판정을 요약**하므로 요약이 원본과
어긋날 수 있고, 어긋나도 화면은 멀쩡해 보인다. 특히 위험한 둘:

1. **판정과 규칙이 따로 논다.** 화면이 `TIME_OVERFIT_CONFIRMED` 를 띄우면서 그 옆에
   다른 문턱을 적으면, 사람은 화면의 문턱으로 판정을 이해한다.
2. **renderer 키 오타.** 키가 안 맞으면 조용히 raw fallback 으로 떨어진다. 에러가 없어서
   "아직 안 만들었나 보다"로 읽히고, 만들어 둔 뷰가 영영 안 뜬다.

판정 규칙은 `scripts/calendar_sens/time_split.py` 가 단일 정의다. 여기서 재선언하지 않고
**import 해서** 대조한다 — 규칙을 복제하면 복제본끼리 어긋나는 걸 아무도 못 잡는다.
"""
from __future__ import annotations

import pytest

from dashboard.b_views import B_RENDERERS
from dashboard.series import SERIES
from dashboard.series_view import (
    bootstrap_excludes_zero,
    rank_shift_rows,
    time_split,
    verdict_rule_rows,
)


@pytest.fixture(scope='module')
def ts():
    d = time_split()
    if d is None:
        pytest.skip('time_split.json 이 없다 (git 미추적). 서버에서 판정할 것.')
    return d


# ── renderer 등록 ────────────────────────────────────────────────────────────

def test_every_registered_renderer_belongs_to_a_real_series():
    """등록된 렌더러 키가 실제 축의 `renderer` 여야 한다. 오타면 영영 안 뜬다."""
    declared = {s.renderer for s in SERIES if s.renderer}
    orphan = set(B_RENDERERS) - declared
    assert not orphan, (
        f'어느 축도 요청하지 않는 렌더러가 등록돼 있다: {sorted(orphan)} — '
        f'만들어 둔 뷰가 화면에 영영 안 뜬다')


def test_b_series_with_a_renderer_either_render_or_fall_back_visibly():
    """전용 뷰가 없는 B형은 **원본 경로가 있어야** 한다 — 빈 화면이 되면 안 된다."""
    for s in SERIES:
        if s.kind != 'B' or s.renderer in B_RENDERERS:
            continue
        assert s.paths, f'{s.id}: 전용 뷰도 없고 원본 경로도 없다 — 화면에 아무것도 없다'


# ── 시간분할 뷰가 판정을 옳게 대변하는가 ─────────────────────────────────────

def test_displayed_rule_reproduces_the_recorded_verdict(ts):
    """화면이 보여주는 규칙으로 판정을 다시 내리면 산출물의 판정과 같아야 한다.

    규칙 정의는 산출 스크립트에서 **import** 한다. 복제하면 어긋나도 못 잡는다.
    """
    from scripts.calendar_sens.time_split import BACK_FLOOR, FRONT_TOP, judge

    focal = ts['focal']
    assert judge(focal['front_rank'], focal['back_rank']) == ts['verdict'], \
        '산출물의 판정이 규칙과 어긋난다 — 산출물 쪽이 낡았거나 규칙이 바뀌었다'

    # 화면에 적는 문턱이 실제 상수와 같은가.
    pr = ts['pre_registered']
    assert (pr['front_top'], pr['back_floor']) == (FRONT_TOP, BACK_FLOOR), (
        f'산출물의 사전등록 문턱({pr["front_top"]}/{pr["back_floor"]})이 코드 상수'
        f'({FRONT_TOP}/{BACK_FLOOR})와 다르다 — 화면이 틀린 근거를 보여준다')

    rows = verdict_rule_rows(ts)
    assert str(FRONT_TOP) in rows[0]['사전등록 조건']
    assert str(BACK_FLOOR) in rows[1]['사전등록 조건']

    # CONFIRMED 는 두 조건을 **모두** 넘었을 때만 나온다. 화면의 체크박스와 판정
    # 배지가 따로 놀면(하나만 체크된 채 CONFIRMED) 사람은 규칙을 잘못 배운다.
    if ts['verdict'] == 'TIME_OVERFIT_CONFIRMED':
        assert all(r['충족'] for r in rows), \
            f'판정은 CONFIRMED 인데 화면의 규칙 행이 전부 충족은 아니다: {rows}'
    else:
        assert not all(r['충족'] for r in rows), \
            f'두 조건을 다 넘었는데 판정이 {ts["verdict"]} 다 — 규칙과 판정이 어긋난다'


def test_the_confidence_interval_really_does_include_zero(ts):
    """CI 가 0 을 배제하지 못한다는 화면의 경고가 사실인가.

    이 경고가 이 뷰의 존재 이유다. ρ 와 판정이 나란히 뜨면 "상관이 유의해서 과적합"
    이라는 **없는 주장**이 읽힌다. 실제로는 CI 상한이 0 을 살짝 넘고, 판정은 초점
    태그의 사전등록 문턱에서만 나온다. 재발행으로 CI 가 0 을 배제하게 되면 경고 문구를
    고쳐야 하므로, 그때 이 검사가 깨져야 한다.
    """
    b = ts['bootstrap']
    assert not bootstrap_excludes_zero(ts), (
        f'CI [{b["ci_low"]:.3f}, {b["ci_high"]:.3f}] 가 이제 0 을 배제한다 — '
        f'"유의해서가 아니다"라는 화면 경고가 더 이상 맞지 않는다')
    assert b['ci_low'] < 0 < b['ci_high']


def test_rank_shift_rows_cover_every_tag_and_mark_one_focal(ts):
    rows = rank_shift_rows(ts)
    assert len(rows) == len(ts['rows']), '태그가 표에서 누락됐다'
    focal = [r for r in rows if r['초점']]
    assert len(focal) == 1, f'초점 태그가 {len(focal)}개다 — 정확히 하나여야 한다'
    assert focal[0]['태그'] == ts['pre_registered']['focal_tag']
    assert focal[0]['이동'] == focal[0]['뒤 순위'] - focal[0]['앞 순위']
    # 앞 순위 오름차순이어야 기울기 그래프의 왼쪽 축이 정렬돼 보인다.
    assert [r['앞 순위'] for r in rows] == sorted(r['앞 순위'] for r in rows)


def test_pre_registration_note_is_present(ts):
    """사전등록 사실 자체가 산출물에 있어야 한다.

    "수치 산출 전에 정했다"가 이 판정의 힘 전부다. 그 기록이 없으면 화면은 사후에
    고른 문턱과 구별할 수 없는 것을 보여주게 된다.
    """
    pr = ts['pre_registered']
    assert pr.get('note'), '사전등록 경위가 비었다'
    assert pr.get('seed'), 'bootstrap seed 가 없다 — 재현 불가'
    assert ts.get('disclaimer'), '진단 전용이라는 단서가 없다'
