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
import inspect
import json
import logging
import os
from collections import Counter
from datetime import date, datetime
from multiprocessing import Pool, cpu_count
from pathlib import Path

from backtest.ablation import (
    ABLATION_CONFIGS,
    RANDOM_REPEATS,
    RANDOM_TAGS,
    build_ablation_pipeline,
)
from backtest.configs.schedule import (
    CALENDAR_CHOICES,
    RebalancePoint,
    get_schedule,
    tag_suffix,
)
from backtest.engine import BacktestEngine
from backtest.metrics import compute_metrics

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

# 기본 종목 수는 재선언하지 않고 build_ablation_pipeline 시그니처에서 읽는다
# (CLAUDE.md 코드 정합성 규칙: 상수 재선언 금지). 그쪽이 바뀌면 여기도 따라간다.
DEFAULT_N_STOCKS: int = inspect.signature(
    build_ablation_pipeline).parameters['n_stocks'].default

OUT_DIR = Path('experiments/ablation')


def _run_one(args: tuple) -> dict:
    """멀티프로세싱 워커. (tag, config, seed, rebalance_points, valuation_date) → metrics dict."""
    tag, config, seed, rebalance_points, valuation_date = args
    pipeline = build_ablation_pipeline(tag, config, seed=seed)
    engine   = BacktestEngine(pipeline)
    result   = engine.run(rebalance_points, run_name=tag, ablation_tag=tag,
                          valuation_date=valuation_date)
    m        = result['metrics']
    return {
        'seed':               seed,
        'cagr':               m['cagr'],
        'net_cagr':           m.get('net_cagr', 0.0),
        'alpha':              m['alpha'],
        'alpha_kosdaq':       m.get('alpha_kosdaq', 0.0),
        'sharpe':             m['sharpe'],
        'net_sharpe':         m.get('net_sharpe', 0.0),
        'mdd':                m['mdd'],
        'robustness':         m['robustness'],
        'benchmark_cagr':     m['benchmark_cagr'],
        'kosdaq_cagr':        m.get('kosdaq_cagr', 0.0),
        'avg_turnover':       m.get('avg_turnover', 0.0),
        'cagr_optimistic':    m.get('cagr_optimistic', 0.0),
        'cagr_conservative':  m.get('cagr_conservative', 0.0),
        'n_periods':          m['n_periods'],
    }


def calendar_metadata(points: list[RebalancePoint]) -> dict:
    """이 실행이 실제로 쓴 캘린더를 앵커에서 **파생**한다. 라벨을 믿지 않는다.

    "안 A 는 분기다"라는 사실이 지금까지 **태그 이름 `_A` 에만** 있었다 — `n_stocks`
    와 똑같은 병이다(2026-08-12). 이름과 내용이 어긋나도 잡을 수단이 없었고, 그래서
    "캘린더 A/C 메타데이터 정합" 검사를 아예 만들 수 없었다.

    `args.calendar` 라벨이 아니라 앵커에서 뽑는 이유: 라벨은 사람이 넘기는 값이라
    틀릴 수 있지만, 앵커는 엔진이 실제로 순회한 것이다. `report_types` 분포가
    분기(Q1/Q3 포함)와 반기(FY/H1 뿐)를 내용으로 구별해 준다.
    """
    ids = sorted({p.calendar_id for p in points})
    return {
        # 섞이면 숨기지 않고 그대로 드러낸다 — 섞인 실행은 그 자체가 사고다.
        'id':            ids[0] if len(ids) == 1 else '+'.join(ids),
        'n_anchors':     len(points),
        'report_types':  dict(sorted(Counter(p.report_type for p in points).items())),
        'first_anchor':  min(p.date for p in points).isoformat(),
        'last_anchor':   max(p.date for p in points).isoformat(),
    }


def run_deterministic(tag: str, config: dict, rebalance_points: list[RebalancePoint],
                      valuation_date: date | None = None,
                      n_stocks: int | None = None) -> tuple[dict, list[dict]]:
    """단일 실행 (D/E/F/G). (metrics_dict, period_results) 반환.

    `n_stocks`가 None이면 `build_ablation_pipeline`의 기본값(20)을 쓴다.
    """
    log.info(f'[{tag}] 실행 시작' + (f' (n_stocks={n_stocks})' if n_stocks else ''))
    kw = {} if n_stocks is None else {'n_stocks': n_stocks}
    pipeline = build_ablation_pipeline(tag, config, seed=None, **kw)
    engine   = BacktestEngine(pipeline)
    result   = engine.run(rebalance_points, run_name=tag, ablation_tag=tag,
                          valuation_date=valuation_date or date.today())
    m        = result['metrics']
    metrics  = {
        'seed':               None,
        'cagr':               m['cagr'],
        'net_cagr':           m.get('net_cagr', 0.0),
        'alpha':              m['alpha'],
        'alpha_kosdaq':       m.get('alpha_kosdaq', 0.0),
        'sharpe':             m['sharpe'],
        'net_sharpe':         m.get('net_sharpe', 0.0),
        'mdd':                m['mdd'],
        'robustness':         m['robustness'],
        'benchmark_cagr':     m['benchmark_cagr'],
        'kosdaq_cagr':        m.get('kosdaq_cagr', 0.0),
        'avg_turnover':       m.get('avg_turnover', 0.0),
        'cagr_optimistic':    m.get('cagr_optimistic', 0.0),
        'cagr_conservative':  m.get('cagr_conservative', 0.0),
        'n_periods':          m['n_periods'],
        # 종목 수는 지금까지 **어떤 산출물에도 기록되지 않았고** 태그 이름 문자열
        # (`_n13`)에만 있었다. 이름과 내용이 어긋나도 아무도 못 잡던 원인이다.
        'n_stocks':           n_stocks if n_stocks is not None else DEFAULT_N_STOCKS,
        # 캘린더도 이름(`_A`/`_C`)에만 있었다 — 위와 같은 이유로 내용에 기록한다.
        'calendar':           calendar_metadata(rebalance_points),
    }
    log.info(
        f'[{tag}] CAGR={m["cagr"]:.1%} (net={m.get("net_cagr", 0):.1%}) '
        f'[상폐: 낙관={m.get("cagr_optimistic", 0):.1%} 보수={m.get("cagr_conservative", 0):.1%}] '
        f'Alpha(KS)={m["alpha"]:.1%} Alpha(KQ)={m.get("alpha_kosdaq", 0):.1%} '
        f'Turnover={m.get("avg_turnover", 0):.0%} MDD={m["mdd"]:.1%}'
    )
    return metrics, result['period_results']


def run_random_distribution(
    tag:              str,
    config:           dict,
    rebalance_points: list[RebalancePoint],
    valuation_date:   date | None = None,
    n_repeats:        int = RANDOM_REPEATS,
    n_workers:        int | None = None,
) -> list[dict]:
    """500회 반복 실행 (A/B/C). 분포 리스트 반환."""
    workers = n_workers or max(1, cpu_count() - 1)
    log.info(f'[{tag}] 랜덤 {n_repeats}회 반복 — workers={workers}')

    tasks = [(tag, config, seed, rebalance_points, valuation_date) for seed in range(n_repeats)]
    with Pool(processes=workers) as pool:
        results = pool.map(_run_one, tasks)

    cagrs = [r['cagr'] for r in results]
    log.info(
        f'[{tag}] 중앙값 CAGR={sorted(cagrs)[n_repeats//2]:.1%}  '
        f'p5={sorted(cagrs)[int(n_repeats*0.05)]:.1%}  '
        f'p95={sorted(cagrs)[int(n_repeats*0.95)]:.1%}'
    )
    return results


def save_deterministic(tag: str, result: dict, out_tag: str | None = None) -> None:
    out_tag = out_tag or tag
    path = OUT_DIR / f'{out_tag}.json'
    path.write_text(
        json.dumps({'tag': out_tag, 'run_at': datetime.now().isoformat(), **result}, indent=2),
        encoding='utf-8',
    )
    log.info(f'  → {path}')


def save_periods(tag: str, period_results: list[dict], out_tag: str | None = None) -> None:
    """구간별 수익률 및 필터 통과 수를 CSV로 저장."""
    import csv
    FILTER_KEYS = ['HardFilter', 'StabilityFilter', 'FactorScreener', 'MomentumFilter']
    path = OUT_DIR / f'{out_tag or tag}_periods.csv'
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow([
            'rebalance_date', 'next_date',
            'period_return', 'net_return', 'turnover', 'transaction_cost',
            'kospi_return', 'kosdaq_return',
            'n_gate', 'n_stocks',
            'hard_passed', 'stability_passed', 'screener_passed', 'momentum_passed',
        ])
        for r in period_results:
            stats = r.get('universe_stats', {})
            w.writerow([
                r['rebalance_date'].isoformat(),
                r['next_date'].isoformat(),
                r['period_return'],
                r.get('net_return', ''),
                r.get('turnover', ''),
                r.get('transaction_cost', ''),
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


def save_distribution(tag: str, results: list[dict], out_tag: str | None = None) -> None:
    import csv
    path = OUT_DIR / f'{out_tag or tag}_dist.csv'
    fields = [
        'seed', 'cagr', 'net_cagr', 'alpha', 'alpha_kosdaq',
        'sharpe', 'net_sharpe', 'mdd', 'robustness',
        'benchmark_cagr', 'kosdaq_cagr', 'avg_turnover',
        'cagr_optimistic', 'cagr_conservative', 'n_periods',
    ]
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(results)
    log.info(f'  → {path} ({len(results)}행)')


def make_summary(det_results: dict[str, dict], dist_stats: dict[str, dict],
                 prior_scenarios: dict[str, dict] | None = None) -> dict:
    """비교 요약 (판정 기준 포함).

    `prior_scenarios`가 주어지면 그 위에 이번 실행분을 **덮어쓰기 병합**한다.
    통째로 새로 쓰면 이번에 안 돌린 태그의 기록이 조용히 사라진다 — 실제로
    2026-07-18 공표본(39태그)이 이후 부분 실행들에 이렇게 지워졌다.
    태그별 `run_at`을 함께 적어 어느 항목이 오래된 코드 산출인지 추적 가능하게 한다.
    """
    now = datetime.now().isoformat()
    summary: dict = {'generated_at': now, 'scenarios': dict(prior_scenarios or {})}

    for tag, r in det_results.items():
        entry = {
            k: round(v, 6) for k, v in r.items()
            if k != 'seed' and isinstance(v, (int, float))
        }
        entry['run_at'] = now
        summary['scenarios'][tag] = entry

    for tag, s in dist_stats.items():
        summary['scenarios'][tag] = {**s, 'run_at': now}

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
    if 'D_no_r6' in s and 'D_pbr_only' in s:
        # STEP 3 신호분리: 동일 필터(R1~R5, R6 제외) 하에서 RIM 랭킹 vs 순수 1/PBR 랭킹 비교
        judgements['D_no_r6>D_pbr_only (RIM 고유 신호, 1/PBR 재포장 아님)'] = (
            cagr('D_no_r6') > cagr('D_pbr_only')
        )
        judgements['_D_no_r6_cagr']   = round(cagr('D_no_r6'), 6)
        judgements['_D_pbr_only_cagr'] = round(cagr('D_pbr_only'), 6)
    if 'F_no_r6' in s and 'F_pbr_only' in s:
        # STEP 3 후속: 모멘텀 결합 시에도 RIM 랭킹이 1/PBR 랭킹보다 나은가 —
        # D_no_r6 < D_pbr_only(2026-07-15 재실행에서 반전)가 모멘텀 위에서도 유지되면
        # "1/PBR+모멘텀"이 채택안(RIM+모멘텀)의 더 단순한 대체안이 된다
        judgements['F_no_r6>F_pbr_only (모멘텀 결합 시 RIM 고유 신호)'] = (
            cagr('F_no_r6') > cagr('F_pbr_only')
        )
        judgements['_F_no_r6_cagr']    = round(cagr('F_no_r6'), 6)
        judgements['_F_pbr_only_cagr'] = round(cagr('F_pbr_only'), 6)
    if 'D_no_r6' in s and 'D_factor_only' in s:
        # STEP 3B 후속: FactorScreener 4팩터 합산 점수를 RIM 없이 단독 선정 기준으로 썼을 때
        # RIM 랭킹(D_no_r6) 대비 얼마나 나쁜지/좋은지 — "위치 문제 vs 구성 자체 문제" 분리
        judgements['D_no_r6>D_factor_only (팩터 컴포지트 단독 대비 RIM 우위)'] = (
            cagr('D_no_r6') > cagr('D_factor_only')
        )
        judgements['_D_factor_only_cagr'] = round(cagr('D_factor_only'), 6)
        if 'C_no_r6' in s:
            judgements['D_factor_only>C_no_r6 (팩터 컴포지트 자체가 랜덤보다 나은가)'] = (
                cagr('D_factor_only') > cagr('C_no_r6')
            )
    if 'D_rim_only' in s:
        # FactorScreener 단일팩터 진단: D_rim_only(스크리너 없음) 대비 각 팩터 단독 프리필터+RIM 비교
        d_cagr = cagr('D_rim_only')
        for factor_tag in ('E_rev_only', 'E_op_only', 'E_gpa_only', 'E_pbr_only'):
            if factor_tag in s:
                judgements[f'{factor_tag}<D_rim_only (해당 팩터 프리필터가 알파를 깎는가)'] = (
                    cagr(factor_tag) < d_cagr
                )
                judgements[f'_{factor_tag}_cagr'] = round(cagr(factor_tag), 6)

    # StabilityFilter 검증 (SPEC_05 부록 A)
    if 'D_rim_only' in s and 'D_no_stability' in s:
        # G-2: RIM 경로 위에서 stability 레이어의 순증 기여 (모멘텀 교란 없음)
        judgements['D_rim_only>D_no_stability (stability 레이어 기여, RIM 경로)'] = (
            cagr('D_rim_only') > cagr('D_no_stability')
        )
        judgements['_D_no_stability_cagr'] = round(cagr('D_no_stability'), 6)
    if 'F_momentum_rim' in s and 'F_no_stability_clean' in s:
        # G-3: 채택 파이프라인(screener 없음)에서 stability 레이어의 순증 기여 — 결정적 관문
        # H_no_stability는 screener까지 같이 꺼져 교란되므로 이 비교가 깨끗한 대조군
        judgements['F>F_no_stability_clean (stability 레이어 기여, 채택 파이프라인)'] = (
            cagr('F_momentum_rim') > cagr('F_no_stability_clean')
        )
        judgements['_F_no_stability_clean_cagr'] = round(cagr('F_no_stability_clean'), 6)
    if 'D_rim_only' in s:
        # G-4: R1~R5 leave-one-out — D_rim_only(전체 룰 적용) 대비 각 룰 제외 시 하락폭 = 그 룰의 기여
        d_cagr = cagr('D_rim_only')
        for i in range(1, 6):
            rule_tag = f'D_no_r{i}'
            if rule_tag in s:
                judgements[f'{rule_tag}<D_rim_only (R{i} 개별 기여)'] = cagr(rule_tag) < d_cagr
                judgements[f'_{rule_tag}_cagr'] = round(cagr(rule_tag), 6)

    if 'C_pbr_path_random' in s and 'F_pbr_no_r3r4' in s:
        # SPEC_10 §5-1 G1: RIM에 요구했던 것과 동일한 분포 관문 — 채택 후보가
        # 동일 필터 풀 무작위 추첨의 p95를 넘어야 랭킹 고유 기여 인정
        judgements['G1: F_pbr_no_r3r4>=C_pbr_path_random_p95 (SPEC_10 §5-1)'] = (
            cagr('F_pbr_no_r3r4') >= p95('C_pbr_path_random')
        )
        judgements['_C_pbr_path_random_p95'] = round(p95('C_pbr_path_random'), 6)
    if 'U_pbr_path_ew' in s and 'F_pbr_no_r3r4' in s:
        # SPEC_10 §5-1 G2: 랭킹이 유니버스 축소 이상의 기여를 하는가 (net 기준)
        f_net = s.get('F_pbr_no_r3r4', {}).get('net_cagr', 0.0)
        u_net = s.get('U_pbr_path_ew', {}).get('net_cagr', 0.0)
        judgements['G2: F_pbr_no_r3r4_net>U_pbr_path_ew_net (SPEC_10 §5-1)'] = f_net > u_net
        judgements['_U_pbr_path_ew_net_cagr'] = round(u_net, 6)

    if 'F_momentum_rim' in s:
        # R2/R3/R4 단일·조합 제외 — 채택 파이프라인(F) 기준, R1과의 중복·상호작용 확인
        f_cagr = cagr('F_momentum_rim')
        for combo_tag in ('F_no_r2', 'F_no_r3', 'F_no_r4',
                          'F_no_r2r3', 'F_no_r2r4', 'F_no_r3r4', 'F_no_r2r3r4'):
            if combo_tag in s:
                judgements[f'{combo_tag}<F_momentum_rim'] = cagr(combo_tag) < f_cagr
                judgements[f'_{combo_tag}_cagr'] = round(cagr(combo_tag), 6)

    summary['judgements'] = judgements
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description='Ablation Test 실행')
    parser.add_argument('--tags',        nargs='+', help='실행할 태그 목록 (기본: 전체)')
    parser.add_argument('--random-only', action='store_true', help='랜덤 시나리오(A/B/C)만 실행')
    parser.add_argument('--det-only',    action='store_true', help='비랜덤 시나리오(D/E/F/G)만 실행')
    parser.add_argument('--repeats',     type=int, default=RANDOM_REPEATS, help='랜덤 반복 횟수')
    parser.add_argument('--workers',     type=int, default=None,           help='병렬 프로세스 수')
    parser.add_argument('--valuation-date', default=None,
                        help='열린 구간 평가 기준일 YYYY-MM-DD (기본: 오늘 — CLI에서 결정, '
                             '엔진은 date.today()를 내부 호출하지 않는다)')
    # 2026-08-11 n 스윕(커밋 2421ee1)은 저장소에 없는 임시 스크립트로 돌아
    # `run_at` 없이 summary 병합도 안 된 산출물을 남겼다 — 운영 설정(n=13)이 바로
    # 그 산출물이었다. 스윕을 정규 경로로 끌어들여 재현 가능하게 한다.
    parser.add_argument('--n-stocks', type=int, default=None,
                        help='포트폴리오 종목 수. 지정 시 산출물 태그에 `_n{K}` 접미사가 '
                             '붙는다 (기본: build_ablation_pipeline 기본값 20, 접미사 없음)')
    parser.add_argument('--calendar', choices=CALENDAR_CHOICES, default='SEMIANNUAL',
                        help='리밸런싱 캘린더 (SPEC_13 §7). 기본 SEMIANNUAL = 기존 동작·'
                             '기존 파일명. A/C는 산출물에 _A/_C 접미사가 붙는다.')
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    tags_to_run = set(args.tags or ABLATION_CONFIGS.keys())
    if args.random_only:
        tags_to_run &= RANDOM_TAGS
    if args.det_only:
        tags_to_run -= RANDOM_TAGS

    rebalance_points = list(get_schedule(args.calendar))
    suffix           = tag_suffix(args.calendar)
    # --n-stocks 를 **명시하면 항상** `_n{K}` 접미사를 붙인다 (K가 기본값 20이어도).
    # 기존 스윕 산출물(_n10/_n12/_n13/_n20)이 이 규약이고, 소비처가 키를
    # `f'{tag}_n{N_STOCKS}'` 로 조립할 수 있어야 조회가 균일해진다.
    # 플래그를 안 주면 접미사 없음 = 종전 전체 실행과 비트 동일.
    n_sfx            = '' if args.n_stocks is None else f'_n{args.n_stocks}'
    valuation_date   = (date.fromisoformat(args.valuation_date)
                       if args.valuation_date else date.today())
    log.info(f'valuation_date = {valuation_date}  calendar = {args.calendar} '
             f'({len(rebalance_points)}개 앵커)')

    det_results:  dict[str, dict] = {}
    dist_stats:   dict[str, dict] = {}

    for tag in ABLATION_CONFIGS:   # 정해진 순서 유지
        if tag not in tags_to_run:
            continue
        config  = ABLATION_CONFIGS[tag]
        # summary 안의 키는 캘린더 접미사를 갖지 않는다 (파일이 이미 캘린더별로 갈린다).
        # n 접미사는 **갖는다** — n 이 다르면 다른 전략이라 같은 키를 쓰면 서로 덮는다.
        key     = f'{tag}{n_sfx}'
        out_tag = f'{key}{suffix}'

        if tag in RANDOM_TAGS:
            # 귀무분포의 추첨 종목 수는 config의 `random_n`이 `n_stocks`를 이긴다
            # (build_ablation_pipeline). --n-stocks 를 줬으면 그 의도를 따르게
            # config 사본에서 갈아끼운다 — 판정 대상과 귀무분포의 n 이 어긋나면
            # 게이트가 성립하지 않는다 (2026-08-12: n=13 전략을 n=20 풀로 판정 중이었다).
            run_config = ({**config, 'random_n': args.n_stocks}
                          if args.n_stocks is not None else config)
            results  = run_random_distribution(tag, run_config, rebalance_points, valuation_date,
                                               n_repeats=args.repeats, n_workers=args.workers)
            save_distribution(tag, results, out_tag)
            cagrs = sorted(r['cagr'] for r in results)
            n     = len(cagrs)
            dist_stats[key] = {
                'median_cagr':  round(cagrs[n // 2], 6),
                'p5_cagr':      round(cagrs[int(n * 0.05)], 6),
                'p95_cagr':     round(cagrs[int(n * 0.95)], 6),
                'n_repeats':    n,
                'n_stocks':     run_config.get('random_n'),
                'calendar':     calendar_metadata(rebalance_points),
            }
        else:
            result, period_results = run_deterministic(tag, config, rebalance_points,
                                                       valuation_date, n_stocks=args.n_stocks)
            save_deterministic(tag, result, out_tag)
            save_periods(tag, period_results, out_tag)
            det_results[key] = result

    # 캘린더별 분리 저장 — 접미사 없이 쓰면 기존 공식 산출물(반기) 기록이 유실된다.
    # 캘린더 분리는 캘린더 간 유실만 막으므로, 같은 캘린더 안 태그 간 유실은
    # 아래 병합으로 막는다 (run_daily_nav.py와 동일 패턴).
    summary_path = OUT_DIR / f'summary{suffix}.json'
    prior_scenarios: dict[str, dict] = {}
    if summary_path.exists():
        prior_scenarios = json.loads(
            summary_path.read_text(encoding='utf-8')).get('scenarios', {})

    summary = make_summary(det_results, dist_stats, prior_scenarios)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
    log.info(f'\n판정 결과:')
    for k, v in summary.get('judgements', {}).items():
        log.info(f'  {"✅" if v else "❌"} {k}')
    log.info(f'\n요약 저장: {summary_path}')


if __name__ == '__main__':
    main()
