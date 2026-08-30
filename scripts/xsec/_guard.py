"""진입점 가드 — `DOTENV-CWD-SILENT-5433` 대응.

`[검증된 사실]` 2026-08-25 실측: 워크트리 밖(`/tmp` 등)에서 실행하면 `load_dotenv()` 가
그 워크트리의 `.env` 를 찾지 못하고 `ingest/connection.py` 의 기본값으로 **운영 DB(5433)에
조용히 붙는다.** 실패하지 않고 성공하기 때문에 결과가 오염돼도 드러나지 않는다.
`DB_PORT` 환경변수는 충분한 판별자가 아니다 — 미설정이면 `None` 이고, 그때가 바로 위험한 때다.

가드는 두 종류다. **어느 쪽이 적용되는지는 그 진입점이 DB 에 붙는지가 정한다.**

  `assert_shadow_db(conn)`    — DB 에 붙는 진입점용. 섀도우에만 있는 테이블로 판별한다.
  `assert_no_db_imported()`   — DB 에 **붙지 않는** 진입점용. 붙지 않았음을 증명한다.

`[Claude 의견]` A-0·A-1 은 산출물 파일만 읽고 DB 에 접속하지 않는다. 그런 스크립트에
`assert_shadow_db` 를 넣으려면 **가드를 세우려고 없던 접속을 만들어야** 한다 — 지시의
글자는 지키고 목적은 어기는 짓이다. 대신 접속하지 않았음을 기계로 증명한다.
이쪽이 더 강한 성질이다: "옳은 DB 에 붙었다" 보다 "어떤 DB 에도 붙지 않았다" 가 세다.
"""
from __future__ import annotations

import sys

#: 섀도우 DB 에만 있는 테이블 (CLOSEOUT §7 재사용 가능 자산).
#: `[Claude 의견]` 이 판별자에는 구멍이 있다 — 이 테이블이 언젠가 정리되면 판별이
#: 조용히 멈춘다. 그래서 `if 존재` 가 아니라 `assert 존재` 다. 없으면 통과가 아니라 중단이다.
SHADOW_DISCRIMINATOR = 'price_history_recheck'

#: DB 접속을 만드는 모듈. 이 중 하나라도 import 돼 있으면 "접속 안 함" 주장이 깨진다.
_DB_MODULES = ('ingest.connection', 'psycopg2')


class GuardFailed(RuntimeError):
    """가드 실패. **경고로 낮추지 마라** — 이 예외가 운영 DB 오염을 막는 마지막 선이다."""


def assert_shadow_db(conn) -> None:
    """DB 에 붙는 진입점용. 섀도우가 아니면 즉시 중단한다 (fail-closed)."""
    with conn.cursor() as cur:
        cur.execute('select to_regclass(%s) is not null', (f'public.{SHADOW_DISCRIMINATOR}',))
        ok = cur.fetchone()[0]
    if not ok:
        raise GuardFailed(
            f'섀도우 DB 가 아니다 — {SHADOW_DISCRIMINATOR} 가 없다. '
            f'DOTENV-CWD-SILENT-5433: 워크트리 밖 실행이면 운영(5433)에 붙었을 수 있다. '
            f'워크트리 안에서 실행하라.'
        )


def assert_no_db_imported(where: str) -> None:
    """DB 에 붙지 않는 진입점용. 접속 모듈이 import 되지 않았음을 증명한다.

    A-0·A-1 은 산출물 파일만 읽는다. 나중에 누군가 이 경로에 DB 조회를 끼워 넣으면
    이 단언이 먼저 깨지고, 그 사람은 `assert_shadow_db` 를 쓸지 판단하게 된다.
    """
    loaded = [m for m in _DB_MODULES if m in sys.modules]
    if loaded:
        raise GuardFailed(
            f'{where}: DB 접속 모듈이 import 됐다 {loaded}. '
            f'이 진입점은 산출물 파일만 읽어야 한다. DB 가 정말 필요해졌다면 '
            f'assert_no_db_imported 를 지우지 말고 assert_shadow_db 로 **교체**하라.'
        )
