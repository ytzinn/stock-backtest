"""
마이그레이션 SQL을 psycopg2로 적용.

실행:
    python -m ingest.migrations.apply v8_xbrl_original
    python -m ingest.migrations.apply v8_xbrl_original --dry-run
"""
import argparse
import sys
from pathlib import Path

from ingest.connection import db_conn


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('migration', help='마이그레이션 파일명 (확장자 제외)')
    parser.add_argument('--dry-run', action='store_true', help='SQL만 출력, 적용 안 함')
    args = parser.parse_args()

    sql_path = Path(__file__).parent / f'{args.migration}.sql'
    if not sql_path.exists():
        print(f'ERROR: {sql_path} 없음', file=sys.stderr)
        sys.exit(1)

    sql = sql_path.read_text(encoding='utf-8')

    if args.dry_run:
        print(f'=== {sql_path.name} (dry-run) ===')
        print(sql)
        return

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql)
        print(f'✅  {args.migration} 적용 완료')


if __name__ == '__main__':
    main()
