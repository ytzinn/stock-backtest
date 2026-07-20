"""
SPEC_10 §3-1 — C_pbr_path_random 1,000회 fast-path 실행.

풀은 리밸런싱일당 1회 구축하고 (필터 스택은 채택 후보와 동일: HARD +
Stability{R1,R2,R5,R6} + Momentum), 종목별 가격·상폐 데이터도 풀 단위로 1회
prefetch한 뒤 추첨만 반복한다. 산식은 전부 engine SSOT 재사용:
  - 추첨 재현: ablation._RandomSelectPipeline과 동일한 rng(f"{seed}:{date}") 셔플
    + build_portfolio(상위 20)
  - 구간 수익률: engine._period_stock_data(풀 prefetch) + engine._aggregate_period_return
  - turnover: engine._calc_turnover, CAGR: metrics.compute_cagr (캘린더 경과일 기준)

**등가성 게이트 (필수, 기본 활성)**: --verify-seed의 추첨 1회를 전체 엔진
(BacktestEngine + _RandomSelectPipeline)으로 재실행해 구간별 편입·gross·CAGR가
일치(1e-12)해야 본 실행 결과를 저장한다. 불일치 시 즉시 중단.

실행 (서버, 크론 동결 스냅샷):
  venv/bin/python -m scripts.robustness.run_random_pool --valuation-date 2026-07-19

출력 (experiments/robustness/):
  C_pbr_path_random_draws.csv       — seed별 cagr·net_cagr (완결 구간 기준)
  C_pbr_path_random_periods.csv.gz  — seed × 구간 gross/net/turnover/tc
  C_pbr_path_random_contrib.csv.gz  — seed × 구간 × 종목 유효비중·수익률 (G3′/G4′ 귀무분포용)
  pools.json                        — 리밸런싱일별 풀 (감사·재현용)
  random_summary.json               — 분포 통계 + seed 체계 기록
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import logging
import random
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from backtest.ablation import ABLATION_CONFIGS, build_ablation_pipeline
from backtest.configs.constants import COST_BUY, COST_SELL
from backtest.configs.rebalance_dates import REBALANCE_DATES
from backtest.data_access import load_gate_passed_tickers, load_pit_series_ttm
from backtest.engine import (
    BacktestEngine,
    _aggregate_period_return,
    _calc_turnover,
    _period_stock_data,
    _report_type,
)
from backtest.metrics import compute_cagr
from backtest.portfolio import build_portfolio
from ingest.connection import get_connection

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

TAG     = 'C_pbr_path_random'
OUT_DIR = Path('experiments/robustness')
N_DRAWS = 1000
N_PICK  = 20


def _abort_if_cron_window() -> None:
    """DRIFT-INGEST-001: 크론 시간대(UTC 10:00~10:45) 실행 금지."""
    now = datetime.now(timezone.utc)
    minutes = now.hour * 60 + now.minute
    if 10 * 60 <= minutes < 10 * 60 + 45:
        raise SystemExit('DRIFT-INGEST-001: 크론 시간대(UTC 10:00~10:45) — 실행 금지.')


def _closed_period_pairs() -> list[tuple[date, date]]:
    """완결 구간 (rebal, next) 쌍 — 마지막(열린) 구간 제외."""
    return [(REBALANCE_DATES[i], REBALANCE_DATES[i + 1])
            for i in range(len(REBALANCE_DATES) - 1)]


def build_pools(conn) -> tuple[dict[date, list[str]], dict[date, dict]]:
    """
    리밸런싱일별 풀(필터 통과 종목, build_universe 반환 순서 그대로 — 셔플 재현에
    순서가 필요) + 풀 종목별 (price_start, price_end, last) prefetch.
    """
    pipeline = build_ablation_pipeline(TAG, ABLATION_CONFIGS[TAG], seed=None)
    pools:      dict[date, list[str]] = {}
    stock_data: dict[date, dict[str, tuple]] = {}

    for rebal, nxt in _closed_period_pairs():
        rtype       = _report_type(rebal)
        gate_passed = load_gate_passed_tickers(conn, rebal, report_type=rtype)
        if not gate_passed:
            log.info('%s: gate=0 (TTM 미충족) — 빈 구간', rebal)
            pools[rebal] = []
            stock_data[rebal] = {}
            continue
        pit_series = load_pit_series_ttm(conn, rebal, report_type=rtype)
        universe   = pipeline.build_universe(gate_passed, rebal, pit_series, conn)['universe']
        pools[rebal] = universe

        # 풀 전체의 종목별 가격·상폐 데이터 1회 prefetch (weight 값은 미사용 자리)
        data = _period_stock_data(conn, {t: 1.0 for t in universe}, rebal, nxt)
        stock_data[rebal] = {t: (ps, pe, last) for t, _w, ps, pe, last in data}
        log.info('%s: pool=%d (가격 유효 %d)', rebal, len(universe), len(stock_data[rebal]))

    return pools, stock_data


def _draw_portfolio(pool: list[str], seed: int, rebal: date) -> dict[str, float]:
    """_RandomSelectPipeline.score_and_rank + build_portfolio와 동일한 추첨 재현."""
    rng = random.Random(f'{seed}:{rebal.isoformat()}')
    shuffled = list(pool)
    rng.shuffle(shuffled)
    candidates = [
        {'ticker': t, 'upside_pct': 0.0, 'model': 'RANDOM', 'fair_value': 0.0, 'price': 0.0}
        for t in shuffled
    ]
    return build_portfolio(candidates, n_stocks=N_PICK)


def run_draws(pools, stock_data, n_draws: int):
    """전 시드 추첨 실행 (DB 무접촉 — prefetch 데이터만 사용)."""
    pairs = _closed_period_pairs()
    span = None
    active_pairs = [(r, n) for r, n in pairs if pools.get(r)]
    if active_pairs:
        span = dict(start_date=active_pairs[0][0], end_date=active_pairs[-1][1])

    draws_rows, period_rows, contrib_rows = [], [], []
    for seed in range(n_draws):
        prev: dict[str, float] = {}
        gross_list, net_list, idx = [], [], []
        for rebal, nxt in pairs:
            pool = pools.get(rebal) or []
            if not pool:
                continue
            portfolio = _draw_portfolio(pool, seed, rebal)
            sd = stock_data[rebal]
            valid = [(t, w, *sd[t]) for t, w in portfolio.items() if t in sd]
            gross, _opt, _cons = _aggregate_period_return(valid) if valid else (0.0, 0.0, 0.0)
            turnover = _calc_turnover(prev, portfolio)
            tc       = turnover * (COST_SELL + COST_BUY)
            net      = gross - tc
            prev     = portfolio

            gross_list.append(gross)
            net_list.append(net)
            idx.append(rebal)
            period_rows.append((seed, rebal.isoformat(), gross, net, turnover, tc, len(portfolio)))

            total_w = sum(w for _, w, *_ in valid)
            for t, w, ps, pe, _last in valid:
                contrib_rows.append((seed, rebal.isoformat(), t, w / total_w, pe / ps - 1))

        s_idx = pd.DatetimeIndex(idx)
        cagr     = compute_cagr(pd.Series(gross_list, index=s_idx), **(span or {}))
        net_cagr = compute_cagr(pd.Series(net_list, index=s_idx), **(span or {}))
        draws_rows.append((seed, cagr, net_cagr))
        if (seed + 1) % 200 == 0:
            log.info('추첨 진행 %d/%d', seed + 1, n_draws)

    return draws_rows, period_rows, contrib_rows


def verify_against_engine(conn, pools, stock_data, seed: int, valuation_date: date) -> None:
    """등가성 게이트: fast-path seed 추첨 vs 전체 엔진 실행 — 불일치 시 중단."""
    log.info('[등가성 게이트] seed=%d 전체 엔진 대조 실행 시작', seed)
    pipeline = build_ablation_pipeline(TAG, ABLATION_CONFIGS[TAG], seed=seed)
    engine   = BacktestEngine(pipeline)
    result   = engine.run(REBALANCE_DATES, run_name=f'{TAG}_verify', ablation_tag=TAG,
                          valuation_date=valuation_date)
    engine_closed = [r for r in result['period_results']
                    if r['n_gate'] > 0 and not r['is_open_period']]

    pairs = [(r, n) for r, n in _closed_period_pairs() if pools.get(r)]
    if len(engine_closed) != len(pairs):
        raise SystemExit(
            f'[등가성 게이트 실패] 완결 구간 수 불일치: engine={len(engine_closed)} fast={len(pairs)}'
        )

    prev: dict[str, float] = {}
    for er, (rebal, nxt) in zip(engine_closed, pairs):
        assert er['rebalance_date'] == rebal
        portfolio = _draw_portfolio(pools[rebal], seed, rebal)
        if set(portfolio) != set(er['portfolio']):
            raise SystemExit(
                f'[등가성 게이트 실패] {rebal}: 편입 상이 — 셔플 재현 결함. '
                f'fast-only={set(portfolio) - set(er["portfolio"])} '
                f'engine-only={set(er["portfolio"]) - set(portfolio)}'
            )
        sd = stock_data[rebal]
        valid = [(t, w, *sd[t]) for t, w in portfolio.items() if t in sd]
        gross, _o, _c = _aggregate_period_return(valid) if valid else (0.0, 0.0, 0.0)
        if abs(gross - er['period_return']) > 1e-12:
            raise SystemExit(
                f'[등가성 게이트 실패] {rebal}: gross 불일치 '
                f'fast={gross!r} engine={er["period_return"]!r}'
            )
        turnover = _calc_turnover(prev, portfolio)
        if abs(turnover - er['turnover']) > 1e-12:
            raise SystemExit(f'[등가성 게이트 실패] {rebal}: turnover 불일치')
        prev = portfolio
    log.info('[등가성 게이트] PASS — 편입·gross·turnover 전 구간 일치 (tol 1e-12)')


def main() -> None:
    parser = argparse.ArgumentParser(description='SPEC_10 C_pbr_path_random fast-path')
    parser.add_argument('--n-draws',        type=int, default=N_DRAWS)
    parser.add_argument('--verify-seed',    type=int, default=0)
    parser.add_argument('--skip-verify',    action='store_true',
                        help='등가성 게이트 생략 (디버그 전용 — 공식 실행 금지)')
    parser.add_argument('--valuation-date', required=True,
                        help='등가성 게이트 엔진 실행용 (완결 지표에는 영향 없음)')
    args = parser.parse_args()

    _abort_if_cron_window()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    valuation_date = date.fromisoformat(args.valuation_date)

    conn = get_connection()
    try:
        pools, stock_data = build_pools(conn)
        if not args.skip_verify:
            verify_against_engine(conn, pools, stock_data, args.verify_seed, valuation_date)
        else:
            log.warning('등가성 게이트 생략 (--skip-verify) — 공식 수치로 사용 금지')
    finally:
        conn.close()

    draws, periods, contribs = run_draws(pools, stock_data, args.n_draws)

    with (OUT_DIR / f'{TAG}_draws.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['seed', 'cagr', 'net_cagr'])
        w.writerows(draws)
    with gzip.open(OUT_DIR / f'{TAG}_periods.csv.gz', 'wt', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['seed', 'rebalance_date', 'gross', 'net', 'turnover', 'tc', 'n_stocks'])
        w.writerows(periods)
    with gzip.open(OUT_DIR / f'{TAG}_contrib.csv.gz', 'wt', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['seed', 'rebalance_date', 'ticker', 'weight_eff', 'ret'])
        w.writerows(contribs)
    (OUT_DIR / 'pools.json').write_text(
        json.dumps({d.isoformat(): p for d, p in pools.items()}, ensure_ascii=False, indent=1),
        encoding='utf-8')

    cagrs = sorted(r[1] for r in draws)
    n = len(cagrs)
    summary = {
        'tag': TAG, 'generated_at': datetime.now().isoformat(),
        'n_draws': n, 'seed_scheme': 'random.Random(f"{seed}:{rebalance_date}") — seed 0..n-1',
        'verify_seed': None if args.skip_verify else args.verify_seed,
        'median_cagr': cagrs[n // 2], 'p5_cagr': cagrs[int(n * 0.05)],
        'p95_cagr': cagrs[int(n * 0.95)],
        'pool_sizes': {d.isoformat(): len(p) for d, p in pools.items()},
    }
    (OUT_DIR / 'random_summary.json').write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
    log.info('완료: n=%d median=%.4f p5=%.4f p95=%.4f',
             n, summary['median_cagr'], summary['p5_cagr'], summary['p95_cagr'])

    if args.skip_verify:
        sys.exit(2)


if __name__ == '__main__':
    main()
