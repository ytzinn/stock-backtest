"""용어사전(sub2)이 실제 산출물과 어긋나지 않는가.

용어사전을 산문으로 두지 않고 등록 대장으로 만든 값이 여기서 나온다. 설명글은 **한 번
쓰면 아무도 다시 안 본다** — 산출물이 재발행되면 조용히 낡고, 낡은 설명은 없는 것보다
나쁘다. "구간 21개"라고 적힌 설명을 믿고 사람이 21로 계산하면 1.86%p 어긋나기 때문이다.

그래서 두 종류를 검사한다:

- **구조** — id 유일 / 근거 경로 실재 / 축 id 실재. 산출물 없이도 항상 돈다.
- **값** — 본문에 박힌 23·21·20, −34.14%·−58.12% 같은 숫자를 산출물에서 다시 계산해
  대조한다. 산출물이 없는 환경(막 clone 한 개발 PC)에서는 skip 이고, **skip 은 통과가
  아니다** (`tests/integrity/README.md`).
"""
from __future__ import annotations

import json
import re

import pandas as pd
import pytest

from backtest import canonical_state as cs
from dashboard.glossary import (
    GLOSSARY,
    GLOSSARY_BY_ID,
    index_rows,
    search,
    source_file,
    terms_for,
)
from dashboard.series import SERIES, SERIES_BY_ID


def _norm(text: str) -> str:
    """U+2212(−)와 ASCII 하이픈을 같은 것으로 본다. 문서는 −, 파이썬은 - 를 쓴다."""
    return text.replace('−', '-')


@pytest.fixture(scope='module')
def adopted():
    """현행 채택안의 수집 결과. 산출물이 없으면 값 검사를 skip 한다."""
    d = cs.collect()
    if d['abl_tag'] is None or d['nav_tag'] is None:
        pytest.skip('현행 채택안 산출물이 없다 (git 미추적). 서버에서 판정할 것.')
    return d


# ── 구조 (산출물 없이도 항상 돈다) ───────────────────────────────────────────

def test_ids_and_terms_are_unique():
    ids = [t.id for t in GLOSSARY]
    terms = [t.term for t in GLOSSARY]
    assert len(ids) == len(set(ids)), f'용어 id 중복: {ids}'
    assert len(terms) == len(set(terms)), f'표제어 중복: {terms}'


def test_every_term_has_a_one_liner_and_a_body():
    """한 줄과 본문 **둘 다** 있어야 한다.

    한 줄만 있으면 헷갈리는 순간에 판별을 못 하고, 본문만 있으면 목록에서 못 찾는다.
    훑어보기와 확인하기는 다른 동작이라 한쪽으로 대신할 수 없다.
    """
    for t in GLOSSARY:
        assert t.term.strip(), f'{t.id}: 표제어가 비었다'
        assert len(t.one_line.strip()) > 10, f'{t.id}: 한 줄 정의가 너무 짧다'
        assert len(t.body.strip()) > 100, f'{t.id}: 본문이 사실상 비었다'


def test_source_paths_exist():
    """근거 경로가 살아 있는가. 죽은 링크는 근거가 아니라 근거인 척이다.

    시리즈 등록 대장에 같은 검사를 만들었을 때 SPEC 파일명 4개가 틀려 있었다.
    손으로 적은 경로는 반드시 틀린다는 전제로 검사한다.
    """
    dead = [f'{t.id}: {s}' for t in GLOSSARY for s in t.sources
            if not (cs.ROOT / source_file(s)).exists()]
    assert not dead, '용어 근거 경로가 없다:\n  ' + '\n  '.join(dead)


def test_source_line_numbers_are_within_the_file():
    """`path:LINE` 의 줄 번호가 파일 길이를 넘지 않아야 한다.

    줄 번호는 코드가 움직이면 낡는다. 정확히 그 줄인지까지는 검사할 수 없지만,
    파일 끝을 넘어간 번호는 확실히 거짓이다 — 최소한 그건 잡는다.
    """
    bad = []
    for t in GLOSSARY:
        for s in t.sources:
            m = re.search(r':(\d+)$', s)
            if not m:
                continue
            path = cs.ROOT / source_file(s)
            n_lines = len(path.read_text(encoding='utf-8').splitlines())
            if int(m.group(1)) > n_lines:
                bad.append(f'{t.id}: {s} (파일은 {n_lines}줄)')
    assert not bad, '근거 줄 번호가 파일 끝을 넘는다:\n  ' + '\n  '.join(bad)


def test_series_references_are_real_axes():
    """축 전용 용어가 가리키는 축 id 가 실재해야 한다. 오타면 영영 안 뜬다."""
    unknown = [(t.id, sid) for t in GLOSSARY for sid in t.series
               if sid not in SERIES_BY_ID]
    assert not unknown, f'존재하지 않는 축을 가리킨다: {unknown}'


def test_common_terms_reach_every_axis():
    """공통 용어는 모든 축에 붙는다 — 새 축이 생겨도 자동으로 따라붙어야 한다.

    축을 하나하나 열거해야 붙는 구조였다면 축이 늘어날 때마다 조용히 누락된다.
    """
    common = {t.id for t in GLOSSARY if t.is_common}
    assert common, '공통 용어가 하나도 없다 — 기본값이 죽었다'
    for s in SERIES:
        got = {t.id for t in terms_for(s.id)}
        assert common <= got, f'{s.id} 축에 공통 용어가 빠졌다: {common - got}'


def test_axis_specific_terms_are_reachable():
    for t in GLOSSARY:
        for sid in t.series:
            assert t in terms_for(sid), f'{t.id} 가 {sid} 축에서 안 뜬다'


def test_search_finds_terms_by_the_identifier_people_actually_see():
    """사람은 화면에서 본 식별자로 찾는다. 한글 설명으로 안 찾는다."""
    assert GLOSSARY_BY_ID['tag_vs_artifact_key'] in search('artifact_key')
    assert GLOSSARY_BY_ID['source_file_vs_summary'] in search('median_cagr')
    assert GLOSSARY_BY_ID['mdd_basis'] in search('MDD')
    assert search('') == GLOSSARY          # 빈 검색은 전체
    assert search('존재하지않는용어') == ()


def test_index_rows_are_renderable():
    """표 행은 화면이 아니라 뷰모델이 만든다 — 화면 안에 있으면 검사할 수 없다."""
    rows = index_rows()
    assert len(rows) == len(GLOSSARY)
    assert all(isinstance(v, str) and v for r in rows for v in r.values()), \
        '표 행에 빈 칸이나 비문자열이 있다 — Arrow 직렬화가 터진다'


# ── 값 — 본문의 숫자를 산출물에서 다시 계산해 대조한다 ───────────────────────

def test_period_layer_numbers_match_the_artifacts(adopted):
    """23 → 21 → 20 이 실제 산출물의 숫자인가.

    이 셋이 어긋나면 화면 설명이 사람을 틀린 계산으로 이끈다. 1.86%p 오염이 바로
    그 경로였다 — 21 로 세고 연수를 구간수÷2 로 잡았다.
    """
    key = adopted['key']
    csv = cs.ABL_DIR / f'{key}_periods.csv'
    if not csv.exists():
        pytest.skip(f'{csv.name} 이 없다 (git 미추적).')

    df = pd.read_csv(csv)
    raw, gated = len(df), int((df['n_gate'] > 0).sum())
    closed = adopted['abl_tag']['n_periods']

    body = GLOSSARY_BY_ID['period_layers'].body
    assert key in body, (
        f'용어사전이 예로 든 태그가 현행 채택안({key})이 아니다 — 채택안이 바뀌면 '
        f'표의 숫자도 함께 갱신해야 한다.')
    for n, label in ((raw, '구간 CSV 원본 행'), (gated, 'n_gate>0'), (closed, '완결 구간')):
        assert f'**{n}**' in body, f'{label} 이 실제로는 {n} 인데 용어사전 표에 없다'
    assert raw >= gated >= closed, (
        f'세 층의 대소가 뒤집혔다 ({raw}/{gated}/{closed}) — 용어사전 서술이 무효다')


def test_mdd_basis_numbers_match_the_artifacts(adopted):
    """구간·gross / 일별·gross / 일별·net 세 값이 산출물과 같은가.

    두 축(측정 빈도 · 비용)이 겹쳐 있어 "구간 −34% vs 일별 −58%" 로만 적으면 차이의
    원인을 비용으로 오해한다. 실제로는 빈도가 23%p, 비용이 1%p 다.
    """
    body = _norm(GLOSSARY_BY_ID['mdd_basis'].body)
    nav = adopted['nav_tag']
    values = {
        '구간·gross':  adopted['abl_tag']['mdd'],
        '일별·gross':  nav['daily_mdd_gross'],
        '일별·net':    nav['net']['daily_mdd'],
    }
    for label, v in values.items():
        assert f'{abs(v) * 100:.2f}%' in body, \
            f'{label} MDD 가 실제로는 {v * 100:.2f}% 인데 용어사전 본문에 그 값이 없다'

    # 서술의 방향 자체도 검사한다 — 숫자만 맞고 해석이 뒤집힌 경우를 잡는다.
    freq_gap = abs(values['일별·gross'] - values['구간·gross'])
    cost_gap = abs(values['일별·net'] - values['일별·gross'])
    assert freq_gap > cost_gap * 5, (
        f'측정 빈도 격차({freq_gap:.2%})가 비용 격차({cost_gap:.2%})를 크게 웃돈다는 '
        f'서술이 더 이상 사실이 아니다 — 본문을 고쳐라')


def test_endpoint_mdd_is_the_same_number_the_comparison_table_shows(adopted):
    """산출물 `mdd` 가 곧 `endpoint_mdd_gross` 인가.

    용어사전이 "`mdd` = `endpoint_mdd_gross`" 라고 못 박았다. 두 필드가 갈라지면
    비교표 열이 어느 정의인지 다시 알 수 없어진다.
    """
    assert adopted['abl_tag']['mdd'] == pytest.approx(
        adopted['nav_tag']['endpoint_mdd_gross'], abs=1e-9)


def test_truncation_curve_claims_match_the_artifact():
    """곡선의 모든 점이 절단이고, 재실행은 별도 배열에 있는가.

    인수인계 문서는 "이름은 둘 다 n 이고 `method` 필드로 구분"이라고 적었지만 실제
    산출물은 그렇지 않다 — `points[]` 는 전부 `truncation` 이고 재실행값은
    `cross_check_vs_rerun[]` 에 따로 있다. 용어사전은 산출물 쪽을 적었고, 여기서 그걸
    못 박는다.
    """
    path = cs.ROOT / 'experiments/analysis/n_stocks_curve.json'
    if not path.exists():
        pytest.skip('n_stocks_curve.json 이 없다 (git 미추적).')
    d = json.loads(path.read_text(encoding='utf-8'))

    methods = {p['method'] for p in d['points']}
    assert methods == {'truncation'}, \
        f'곡선 점의 method 가 절단만이 아니다: {methods} — 용어사전 서술이 무효다'
    assert d['cross_check_vs_rerun'], '재실행 대조 배열이 비었다'

    body = GLOSSARY_BY_ID['rerun_vs_truncation'].body
    n_rerun = len(d['cross_check_vs_rerun'])
    assert f'**{n_rerun}개**' in body, \
        f'재실행이 실제로는 {n_rerun}개인데 용어사전에 그 개수가 없다'
