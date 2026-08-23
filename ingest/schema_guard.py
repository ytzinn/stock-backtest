"""
적재 진입 시점 스키마 검사 — **런타임 SQL 에러 대신 진입 시점 거부.**

2026-08-23 사고 직전 상황: `market_cap_ingest` 의 `ON CONFLICT ... updated_at = now()`
가 커밋됐는데 v12 마이그레이션은 아직 적용 전이었다. 코드만 배포됐다면 다음 크론
적재가 `column "updated_at" does not exist` 로 죽는다 — 그것도 **INSERT 를 시도한
뒤에**, 로그 깊은 곳에서. 그 시점엔 이미 부분 적재가 일어난 뒤다.

이 모듈은 그 실패를 앞으로 당긴다. 적재 함수가 아니라 `main()` 진입에서 걸리므로
DB 에 아무것도 쓰기 전에 멈추고, 메시지가 **적용할 마이그레이션 이름을 직접 말한다.**

`stability_filter` 의 on_insufficient · `price_ingest` 의 rewrite_reason 과 같은 원칙:
사실 주장("이 컬럼은 있다")을 주석이나 암묵 전제에 두지 않고 실행 시점 검사로 옮긴다.
"""
from __future__ import annotations

from typing import NamedTuple

from ingest.connection import db_conn


class SchemaTooOld(RuntimeError):
    """코드가 요구하는 스키마보다 DB 가 낮다. 적재를 시작하면 안 된다."""


class Requirement(NamedTuple):
    """`kind='column'` 이면 컬럼, `kind='constraint'` 이면 제약 이름을 본다.

    제약도 봐야 한다 — `delisting_ingest` 의 `ON CONFLICT ON CONSTRAINT ...` 는
    컬럼이 다 있어도 제약이 없으면 같은 방식으로 죽는다. 컬럼만 검사하면
    그 경로에 대해서는 **통과만 하는 장식**이 된다.
    """
    table: str
    name: str               # 컬럼명 또는 제약명
    migration: str          # 적용할 마이그레이션 파일명 (확장자 제외)
    why: str
    kind: str = 'column'


#: 모듈별 요구사항. 새 컬럼·제약을 쓰는 코드를 넣을 때 **여기에 함께 등록**한다.
PRICE_INGEST = (
    Requirement('price_history', 'updated_at', 'v11_price_history_updated_at',
                '재작성 시각 기록 - ON CONFLICT DO UPDATE 가 이 컬럼에 쓴다'),
)
MARKET_CAP_INGEST = (
    Requirement('market_cap_history', 'updated_at', 'v12_market_cap_updated_at',
                '재작성 시각 기록 - ON CONFLICT DO UPDATE 가 이 컬럼에 쓴다'),
)
DELISTING_INGEST = (
    Requirement('stock_listing_events', 'stock_listing_events_natural_key',
                'v10_listing_events_unique',
                'ON CONFLICT ON CONSTRAINT 가 이 이름을 직접 참조한다',
                kind='constraint'),
)


def require_schema(reqs: tuple[Requirement, ...], *, who: str) -> None:
    """요구 컬럼·제약이 전부 있는지 확인. 하나라도 없으면 SchemaTooOld.

    적재를 **한 행도 쓰기 전에** 부른다. 여기서 죽으면 DB 는 손대지 않은 상태다.
    """
    if not reqs:
        return
    cols = tuple((r.table, r.name) for r in reqs if r.kind == 'column')
    cons = tuple(r.name for r in reqs if r.kind == 'constraint')
    have: set[tuple[str, str]] = set()
    with db_conn() as conn:
        cur = conn.cursor()
        if cols:
            cur.execute(
                """
                SELECT table_name, column_name FROM information_schema.columns
                WHERE (table_name, column_name) IN %s
                """,
                (cols,),
            )
            have |= {(t, c) for t, c in cur.fetchall()}
        if cons:
            cur.execute(
                "SELECT conrelid::regclass::text, conname FROM pg_constraint "
                "WHERE conname IN %s",
                (cons,),
            )
            have |= {(t, c) for t, c in cur.fetchall()}

    missing = [r for r in reqs if (r.table, r.name) not in have]
    if missing:
        raise SchemaTooOld(format_missing(missing, who))


def format_missing(missing: list[Requirement], who: str) -> str:
    """거부 메시지. **적용할 마이그레이션 이름을 직접 말해야** 한다 —
    'column does not exist' 만 보고 어느 마이그레이션인지 찾느라 시간을 쓰지 않도록."""
    lines = [f'{who}: DB 스키마가 코드보다 낮다 - 적재를 시작하지 않는다.', '']
    for r in missing:
        what = '제약' if r.kind == 'constraint' else '컬럼'
        lines.append(f'  없음({what}): {r.table}.{r.name}  ({r.why})')
        lines.append(f'    적용: python -m ingest.migrations.apply {r.migration}')
    lines += ['',
              '  운영(5433)·섀도우(5436) 양쪽에 적용해야 한다.',
              '  이 검사가 없으면 INSERT 도중 SQL 에러로 죽어 부분 적재가 남는다.']
    return '\n'.join(lines)
