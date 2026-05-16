"""
수집 진도 한눈에 확인.

실행:
    python -m ingest.quick_status
"""
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ingest.connection import db_conn

LOG_DIR = Path('/opt/stock-backtest/logs')


def _active_procs(*keywords: str) -> list[str]:
    result = subprocess.run(['pgrep', '-af', 'python'], capture_output=True, text=True)
    lines = []
    for line in result.stdout.strip().splitlines():
        if 'quick_status' in line:
            continue
        if any(kw in line for kw in keywords):
            lines.append(line.strip())
    return lines


def _log_summary(name: str, tail_lines: int = 2) -> str:
    path = LOG_DIR / name
    if not path.exists():
        return f'    {name}: 파일 없음\n'
    stat = path.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    ago_min = int((datetime.now(timezone.utc) - mtime).total_seconds() / 60)
    ago_str = f'{ago_min}분 전' if ago_min < 120 else f'{ago_min // 60}시간 전'
    text_lines = path.read_text(errors='replace').splitlines()
    tail = '\n'.join(f'    | {l}' for l in text_lines[-tail_lines:]) if text_lines else '    | (비어있음)'
    return f'    수정: {mtime.strftime("%m-%d %H:%M")} UTC ({ago_str})\n{tail}\n'


def main() -> None:
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    print(f'\n{"=" * 55}')
    print(f'  수집 현황  ({now_str})')
    print(f'{"=" * 55}\n')

    with db_conn() as conn:
        cur = conn.cursor()

        # ── 1. DART ingest 상태 ──────────────────────────────────
        cur.execute("""
            SELECT status, COUNT(*) FROM ingest_status
            GROUP BY status ORDER BY status
        """)
        dart_rows = {r[0]: r[1] for r in cur.fetchall()}
        print(f'[DART ingest]  done={dart_rows.get("done", 0):,}  '
              f'error={dart_rows.get("error", 0)}  '
              f'pending={dart_rows.get("pending", 0)}')

        # ── 3. 가격 / 시가총액 최신 날짜 ────────────────────────
        cur.execute("""
            SELECT MAX(date),
                   COUNT(DISTINCT ticker) FILTER (WHERE date = (SELECT MAX(date) FROM price_history))
            FROM price_history
        """)
        ph_date, ph_cnt = cur.fetchone()

        cur.execute("""
            SELECT MAX(date),
                   COUNT(DISTINCT ticker) FILTER (WHERE date = (SELECT MAX(date) FROM market_cap_history))
            FROM market_cap_history
        """)
        mc_date, mc_cnt = cur.fetchone()

        today = datetime.now(timezone.utc).date()
        ph_lag = (today - ph_date).days if ph_date else '?'
        mc_lag = (today - mc_date).days if mc_date else '?'

        print(f'[price]        최신={ph_date}  커버={ph_cnt:,}종목  (lag {ph_lag}일)')
        print(f'[market_cap]   최신={mc_date}  커버={mc_cnt:,}종목  (lag {mc_lag}일)')

    # ── 4. 실행 중인 프로세스 ─────────────────────────────────
    print(f'\n[프로세스]')
    ingest_procs = _active_procs('dart_ingest', 'price_ingest', 'market_cap_ingest', 'pit_loader', 'dq_gate')
    print(f'  dart_ingest   : {"실행 중 🟢" if ingest_procs else "중지됨 ⚪"}')

    # ── 5. 최근 로그 요약 ────────────────────────────────────
    print(f'\n[최근 로그]')
    for log_name in [
        'dart_ingest.log',
        'price_ingest.log',
    ]:
        print(f'  ── {log_name}')
        print(_log_summary(log_name, tail_lines=2), end='')

    print(f'{"=" * 55}\n')


if __name__ == '__main__':
    main()
