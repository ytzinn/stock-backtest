"""주장 검증기 — 저장소가 스스로 적어 둔 태그 관계가 조건표와 맞는가.

## 왜 기존 검사와 다른가

`docs/TAG_MATRIX.md` 계열 검사는 **셀 것을 사람이 정한다**. 그래서 아무도 생각 못 한
부재는 구조적으로 못 본다 — 2026-08-15 하루에 네 건이 사용자 눈으로 잡혔고 넷 다
"세라고 시키지 않은 것"이었다.

여기는 방향이 반대다. 산출물이 **이미 주장하고 있는 것**(`stage_b` 의 `n_axes`,
`gate_results` 의 `draws_tag`·`u_tag`, 왜-지도의 `Delta.axes`)을 조건표와 대조한다.
새 산출물이 생기면 새 주장이 따라오므로 **커버리지가 저절로 자란다.**

실제로 만들자마자 G2 벤치의 모멘텀 불일치를 스스로 짚었다 — 아무도 "G2 벤치의
모멘텀을 확인하라"는 검사를 짜 넣지 않았는데도.
"""
from __future__ import annotations

import pytest

from dashboard.claims import (
    KNOWN,
    blind_spots,
    condition_axes,
    stale_exemptions,
    universe_axes,
    verify,
)


def test_no_unregistered_claim_violations():
    """저장소의 주장과 조건표가 어긋나면 **깨진다.**

    경고로 두지 않는 이유: 이 저장소에서 보고만 하는 신호는 이미 한 번 무시됐다.
    매트릭스가 "등록 대장에 없는 태그 4개"를 문서에 적어 두고 있었는데, 인수인계
    문서가 그걸 작업 항목으로 올리기 전까지 아무도 조치하지 않았다.

    새 위반이면 고치고, 알면서 두는 것이면 `claims.KNOWN` 에 **사유와 날짜를 적어**
    등록하라. 등록은 면제가 아니라 기록이다.
    """
    found = verify()
    assert not found, '주장과 조건표가 어긋난다:\n' + '\n'.join(
        f'  [{v.source}] {v.claim}\n      실제: {v.actual}\n      예외 등록 키: {v.key}'
        for v in found)


def test_known_violations_expire_when_fixed():
    """**더 이상 위반이 아닌 예외는 지워야 한다.**

    예외가 자기만료되지 않으면 억제 목록이 되고, 억제 목록은 시간이 지나면 아무도
    안 보는 사각지대가 된다 — 매트릭스가 그랬던 것과 정확히 같은 실패다.
    """
    stale = stale_exemptions()
    assert not stale, (
        f'해소된 위반이 예외 목록에 남아 있다: {stale} — `claims.KNOWN` 에서 지워라. '
        f'남겨 두면 나중에 같은 위반이 다시 생겨도 조용히 통과한다')


def test_every_known_violation_records_why_and_when():
    """예외에 사유·날짜가 없으면 다음 사람이 지워도 되는지 알 수 없다."""
    for key, reason in KNOWN.items():
        assert len(reason) > 30, f'{key}: 사유가 사실상 비었다'
        assert '2026' in reason, f'{key}: 언제 내린 판단인지 없다'


def test_blind_spot_probe_finds_nothing_unmodelled():
    """조건표가 **못 보는 설정 키**가 없어야 한다.

    주장 검증기가 못 잡는 것을 잡는 쪽이다 — 그쪽은 누군가 적어 둔 주장만 보지만,
    여기는 아무도 모델링하지 않은 차원을 찾는다. 처음 돌렸을 때 `screener_weights` 가
    걸렸고(단일 팩터 변형 넷이 11개 열 전부 같게 보였다) 스크리너 열에 팩터 이름을
    실어 막았다.

    새 설정 키가 생기면 여기서 걸린다. 표에 안 보이는 키로 갈리는 두 태그는
    **매트릭스에서 같아 보이고, 짝 대조군도 같은 답을 받는다.**
    """
    blind = blind_spots()
    assert not blind, (
        f'조건표가 못 보는 설정 키: {blind} — 이 키로 갈리는 태그들이 표에서 '
        f'같아 보인다. 열을 추가하거나(`series_view.pipeline_facts`) 그 키가 '
        f'조건이 아닌 이유를 남겨라')


# ── 축 세는 눈금이 stage_b 와 같은가 ────────────────────────────────────────

def test_rules_are_counted_one_axis_each():
    """룰은 **하나하나가 축**이다 — `stage_b` 의 `n_axes` 와 같은 눈금.

    매트릭스의 `안정성 룰` 은 열이 하나라 문자열로 비교하면 4개 룰 차이도 1이 된다.
    두 눈금을 뭉개면 "단일축"의 뜻이 갈리고, 그건 이 저장소가 반복해서 밟은 함정이다.
    """
    # {R1,R2,R5,R6} vs {} — 룰 4개
    assert len(condition_axes('F_pbr_no_r3r4', 'F_pbr_nostab')) == 4
    # {R1,R2,R5,R6} vs {R1,R2,R3,R4,R5,R6} — 룰 2개 (R3·R4)
    assert len(condition_axes('F_pbr_no_r3r4', 'F_pbr_r6')) == 2
    # 룰은 같고 랭킹·컷만 — 2개
    assert set(condition_axes('F_pbr_no_r3r4', 'F_no_r3r4')) == {'랭킹 신호', '밸류에이션 컷'}


def test_universe_axes_ignore_ranking_but_not_momentum():
    """관문이 재려는 차이(랭킹)는 빼고, 유니버스를 바꾸는 것은 전부 센다.

    모멘텀이 여기 빠져 있으면 레거시 MA 20/60 풀이 MA200 전략의 짝으로 통과한다 —
    2026-08-15 이전 G1 이 정확히 그 상태였다.
    """
    # 채택안과 그 귀무분포: 랭킹만 다르므로 유니버스 축은 없어야 한다
    assert universe_axes('F_pbr_ma200', 'C_pbr_ma200_random') == []
    # 레거시 풀과는 모멘텀이 다르다
    assert universe_axes('F_pbr_ma200', 'C_pbr_path_random') == ['모멘텀']


def test_the_n_chain_is_verified_not_stripped(tmp_path):
    """**종목 수 축이 검증기에 들어가 있는가.**

    조건표는 태그 단위라 n 을 모른다(n 은 실행 파라미터다). 그래서 `base_tag()` 가
    `_n13` 을 떼어 내는데, 거기서 멈추면 **2026-08-12 에 G1 을 깨뜨린 바로 그 축**이
    새 검증기의 사각지대가 된다 — 그때 n=13 전략을 n=20 귀무분포로 판정하고 있었고,
    그 방향은 합격선을 낮춰 게이트를 관대하게 만드는 쪽이었다.

    그래서 산출물이 **기록한** n 을 사슬로 대조한다: 게이트가 적은 n ↔ 대상 산출물의
    n ↔ 귀무분포 요약의 n, 그리고 대상 n == 귀무 n.
    """
    import json

    from dashboard.claims import ROOT, _gate_claims, _recorded_n

    path = ROOT / 'experiments/robustness/gate_results_F_pbr_ma200_n13.json'
    if not path.exists():
        pytest.skip('채택안 게이트 산출물이 없다 (git 미추적 환경).')
    g = json.loads(path.read_text(encoding='utf-8'))

    # 사슬이 실제로 이어져 있어야 한다 — 하나라도 None 이면 검증이 헛돈다.
    assert g['n_stocks'] == g['draws_n_stocks'], '게이트가 대상과 귀무의 n 을 다르게 적었다'
    assert _recorded_n(g['tag']) == g['n_stocks'], '대상 산출물의 기록 n 이 다르다'
    assert _recorded_n(g['draws_tag']) == g['draws_n_stocks'], '귀무분포 요약의 기록 n 이 다르다'

    # 그리고 검사가 그 불일치를 **실제로 잡는지** 확인한다. 실제 산출물은 건드리지
    # 않는다 — 검사가 추적 중인 데이터를 고치면 그 자체가 오염 통로다.
    tmp = tmp_path / 'robustness'
    tmp.mkdir()
    (tmp / path.name).write_text(
        json.dumps({**g, 'draws_n_stocks': g['n_stocks'] + 7}, ensure_ascii=False),
        encoding='utf-8')
    keys = {v.key[0] for v in _gate_claims(rob_dir=tmp)}
    assert 'gate_null_n' in keys, f'n 을 어긋나게 했는데 검사가 조용하다 (잡힌 것: {keys})'


def test_gate_artifacts_declare_their_subject():
    """게이트 산출물은 **어느 전략의 성적표인지 스스로 밝혀야** 한다.

    종전 검증기는 밝히지 않은 산출물에 기본 태그를 채워 넣고 아는 척 검증했다 —
    CLAUDE.md 가 금지하는 "조용한 기본값"이다. 지금은 위반으로 보고하고, 필드가
    생기기 전 산출물만 사유와 함께 예외로 둔다.
    """
    from dashboard.claims import KNOWN, _gate_claims

    undeclared = {v.key for v in _gate_claims() if v.key[0] == 'gate_undeclared'}
    assert undeclared <= set(KNOWN), (
        f'대상을 안 밝힌 게이트 산출물이 예외에 없다: {undeclared - set(KNOWN)} — '
        f'새 산출물이라면 `--f-tag` 를 주고 다시 산출하라')


def test_the_g2_mismatch_is_registered_not_forgotten():
    """알면서 두기로 한 G2 불일치가 **기록으로** 남아 있는가.

    고치면 `test_known_violations_expire_when_fixed` 가 깨져서 이 예외를 지우게 된다.
    """
    key = ('gate_benchmark', 'F_pbr_ma200', 'U_pbr_path_ew')
    assert key in KNOWN, 'G2 불일치가 예외 목록에서 사라졌다 — 해소됐다면 이 검사도 고쳐라'
    assert universe_axes('F_pbr_ma200', 'U_pbr_path_ew') == ['모멘텀'], \
        'G2 벤치의 불일치 내용이 바뀌었다 — 예외 사유를 다시 확인하라'


def _stage_b_copy(tmp_path, mutate) -> object:
    """`stage_b.json` 사본을 고쳐 검사가 무는지 본다. **원본은 건드리지 않는다.**"""
    import json

    from dashboard.claims import ROOT

    src = ROOT / 'experiments/calendar_sens/stage_b.json'
    if not src.exists():
        pytest.skip('stage_b 산출물이 없다 (서버가 원본).')
    d = json.loads(src.read_text(encoding='utf-8'))
    mutate(d)
    out = tmp_path / 'stage_b.json'
    out.write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')
    return out


def test_window_claims_hold_and_the_check_bites(tmp_path):
    """블록별 **기간 선언**이 자기 셀들과 맞는가, 그리고 어긋나면 잡히는가.

    룰 contrast 는 2016-05-18 부터인데 랭킹×컷은 2017-05-18 부터다 — RIM 스코어가
    TTM 순이익을 요구해 2016 중간결산 앵커에서 포트폴리오가 0종목이 되기 때문이다.
    **δ 를 블록 사이에서 비교하면 랭킹 차이가 아니라 "1년 현금보유 vs 1년 투자"를
    재게 된다.** 산출물이 `not_comparable_with` 로 적어 두는데 아무도 대조하지 않았다.
    """
    from dashboard.claims import _window_claims

    assert not _window_claims(), '현행 stage_b 의 기간 선언이 이미 어긋나 있다'

    # 연율화 분모를 흔든다 — 이게 틀리면 모든 CAGR 이 틀린다.
    def break_years(d):
        d['common_period']['years'] = d['common_period']['years'] + 1.0

    got = _window_claims(_stage_b_copy(tmp_path, break_years))
    assert any(v.key[0] == 'window_years' for v in got), \
        f'years 를 흔들었는데 검사가 조용하다 ({[v.key for v in got]})'

    # 블록 안에서 기간을 섞는다.
    def mix_window(d):
        d['contrasts_single_axis'][0]['window_start'] = '2018-01-01'

    got = _window_claims(_stage_b_copy(tmp_path, mix_window))
    assert any(v.key[0] == 'window_mixed' for v in got)


def test_reference_values_track_the_ablation_artifacts(tmp_path):
    """`stage_b` 의 **반기 참조값**이 ablation 산출물과 같은가.

    두 산출물 계열을 잇는 유일한 검사다. `semiannual_gross_ref_full_period` 는
    ablation 의 gross CAGR 을 베껴 온 값이라 **그 태그를 재실행하면 조용히 낡는다.**
    전 contrast 의 baseline 이 `F_pbr_no_r3r4` 라 그 하나만 재실행해도 블록 전체가
    어긋난다 — 2026-08-15 재발행이 마침 다른 태그여서 비껴갔을 뿐이다.
    """
    from dashboard.claims import _reference_claims

    assert not _reference_claims(), (
        '참조값이 이미 낡았다 — stage_b 가 인용하는 태그를 재실행하고 stage_b 를 '
        '안 돌렸다는 뜻이다')

    def drift(d):
        d['contrasts_single_axis'][0]['semiannual_gross_ref_full_period'] = 0.999

    got = _reference_claims(_stage_b_copy(tmp_path, drift))
    assert any(v.key[0] == 'ref_stale' for v in got), \
        f'참조값을 흔들었는데 검사가 조용하다 ({[v.key for v in got]})'


@pytest.mark.parametrize('bad_axes', [0, 99])
def test_checker_actually_fires(bad_axes):
    """검사가 **살아 있는지** 확인한다. 통과만 하는 검사는 없는 것과 같다."""
    from dataclasses import replace

    from dashboard.claims import _delta_claims
    from dashboard.series import SERIES_BY_ID

    spec = SERIES_BY_ID['r6_loo']
    original = spec.why.deltas[0]
    broken = replace(original, axes=bad_axes)
    object.__setattr__(spec.why, 'deltas', (broken,) + spec.why.deltas[1:])
    try:
        assert _delta_claims(), f'축 {bad_axes} 로 망가뜨렸는데 검사가 조용하다'
    finally:
        object.__setattr__(spec.why, 'deltas', (original,) + spec.why.deltas[1:])
    assert not _delta_claims(), '원복 후에도 위반이 남았다 — 검사가 상태를 오염시킨다'
