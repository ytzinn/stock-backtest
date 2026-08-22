"""
STALE 태그 인용 차단 — A-2(R3·R4 fail-closed, 2026-08-22) 로 무효화된 산출물.

이 저장소는 "낡은 수치가 산문에 박혀 있으면 다음 세션이 그대로 인용한다"를 반복해서
겪었다 (CLAUDE.md '문서 정정 규칙'). 규칙 정의가 바뀌어 산출물이 무효가 된 경우도
같은 함정이라, **인용하려는 시도가 걸리도록** 여기에 검사를 둔다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backtest.ablation import ABLATION_CONFIGS, STALE_TAGS

ADOPTED = 'F_pbr_ma200'
_ALL = {'R1', 'R2', 'R3', 'R4', 'R5', 'R6'}


def _rules(tag: str) -> set[str]:
    cfg = ABLATION_CONFIGS[tag]
    if not cfg.get('use_stability'):
        return set()
    rules = set(cfg.get('stability_rules', _ALL))
    if not cfg.get('stability_r6', True):
        rules -= {'R6'}
    return rules


def test_stale_set_matches_configs():
    """레지스트리가 설정에서 유도되는가 — 손으로 적은 목록이 낡는 것을 막는다."""
    expected = {t for t in ABLATION_CONFIGS if _rules(t) & {'R3', 'R4'}}
    assert set(STALE_TAGS) == expected, (
        f'STALE_TAGS 가 설정과 어긋난다. 누락 {expected - set(STALE_TAGS)} / '
        f'초과 {set(STALE_TAGS) - expected}'
    )


def test_stale_set_is_not_empty():
    """양성 대조 — 비어 있으면 검사 자체가 무의미해진다."""
    assert len(STALE_TAGS) >= 20, f'STALE 태그가 {len(STALE_TAGS)}개뿐 — 유도 로직 확인'


def test_adopted_tag_is_not_stale():
    """채택안은 R3·R4 를 쓰지 않으므로 A-2 의 영향을 받지 않는다."""
    assert ADOPTED not in STALE_TAGS
    assert not (_rules(ADOPTED) & {'R3', 'R4'})


@pytest.mark.parametrize('tag', sorted(STALE_TAGS))
def test_every_stale_tag_has_a_reason(tag):
    reason = STALE_TAGS[tag]
    assert 'A-2' in reason and ('R3' in reason or 'R4' in reason), reason


def test_stale_artifacts_are_older_than_the_rule_change():
    """STALE 태그의 산출물이 규칙 변경 이후 값처럼 보이지 않는가.

    산출물이 없으면(미추적·미실행) 통과 — 이 검사는 '있는데 낡은 것'만 잡는다.
    재실행해서 새로 만들면 STALE_TAGS 에서 빼야 하고, 그때 이 검사가 알려준다.
    """
    abl = Path('experiments/ablation')
    if not abl.is_dir():
        pytest.skip('산출물 디렉토리 없음')
    fresh = []
    for tag in STALE_TAGS:
        p = abl / f'{tag}.json'
        if not p.exists():
            continue
        import json
        run_at = json.loads(p.read_text(encoding='utf-8')).get('run_at', '')
        if str(run_at) >= '2026-08-22':
            fresh.append((tag, run_at))
    assert not fresh, (
        f'STALE 로 표시된 태그의 산출물이 규칙 변경(2026-08-22) 이후에 생성됐다: {fresh}. '
        f'재실행했다면 STALE_TAGS 에서 제거하라.'
    )
