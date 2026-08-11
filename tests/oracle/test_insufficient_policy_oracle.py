"""
모멘텀 필터 `on_insufficient` 스위치의 오라클
(`docs/설계/[이슈] 모멘텀필터_coverage_gate_미구현.md` C′안).

배경: HardFilter는 상장 6개월(약 124거래일)에서 유니버스에 넣는데 MA200은 200거래일,
52주 고가는 252거래일이 필요하다. 그 틈의 종목은 신호가 좋아서가 아니라 자료가 없어서
`passed=True`로 통과했다(fail-open). MA 20/60은 요구 이력이 80거래일 < 124라 구조적으로
발생하지 않았고, MA200 채택으로 처음 드러났다.

**이 파일의 핵심은 기존 태그가 하나도 안 바뀌는 것이다.** `on_insufficient` 키가 없으면
전부 'pass' = 종전 동작이어야 한다.
"""
from __future__ import annotations

import pytest

from backtest.ablation import ABLATION_CONFIGS
from backtest.filters.momentum_criteria import (
    CriterionResult,
    MomentumCriterionFilter,
    build_momentum_criterion_filter,
)


def _filter(on_insufficient=None) -> MomentumCriterionFilter:
    cfg = {'type': 'ma200', 'tag': 'test_ma200', 'ma_window': 200}
    if on_insufficient is not None:
        cfg['on_insufficient'] = on_insufficient
    return build_momentum_criterion_filter(cfg)


def _insufficient() -> CriterionResult:
    """창을 못 채워 자동 통과된 결과 — 정책의 대상."""
    return CriterionResult(True, None, 'passed_insufficient_data', 187, 'insufficient')


# ── 기본값은 종전 동작 (하위호환) ────────────────────────────────────────────

def test_default_is_pass():
    assert _filter().on_insufficient == 'pass'


def test_pass_policy_returns_result_untouched():
    """'pass'는 결과 객체를 **그대로** 돌려줘야 한다 — 비트 불변의 근거."""
    f = _filter('pass')
    r = _insufficient()
    out = f._apply_insufficient_policy(r)
    assert out is r, '결과 객체가 교체되면 기존 태그 재현성을 보장할 수 없다'


def test_key_absent_means_pass():
    """키가 없으면 종전 동작 — 프레임워크 기본값 계약.

    `[2026-08-12 갱신]` 종전 이 자리에는 "어떤 태그도 이 키를 선언하지 않는다"는
    테스트가 있었다. 그건 스위치 도입 **시점의 사실**이었지 correctness 속성이
    아니다. ablation.py 가 전 태그에 'reject'를 일괄 적용하면서 의도가 바뀌었으므로,
    프레임워크 계약(키 부재 → 'pass')만 여기 남기고 태그별 의도는 아래에서 검증한다.
    """
    cfg = {'type': 'ma200', 'tag': 't', 'ma_window': 200}
    assert build_momentum_criterion_filter(cfg).on_insufficient == 'pass'


@pytest.mark.parametrize('tag', [t for t, c in ABLATION_CONFIGS.items()
                                 if isinstance(c.get('momentum_criterion'), dict)])
def test_every_momentum_tag_is_fail_closed(tag):
    """모멘텀 계열 전 태그가 fail-closed 여야 §14-5 이웃 비교가 동일 조건이 된다.

    창 길이가 태그마다 달라 노출 정도가 다른데(ma300 300거래일 vs ma5_20 40거래일),
    일부만 fail-closed 면 오염된 태그와 깨끗한 태그의 순위를 나란히 매기게 된다.
    """
    cfg = ABLATION_CONFIGS[tag]['momentum_criterion']
    assert cfg.get('on_insufficient') == 'reject', f'{tag}: fail-closed 누락'
    assert build_momentum_criterion_filter(dict(cfg)).on_insufficient == 'reject'


# ── reject 정책 ──────────────────────────────────────────────────────────────

def test_reject_policy_flips_insufficient_to_rejected():
    out = _filter('reject')._apply_insufficient_policy(_insufficient())
    assert out.passed is False
    assert out.reason_code == 'rejected_insufficient_data'
    assert out.data_status == 'insufficient', 'data_status 는 보존해야 진단이 구분된다'


def test_reject_policy_preserves_signal_rejection_reason():
    """신호 탈락은 이 정책과 무관하다 — reason_code 가 덮이면 안 된다."""
    r = CriterionResult(False, -0.12, 'rejected_by_signal', 200, 'ok')
    out = _filter('reject')._apply_insufficient_policy(r)
    assert out is r


@pytest.mark.parametrize('status,passed,reason', [
    ('ok',      True,  'passed_by_signal'),
    ('invalid', True,  'invalid_data'),
])
def test_reject_policy_only_touches_insufficient(status, passed, reason):
    """'invalid'는 §4-4에서 따로 다루는 축이다 — 이 스위치가 건드리면 안 된다."""
    r = CriterionResult(passed, 0.05, reason, 200, status)
    assert _filter('reject')._apply_insufficient_policy(r) is r


# ── fail-fast ────────────────────────────────────────────────────────────────

def test_unknown_value_raises():
    with pytest.raises(ValueError, match='on_insufficient'):
        _filter('skip')


def test_key_is_not_forwarded_to_criterion():
    """criterion 생성자로 새면 ghost parameter 가드에 걸려 터진다."""
    f = _filter('reject')
    assert f.criterion.name == 'ma200'
    assert not hasattr(f.criterion, 'on_insufficient')
