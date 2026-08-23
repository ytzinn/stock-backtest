"""
fail-closed 불변식 — **없는 것은 오류를 내지 않는다**를 테스트로 못박는다.

    ∀ 규칙 R ∈ {R1..R6}, ∀ 입력 필드 f:
        R(입력에서 f 를 결측시킨 것) ≠ PASS      (정책이 'reject' 일 때)

이 한 줄이 fs_div · R6 · R2 · R3 · R4 결함을 전부 잡았을 것이다. 여섯 세션의 결함이
모두 같은 유형이었다 — 조건문 모양(`and x is not None`) 안에 숨은 fail-open.

정책이 'pass' 인 규칙은 **지금 fail-open 인 것이 사실**이므로 불변식을 요구하지 않는다.
대신 그 목록을 `test_open_policy_scope_is_declared` 가 고정해, 조용히 늘어나지 못하게 한다.
"""
from __future__ import annotations

from datetime import date

import pytest

from backtest.filters.stability_filter import (
    CURRENT_INSUFFICIENT_POLICY,
    INSUFFICIENT_CHOICES,
    _financial_stability_filter,
)

REBAL = date(2024, 4, 3)
ALL_RULES = frozenset({'R1', 'R2', 'R3', 'R4', 'R5', 'R6'})
ALL_REJECT = {r: 'reject' for r in ALL_RULES}

#: 규칙별 "판정에 필요한 입력" — 이 중 하나라도 빠지면 PASS 가 나오면 안 된다.
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    'R1': ('자본총계', '부채총계'),
    'R2': ('자본총계', '단기차입금'),
    'R3': ('매출액',),
    'R4': ('영업활동현금흐름',),
    'R5': ('영업활동현금흐름', '재무활동현금흐름'),
    'R6': ('당기순이익', '영업활동현금흐름', '지배기업소유주지분'),
}


def _healthy() -> dict:
    """모든 규칙을 통과하는 건전한 재무 스냅샷."""
    return {
        '자본총계': 1_000.0,
        '부채총계': 500.0,          # 부채비율 50%
        '단기차입금': 100.0,          # 차입금비율 10%
        '매출액': 1_000.0,
        '영업활동현금흐름': 200.0,
        '재무활동현금흐름': -50.0,
        '당기순이익': 200.0,
        '지배기업소유주지분': 1_000.0,   # adjROE 20% > r
    }


def _run(pit0, pit1, pit2, rules, policy):
    return _financial_stability_filter(
        'T', REBAL, pit0, pit1, pit2, True, frozenset(rules), on_insufficient=policy
    )


def test_healthy_input_passes_all_rules():
    """양성 대조의 반대쪽 — 결측이 없으면 통과해야 한다. 아니면 픽스처가 틀린 것이다."""
    h = _healthy()
    ok, reasons = _run(h, _healthy(), _healthy(), ALL_RULES, ALL_REJECT)
    assert ok, f'건전한 입력이 탈락했다 — 픽스처 오류: {reasons}'


@pytest.mark.parametrize('rule', sorted(ALL_RULES))
def test_missing_field_never_passes(rule):
    """**본 불변식.** 필요한 입력이 빠지면 그 규칙은 PASS 를 내지 않는다."""
    for field in REQUIRED_FIELDS[rule]:
        pit0, pit1, pit2 = _healthy(), _healthy(), _healthy()
        for p in (pit0, pit1, pit2):
            p.pop(field, None)
        ok, reasons = _run(pit0, pit1, pit2, {rule}, ALL_REJECT)
        assert not ok, (
            f'{rule}: {field!r} 가 없는데 통과했다 — fail-open. '
            f'없는 것이 오류를 내지 않는 바로 그 결함이다.'
        )


@pytest.mark.parametrize('rule', sorted(ALL_RULES))
def test_empty_input_never_passes(rule):
    """전 계정 결측 — 가장 단순한 형태."""
    ok, _ = _run({}, None, None, {rule}, ALL_REJECT)
    assert not ok, f'{rule}: 빈 입력이 통과했다'


def test_policy_is_required_and_validated():
    """정책은 필수 인자다 — 기본값에 결정을 맡기지 않는다."""
    with pytest.raises(TypeError):
        _financial_stability_filter('T', REBAL, _healthy(), None, None)   # noqa
    with pytest.raises(ValueError):
        _run(_healthy(), None, None, ALL_RULES, {'R1': 'pass'})           # 일부 누락
    with pytest.raises(ValueError):
        _run(_healthy(), None, None, ALL_RULES, {**ALL_REJECT, 'R1': 'skip'})


def test_open_policy_scope_is_declared():
    """아직 fail-open 인 규칙 목록을 고정한다 — 조용히 늘어나지 못하게.

    줄이는 것은 자유이나 늘리려면 이 테스트를 고쳐야 하고, 그러면 눈에 띈다.
    """
    assert set(CURRENT_INSUFFICIENT_POLICY) == ALL_RULES
    assert all(v in INSUFFICIENT_CHOICES for v in CURRENT_INSUFFICIENT_POLICY.values())
    open_now = {r for r, v in CURRENT_INSUFFICIENT_POLICY.items() if v == 'pass'}
    assert open_now == {'R1', 'R2', 'R5', 'R6'}, (
        f'fail-open 범위가 바뀌었다: {sorted(open_now)}. '
        f'닫았다면 이 테스트를 함께 고쳐라 (RULE-SILENT-PASS).'
    )


def test_pass_policy_preserves_legacy_behaviour():
    """'pass' 정책에서는 종전처럼 결측이 통과한다 — A-2 이전 동작 보존 확인."""
    legacy = {r: 'pass' for r in ALL_RULES}
    ok, _ = _run({}, None, None, {'R1', 'R2', 'R5', 'R6'}, legacy)
    assert ok, '정책 pass 인데 결측이 탈락했다 — 하위호환이 깨졌다'
