"""
docs/CANONICAL.md 생성기의 옳음 증명.

지키려는 것 3가지 (전부 2026-08-12 에 실제로 데인 것들):

1. **재생성 멱등성** — 같은 재료로 두 번 찍으면 바이트가 같아야 한다. 안 그러면
   "재료가 바뀌었나 / 누가 손댔나"를 기계가 판정할 수 없다. 생성 시각·mtime 을 찍지
   않기로 한 결정이 여기서 지켜지는지 확인한다.
2. **태그 키 조립** — 운영 종목 수가 20→13 으로 바뀌었는데 태그 이름은 그대로였다.
   기본 태그로 조용히 폴백하면 n=20 성적(14.52%)이 운영(18.55%) 수치로 발행된다.
   폴백하지 말고 **없다고 보고**해야 한다.
3. **정합성 검사 발화** — 검사가 실제로 걸리는지. 안 걸리는 검사는 없느니만 못하다.
"""
from __future__ import annotations

import copy
import re

import pytest

from scripts.make_canonical import build, check, collect, render


# ── 1. 멱등성 ────────────────────────────────────────────────────────────────

def test_regeneration_is_byte_identical():
    """두 번 생성해 바이트가 같아야 한다 (생성 시각·mtime 미포함의 귀결)."""
    first, _ = build()
    second, _ = build()
    assert first == second


def test_every_timestamp_in_output_comes_from_a_source():
    """문서에 찍힌 타임스탬프는 **전부 재료에서 온 것**이어야 한다.

    생성 시각을 찍으면 실행마다 diff 가 생겨 멱등성 검사가 늑대소년이 된다.
    문자열 금지("생성 시각"이 이슈 본문에 정당하게 나온다)가 아니라, 실제 불변식으로
    검사한다 — 출력의 모든 ISO 타임스탬프가 입력이 가진 스탬프의 부분집합인가.
    """
    d = _base()
    allowed = {d['abl_tag']['run_at'], d['nav_tag']['generated_at'],
               d['gates']['generated_at']}
    text = render(d, [])
    found = set(re.findall(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', text))
    orphans = {t for t in found if not any(a.startswith(t) for a in allowed)}
    assert not orphans, f'재료에 없는 타임스탬프가 찍혔다: {orphans}'


def test_output_does_not_depend_on_git_head():
    """생성 커밋 SHA 를 담지 않는다 — **자기참조**라서 멱등성이 원리적으로 깨진다.

    파일이 HEAD 를 적는데 그 파일을 커밋하면 HEAD 가 바뀐다. 그러면 커밋 직후
    `--check` 가 항상 실패해 검사가 영구히 빨간불이 된다. 2026-08-12 에 실제로
    이 상태로 커밋했고, 서버 교차 검사에서 발견했다.
    """
    assert 'git_sha' not in collect(), 'HEAD 의존 필드가 되살아났다'


# ── 2. 태그 키 조립 ──────────────────────────────────────────────────────────

def test_lookup_key_carries_n_stocks():
    """조회 키는 `{tag}_n{N_STOCKS}` 로 조립돼야 한다 — 태그 이름만으로 찾으면 안 된다."""
    d = collect()
    assert d['key'] == f'{d["tag"]}_n{d["n_stocks"]}'
    assert d['key'] != d['tag']


def _base(**over):
    """검사 함수에 넣을 최소 입력. 기본 상태는 '문제 없음'."""
    d = {
        'tag': 'F_x', 'n_stocks': 13, 'key': 'F_x_n13',
        'abl_tag': {'cagr': 0.2, 'net_cagr': 0.18, 'n_periods': 20,
                    'run_at': '2026-08-11T23:20:00'},
        'nav_tag': {'net_cagr': 0.185, 'net': {'daily_mdd': -0.58, 'daily_sharpe': 0.72},
                    'generated_at': '2026-08-11T23:35:40'},
        'abl_summary_has_key': True,
        'gates': {'tag': 'F_x_n13', 'draws_n_stocks': 13, 'generated_at': '2026-08-12T00:00:00',
                  'hard_gates': {'G1': {'pass': True}, 'G2': {'pass': True},
                                 'G5': {'pass': False}}},
        'manifest': {'selected_tickers': ['000210'] * 13, 'strategy_version': 'F_x v1.0',
                     'config_hash': 'abc', 'git_commit_sha': 'deadbeef',
                     'signal_date': '2026-08-10', 'expected_turnover': 0.92},
        'tape_cap': 13,
        'issues': [], 'sources': {}, 'config': {},
        'constants': {'RF': 0.0263, 'RK': 0.0873, 'OMEGA': 0.62, 'VB_CAP': 5.0},
    }
    d.update(over)
    return d


def test_clean_state_has_no_problems():
    """정상 상태에서는 경고가 하나도 없어야 한다 — 늘 켜져 있는 경고는 무시된다."""
    assert check(_base()) == []


def test_missing_n_suffixed_metrics_is_reported_not_silently_substituted():
    """운영 키의 지표가 없으면 **없다고 보고**해야 한다 (기본 태그로 대체 금지)."""
    problems = check(_base(abl_tag=None, nav_tag=None))
    assert any('F_x_n13.json' in p for p in problems)
    assert any('daily_nav' in p and 'F_x_n13' in p for p in problems)


# ── 3. 정합성 검사 발화 ──────────────────────────────────────────────────────

@pytest.mark.parametrize('override, needle', [
    ({'gates': None},                       'gate_results_F_x_n13.json'),
    ({'abl_summary_has_key': False},        '병합돼 있지 않다'),
    ({'tape_cap': 20},                      'tape 의 종목 수 상한이 20'),
    ({'tape_cap': None},                    'tape) 이 없다'),
])
def test_each_check_fires(override, needle):
    problems = check(_base(**override))
    assert any(needle in p for p in problems), f'{needle!r} 검사가 발화하지 않았다: {problems}'


def test_gate_tag_misattribution_is_caught():
    """게이트 산출물이 **다른 전략** 것이면 잡아야 한다 — 2026-08-12 오귀속 재발 방지."""
    g = copy.deepcopy(_base()['gates'])
    g['tag'] = 'F_pbr_no_r3r4'
    problems = check(_base(gates=g))
    assert any('오귀속' in p for p in problems)


def test_null_pool_n_mismatch_is_caught():
    """귀무분포의 n 이 운영과 다르면 G1 이 성립하지 않는다."""
    g = copy.deepcopy(_base()['gates'])
    g['draws_n_stocks'] = 20
    problems = check(_base(gates=g))
    assert any('귀무분포' in p and '합격선' in p for p in problems)


def test_manifest_claiming_pass_without_gate_artifact_is_caught():
    """게이트 산출물 없이 manifest 문자열이 PASS 를 주장하면 잡아야 한다."""
    m = copy.deepcopy(_base()['manifest'])
    m['strategy_version'] = 'F_x v1.0 (SPEC_10 관문: G1·G2 PASS, G5 FAIL)'
    problems = check(_base(manifest=m, gates=None))
    assert any('PASS 를 주장' in p for p in problems)


# ── 렌더링 계약 ──────────────────────────────────────────────────────────────

def test_render_surfaces_warnings_at_top():
    """경고는 본문 수치보다 **위**에 와야 한다 — 아래 있으면 안 읽힌다."""
    d = _base(gates=None)
    text = render(d, check(d))
    assert text.index('정합성 경고') < text.index('## 성적')


def test_render_refuses_to_print_other_tags_gate_result():
    """게이트 미산출이면 판정표를 그리지 않는다 (빈 표도, 대체값도 안 된다)."""
    d = _base(gates=None)
    text = render(d, check(d))
    gate_section = text.split('## SPEC_10 하드 게이트')[1].split('##')[0]
    assert '미산출' in gate_section
    assert 'PASS' not in gate_section
