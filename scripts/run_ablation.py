"""
Ablation Test A~G 전체 실행 스크립트.

결과 저장:
  experiments/ablation/{tag}.json          — 비랜덤 시나리오 (D/E/F/G)
  experiments/ablation/{tag}_dist.csv      — 랜덤 시나리오 500회 분포 (A/B/C)
  experiments/ablation/summary.json        — 전체 비교 요약

실행:
  venv/bin/python -m scripts.run_ablation
  venv/bin/python -m scripts.run_ablation --tags D_rim_only G_full
  venv/bin/python -m scripts.run_ablation --random-only    # A/B/C 500회 분포만
  venv/bin/python -m scripts.run_ablation --det-only       # D/E/F/G 단일 실행만
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import date, datetime
from multiprocessing import Pool, cpu_count
from pathlib import Path

from backtest.ablation import (
    ABLATION_CONFIGS,
    RANDOM_REPEATS,
    RANDOM_TAGS,
    build_ablation_pipeline,
)
from backtest.configs.rebalance_dates import REBALANCE_DATES
from backtest.engine import BacktestEngine
from backtest.metrics import compute_metrics

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

OUT_DIR = Path('experiments/ablation')


def _run_one(args: tuple) -> dict:
    """멀티프로세싱 워커. (tag, config, seed, rebalance_dates) → metrics dict."""
    tag, config, seed, rebalance_dates = args
    pipeline = build_ablation_pipeline(tag, config, seed=seed)
    engine   = BacktestEngine(pipeline)
    result   = engine.run(rebalance_dates, run_name=tag, ablation_tag=tag)
    m        = result['metrics']
    return {
        'seed':           seed,
        'cagr':           m['cagr'],
        'alpha':          m['alpha'],
        'alpha_kosdaq':   m.get('alpha_kosdaq', 0.0),
        'sharpe':         m['sharpe'],
        'mdd':            m['mdd'],
        'robustness':     m['robustness'],
        'benchmark_cagr': m['benchmark_cagr'],
        'kosdaq_cagr':    m.get('kosdaq_cagr', 0.0),
        'n_periods':      m['n_periods'],
    }


def run_deterministic(tag: str, config: dict, rebalance_dates: list[date]) -> tuple[dict, list[dict]]:
    """단일 실행 (D/E/F/G). (metrics_dict, period_results) 반환."""
    log.info(f'[{tag}] 실행 시작')
    pipeline = build_ablation_pipeline(tag, config, seed=None)
    engine   = BacktestEngine(pipeline)
    result   = engine.run(rebalance_dates, run_name=tag, ablation_tag=tag)
    m        = result['metrics']
    metrics  = {
        'seed':           None,
        'cagr':           m['cagr'],
        'alpha':          m['alpha'],
        'alpha_kosdaq':   m.get('alpha_kosdaq', 0.0),
        'sharpe':         m['sharpe'],
        'mdd':            m['mdd'],
        'robustness':     m['robustness'],
        'benchmark_cagr': m['benchmark_cagr'],
        'kosdaq_cagr':    m.get('kosdaq_cagr', 0.0),
        'n_periods':      m['n_periods'],
    }
    log.info(
        f'[{tag}] CAGR={m["cagr"]:.1%} '
        f'Alpha(KOSPI)={m["alpha"]:.1%} Alpha(KOSDAQ)={m.get("alpha_kosdaq", 0):.1%} '
        f'MDD={m["mdd"]:.1%} Sharpe={m["sharpe"]:.2f}'
    )
    return metrics, result['period_results']


def run_random_distribution(
    tag:             str,
    config:          dict,
    rebalance_dates: list[date],
    n_repeats:       int = RANDOM_REPEATS,
    n_workers:       int | None = None,
) -> list[dict]:
    """500회 반복 실행 (A/B/C). 분포 리스트 반환."""
    workers = n_workers or max(1, cpu_count() - 1)
    log.info(f'[{tag}] 랜덤 {n_repeats}회 반복 — workers={workers}')

    tasks = [(tag, config, seed, rebalance_dates) for seed in range(n_repeats)]
    with Pool(processes=workers) as pool:
        results = pool.map(_run_one, tasks)

    cagrs = [r['cagr'] for r in results]
    log.info(
        f'[{tag}] 중앙값 CAGR={sorted(cagrs)[n_repeats//2]:.1%}  '
        f'p5={sorted(cagrs)[int(n_repeats*0.05)]:.1%}  '
        f'p95={sorted(cagrs)[int(n_repeats*0.95)]:.1%}'
    )
    return results


def save_deterministic(tag: str, result: dict) -> None:
    path = OUT_DIR / f'{tag}.json'
    path.write_text(
        json.dumps({'tag': tag, 'run_at': datetime.now().isoformat(), **result}, indent=2),
        encoding='utf-8',
    )
    log.info(f'  → {path}')


def save_periods(tag: str, period_results: list[dict]) -> None:
    """구간별 수익률 및 필터 통과 수를 CSV로 저장."""
    import csv
    FILTER_KEYS = ['HardFilter', 'StabilityFilter', 'FactorScreener', 'MomentumFilter']
    path = OUT_DIR / f'{tag}_periods.csv'
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow([
            'rebalance_date', 'next_date', 'period_return', 'kospi_return', 'kosdaq_return',
            'n_gate', 'n_stocks',
            'hard_passed', 'stability_passed', 'screener_passed', 'momentum_passed',
        ])
        for r in period_results:
            stats = r.get('universe_stats', {})
            w.writerow([
                r['rebalance_date'].isoformat(),
                r['next_date'].isoformat(),
                r['period_return'],
                r['kospi_return'],
                r.get('kosdaq_return', ''),
                r.get('n_gate', ''),
                r['n_stocks'],
                stats.get('HardFilter',      {}).get('passed', ''),
                stats.get('StabilityFilter', {}).get('passed', ''),
                stats.get('FactorScreener',  {}).get('passed', ''),
                stats.get('MomentumFilter',  {}).get('passed', ''),
            ])
    log.info(f'  → {path}')


def save_distribution(tag: str, results: list[dict]) -> None:
    import csv
    path = OUT_DIR / f'{tag}_dist.csv'
    fields = ['seed', 'cagr', 'alpha', 'alpha_kosdaq', 'sharpe', 'mdd', 'robustness', 'benchmark_cagr', 'kosdaq_cagr', 'n_periods']
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(results)
    log.info(f'  → {path} ({len(results)}행)')


def make_summary(det_results: dict[str, dict], dist_stats: dict[str, dict]) -> dict:
    """비교 요약 (판정 기준 포함)."""
    summary: dict = {'generated_at': datetime.now().isoformat(), 'scenarios': {}}

    for tag, r in det_results.items():
        summary['scenarios'][tag] = {
            k: round(v, 6) for k, v in r.items()
            if k != 'seed' and isinstance(v, (int, float))
        }

    for tag, s in dist_stats.items():
        summary['scenarios'][tag] = s

    # 판정 기준 평가
    s = summary['scenarios']
    def cagr(t: str) -> float:
        v = s.get(t, {})
        if 'cagr' in v:
            return v['cagr']
        return v.get('median_cagr', 0.0)

    def p95(t: str) -> float:
        return s.get(t, {}).get('p95_cagr', 0.0)

    judgements = {}
    if 'C_stability_random' in s and 'B_hard_random' in s:
        judgements['C>B (재무안정성 기여, p95 기준)'] = cagr('C_stability_random') > p95('B_hard_random')
    if 'D_rim_only' in s and 'C_stability_random' in s:
        # SPEC_05 §11: D CAGR이 C_stability_random p95 이상이어야 RIM 통계적으로 유효
        c_p95 = p95('C_stability_random')
        d_cagr = cagr('D_rim_only')
        judgements['D>C_p95 (RIM 유효성, SPEC_05 §11)'] = d_cagr >= c_p95
        judgements['_D_cagr']  = round(d_cagr, 6)
        judgements['_C_p95']   = round(c_p95, 6)
    if 'E_screener_rim' in s and 'D_rim_only' in s:
        judgements['E>D (팩터 스크리닝 기여)'] = cagr('E_screener_rim') > cagr('D_rim_only')
    if 'F_momentum_rim' in s and 'D_rim_only' in s:
        judgements['F>D (모멘텀 기여)'] = cagr('F_momentum_rim') > cagr('D_rim_only')
    if 'G_full' in s and 'D_rim_only' in s:
        judgements['G>D (전체 필터 기여)'] = cagr('G_full') > cagr('D_rim_only')

    summary['judgements'] = judgements
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description='Ablation Test 실행')
    parser.add_argument('--tags',        nargs='+', help='실행할 태그 목록 (기본: 전체)')
    parser.add_argument('--random-only', action='store_true', help='랜덤 시나리오(A/B/C)만 실행')
    parser.add_argument('--det-only',    action='store_true', help='비랜덤 시나리오(D/E/F/G)만 실행')
    parser.add_argument('--repeats',     type=int, default=RANDOM_REPEATS, help='랜덤 반복 횟수')
    parser.add_argument('--workers',     type=int, default=None,           help='병렬 프로세스 수')
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    tags_to_run = set(args.tags or ABLATION_CONFIGS.keys())
    if args.random_only:
        tags_to_run &= RANDOM_TAGS
    if args.det_only:
        tags_to_run -= RANDOM_TAGS

    rebalance_dates = REBALANCE_DATES

    det_results:  dict[str, dict] = {}
    dist_stats:   dict[str, dict] = {}

    for tag in ABLATION_CONFIGS:   # 정해진 순서 유지
        if tag not in tags_to_run:
            continue
        config = ABLATION_CONFIGS[tag]

        if tag in RANDOM_TAGS:
            results  = run_random_distribution(tag, config, rebalance_dates,
                                               n_repeats=args.repeats, n_workers=args.workers)
            save_distribution(tag, results)
            cagrs = sorted(r['cagr'] for r in results)
            n     = len(cagrs)
            dist_stats[tag] = {
                'median_cagr':  round(cagrs[n // 2], 6),
                'p5_cagr':      round(cagrs[int(n * 0.05)], 6),
                'p95_cagr':     round(cagrs[int(n * 0.95)], 6),
                'n_repeats':    n,
            }
        else:
            result, period_results = run_deterministic(tag, config, rebalance_dates)
            save_deterministic(tag, result)
            save_periods(tag, period_results)
            det_results[tag] = result

    summary = make_summary(det_results, dist_stats)
    summary_path = OUT_DIR / 'summary.json'
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
    log.info(f'\n판정 결과:')
    for k, v in summary.get('judgements', {}).items():
        log.info(f'  {"✅" if v else "❌"} {k}')
    log.info(f'\n요약 저장: {summary_path}')


if __name__ == '__main__':
    main()
