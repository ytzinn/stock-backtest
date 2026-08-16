"""화면이 실제로 렌더되는가 (설계메모 v3 §8 — 화면 검증).

## 왜 데이터 검사만으로 부족한가

이 저장소는 **데이터 계층에선 멀쩡한데 화면에서만 죽는** 결함을 이미 두 번 만났다.

1. `ScenarioRef` 가 해시 불가였다 (frozen dataclass + dict 필드). Streamlit 이 위젯
   옵션을 해싱하므로 셀렉트 옵션으로 쓰는 순간 화면이 죽는다 (2026-08-14).
2. `st.page_link` 가 페이지 컨텍스트 없이 `url_pathname` 으로 죽는다 (2026-08-15,
   용어사전을 붙이다 발견 — 그래서 그 위젯을 안 쓴다).

둘 다 파이썬 함수를 직접 부르는 테스트로는 절대 안 잡힌다.

## 축을 지정해야 한다

`AppTest` 를 그냥 돌리면 드롭다운 기본값(레이어 축)만 렌더된다. 다른 축의 렌더 경로는
실행되지 않으므로, **그 축의 코드는 검증 없이 배포된다** — 실제로 종목 수 곡선이 그렇게
나갔다. 그래서 `session_state['series_pick']` 로 축을 못 박고 돈다.

전 축을 다 돌리면 느려서 fast suite 를 두 배로 만든다. 렌더 경로가 서로 다른
**대표 축만** 고른다: notes 있는 A형 / B형 raw fallback / 전용 renderer 를 가진 A형.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from dashboard.series import SERIES_BY_ID

pytest.importorskip('streamlit.testing.v1', reason='Streamlit 테스트 하네스가 없다')

from streamlit.testing.v1 import AppTest  # noqa: E402

PAGES = Path(__file__).resolve().parent.parent.parent / 'dashboard/pages'

#: 렌더 경로가 서로 다른 대표 축.
REPRESENTATIVE = [
    'layers',              # A형 + notes + 랜덤 분포 탭
    'n_stocks',            # A형 + 전용 renderer(곡선) + 교차검증
    'time_overfit',        # B형 전용 뷰 (순위 역전)
    'calendar_bootstrap',  # B형 전용 뷰 (forest plot · 다중 판정)
    'decomposition',       # B형 전용 뷰 (사전등록 없는 축)
    'regime_overlay',      # B형 전용 뷰 (철회된 실행 + 히트맵 + PNG)
    'pbr_rules',           # A형 + 왜-지도 + 세트 + "다른 데 있는 태그"
    'benchmarks',          # A형인데 `paths` 를 쓴다 (원본 목록이 B형 전용이었다)
    'daily_nav',           # B형 전용 뷰 (G5 판정 + 낙폭 세 층)
]


def _run(page: str, **state) -> AppTest:
    at = AppTest.from_file(str(PAGES / page), default_timeout=120)
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    return at


def _assert_clean(at: AppTest, what: str) -> None:
    if at.exception:
        raise AssertionError(
            f'{what} 렌더 중 예외:\n  ' +
            '\n  '.join(str(e.message) for e in at.exception))


@pytest.mark.parametrize('series_id', REPRESENTATIVE)
def test_series_explorer_renders_each_representative_axis(series_id):
    """축을 못 박고 렌더한다. 기본값만 돌리면 나머지 축은 검증 없이 나간다."""
    at = _run('series_explorer.py', series_pick=SERIES_BY_ID[series_id])
    _assert_clean(at, f'series_explorer[{series_id}]')


@pytest.mark.parametrize('series_id', ['layers', 'time_overfit', 'decomposition'])
def test_header_explains_the_kind_and_status_codes(series_id):
    """유형·상태가 **고른 축에 맞게** 뜨고, 코드가 아니라 뜻까지 보여야 한다.

    예전에는 이 둘이 필터(multiselect)여서 화면 첫 줄이 "무엇을 볼까"가 아니라
    "무엇을 걸러낼까"로 시작했고, 골라도 `A`·`ARCHIVED` 라는 코드만 남았다.
    """
    from dashboard.series import KIND_MEANING

    spec = SERIES_BY_ID[series_id]
    at = _run('series_explorer.py', series_pick=spec)
    _assert_clean(at, f'series_explorer[{series_id}]')

    caps = ' '.join(str(getattr(c, 'value', '')) for c in at.caption)
    assert KIND_MEANING[spec.kind][1] in caps, \
        f'유형 {spec.kind} 의 설명이 화면에 없다 — 코드만 띄우고 있다'
    assert spec.status.meaning in caps, \
        f'상태 {spec.status.code} 의 뜻이 화면에 없다'


def test_series_explorer_shows_the_glossary_next_to_the_numbers():
    """용어 패널이 실제로 화면에 뜨는가.

    용어사전을 별도 페이지에만 두면 헷갈리는 사람은 **헷갈리는 줄 모르는 채로** 표를
    읽는다. 숫자 옆에 있어야 하고, 그게 붙어 있는지는 화면에서만 확인된다.
    """
    at = _run('series_explorer.py', series_pick=SERIES_BY_ID['n_stocks'])
    _assert_clean(at, 'series_explorer[n_stocks]')
    labels = [e.label for e in at.expander]
    assert any('헷갈리는 이름' in (lab or '') for lab in labels), \
        f'용어 패널이 화면에 없다. 현재 패널: {labels}'


def test_axis_shows_the_tags_whose_numbers_live_elsewhere():
    """비교표에 행이 없는 전략이 **어디에 값이 있는지** 화면이 말해야 한다.

    넷이 그 상태로 방치돼 있었다. 특히 `C_pbr_path_random` 은 채택안 G1 관문의
    귀무분포 그 자체인데, ablation 산출물이 없다는 이유만으로 화면 어디에도 안 떴다.
    안 띄우면 그 전략은 존재하지 않는 것과 같다 (2026-08-15).
    """
    for series_id, tag in (('pbr_rules', 'F_pbr_no_r3r4r5'),
                           ('benchmarks', 'C_pbr_path_random'),
                           ('ranking_signal', 'F_rimrank_no_r3r4')):
        at = _run('series_explorer.py', series_pick=SERIES_BY_ID[series_id])
        _assert_clean(at, f'series_explorer[{series_id}]')
        labels = [e.label or '' for e in at.expander]
        assert any('비교표에 없는 전략' in lab for lab in labels), \
            f'{series_id}: "다른 데 있는 태그" 패널이 화면에 없다. 현재 패널: {labels}'
        frames = ' '.join(df.value.to_csv() for df in at.dataframe)
        assert tag in frames, f'{series_id}: `{tag}` 이 화면 어느 표에도 없다'


def test_axis_never_calls_a_relocated_tag_missing():
    """"다른 데 있다"가 빨간 "산출물 없음" 오류로 뜨면 안 된다.

    정상 상태를 영구 오류로 띄우면, 진짜 유실이 났을 때 아무도 그 줄을 안 읽는다.
    """
    for series_id in ('pbr_rules', 'benchmarks', 'ranking_signal'):
        at = _run('series_explorer.py', series_pick=SERIES_BY_ID[series_id])
        _assert_clean(at, f'series_explorer[{series_id}]')
        errs = ' '.join(str(getattr(e, 'value', '')) for e in at.error)
        assert '산출물이 없는 키' not in errs, \
            f'{series_id}: 산출물이 다른 데 있는 태그를 유실로 띄운다 — {errs}'


def test_pbr_rules_axis_says_the_adopted_set_is_not_the_leader():
    """이 축의 첫 줄이 화면에 떠야 한다 — 채택안이 CAGR 1등이 아니라는 사실.

    표만 보면 사람은 맨 위 숫자를 채택 근거로 읽는다. 실제 근거는 "1등과의 격차가
    한 구간·한 종목에서 나왔고 낙폭은 채택안이 최저" 라서, 그 설명이 빠지면
    화면은 채택 결정과 반대되는 인상을 준다.
    """
    at = _run('series_explorer.py', series_pick=SERIES_BY_ID['pbr_rules'])
    _assert_clean(at, 'series_explorer[pbr_rules]')
    text = ' '.join(
        str(getattr(e, 'value', '') or getattr(e, 'body', ''))
        for group in (at.markdown, at.caption, at.warning, at.info)
        for e in group)
    for must in ('1등이 아니다', '2025-08-20', 'max 선택 금지', '−31.83%'):
        assert must in text, f'PBR 룰 축 왜-지도에 `{must}` 이 화면에 없다'


def test_time_overfit_view_shows_the_verdict_next_to_its_pre_registered_rule():
    """판정과 **그것을 만든 사전등록 규칙**이 한 화면에 함께 떠야 한다.

    전용 뷰가 원본 파일 목록보다 나은 이유가 이것뿐이다. 판정 문자열만 띄우면 사람은
    옆에 있는 다른 숫자(ρ, CI)로 사후 설명을 만든다. 규칙이 화면에서 빠지면 뷰는
    raw 목록보다 **나쁘다** — 근거 없이 결론만 주기 때문이다.
    """
    at = _run('series_explorer.py', series_pick=SERIES_BY_ID['time_overfit'])
    _assert_clean(at, 'series_explorer[time_overfit]')

    text = ' '.join(
        str(getattr(e, 'value', '') or getattr(e, 'body', ''))
        for group in (at.markdown, at.caption, at.warning, at.error, at.info)
        for e in group)
    for must in ('TIME_OVERFIT_CONFIRMED', '사전등록', '수치 산출'):
        assert must in text, f'시간분할 뷰에 `{must}` 이 화면에 없다'
    assert any('0 을 배제하지 못' in str(getattr(e, 'value', '')) for e in at.warning), \
        'CI 가 0 을 배제하지 못한다는 경고가 화면에 없다 — 없으면 ρ 가 판정 근거로 읽힌다'


def test_calendar_bootstrap_view_says_j1_is_undefined_not_zero():
    """"방향 일치율 0%" 로 읽히지 않게 하는 경고가 화면에 있어야 한다.

    분모가 0인 비율을 0% 로 그리는 것은 화면이 할 수 있는 가장 조용한 거짓말이다 —
    "캘린더가 룰 결론을 전부 뒤집었다"로 읽히는데 실제 결과는 정반대에 가깝다
    (뒤집힐 만큼 뚜렷한 방향이 애초에 없었다).
    """
    at = _run('series_explorer.py', series_pick=SERIES_BY_ID['calendar_bootstrap'])
    _assert_clean(at, 'series_explorer[calendar_bootstrap]')
    assert any('0% 가 아니라 정의되지 않' in str(getattr(e, 'value', ''))
               for e in at.warning), 'J1 경고가 화면에 없다'


def test_regime_overlay_view_flags_the_retracted_run():
    """철회된 실행이 있다는 사실이 화면 맨 위에 떠야 한다.

    같은 그리드가 두 날짜로 있고 옛것은 버그로 `68/144 통과` 를 냈다. 화면이 말하지
    않으면 원본 파일 목록에서 그 CSV 를 열어 인용한다 — 이 저장소가 이미 겪은 일이다
    (2026-07-10 리포트 수치 인용 금지).
    """
    at = _run('series_explorer.py', series_pick=SERIES_BY_ID['regime_overlay'])
    _assert_clean(at, 'series_explorer[regime_overlay]')
    boxes = ' '.join(str(getattr(e, 'value', '')) for e in at.error)
    assert '철회된 실행' in boxes, '철회 경고가 화면에 없다'
    assert '68/144' in boxes, '철회된 수치가 무엇이었는지 화면이 밝히지 않는다'


def test_daily_nav_view_shows_g5_next_to_its_pre_registered_limit():
    """G5 판정과 **그것을 만든 한계선**이 한 화면에 함께 떠야 한다.

    이 축이 없던 동안 일별 NAV 36개가 화면 밖에 있었는데, **채택 보류의 유일한
    사유(G5 FAIL)가 거기서 나온다.** 판정 근거가 화면에 없으면 낡아도 아무도 모른다 —
    `C_pbr_path_random` 이 G1 귀무분포인데 안 보이던 것과 같은 상황이었다.
    """
    at = _run('series_explorer.py', series_pick=SERIES_BY_ID['daily_nav'])
    _assert_clean(at, 'series_explorer[daily_nav]')

    text = ' '.join(
        str(getattr(e, 'value', '') or getattr(e, 'body', ''))
        for group in (at.markdown, at.caption, at.warning, at.info, at.error)
        for e in group)
    for must in ('G5', '사전 등록', '-45%'):
        assert must in text, f'일별 NAV 뷰에 `{must}` 이 화면에 없다'
    assert '새로 판정하지 않' in text, \
        '이 화면이 판정을 소유하지 않는다는 사실이 안 적혀 있다'


def test_daily_nav_view_separates_the_two_mdd_axes():
    """낙폭 **세 층**이 표로 떠야 한다. 하나만 인용하면 24%p 가 사라진다.

    같은 전략의 낙폭이 −34% 로도 −58% 로도 인용돼 왔다. 축이 둘(측정 빈도 × 비용)인데
    화면이 하나만 보여주면 그 혼동이 계속된다.
    """
    from dashboard.series_view import daily_nav_summary, mdd_layers

    d = daily_nav_summary()
    if d is None:
        pytest.skip('일별 NAV 산출물이 없다 (git 미추적).')

    at = _run('series_explorer.py', series_pick=SERIES_BY_ID['daily_nav'])
    _assert_clean(at, 'series_explorer[daily_nav]')
    frames = ' '.join(df.value.to_csv() for df in at.dataframe)
    for basis in ('구간 · gross', '일별 · gross', '일별 · net'):
        assert basis in frames, f'낙폭 표에 `{basis}` 층이 없다'

    # 화면 값이 산출물과 같아야 한다 — 화면은 지표를 다시 계산하지 않는다.
    from backtest.canonical_state import collect

    t = d['tags'][collect()['key']]
    rows = mdd_layers(t)
    assert rows[0]['값'] == round(t['endpoint_mdd_gross'] * 100, 2)
    assert rows[1]['값'] == round(t['daily_mdd_gross'] * 100, 2)
    assert rows[2]['값'] == round(t['net']['daily_mdd'] * 100, 2)
    # 빈도 몫이 비용 몫보다 훨씬 커야 한다 (캡션이 그렇게 말한다).
    assert abs(rows[1]['앞 줄 대비 (%p)']) > 10 * abs(rows[2]['앞 줄 대비 (%p)']), \
        '빈도와 비용의 크기 관계가 바뀌었다 — 캡션 문구를 다시 확인하라'


def test_daily_nav_view_flags_tags_whose_reconciliation_failed():
    """정합 게이트가 실패한 전략을 **경고로** 띄우는가.

    그 전략의 일별 값은 판정에 쓰면 안 된다. 표에 섞여만 있으면 같은 무게로 읽힌다.
    """
    from dashboard.series_view import daily_nav_summary, nav_gate_rows

    d = daily_nav_summary()
    if d is None:
        pytest.skip('일별 NAV 산출물이 없다 (git 미추적).')
    failed = [r for r in nav_gate_rows(d) if r['정합 게이트'] != 'PASS']
    if not failed:
        pytest.skip('정합 실패 전략이 없다 — 경고 경로를 확인할 수 없다.')

    at = _run('series_explorer.py', series_pick=SERIES_BY_ID['daily_nav'])
    _assert_clean(at, 'series_explorer[daily_nav]')
    warns = ' '.join(str(getattr(e, 'value', '')) for e in at.warning)
    assert '정합 게이트가 실패한 전략' in warns, '정합 실패 경고가 화면에 없다'
    assert failed[0]['전략'] in warns, f"{failed[0]['전략']} 이 경고에 안 적혀 있다"


def test_decomposition_view_says_there_is_no_pre_registration():
    """사전등록이 **없는** 축이라는 사실이 화면에 있어야 한다.

    캘린더 축들과 같은 화면 문법으로 그려지면 같은 무게로 읽힌다. 문턱 없이 사후에
    본 수치라는 점이 이 축을 읽는 방법 전부다.
    """
    at = _run('series_explorer.py', series_pick=SERIES_BY_ID['decomposition'])
    _assert_clean(at, 'series_explorer[decomposition]')
    warns = ' '.join(str(getattr(e, 'value', '')) for e in at.warning)
    assert 'pre_registered' in warns and '탐색적 진단' in warns, \
        '사전등록 부재 경고가 화면에 없다'


def test_glossary_page_renders_and_search_narrows():
    """용어사전 페이지와 검색이 도는가."""
    at = _run('glossary.py')
    _assert_clean(at, 'glossary')
    assert len(at.expander) > 1, '용어 항목이 화면에 하나도 없다'

    at.text_input[0].set_value('artifact_key').run()
    _assert_clean(at, 'glossary/search')
    assert len(at.expander) == 1, \
        f'`artifact_key` 검색이 좁혀지지 않는다 (항목 {len(at.expander)}개)'


@pytest.mark.parametrize('series_id', ['layers', 'r6_loo', 'ranking_signal'])
def test_why_map_panel_reaches_the_screen(series_id):
    """왜-지도가 실제로 화면에 뜨는가.

    등록 대장에만 있고 화면에 안 뜨면 아무도 안 읽는다 — 그게 이 계보가 여태 검토
    문서에만 흩어져 있던 상태였다. 결정 이력과 경고가 함께 떠야 한다.
    """
    from dashboard.series import SERIES_BY_ID as _BY_ID

    spec = _BY_ID[series_id]
    at = _run('series_explorer.py', series_pick=spec)
    _assert_clean(at, f'series_explorer[{series_id}]')

    labels = [e.label or '' for e in at.expander]
    assert any('어떻게 읽나' in lab for lab in labels), \
        f'왜-지도 패널이 화면에 없다. 현재 패널: {labels}'

    md = [str(getattr(e, 'value', '')) for e in at.markdown]
    text = ' '.join(md)
    assert spec.why.reading[0][:20] in text, '결과 해석이 화면에 없다'
    assert spec.why.history[0][:20] in text, '결정 이력이 화면에 없다'

    # **해석이 이력보다 먼저** 나와야 한다. 사람이 이 화면에 온 이유는 숫자를 봤기
    # 때문이지 방법론이 궁금해서가 아니다.
    first = next(i for i, t in enumerate(md) if spec.why.reading[0][:20] in t)
    later = next(i for i, t in enumerate(md) if spec.why.history[0][:20] in t)
    assert first < later, '결정 이력이 결과 해석보다 위에 있다 — 순서가 뒤집혔다'

    warned = ' '.join(str(getattr(e, 'value', '')) for e in at.warning)
    for w in spec.why.warnings:
        assert w[:20] in warned, f'주의할 점이 화면에 없다: {w[:40]}'
