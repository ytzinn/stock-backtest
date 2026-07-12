"""
ω(초과이익 지속성) 민감도 분석 스크립트.

D_rim_only 시나리오를 ω 격자 [0.4~0.9]로 반복 실행하여
CAGR·Sharpe·MDD·Alpha가 ω에 얼마나 민감한지 측정한다.

실행:
  cd /opt/stock-backtest && venv/bin/python -m scripts.run_omega_sensitivity
  venv/bin/python -m scripts.run_omega_sensitivity --grid 0.4 0.5 0.6 0.62 0.7 0.8 0.9

결과:
  experiments/omega_sensitivity.json
  experiments/omega_sensitivity.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from backtest.ablation import ABLATION_CONFIGS, build_ablation_pipeline
from backtest.configs.rebalance_dates import REBALANCE_DATES
from backtest.engine import BacktestEngine
from backtest.metrics import compute_metrics

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

DEFAULT_GRID = [0.4, 0.5, 0.55, 0.6, 0.62, 0.65, 0.7, 0.8, 0.9]
OUT_DIR = Path('experiments')


def run_one_omega(omega: float) -> dict:
    config   = ABLATION_CONFIGS['D_rim_only']
    pipeline = build_ablation_pipeline('D_rim_only', config, omega=omega)
    engine   = BacktestEngine(pipeline)
    result   = engine.run(REBALANCE_DATES, run_name=f'D_omega_{omega:.2f}',
                          valuation_date=date.today())
    m        = result['metrics']
    log.info(
        f'  ω={omega:.2f}  CAGR={m["cagr"]:.1%}  '
        f'Alpha(KOSPI)={m["alpha"]:.1%}  Alpha(KOSDAQ)={m.get("alpha_kosdaq", 0):.1%}  '
        f'Sharpe={m["sharpe"]:.3f}  MDD={m["mdd"]:.1%}'
    )
    return {
        'omega':         omega,
        'cagr':          round(m['cagr'],          6),
        'alpha_kospi':   round(m['alpha'],          6),
        'alpha_kosdaq':  round(m.get('alpha_kosdaq', 0.0), 6),
        'sharpe':        round(m['sharpe'],         6),
        'mdd':           round(m['mdd'],            6),
        'robustness':    round(m['robustness'],     6),
        'benchmark_cagr':round(m['benchmark_cagr'],6),
        'kosdaq_cagr':   round(m.get('kosdaq_cagr', 0.0), 6),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--grid', nargs='+', type=float, default=DEFAULT_GRID,
                        help='ω 격자 값 목록')
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    grid = sorted(args.grid)

    log.info(f'ω 민감도 분석 시작: grid={grid}')
    rows = []
    for omega in grid:
        log.info(f'ω={omega:.2f} 실행 중...')
        rows.append(run_one_omega(omega))

    # CSV 저장
    csv_path = OUT_DIR / 'omega_sensitivity.csv'
    fields = ['omega', 'cagr', 'alpha_kospi', 'alpha_kosdaq', 'sharpe', 'mdd',
              'robustness', 'benchmark_cagr', 'kosdaq_cagr']
    with csv_path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    # JSON 저장
    json_path = OUT_DIR / 'omega_sensitivity.json'
    json_path.write_text(
        json.dumps({'generated_at': datetime.now().isoformat(), 'results': rows},
                   indent=2, ensure_ascii=False),
        encoding='utf-8',
    )

    # 콘솔 요약
    log.info('\n── ω 민감도 분석 결과 ──')
    log.info(f'{"ω":>6}  {"CAGR":>7}  {"Alpha(KS)":>9}  {"Alpha(KQ)":>9}  {"Sharpe":>6}  {"MDD":>7}')
    for r in rows:
        marker = ' ←' if abs(r['omega'] - 0.62) < 0.001 else ''
        log.info(
            f'{r["omega"]:>6.2f}  {r["cagr"]:>7.1%}  {r["alpha_kospi"]:>9.1%}  '
            f'{r["alpha_kosdaq"]:>9.1%}  {r["sharpe"]:>6.3f}  {r["mdd"]:>7.1%}{marker}'
        )

    log.info(f'\n저장: {csv_path}, {json_path}')

    best = max(rows, key=lambda x: x['cagr'])
    log.info(f'\n최고 CAGR: ω={best["omega"]:.2f} → CAGR={best["cagr"]:.1%}')
    if abs(best['omega'] - 0.62) > 0.05:
        log.info(f'  → 현재 OMEGA=0.62와 차이 있음. constants.py 업데이트 검토.')
    else:
        log.info(f'  → 현재 OMEGA=0.62 근방. 유지.')


if __name__ == '__main__':
    main()
