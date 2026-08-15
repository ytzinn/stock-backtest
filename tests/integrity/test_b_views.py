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

from pathlib import Path

import pytest

from dashboard.b_views import B_RENDERERS
from dashboard.series import SERIES
from dashboard.series_view import (
    DECOMPOSITION_MISSING_FIELDS,
    alpha_survives_episode_22,
    block_sensitivity_rows,
    bootstrap_excludes_zero,
    contrast_rows,
    decomposition,
    direction_hold_is_undefined,
    layer2_frame,
    layer2_gate_rows,
    membership_verdict_rows,
    missing_provenance,
    paired_rows,
    phase_b_runs,
    rank_shift_rows,
    rule_membership,
    stage_b,
    time_split,
    verdict_rule_rows,
    victim_rows,
)


@pytest.fixture(scope='module')
def ts():
    d = time_split()
    if d is None:
        pytest.skip('time_split.json 이 없다 (git 미추적). 서버에서 판정할 것.')
    return d


@pytest.fixture(scope='module')
def sb():
    d = stage_b()
    if d is None:
        pytest.skip('stage_b.json 이 없다 (git 미추적). 서버에서 판정할 것.')
    return d


@pytest.fixture(scope='module')
def dc():
    d = decomposition()
    if d is None:
        pytest.skip('momentum_decomposition.json 이 없다 (git 미추적).')
    return d


# ── 표가 Arrow 로 직렬화되는가 ───────────────────────────────────────────────

def test_every_row_builder_is_arrow_serializable(ts, sb, dc):
    """한 열에 숫자와 문자열을 섞지 않는다.

    섞으면 `st.dataframe` 이 Arrow 변환에 실패하고 Streamlit 이 **조용히 타입을
    고쳐서** 그린다. 화면은 멀쩡해 보이지만 정렬·서식이 의도와 달라지고, 무엇보다
    "고쳐졌다"는 사실이 아무 데도 안 남는다. 비교표에서 `구간`·`n` 열을 문자열로
    통일한 이유가 이것이고(2026-08-14), 여기서도 같은 함정을 밟았다(사전등록 문턱 표).
    """
    import pandas as pd
    import pyarrow as pa

    builders = {
        'verdict_rule_rows': verdict_rule_rows(ts),
        'rank_shift_rows': rank_shift_rows(ts),
        'contrast_rows': contrast_rows(sb),
        'block_sensitivity_rows': block_sensitivity_rows(sb),
        'victim_rows': victim_rows(dc),
        'paired_rows': paired_rows(dc),
    }
    rm = rule_membership()
    if rm is not None:
        builders['membership_verdict_rows'] = membership_verdict_rows(rm)
    for name, rows in builders.items():
        try:
            pa.Table.from_pandas(pd.DataFrame(rows))
        except pa.ArrowInvalid as e:   # pragma: no cover - 실패 시에만
            raise AssertionError(f'{name}: Arrow 직렬화 실패 — 한 열에 타입이 섞였다\n{e}')


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
    assert focal[0]['전략'] == ts['pre_registered']['focal_tag']
    assert focal[0]['이동'] == focal[0]['뒤 순위'] - focal[0]['앞 순위']
    # 앞 순위 오름차순이어야 기울기 그래프의 왼쪽 축이 정렬돼 보인다.
    assert [r['앞 순위'] for r in rows] == sorted(r['앞 순위'] for r in rows)


# ── 캘린더 bootstrap 뷰 ──────────────────────────────────────────────────────

def test_recorded_action_follows_from_the_recorded_judgments(sb):
    """기록된 조치가 세 판정에서 실제로 도출되는가. 규칙은 산출 스크립트에서 import 한다."""
    from scripts.calendar_sens.stage_b import _action

    j = sb['judgment']
    derived = _action(j['Q1'], j['Q2_D'], j['Q2_M'])
    assert derived['action'] == j['action'], \
        f"판정 {j['Q1']}/{j['Q2_D']}/{j['Q2_M']} 에서 나올 조치는 {derived['action']} 인데 " \
        f"산출물엔 {j['action']} 이 적혀 있다"
    assert derived['text'] == j['text']


def test_direction_hold_rate_is_undefined_not_zero(sb):
    """J1 이 `null` 이고 분모가 0 인가.

    화면이 이걸 0% 로 그리면 "캘린더가 룰 결론을 전부 뒤집었다"는 강한 주장이 된다.
    실제로는 방향이 잡힌 contrast 가 하나도 없어 **잴 수 없었다** 이다. 재발행으로
    방향이 잡히면 이 검사가 깨지고, 그때 화면의 경고문도 함께 고쳐야 한다.
    """
    m = sb['summary_metrics']
    assert direction_hold_is_undefined(sb), \
        f"J1 이 이제 정의된다({m['J1_direction_hold_rate']}, 분모 {m['J1_denominator']}) — " \
        f"'0%가 아니라 정의되지 않는다'는 화면 경고가 더 이상 맞지 않는다"
    assert m['J1_direction_hold_rate'] is None
    assert m['n_direction_held'] == 0
    # 경고문이 인용하는 개수가 실제 단일축 contrast 수와 같아야 한다.
    assert m['n_neutral_inconclusive'] == len(sb['contrasts_single_axis'])


def test_multi_axis_contrasts_are_flagged_not_blended(sb):
    """다축 contrast 가 단일축과 구별돼야 한다.

    `C_R3R4`(2축)·`C_STAB`(4축)는 룰을 여러 개 동시에 건드리므로 "어느 룰의 효과인가"를
    말할 수 없다. 표에서 구별이 사라지면 전부 단일 룰 효과로 읽힌다.
    """
    rows = {r['contrast']: r for r in contrast_rows(sb)}
    assert len(rows) == len(sb['contrasts_single_axis']) + len(sb['contrasts_multi_axis'])
    for c in sb['contrasts_single_axis']:
        assert rows[c['contrast_id']]['단일축'] is True
        assert rows[c['contrast_id']]['축 수'] == 1
    for c in sb['contrasts_multi_axis']:
        assert rows[c['contrast_id']]['단일축'] is False
        assert rows[c['contrast_id']]['축 수'] > 1


def test_delta_is_the_gap_between_the_two_calendars(sb):
    """표의 δ 가 두 캘린더 효과의 차이인가. 화면 캡션이 그렇게 설명한다."""
    for r, c in zip(contrast_rows(sb),
                    sb['contrasts_single_axis'] + sb['contrasts_multi_axis']):
        assert r['δ (안C−반기)'] == pytest.approx(
            c['e_altC_net'] - c['e_semiannual_net'], abs=1e-9), \
            f"{r['contrast']}: δ 가 안C−반기 가 아니다"


def test_rank_cut_block_is_kept_separate(sb):
    """랭킹×컷 2×2 가 룰 contrast 와 **섞이지 않아야** 한다.

    구간이 다르다(2017-05-18 시작 vs 2016-05-18). 같은 forest plot 에 올리면 기간이
    다른 효과를 눈으로 비교하게 된다. 산출물이 `not_comparable_with` 를 명시하는 이유다.
    """
    rc = sb['contrasts_rank_cut_2x2']
    ids = {r['contrast'] for r in contrast_rows(sb)}
    assert not (ids & {c['contrast_id'] for c in rc['cells']}), \
        'contrast_rows 가 랭킹×컷 셀을 섞어 넣었다 — 기간이 다르므로 같이 그리면 안 된다'
    assert rc['window']['start'] != sb['common_period']['S'], \
        '두 구간이 같아졌다 — 분리 근거가 사라졌으니 화면 설명을 재검토하라'
    assert rc.get('not_comparable_with') and rc.get('status')


def test_block_sensitivity_marks_the_pre_registered_length(sb):
    """사전등록 블록(21일)이 표에서 표시되고, 뒤집힘이 그대로 옮겨지는가."""
    rows = block_sensitivity_rows(sb)
    pre = [r for r in rows if r['사전등록']]
    assert len(pre) == 1 and pre[0]['블록 (일)'] == sb['pre_registered']['block_days']

    flips = sb['block_length_sensitivity']['flips_vs_block21']
    for r in rows:
        expected = ', '.join(flips.get(str(r['블록 (일)']), [])) or '—'
        assert r['21일 대비 뒤집힘'] == expected


# ── 레짐 오버레이 뷰 — 철회된 실행을 정본으로 집지 않는가 ───────────────────

@pytest.fixture(scope='module')
def pb():
    runs = phase_b_runs()
    if not runs:
        pytest.skip('phaseB CSV 가 없다 (git 미추적).')
    return runs


def test_exactly_one_run_is_canonical_and_it_is_the_latest(pb):
    canon = [r for r in pb if r['canonical']]
    assert len(canon) == 1, f'정본이 {len(canon)}개다 — 정확히 하나여야 한다'
    assert canon[0]['date'] == max(r['date'] for r in pb)


def test_the_retracted_run_is_not_canonical(pb):
    """2026-07-10 실행은 **철회됐다.** 정본으로 잡히면 안 된다.

    always-on 비교군의 구간 불일치 버그로 `68/144 통과` 가 나왔고 그 결론은
    `2026.07.11._REGIME_PHASE_B.md` 에서 철회됐다. glob 순서에 기대면 이 파일이
    먼저 잡힐 수 있고, 그러면 화면이 철회된 수치를 정본으로 띄운다.
    """
    retracted = [r for r in pb if r['date'] == '2026-07-10']
    if not retracted:
        pytest.skip('철회된 2026-07-10 실행이 이 환경에 없다.')
    assert not retracted[0]['canonical'], '철회된 실행이 정본으로 잡혔다'

    df = layer2_frame(retracted[0]['layer2'])
    assert int(df['is_candidate'].sum()) > 0, \
        '철회된 실행의 통과 조합이 0이 되었다 — 화면의 철회 설명을 재검토하라'


def test_canonical_run_reproduces_the_zero_candidate_conclusion(pb):
    """정본 실행의 결론이 `0/144` 인가. 이 축의 status 가 그 결론을 인용한다."""
    canon = next(r for r in pb if r['canonical'])
    df = layer2_frame(canon['layer2'])
    rows = {r['게이트']: r for r in layer2_gate_rows(df)}
    assert rows['전부 통과 (후보)']['통과'] == 0, (
        f"정본 실행에서 후보가 {rows['전부 통과 (후보)']['통과']}개 나왔다 — "
        f"`Layer2 0/144 기여 없음` 이라는 축 status 와 어긋난다")
    # 개별 게이트는 통과가 있어야 "넷 동시는 0" 이라는 서술이 의미를 가진다.
    assert any(rows[c]['통과'] > 0 for c in ('C1', 'C2', 'C3', 'C4'))


def test_alpha_concentration_in_episode_22_still_holds(pb):
    """#22 를 빼면 알파가 사라진다는 화면 경고가 사실인가."""
    canon = next(r for r in pb if r['canonical'])
    a = alpha_survives_episode_22(layer2_frame(canon['layer2']))
    assert a['ex22_positive'] < a['total_positive'], (
        f"#22 제외 후 알파 양(+) 조합이 {a['ex22_positive']}, 전체 기준 "
        f"{a['total_positive']} — '한 에피소드에 몰려 있다'는 경고가 더 이상 맞지 않는다")


# ── 성과 분해 뷰 ─────────────────────────────────────────────────────────────

def test_b_series_do_not_claim_each_others_files():
    """두 B형 축이 같은 원본 파일을 소유하지 않는다.

    `experiments/analysis/*.json` 으로 훑으면 종목 수 축의 `n_stocks_curve.json` 까지
    분해 축의 목록에 뜬다 (2026-08-15 발견). A형에서 `exclude` 로 막은 것과 같은
    종류인데, B형은 glob 이라 아무도 안 보고 있었다.
    """
    import glob as _glob

    from backtest.canonical_state import ROOT

    owned: dict[str, str] = {}
    clashes = []
    for s in SERIES:
        for pattern in s.paths:
            for hit in _glob.glob(str(ROOT / pattern)):
                prev = owned.setdefault(hit, s.id)
                if prev != s.id:
                    clashes.append(f'{Path(hit).name}: {prev} ↔ {s.id}')
    assert not clashes, '두 축이 같은 산출물 파일을 소유한다:\n  ' + '\n  '.join(clashes)


def test_decomposition_artifacts_have_no_pre_registration(dc):
    """이 축에 사전등록·단서가 **없다는 사실**이 유지되는가.

    캘린더 축은 문턱을 수치 산출 전에 커밋해 두고 그걸로 판정했다. 분해 산출물은
    셋 다 없어서 사후 탐색이고, 화면이 그 차이를 경고한다. 나중에 산출물이 이 필드를
    갖게 되면 이 검사가 깨져야 한다 — 그때 경고문이 거짓이 되기 때문이다.
    """
    assert set(missing_provenance(dc)) == set(DECOMPOSITION_MISSING_FIELDS), \
        f'분해 산출물의 계보 필드 구성이 바뀌었다 (없는 것: {missing_provenance(dc)}) — ' \
        f'"사전등록이 없다"는 화면 경고를 재검토하라'


def test_victim_rows_reconcile_with_the_recorded_counts(dc):
    """"희생자가 이긴 구간" 이 산출물의 집계와 어긋나지 않는가.

    화면은 `victims_underperformed_f` (14) 를 크게 띄우면서 동시에 "6개 구간에서는
    희생자가 이겼다"고 말한다. 둘의 합이 전체 구간이 아니면 둘 중 하나가 거짓이다.
    """
    mv = dc['momentum_victims']
    rows = victim_rows(dc)
    won = sum(1 for r in rows if r['희생자가 이겼나'])
    assert len(rows) == mv['periods_with_victims']
    assert won + mv['victims_underperformed_f'] == mv['periods_with_victims'], \
        f'이긴 구간 {won} + 못한 구간 {mv["victims_underperformed_f"]} 가 ' \
        f'전체 {mv["periods_with_victims"]} 와 다르다'
    for r in rows:
        assert r['희생자가 이겼나'] == (r['희생자 평균 수익'] > r['F 구간 수익 (gross)'])


def test_membership_verdict_carries_the_dates_that_produced_it():
    """판정 옆에 어긋난 날짜가 함께 실려야 한다. 강도를 알 수 없으면 판정이 과장된다."""
    rm = rule_membership()
    if rm is None:
        pytest.skip('rule_membership.json 이 없다 (git 미추적).')
    v = rm['verdict']
    rows = membership_verdict_rows(rm)
    assert rows[0]['해당 구간 수'] == len(v['r2_diff_dates'])
    assert rows[1]['해당 구간 수'] == len(v['r4_diff_dates'])
    for date in v['r2_diff_dates']:
        assert date in rows[0]['어긋난 날짜']
    for date in v['r4_diff_dates']:
        assert date in rows[1]['어긋난 날짜']
    # 판정 문자열이 bool 을 뒤집어 옮기지 않았는가.
    assert rows[0]['판정'] == ('아니오' if not v['r2_deterministically_removable'] else '예')
    assert rows[1]['판정'] == ('예' if v['r4_addition_changes_top20'] else '아니오')


def test_pre_registration_note_is_present(ts):
    """사전등록 사실 자체가 산출물에 있어야 한다.

    "수치 산출 전에 정했다"가 이 판정의 힘 전부다. 그 기록이 없으면 화면은 사후에
    고른 문턱과 구별할 수 없는 것을 보여주게 된다.
    """
    pr = ts['pre_registered']
    assert pr.get('note'), '사전등록 경위가 비었다'
    assert pr.get('seed'), 'bootstrap seed 가 없다 — 재현 불가'
    assert ts.get('disclaimer'), '진단 전용이라는 단서가 없다'
