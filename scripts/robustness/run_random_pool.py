"""
SPEC_10 §3-1 — C_pbr_path_random 1,000회 fast-path 실행.

풀은 리밸런싱일당 1회 구축하고 (필터 스택은 채택 후보와 동일: HARD +
Stability{R1,R2,R5,R6} + Momentum), 종목별 가격·상폐 데이터와 **PIT 시장**도 풀
단위로 1회 prefetch한 뒤 추첨만 반복한다. 산식은 전부 engine SSOT 재사용:
  - 추첨 재현: ablation._RandomSelectPipeline과 동일한 rng(f"{seed}:{date}") 셔플
    + build_portfolio(상위 20)
  - 구간 수익률: engine._period_stock_data(풀 prefetch) + engine._aggregate_period_return
  - 거래비용: engine._transaction_cost_from_markets (CORR-COST-001 매수/매도 분리 +
    시장별 매도요율). 시장은 추첨과 무관하므로 날짜당 1회 조회분을 전 추첨이 공유한다.
  - turnover: engine._calc_turnover, gross CAGR: metrics.compute_cagr (캘린더 경과일)
  - **net CAGR: 승법 terminal NAV** (SPEC_13 §9-1) — `net_growth *= (1−tc)(1+gross)`.
    산술 net=gross−tc 정의는 폐기됐다(일별 NAV 엔진과 정의 통일).

**등가성 게이트 3종 (필수, 기본 활성 — SPEC_13 §9-1a)**: --verify-seed의 추첨 1회로
검증하며 하나라도 실패하면 결과를 저장하지 않고 중단한다:
  기존   편입·gross·turnover·tc가 전체 엔진 실행과 일치 (1e-12)
  EQ-1   승법 net 산식이 stitch_periods 정의와 일치 (동일 gross 수열, 1e-12)
  EQ-2   terminal net NAV가 실제 일별 NAV 경로와 일치 (1e-6, 상폐 구간 포함 시 1e-3
         — 엔진은 구간말·일별 NAV는 감지일에 haircut을 적용하는 정의 차이. G-NAV-1과
         동일 정책)

실행 (서버, 크론 동결 스냅샷):
  venv/bin/python -m scripts.robustness.run_random_pool --valuation-date 2026-07-19
  venv/bin/python -m scripts.robustness.run_random_pool --calendar A --valuation-date ...

출력 (experiments/robustness/) — 캘린더 A/C는 파일명에 _A/_C 접미사가 붙는다
(반기는 무접미사 유지 = 기존 공식 산출물 보존):
  C_pbr_path_random_draws.csv       — seed별 cagr·net_cagr (완결 구간 기준)
  C_pbr_path_random_periods.csv.gz  — seed × 구간 gross/net/turnover/tc
                                      (net은 **구간 수익률** — gate_analysis G3′가 소비)
  C_pbr_path_random_contrib.csv.gz  — seed × 구간 × 종목 유효비중·수익률 (G3′/G4′ 귀무분포용)
  pools.json                        — 리밸런싱일별 풀 (감사·재현용)
  random_summary.json               — 분포 통계(gross·net) + seed 체계 기록
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
from backtest.configs.schedule import (
    CALENDAR_CHOICES,
    RebalancePoint,
    get_schedule,
    tag_suffix,
)
from backtest.daily_nav import daily_nav_for_period, stitch_periods
from backtest.data_access import (
    get_markets,
    load_gate_passed_tickers,
    load_pit_series_ttm,
)
from backtest.engine import (
    BacktestEngine,
    _aggregate_period_return,
    _calc_transaction_cost,
    _calc_turnover,
    _period_stock_data,
    _transaction_cost_from_markets,
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

# SPEC_13 §9-1a 등가성 게이트 허용오차
TOL_EQ1            = 1e-12   # 승법 산식 (동일 gross 수열 대조 — 정의상 완전 일치 가능)
TOL_EQ2_CLEAN      = 1e-6    # 일별 NAV 경로 대조 (G-NAV-1과 동일 정책)
TOL_EQ2_DELIST     = 1e-3    # 상폐 구간 포함 시 (haircut 시점 차이 — 정의 차이)


def _abort_if_cron_window() -> None:
    """DRIFT-INGEST-001: 크론 시간대(UTC 10:00~10:45) 실행 금지."""
    now = datetime.now(timezone.utc)
    minutes = now.hour * 60 + now.minute
    if 10 * 60 <= minutes < 10 * 60 + 45:
        raise SystemExit('DRIFT-INGEST-001: 크론 시간대(UTC 10:00~10:45) — 실행 금지.')


def _closed_period_pairs(
    rebalance_points: list[RebalancePoint],
) -> list[tuple[RebalancePoint, RebalancePoint]]:
    """완결 구간 (rebal, next) RebalancePoint 쌍 — 마지막(열린) 구간 제외."""
    return [(rebalance_points[i], rebalance_points[i + 1])
            for i in range(len(rebalance_points) - 1)]


def build_pools(
    conn, rebalance_points: list[RebalancePoint],
) -> tuple[dict[date, list[str]], dict[date, dict], dict[date, dict]]:
    """
    리밸런싱일별 풀(필터 통과 종목, build_universe 반환 순서 그대로 — 셔플 재현에
    순서가 필요) + 풀 종목별 (price_start, price_end, last) prefetch
    + 리밸런싱일별 PIT 시장 prefetch.

    시장은 리밸런싱일에만 의존하고 어떤 종목이 추첨됐는지와 무관하므로, 날짜당 1회
    조회해 1,000회 추첨 전체가 공유한다 (거래비용 계산 시 DB 왕복 제거).

    **매도 종목은 이번 풀에 없을 수 있다** — 리밸런싱일 t에 파는 종목은 t-1 구간의
    보유분이고, 그 종목이 t 시점 유니버스에서 탈락했으면 pools[t]에 없다. 따라서
    시장 조회 대상은 pools[t]가 아니라 **전 구간 풀의 합집합**이어야 한다(누락 시
    sell_cost가 KOSPI 기본값으로 조용히 대체돼 tc가 틀린다).
    """
    pipeline = build_ablation_pipeline(TAG, ABLATION_CONFIGS[TAG], seed=None)
    pools:      dict[date, list[str]] = {}
    stock_data: dict[date, dict[str, tuple]] = {}

    for rebal_rp, nxt_rp in _closed_period_pairs(rebalance_points):
        rebal, nxt  = rebal_rp.date, nxt_rp.date
        rtype       = rebal_rp.report_type
        gate_passed = load_gate_passed_tickers(
            conn, rebal, report_type=rtype, fiscal_year=rebal_rp.fiscal_year
        )
        if not gate_passed:
            log.info('%s: gate=0 (TTM 미충족) — 빈 구간', rebal)
            pools[rebal] = []
            stock_data[rebal] = {}
            continue
        pit_series = load_pit_series_ttm(
            conn, rebal, report_type=rtype, fiscal_year=rebal_rp.fiscal_year
        )
        universe   = pipeline.build_universe(gate_passed, rebal, pit_series, conn)['universe']
        pools[rebal] = universe

        # 풀 전체의 종목별 가격·상폐 데이터 1회 prefetch (weight 값은 미사용 자리)
        data = _period_stock_data(conn, {t: 1.0 for t in universe}, rebal, nxt)
        stock_data[rebal] = {t: (ps, pe, last) for t, _w, ps, pe, last in data}
        log.info('%s: pool=%d (가격 유효 %d)', rebal, len(universe), len(stock_data[rebal]))

    # 시장은 전 구간 풀 합집합에 대해 날짜별로 조회 (위 docstring 참조)
    all_tickers = sorted({t for pool in pools.values() for t in pool})
    markets: dict[date, dict[str, str | None]] = {
        rebal: (get_markets(conn, all_tickers, rebal) if all_tickers else {})
        for rebal in pools
    }
    log.info('시장 prefetch: %d종목 × %d일', len(all_tickers), len(markets))

    return pools, stock_data, markets


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


def _net_cagr_from_growth(net_growth: float, span: dict | None) -> float:
    """승법 terminal NAV → CAGR (SPEC_13 §9-1 SSOT).

    compute_cagr()와 동일한 연수 정의(실제 캘린더 경과일 / 365.25, CORR-METRIC-002).
    산술 net=gross−tc 정의는 쓰지 않는다.
    """
    if not span:
        return 0.0
    years = (span['end_date'] - span['start_date']).days / 365.25
    return float(net_growth ** (1 / years) - 1) if years > 0 else 0.0


def run_draws(pools, stock_data, markets, n_draws: int, rebalance_points):
    """전 시드 추첨 실행 (DB 무접촉 — prefetch 데이터만 사용)."""
    pairs = [(r.date, n.date) for r, n in _closed_period_pairs(rebalance_points)]
    span = None
    active_pairs = [(r, n) for r, n in pairs if pools.get(r)]
    if active_pairs:
        span = dict(start_date=active_pairs[0][0], end_date=active_pairs[-1][1])

    draws_rows, period_rows, contrib_rows = [], [], []
    for seed in range(n_draws):
        prev: dict[str, float] = {}
        gross_list, idx = [], []
        net_growth = 1.0
        for rebal, nxt in pairs:
            pool = pools.get(rebal) or []
            if not pool:
                continue
            portfolio = _draw_portfolio(pool, seed, rebal)
            sd = stock_data[rebal]
            valid = [(t, w, *sd[t]) for t, w in portfolio.items() if t in sd]
            gross, _opt, _cons = _aggregate_period_return(valid) if valid else (0.0, 0.0, 0.0)
            turnover = _calc_turnover(prev, portfolio)
            # CORR-COST-001 시장별 요율 — 날짜당 1회 prefetch한 시장 정보 재사용
            # (산식은 engine._transaction_cost_from_markets SSOT, 복제 금지)
            tc = _transaction_cost_from_markets(prev, portfolio, markets.get(rebal, {}))
            # 승법 net (SPEC_13 §9-1): 리밸런싱일 NAV×(1−tc) 후 구간 성장 — 일별 NAV
            # 엔진(stitch_periods)과 동일 정의. 산술 net=gross−tc 폐기.
            # `net`은 **구간 수익률**로 유지한다 (gate_analysis G3′가 구간 단위로 소비).
            net = (1.0 - tc) * (1.0 + gross) - 1.0
            net_growth *= (1.0 + net)
            prev = portfolio

            gross_list.append(gross)
            idx.append(rebal)
            period_rows.append((seed, rebal.isoformat(), gross, net,
                                turnover, tc, len(portfolio)))

            total_w = sum(w for _, w, *_ in valid)
            for t, w, ps, pe, _last in valid:
                contrib_rows.append((seed, rebal.isoformat(), t, w / total_w, pe / ps - 1))

        s_idx = pd.DatetimeIndex(idx)
        cagr     = compute_cagr(pd.Series(gross_list, index=s_idx), **(span or {}))
        net_cagr = _net_cagr_from_growth(net_growth, span)
        draws_rows.append((seed, cagr, net_cagr))
        if (seed + 1) % 200 == 0:
            log.info('추첨 진행 %d/%d', seed + 1, n_draws)

    return draws_rows, period_rows, contrib_rows


def verify_against_engine(conn, pools, stock_data, markets, seed: int,
                          valuation_date: date, rebalance_points) -> None:
    """등가성 게이트 3종 (SPEC_13 §9-1a) — 하나라도 실패하면 결과 저장 없이 중단.

      기존   : fast-path seed 추첨 vs 전체 엔진 — 편입·gross·turnover (tol 1e-12)
      EQ-1   : 승법 net 산식 vs stitch_periods 정의 (동일 gross 수열, tol 1e-12)
      EQ-2   : fast-path terminal net NAV vs 실제 일별 NAV 경로 (tol 1e-6 / 상폐 1e-3)
    """
    log.info('[등가성 게이트] seed=%d 전체 엔진 대조 실행 시작', seed)
    pipeline = build_ablation_pipeline(TAG, ABLATION_CONFIGS[TAG], seed=seed)
    engine   = BacktestEngine(pipeline)
    result   = engine.run(rebalance_points, run_name=f'{TAG}_verify', ablation_tag=TAG,
                          valuation_date=valuation_date)
    engine_closed = [r for r in result['period_results']
                    if r['n_gate'] > 0 and not r['is_open_period']]

    pairs = [(r.date, n.date) for r, n in _closed_period_pairs(rebalance_points)
             if pools.get(r.date)]
    if len(engine_closed) != len(pairs):
        raise SystemExit(
            f'[등가성 게이트 실패] 완결 구간 수 불일치: engine={len(engine_closed)} fast={len(pairs)}'
        )

    prev: dict[str, float] = {}
    fast_growth = 1.0     # fast-path 승법 net (run_draws와 동일 산식)
    nav_growth  = 1.0     # EQ-2 참조: 실제 일별 NAV 경로
    stitch_inputs: list[dict] = []   # EQ-1 참조: 실제 stitch_periods()에 투입
    any_delisted = False

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

        # 거래비용: prefetch 시장(fast-path) vs 실제 DB 조회(엔진 경로) 대조
        tc_fast = _transaction_cost_from_markets(prev, portfolio, markets.get(rebal, {}))
        tc_db   = _calc_transaction_cost(conn, prev, portfolio, rebal)
        if abs(tc_fast - tc_db) > 1e-12:
            raise SystemExit(
                f'[등가성 게이트 실패] {rebal}: tc 불일치 — 시장 prefetch 결함. '
                f'fast={tc_fast!r} db={tc_db!r}'
            )

        fast_growth *= (1.0 + ((1.0 - tc_fast) * (1.0 + gross) - 1.0))
        # EQ-1 참조: 엔진 gross를 1구간 경로로 넣어 **실제 stitch_periods()** 를 태운다.
        # (인라인으로 재구현하면 항등식이라 무조건 통과 — 정의가 바뀌어도 못 잡는다.
        #  실제 함수를 경유해야 daily_nav 쪽 net 정의 변경을 교차 검출할 수 있다.)
        stitch_inputs.append({
            'rebalance_date': rebal, 'obs_dates': [nxt],
            'nav_path': [1.0 + gross], 'transaction_cost': tc_fast,
        })
        # EQ-2: 실제 일별 NAV 경로 (가격·상폐 처리 전부 daily_nav 엔진 경유)
        _obs, nav, _values = daily_nav_for_period(conn, portfolio, rebal, nxt)
        nav_growth *= (1.0 - tc_fast) * float(nav.iloc[-1])
        # _period_stock_data 계약: last is not None ⟺ 해당 구간에 상장폐지
        any_delisted |= any(last is not None for *_rest, last in valid)
        prev = portfolio

    ref_growth = float(stitch_periods(stitch_inputs)['nav_net'].iloc[-1])
    if abs(fast_growth - ref_growth) > TOL_EQ1:
        raise SystemExit(
            f'[EQ-1 실패] 승법 net 산식이 stitch_periods 정의와 불일치 '
            f'(tol {TOL_EQ1:g}): fast={fast_growth!r} stitch={ref_growth!r}'
        )
    tol2 = TOL_EQ2_DELIST if any_delisted else TOL_EQ2_CLEAN
    if abs(fast_growth - nav_growth) > tol2:
        raise SystemExit(
            f'[EQ-2 실패] terminal net NAV 불일치 (tol {tol2:g}, 상폐포함={any_delisted}): '
            f'fast={fast_growth!r} 일별NAV={nav_growth!r} 차이={fast_growth - nav_growth:.3e}'
        )
    log.info('[등가성 게이트] PASS — 편입·gross·turnover·tc 전 구간 일치 (1e-12)')
    log.info('[EQ-1] PASS — 승법 net 산식 일치 (|Δ|=%.3e ≤ %g)',
             abs(fast_growth - ref_growth), TOL_EQ1)
    log.info('[EQ-2] PASS — terminal net NAV 일치 (|Δ|=%.3e ≤ %g, 상폐포함=%s) '
             'fast=%.10f 일별NAV=%.10f',
             abs(fast_growth - nav_growth), tol2, any_delisted, fast_growth, nav_growth)


def main() -> None:
    parser = argparse.ArgumentParser(description='SPEC_10 C_pbr_path_random fast-path')
    parser.add_argument('--n-draws',        type=int, default=N_DRAWS)
    parser.add_argument('--verify-seed',    type=int, default=0)
    parser.add_argument('--skip-verify',    action='store_true',
                        help='등가성 게이트 생략 (디버그 전용 — 공식 실행 금지)')
    parser.add_argument('--valuation-date', required=True,
                        help='등가성 게이트 엔진 실행용 (완결 지표에는 영향 없음)')
    parser.add_argument('--calendar', choices=CALENDAR_CHOICES, default='SEMIANNUAL',
                        help='리밸런싱 캘린더 (SPEC_13 §7). 기본 SEMIANNUAL = 기존 동작·'
                             '기존 파일명. A/C는 산출물에 _A/_C 접미사가 붙는다.')
    args = parser.parse_args()

    _abort_if_cron_window()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    valuation_date   = date.fromisoformat(args.valuation_date)
    rebalance_points = list(get_schedule(args.calendar))
    suffix           = tag_suffix(args.calendar)
    out_tag          = f'{TAG}{suffix}'
    log.info('calendar = %s (%d개 앵커) → %s', args.calendar, len(rebalance_points), out_tag)

    conn = get_connection()
    try:
        pools, stock_data, markets = build_pools(conn, rebalance_points)
        if not args.skip_verify:
            verify_against_engine(conn, pools, stock_data, markets, args.verify_seed,
                                  valuation_date, rebalance_points)
        else:
            log.warning('등가성 게이트 생략 (--skip-verify) — 공식 수치로 사용 금지')
    finally:
        conn.close()

    draws, periods, contribs = run_draws(pools, stock_data, markets, args.n_draws,
                                         rebalance_points)

    with (OUT_DIR / f'{out_tag}_draws.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['seed', 'cagr', 'net_cagr'])
        w.writerows(draws)
    with gzip.open(OUT_DIR / f'{out_tag}_periods.csv.gz', 'wt', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['seed', 'rebalance_date', 'gross', 'net', 'turnover', 'tc', 'n_stocks'])
        w.writerows(periods)
    with gzip.open(OUT_DIR / f'{out_tag}_contrib.csv.gz', 'wt', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['seed', 'rebalance_date', 'ticker', 'weight_eff', 'ret'])
        w.writerows(contribs)
    (OUT_DIR / f'pools{suffix}.json').write_text(
        json.dumps({d.isoformat(): p for d, p in pools.items()}, ensure_ascii=False, indent=1),
        encoding='utf-8')

    cagrs     = sorted(r[1] for r in draws)
    net_cagrs = sorted(r[2] for r in draws)
    n = len(cagrs)
    summary = {
        'tag': out_tag, 'calendar': args.calendar,
        'generated_at': datetime.now().isoformat(),
        'n_draws': n, 'seed_scheme': 'random.Random(f"{seed}:{rebalance_date}") — seed 0..n-1',
        'verify_seed': None if args.skip_verify else args.verify_seed,
        'cost_model': 'CORR-COST-001 (매수/매도 분리 + 시장별 매도요율)',
        'net_definition': '승법 (SPEC_13 §9-1) — net_growth *= (1−tc)(1+gross)',
        'median_cagr': cagrs[n // 2], 'p5_cagr': cagrs[int(n * 0.05)],
        'p95_cagr': cagrs[int(n * 0.95)],
        # QG1 비교 기준은 net (§9-1 SSOT) — gross p95는 참고용
        'median_net_cagr': net_cagrs[n // 2], 'p5_net_cagr': net_cagrs[int(n * 0.05)],
        'p95_net_cagr': net_cagrs[int(n * 0.95)],
        'pool_sizes': {d.isoformat(): len(p) for d, p in pools.items()},
    }
    (OUT_DIR / f'random_summary{suffix}.json').write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
    log.info('완료: n=%d  gross median=%.4f p95=%.4f  |  net median=%.4f p95=%.4f',
             n, summary['median_cagr'], summary['p95_cagr'],
             summary['median_net_cagr'], summary['p95_net_cagr'])

    if args.skip_verify:
        sys.exit(2)


if __name__ == '__main__':
    main()
