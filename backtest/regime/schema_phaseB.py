"""
schema_phaseB.sql 실행기. schema_regime.py와 동일 패턴 —
서버 호스트 PATH에 psql이 없으므로(Docker 내부 전용, CLAUDE.md) psycopg2로 직접 실행한다.

실행: venv/bin/python -m backtest.regime.schema_phaseB
"""
from __future__ import annotations

import logging
from pathlib import Path

from ingest.connection import get_connection

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

SQL_PATH = Path(__file__).with_name('schema_phaseB.sql')


def main() -> None:
    sql = SQL_PATH.read_text(encoding='utf-8')
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        log.info('overlay_returns 생성/확인 완료')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
