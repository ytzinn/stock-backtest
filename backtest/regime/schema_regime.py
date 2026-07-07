"""
schema_regime.sql 실행기.

CLAUDE.md 규칙상 서버 호스트에 psql이 없다(Docker 내부 전용) — psycopg2로 직접 실행한다.
실행: venv/bin/python -m backtest.regime.schema_regime
"""
from __future__ import annotations

import logging
from pathlib import Path

from ingest.connection import get_connection

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

SQL_PATH = Path(__file__).with_name('schema_regime.sql')


def main() -> None:
    sql = SQL_PATH.read_text(encoding='utf-8')
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        log.info('regime_indicators, strategy_returns_monthly 생성/확인 완료')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
