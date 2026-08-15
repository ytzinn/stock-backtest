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
