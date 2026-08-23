"""
구간 절단 재슬라이스 + G1 재산출 (2026-08-24).

사전등록: `docs/검토/2026.08.24._PREREG_PERIOD_TRUNCATION.md` (커밋 09874a4).
**tape 를 재실행하지 않는다.** 기존 구간별 수익률 기록을 다시 자를 뿐이다.

산식은 전부 SSOT 재사용 (`backtest.metrics`) — 복제 금지.
귀무분포 p95 추정량은 `gate_analysis.py` 와 동일하게 `sorted(x)[int(n*0.95)]`.

실행:
    python -m scripts.analysis.period_truncation
"""
from __future__ import annotations

import csv
import gzip
import json
import logging
import random
from datetime import date
from pathlib import Path

import pandas as pd

from backtest.metrics import (
    compute_cagr,
    compute_mdd,
    compute_robustness,
    compute_sharpe,
)
from scripts.analysis.coverage_slice import (
    PREREG_THRESHOLD,
    contiguous_tail,
    load_rule_coverage,
    selected_anchors,
)

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger(__name__)

ABL_DIR = Path('experiments/ablation')
ROB_DIR = Path('experiments/robustness')
OUT_DIR = Path('experiments/analysis/2026.08.24._period_truncation')

F_TAG     = 'F_pbr_ma200_n13'
DRAWS_TAG = 'C_pbr_ma200_random_n13'
RULES     = frozenset({'R1', 'R2', 'R5', 'R6'})   # ablation.py:109-113 채택안 조합

TOL_POSITIVE_CONTROL = 1e-12    # 사전등록 §1
BOOT_B    = 10_000              # 사전등록 §0-4
BOOT_SEED = 20260824
FRESH_START_TC = 0.0035         # 매수만 발생하는 신규 진입 구간의 tc (실측 상수)


# ── 입력 ────────────────────────────────────────────────────────────────────
def load_f_periods() -> list[dict]:
    """F 태그의 **완결 구간** 행 (n_stocks>0, 열린 구간 제외) — 시간순."""
    rows = list(csv.DictReader((ABL_DIR / f'{F_TAG}_periods.csv').open(encoding='utf-8')))
    active = [r for r in rows if int(r['n_stocks']) > 0]
    # 열린 구간 = next_date 가 리밸런싱 앵커가 아닌 행 (engine.is_open_period 와 동치)
    anchors = {r['rebalance_date'] for r in rows}
    return [r for r in active if r['next_date'] in anchors]


def load_draw_periods() -> dict[int, dict[str, dict]]:
    """{seed: {rebalance_date: row}} — 추첨 구간별 gross/net/turnover/tc."""
    out: dict[int, dict[str, dict]] = {}
    with gzip.open(ROB_DIR / f'{DRAWS_TAG}_periods.csv.gz', 'rt', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            out.setdefault(int(row['seed']), {})[row['rebalance_date']] = row
    return out


# ── 재슬라이스 산식 ─────────────────────────────────────────────────────────
def _span(rows: list[dict]) -> dict:
    return {'start_date': date.fromisoformat(rows[0]['rebalance_date']),
            'end_date':   date.fromisoformat(rows[-1]['next_date'])}


def _series(rows: list[dict], col: str) -> pd.Series:
    idx = pd.DatetimeIndex([r['rebalance_date'] for r in rows])
    return pd.Series([float(r[col]) for r in rows], index=idx)


def slice_f(rows: list[dict]) -> dict:
    """F 지표 재산출 — engine.run() 7절과 같은 정의·같은 SSOT 함수."""
    span   = _span(rows)
    gross  = _series(rows, 'period_return')
    net    = _series(rows, 'net_return')
    kospi  = _series(rows, 'kospi_return')
    kosdaq = _series(rows, 'kosdaq_return')
    cagr   = compute_cagr(gross, **span)
    bench  = compute_cagr(kospi, **span)
    kq     = compute_cagr(kosdaq, **span)
    return {
        'n_periods':      len(rows),
        'start':          rows[0]['rebalance_date'],
        'end':            rows[-1]['next_date'],
        'years':          (span['end_date'] - span['start_date']).days / 365.25,
        'cagr':           cagr,
        'net_cagr':       compute_cagr(net, **span),
        'sharpe':         compute_sharpe(gross),
        'net_sharpe':     compute_sharpe(net),
        'mdd':            compute_mdd(gross),
        'robustness':     compute_robustness(gross, kospi),
        'benchmark_cagr': bench,
        'kosdaq_cagr':    kq,
        'alpha':          cagr - bench,
        'alpha_kosdaq':   cagr - kq,
        'avg_turnover':   sum(float(r['turnover']) for r in rows) / len(rows),
    }


def slice_draw(per: dict[str, dict], dates: list[str], span: dict,
               fresh_start_tc: bool = False) -> tuple[float, float]:
    """한 추첨의 (gross CAGR, net CAGR) — 같은 구간·같은 span.

    net 은 승법 terminal NAV (run_random_pool._net_cagr_from_growth 정의).
    `fresh_start_tc` 는 사전등록 S1 부수 보고용 — 첫 구간 tc 를 매수전용으로 교체.
    """
    idx   = pd.DatetimeIndex(dates)
    gross = pd.Series([float(per[d]['gross']) for d in dates], index=idx)
    growth = 1.0
    for i, d in enumerate(dates):
        g  = float(per[d]['gross'])
        tc = FRESH_START_TC if (fresh_start_tc and i == 0) else float(per[d]['tc'])
        growth *= (1.0 - tc) * (1.0 + g)
    years = (span['end_date'] - span['start_date']).days / 365.25
    return compute_cagr(gross, **span), float(growth ** (1 / years) - 1)


def p95(values: list[float]) -> float:
    """gate_analysis.py 146행과 동일한 추정량 — 정의를 바꾸지 않는다."""
    return sorted(values)[int(len(values) * 0.95)]


def bootstrap_p95_ci(values: list[float], b: int = BOOT_B, seed: int = BOOT_SEED,
                     ) -> tuple[float, float]:
    """복원추출 재표본의 p95 분포 → 95% CI (사전등록 0-4절)."""
    rng = random.Random(seed)
    n = len(values)
    boots = [p95([values[rng.randrange(n)] for _ in range(n)]) for _ in range(b)]
    boots.sort()
    return boots[int(b * 0.025)], boots[int(b * 0.975)]


def label(f: float, p: float, ci: tuple[float, float]) -> str:
    """사전등록 0-4절 최종 라벨."""
    lo, hi = ci
    if f >= p:
        return 'PASS' if f >= hi else 'INCONCLUSIVE (PASS 쪽)'
    return 'INCONCLUSIVE (FAIL 쪽)' if f >= lo else 'FAIL'


def pctl_below(x: float, xs: list[float]) -> float:
    """robustness_lib.percentile_below 와 같은 정의 (동률 절반 가중)."""
    below = sum(1 for v in xs if v < x)
    ties  = sum(1 for v in xs if v == x)
    return (below + 0.5 * ties) / len(xs)


# ── 실행 ────────────────────────────────────────────────────────────────────
def positive_control(f_rows, draws, all_anchors) -> tuple[dict, dict, dict]:
    """전체 20구간 재슬라이스 == 기존 공표 수치. 실패하면 중단한다."""
    log.info('== 양성 대조: 전체 %d구간 재슬라이스 vs 기존 공표 ==', len(f_rows))
    published = json.loads((ABL_DIR / f'{F_TAG}.json').read_text(encoding='utf-8'))
    gate_pub  = json.loads(
        (ROB_DIR / f'gate_results_{F_TAG}.json').read_text(encoding='utf-8'))
    full = slice_f(f_rows)
    fails = []
    for k in ('cagr', 'net_cagr', 'sharpe', 'net_sharpe', 'mdd', 'robustness',
              'alpha', 'alpha_kosdaq', 'benchmark_cagr', 'kosdaq_cagr',
              'avg_turnover', 'n_periods'):
        d = abs(full[k] - published[k])
        ok = d <= TOL_POSITIVE_CONTROL
        log.info('  F.%-15s 재슬라이스=%.15g 공표=%.15g |d|=%.2e %s',
                 k, full[k], published[k], d, 'OK' if ok else '**불일치**')
        if not ok:
            fails.append(f'F.{k}')

    span_full = _span(f_rows)
    pub = {int(r['seed']): (float(r['cagr']), float(r['net_cagr'])) for r in
           csv.DictReader((ROB_DIR / f'{DRAWS_TAG}_draws.csv').open(encoding='utf-8'))}
    re_g, re_n = {}, {}
    worst_g = worst_n = 0.0
    for s, per in draws.items():
        if set(per) != set(all_anchors):
            raise SystemExit(f'seed={s}: 추첨 구간 집합이 F와 불일치 - 중단')
        g, n = slice_draw(per, all_anchors, span_full)
        re_g[s], re_n[s] = g, n
        worst_g = max(worst_g, abs(g - pub[s][0]))
        worst_n = max(worst_n, abs(n - pub[s][1]))
    log.info('  귀무분포 %d추첨 gross 최대|d|=%.2e / net 최대|d|=%.2e',
             len(draws), worst_g, worst_n)
    if worst_g > TOL_POSITIVE_CONTROL:
        fails.append('draws.cagr')
    if worst_n > TOL_POSITIVE_CONTROL:
        fails.append('draws.net_cagr')

    p95_full = p95(list(re_g.values()))
    pub_p95 = gate_pub['hard_gates']['G1']['random_p95']
    d_p95 = abs(p95_full - pub_p95)
    log.info('  p95 재슬라이스=%.15g 공표=%.15g |d|=%.2e %s',
             p95_full, pub_p95, d_p95, 'OK' if d_p95 <= TOL_POSITIVE_CONTROL else '**불일치**')
    if d_p95 > TOL_POSITIVE_CONTROL:
        fails.append('p95')

    d_pct = abs(pctl_below(full['cagr'], list(re_g.values()))
                - gate_pub['hard_gates']['G1']['f_percentile_in_null'])
    log.info('  G1 백분위 |d|=%.2e %s', d_pct,
             'OK' if d_pct <= TOL_POSITIVE_CONTROL else '**불일치**')
    if d_pct > TOL_POSITIVE_CONTROL:
        fails.append('f_percentile')

    if fails:
        raise SystemExit(f'양성 대조 실패 - 재슬라이스 코드가 틀렸다: {fails}')
    log.info('  >> 양성 대조 통과 (허용오차 %g)\n', TOL_POSITIVE_CONTROL)
    return full, re_g, re_n


def summarize(rows_, m, gd, nd) -> dict:
    gv, nv = list(gd.values()), list(nd.values())
    gp, gci = p95(gv), bootstrap_p95_ci(gv)
    np_, nci = p95(nv), bootstrap_p95_ci(nv)
    return {
        'periods': [r['rebalance_date'] for r in rows_],
        'metrics': m,
        'null_gross': {'median': sorted(gv)[len(gv) // 2], 'p5': sorted(gv)[int(len(gv) * .05)],
                       'p95': gp, 'ci95_of_p95': list(gci),
                       'f_percentile': pctl_below(m['cagr'], gv)},
        'null_net': {'median': sorted(nv)[len(nv) // 2], 'p5': sorted(nv)[int(len(nv) * .05)],
                     'p95': np_, 'ci95_of_p95': list(nci),
                     'f_percentile': pctl_below(m['net_cagr'], nv)},
        'G1': {'pass': bool(m['cagr'] >= gp), 'label': label(m['cagr'], gp, gci),
               'f_cagr': m['cagr'], 'random_p95': gp, 'n_draws': len(gv)},
        'G1_net_report_only': {'f_net_cagr': m['net_cagr'], 'random_p95_net': np_,
                               'label': label(m['net_cagr'], np_, nci)},
    }


def main() -> None:
    f_rows = load_f_periods()
    draws  = load_draw_periods()
    all_anchors = [r['rebalance_date'] for r in f_rows]

    full, re_g, re_n = positive_control(f_rows, draws, all_anchors)

    # ---- 사전등록 구간 선택 ------------------------------------------------
    cov = load_rule_coverage()
    sel = [a for a in selected_anchors(RULES, PREREG_THRESHOLD, cov)
           if a in set(all_anchors)]
    keep = contiguous_tail(sel, all_anchors)
    log.info('== 사전등록 절단 (tau=%.0f%%, 규칙 %s) ==',
             PREREG_THRESHOLD * 100, sorted(RULES))
    log.info('  임계 탈락: %s', sorted(set(all_anchors) - set(sel)))
    log.info('  연속화로 추가 상실: %s',
             [a for a in all_anchors if a in set(sel) and a not in set(keep)])
    log.info('  채택 구간 n=%d  %s ~ %s\n', len(keep), keep[0],
             next(r['next_date'] for r in f_rows if r['rebalance_date'] == keep[-1]))

    cut_rows = [r for r in f_rows if r['rebalance_date'] in set(keep)]
    span_cut = _span(cut_rows)
    cut = slice_f(cut_rows)
    cut_pairs = {s: slice_draw(per, keep, span_cut) for s, per in draws.items()}
    cut_g = {s: v[0] for s, v in cut_pairs.items()}
    cut_n = {s: v[1] for s, v in cut_pairs.items()}

    out = {'prereg_commit': '09874a4', 'generator': 'scripts/analysis/period_truncation.py',
           'f_tag': F_TAG, 'draws_tag': DRAWS_TAG, 'rules': sorted(RULES),
           'threshold': PREREG_THRESHOLD,
           'dropped_by_threshold': sorted(set(all_anchors) - set(sel)),
           'dropped_by_contiguity': [a for a in all_anchors
                                     if a in set(sel) and a not in set(keep)],
           'full20': summarize(f_rows, full, re_g, re_n),
           'cut18': summarize(cut_rows, cut, cut_g, cut_n)}

    s1 = {s: slice_draw(per, keep, span_cut, fresh_start_tc=True)[1]
          for s, per in draws.items()}
    s1v = list(s1.values())
    out['cut18']['S1_fresh_start_tc'] = {
        'note': '첫 채택 구간 tc -> 0.0035 (매수전용). 전 추첨 동일 적용. 보고 전용.',
        'null_median': sorted(s1v)[len(s1v) // 2], 'null_p95': p95(s1v)}

    # 제외 구간 기여 분해
    drop_rows = [r for r in f_rows if r['rebalance_date'] not in set(keep)]
    out['decomposition'] = {
        'dropped_periods': [
            {'rebalance_date': r['rebalance_date'], 'next_date': r['next_date'],
             'gross': float(r['period_return']), 'net': float(r['net_return']),
             'kospi': float(r['kospi_return'])} for r in drop_rows],
        'dropped_gross_growth': _prod(drop_rows, 'period_return'),
        'dropped_net_growth': _prod(drop_rows, 'net_return'),
        'full_gross_growth': _prod(f_rows, 'period_return'),
        'cut_gross_growth': _prod(cut_rows, 'period_return'),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / 'truncation_results.json').write_text(
        json.dumps(out, indent=2, ensure_ascii=False, default=float), encoding='utf-8')

    for name in ('full20', 'cut18'):
        r = out[name]
        m = r['metrics']
        log.info('-- %s (n=%d, %.2f년, %s ~ %s) --', name, m['n_periods'], m['years'],
                 m['start'], m['end'])
        log.info('  F gross %8.4f%%  net %8.4f%%  Sharpe %.4f  MDD %7.2f%%  Robust %.2f',
                 m['cagr'] * 100, m['net_cagr'] * 100, m['sharpe'],
                 m['mdd'] * 100, m['robustness'])
        log.info('  KOSPI %.4f%%  alpha %+.4f%%p  KOSDAQ %.4f%%',
                 m['benchmark_cagr'] * 100, m['alpha'] * 100, m['kosdaq_cagr'] * 100)
        g = r['null_gross']
        log.info('  귀무 gross median %.4f%% p95 %.4f%% CI95(p95)=[%.4f%%, %.4f%%] '
                 '-> F 백분위 %.1f%%', g['median'] * 100, g['p95'] * 100,
                 g['ci95_of_p95'][0] * 100, g['ci95_of_p95'][1] * 100,
                 g['f_percentile'] * 100)
        n = r['null_net']
        log.info('  귀무 net   median %.4f%% p95 %.4f%% -> F 백분위 %.1f%% [%s]',
                 n['median'] * 100, n['p95'] * 100, n['f_percentile'] * 100,
                 r['G1_net_report_only']['label'])
        log.info('  >> G1 = %s\n', r['G1']['label'])

    s1r = out['cut18']['S1_fresh_start_tc']
    log.info('  S1(첫구간 tc 매수전용) 귀무 net median %.4f%% p95 %.4f%%',
             s1r['null_median'] * 100, s1r['null_p95'] * 100)
    log.info('산출물: %s', OUT_DIR / 'truncation_results.json')


def _prod(rows: list[dict], col: str) -> float:
    g = 1.0
    for r in rows:
        g *= 1.0 + float(r[col])
    return g


if __name__ == '__main__':
    main()
