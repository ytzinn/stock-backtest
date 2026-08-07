"""
SPEC_13 Q-C2 2단계 — backtest_runs 최초 기록 스크립트.

`backtest_runs` 테이블(ingest/schema.sql)은 컬럼만 있고 지금까지 아무 코드도 INSERT
하지 않았다(전부 read-only 소비). 공식 스냅샷 동결 재실행 결과를 남기는 최소 기록기.

입력: experiments/ablation/{tag}.json (엔진 arithmetic 지표) +
      experiments/daily_nav/summary.json (SPEC_09 daily-NAV 지표, SPEC_13 §9-1 SSOT).
git_commit은 현재 체크아웃의 `git rev-parse HEAD`.

실행 (운영 DB 5433 대상 — 스냅샷 포트로 실행하지 마라, 이 기록은 영구 보존용):
    venv/bin/python -m scripts.audit.record_backtest_run \\
        --tag F_pbr_no_r3r4 --phase Q-C2_baseline_freeze \\
        --data-cutoff 2026-07-28 \\
        --note "Q-C2 2단계: RebalancePoint+DEBT-3 현행안 baseline 재고정"
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

from ingest.connection import db_conn

DB_SCHEMA_VERSION = 'v9_ingest_status_reports'  # ingest/migrations/ 최신 파일명


def _git_commit() -> str:
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()


def _load_metrics(tag: str) -> dict:
    ablation = json.loads(Path(f'experiments/ablation/{tag}.json').read_text(encoding='utf-8'))
    nav_summary = json.loads(Path('experiments/daily_nav/summary.json').read_text(encoding='utf-8'))
    nav = nav_summary['tags'][tag]
    return {
        'engine_arithmetic': {
            'gross_cagr':     ablation['cagr'],
            'net_cagr':       ablation['net_cagr'],
            'sharpe':         ablation['sharpe'],
            'net_sharpe':     ablation['net_sharpe'],
            'mdd_endpoint':   ablation['mdd'],
            'avg_turnover':   ablation['avg_turnover'],
            'n_periods':      ablation['n_periods'],
        },
        'daily_nav_ssot': {
            # SPEC_13 §9-1: QG1~3는 일별 NAV·승법 거래비용 정의를 쓴다.
            'daily_sharpe':        nav['net']['daily_sharpe'],
            'daily_mdd_net':       nav['net']['daily_mdd'],
            'daily_mdd_gross':     nav['daily_mdd_gross'],
            'endpoint_mdd_gross':  nav['endpoint_mdd_gross'],
            'tracking_error_kospi':  nav['tracking_error_kospi'],
            'tracking_error_kosdaq': nav['tracking_error_kosdaq'],
            'n_closed_periods':    nav['n_closed_periods'],
            'all_gates_pass':      nav['all_gates_pass'],
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag', required=True)
    ap.add_argument('--phase', required=True)
    ap.add_argument('--data-cutoff', required=True, help='YYYY-MM-DD (스냅샷 동결 시점)')
    ap.add_argument('--note', default='')
    args = ap.parse_args()

    metrics = _load_metrics(args.tag)
    now = datetime.now(timezone.utc)

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO backtest_runs
                (run_name, phase, params, metrics, ablation_tag,
                 git_commit, data_cutoff_date, db_schema_version,
                 started_at, finished_at, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING run_id
            """,
            (
                args.tag, args.phase,
                json.dumps({'note': args.note}),
                json.dumps(metrics),
                args.tag, _git_commit(),
                date.fromisoformat(args.data_cutoff), DB_SCHEMA_VERSION,
                now, now, 'completed',
            ),
        )
        run_id = cur.fetchone()[0]

    print(f'backtest_runs.run_id={run_id} 기록 완료 (tag={args.tag}, phase={args.phase})')


if __name__ == '__main__':
    main()
