"""
스키마 검사는 **진입 시점**에 걸려야 한다 — INSERT 도중 SQL 에러가 아니라.

2026-08-23: `market_cap_ingest` 의 `ON CONFLICT ... updated_at = now()` 가 커밋됐는데
v12 는 아직 미적용이었다. 코드만 배포됐다면 다음 크론 적재가 부분 적재를 남기고
`column "updated_at" does not exist` 로 죽는다. 이 테스트는 그 검사가 (a) 존재하고
(b) 장식이 아니며 (c) 무엇을 적용해야 하는지 말하는지를 고정한다.
"""
from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

from ingest import schema_guard as SG

MODULES = {
    'ingest/price_ingest.py':      ('PRICE_INGEST',      SG.PRICE_INGEST),
    'ingest/market_cap_ingest.py': ('MARKET_CAP_INGEST', SG.MARKET_CAP_INGEST),
    'ingest/delisting_ingest.py':  ('DELISTING_INGEST',  SG.DELISTING_INGEST),
}
ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('path', sorted(MODULES))
def test_main_calls_require_schema_before_any_work(path):
    """`main()` 의 **첫 실질 동작**이어야 한다 — 뒤로 밀리면 그만큼 부분 적재가 생긴다."""
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == 'main')
    calls = [n.func.id for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert 'require_schema' in calls, f'{path}: main() 이 require_schema 를 부르지 않는다'
    # parse_args 직후여야 한다: 그 사이에 다른 적재 호출이 끼면 안 된다.
    ingesty = [c for c in calls if c.startswith('ingest_') or c.startswith('collect_')]
    if ingesty:
        assert calls.index('require_schema') < calls.index(ingesty[0]), (
            f'{path}: 적재 호출 {ingesty[0]} 이 스키마 검사보다 먼저다')


@pytest.mark.parametrize('path', sorted(MODULES))
def test_declaration_is_not_vacuous(path):
    """선언한 컬럼·제약을 그 모듈이 **실제로 SQL 에서 쓰는지** 본다.

    안 쓰는 이름을 선언하면 검사는 통과만 하는 장식이 된다 — 이 프로젝트에서
    반복된 실패 유형(`is_financial` 이 아무 데서도 안 읽히던 것)과 같다.
    """
    _, reqs = MODULES[path]
    src = (ROOT / path).read_text(encoding='utf-8')
    for r in reqs:
        assert r.name in src, (
            f'{path}: {r.name!r} 을 요구한다고 선언했는데 모듈 안에서 쓰지 않는다')


@pytest.mark.parametrize('req', [r for _, rs in MODULES.values() for r in rs],
                         ids=lambda r: r.name)
def test_named_migration_file_exists(req):
    """메시지가 가리키는 마이그레이션이 실재해야 한다 — 없으면 안내가 막다른 길이다."""
    p = ROOT / 'ingest' / 'migrations' / f'{req.migration}.sql'
    assert p.exists(), f'{req.name}: {p.name} 이 없다'
    assert req.kind in ('column', 'constraint')


def test_message_names_the_migration_and_both_ports():
    """양성 대조 — 없는 요구를 넣으면 메시지가 무엇을 어떻게 적용할지 말한다."""
    bogus = SG.Requirement('market_cap_history', 'nonexistent_col',
                           'v12_market_cap_updated_at', '테스트용')
    msg = SG.format_missing([bogus], who='test')
    assert 'nonexistent_col' in msg
    assert 'python -m ingest.migrations.apply v12_market_cap_updated_at' in msg
    assert '5433' in msg and '5436' in msg


def test_guard_raises_its_own_error_type():
    """SchemaTooOld 는 RuntimeError 하위여야 한다 — 크론이 조용히 삼키지 않도록."""
    assert issubclass(SG.SchemaTooOld, RuntimeError)
    assert 'db_conn' in inspect.getsource(SG.require_schema)
