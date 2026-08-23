"""
과거 행 재작성은 **명시된 경로에서만** — 주석이 아니라 실행 시점 검사여야 한다.

같은 결함이 세 번 반복됐다: `stability_filter` 의 "DQ Gate 에서 이미 제거됨"(10개월간
거짓), `price_ingest` 의 "--full 에서만 호출할 것"(delisting_ingest 가 조건 없이 호출),
`market_cap_ingest` 의 "--full 전용"(같은 구조). 전부 **사실 주장을 주석에 둔** 결과다.

이 테스트는 그 주장이 코드에 있는지만 본다. 네트워크·DB 를 타지 않는다 —
가드는 어떤 부수효과보다 먼저 발동해야 하므로, 인자 검증만으로 확인된다.
"""
from __future__ import annotations

import inspect

import pytest

from ingest import market_cap_ingest as MC
from ingest import price_ingest as PI

#: (모듈, 함수명, 허용 사유 집합)
GUARDED = [
    (PI, 'collect_price_and_turnover', PI.REWRITE_REASONS),
    (MC, 'collect_market_cap',         MC.REWRITE_REASONS),
    (MC, 'ingest_all_full',            MC.REWRITE_REASONS),
    (MC, 'rebuild_from_snapshot',      MC.REWRITE_REASONS),
]


@pytest.mark.parametrize('mod,name,_reasons', GUARDED, ids=lambda v: getattr(v, '__name__', v))
def test_rewrite_reason_has_no_default(mod, name, _reasons):
    """기본값이 있으면 가드가 장식이 된다 — 호출자가 아무것도 결정하지 않고 통과한다."""
    par = inspect.signature(getattr(mod, name)).parameters['rewrite_reason']
    assert par.kind is inspect.Parameter.KEYWORD_ONLY, f'{name}: 키워드 전용이어야 한다'
    assert par.default is inspect.Parameter.empty, (
        f'{name}: rewrite_reason 에 기본값 {par.default!r} 이 있다 — '
        f'결정을 조용히 기본값에 맡기지 않는다'
    )


@pytest.mark.parametrize('mod,name,reasons', GUARDED, ids=lambda v: getattr(v, '__name__', v))
def test_rejects_unlisted_reason(mod, name, reasons):
    """허용 목록 밖이면 ValueError. **DB·네트워크에 닿기 전에** 던져야 한다."""
    fn = getattr(mod, name)
    kw = {'rewrite_reason': 'cron'}          # 실제로 뚫렸던 경로의 이름
    if name == 'collect_price_and_turnover':
        args = ('005930',)
    elif name == 'collect_market_cap':
        args = ('005930', 1000)
    else:
        args = ()
    with pytest.raises(ValueError, match='DRIFT-INGEST-001'):
        fn(*args, **kw)


@pytest.mark.parametrize('mod,name,reasons', GUARDED, ids=lambda v: getattr(v, '__name__', v))
def test_reason_set_is_not_empty_and_excludes_cron(mod, name, reasons):
    """허용 목록에 '증분'·'cron' 류가 들어오면 가드가 무의미해진다."""
    assert reasons, f'{name}: 허용 사유 집합이 비었다'
    assert not ({'cron', 'incremental', 'daily'} & set(reasons)), (
        f'{name}: 정기 실행 경로가 재작성 허용 목록에 들어갔다 — {sorted(reasons)}'
    )


def test_market_cap_upserts_stamp_updated_at():
    """ON CONFLICT DO UPDATE 는 updated_at 을 찍어야 한다 (v12).

    안 찍으면 "언제 재작성됐는가"에 다시 답할 수 없게 된다 — 이번 진단에서
    market_cap_history 의 96.91% 가 언제 쓰였는지 알 수 없었던 이유가 그것이다.
    """
    src = inspect.getsource(MC)
    n_upsert = src.count('ON CONFLICT (ticker, date) DO UPDATE SET')
    assert n_upsert == src.count('updated_at = now()'), (
        f'DO UPDATE {n_upsert}곳 중 updated_at 을 찍지 않는 곳이 있다'
    )
