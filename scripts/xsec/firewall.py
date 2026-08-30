"""SPEC_15 §6-1 — `MDE_ICIR` 사전등록 방화벽.

**문제**: `MDE_ICIR` 의 분모는 실현 `sd(IC_t)` 다. 그걸 구하려면 `IC_t` 전 계열을
계산해야 하고, **그 순간 `mean(IC)` 가 같은 배열 안에 들어 있다.** 즉 "MDE 를 본 측정
산출 전에 커밋한다" 는 요구가 그대로는 절차적으로 실행 불가능하다.

**해소**: 계산은 하되 **방출 지점에서 원천 차단**한다. `a2` 산출물에는 `sd` 와 `MDE` 만
들어가고, `estimate.py`(S-4)가 `mean(IC)` 의 유일한 노출 지점이다.

`[Claude 의견]` **"계산했지만 보지 않았다" 는 준수 근거가 아니다 — 검사가 근거다.**
직전 세션의 메타 테스트가 `or isinstance(AssertionError)` 로 느슨해 자기가 잡으려던 것과
같은 결함이었다. 방화벽도 음성 대조로 발화를 확인하지 않으면 같은 운명이다.
그래서 `assert_clean` 은 경고가 아니라 예외를 던지고, 호출부는 그것을 낮추지 않는다.
"""
from __future__ import annotations

#: `a2` 산출물에 **들어가도 되는** 키. 화이트리스트다 — 새 키를 늘릴 때 여기를 고쳐야 하고,
#: 그 순간 "이게 IC 수준 요약인가" 를 사람이 한 번 판단하게 된다.
ALLOWED_A2_KEYS = frozenset({
    'period_set', 'T', 'sd_null_ic', 'sd_realized_ic',
    'mde_perm', 'mde_icir', 'seed', 'B',
})

#: **절대 방출 금지.** 값이 `null` 이어도 금지다 — 키의 존재 자체가 "이 자리에 그 값이
#: 있었다" 는 신호이고, 다음 사람이 채워 넣게 된다.
FORBIDDEN_KEYS = frozenset({
    'mean_ic', 'ic_series', 'icir', 't_stat', 'p_value', 'lambda_bar',
})


class FirewallBreach(RuntimeError):
    """방화벽 위반. **경고로 낮추지 마라** — 이 예외가 사전등록을 지키는 마지막 선이다."""


def _walk(obj, path='$'):
    """중첩 dict/list 를 전부 훑는다. 최상위만 보면 한 겹만 감싸도 통과한다."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield f'{path}.{k}', k
            yield from _walk(v, f'{path}.{k}')
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from _walk(v, f'{path}[{i}]')


def assert_clean(obj, where: str) -> None:
    """금지 키가 **어느 깊이에도** 없어야 한다. 있으면 즉시 중단."""
    hits = sorted({p for p, k in _walk(obj) if k in FORBIDDEN_KEYS})
    if hits:
        raise FirewallBreach(
            f'{where}: §6-1 금지 키가 산출물에 있다 {hits}. '
            f'값이 null 이어도 금지다 — 키의 존재 자체가 다음 사람에게 채우라는 신호다. '
            f'mean(IC) 의 유일한 노출 지점은 estimate.py(S-4) 다.')


def assert_whitelisted(obj: dict, where: str) -> None:
    """`a2` 최상위는 화이트리스트만 갖는다. `assert_clean` 과 **둘 다** 건다."""
    assert_clean(obj, where)
    extra = sorted(set(obj) - ALLOWED_A2_KEYS)
    if extra:
        raise FirewallBreach(
            f'{where}: 허용되지 않은 최상위 키 {extra}. '
            f'허용 목록 {sorted(ALLOWED_A2_KEYS)}. 새 키가 정말 필요하면 '
            f'firewall.ALLOWED_A2_KEYS 를 **의식적으로** 고쳐라 — 그 순간 그것이 '
            f'IC 수준 요약인지 판단하게 된다.')
