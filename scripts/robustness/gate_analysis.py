"""
SPEC_10 §5 — 사전 등록 판정 계산 (G1/G2/G5 하드 게이트 + G3′/G4′ 진단 + G6′/G7′ 참고).

기준은 2026-07-19 사전 등록 — 실행 중 기준·경고선 수정 금지.
판정 문안 인용은 R-4 보고서(PBR_GATE_OFFICIAL.md)에서, 여기서는 수치·PASS/FAIL만 산출.

입력 (전부 07-18 PIT 동결 스냅샷 산출물):
  experiments/ablation/{F_pbr_no_r3r4, U_pbr_path_ew}.json / _periods.csv / _holdings.json
  experiments/robustness/C_pbr_path_random_{draws.csv, periods.csv.gz, contrib.csv.gz}
  experiments/daily_nav/summary.json  (G5 — 일별 net MDD)

출력: experiments/robustness/gate_results.json + 콘솔 판정표

정의 규약 (문서화):
  - 마진 = 총복리 배수 차 (동일 구간 집합이라 CAGR 대소와 동치 — robustness_lib.margin)
  - LOO·부호검정·G7′은 **net 구간 수익률** 기준 (G2가 net 판정이므로 일관 기준).
    구간 제외 시 잔여 구간 tc는 재계산하지 않는다 (기록값 사용 — 진단 목적).
  - G3′/G4′ 백분위: "F보다 덜 의존적인(反의존) 추첨 비율"로 환산해 보고.
    경고선(사전 등록): F가 귀무분포의 **최악 10%** 안에 들면 경고 발화.
  - F·U의 종목별 기여는 holdings tape의 ret(상폐 haircut 반영, 6자리 반올림) × 1/n_valid.
"""
from __future__ import annotations

import csv
import gzip
import json
import logging
from datetime import datetime
from pathlib import Path

from backtest.configs.rebalance_dates import REBALANCE_DATES
from scripts.robustness.robustness_lib import (
    loo_reversal_count,
    margin,
    percentile_below,
    sign_test,
    topk_removal_margin,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

ABL_DIR = Path('experiments/ablation')
ROB_DIR = Path('experiments/robustness')
NAV_DIR = Path('experiments/daily_nav')

G5_MDD_LIMIT   = -0.45   # 사전 등록 (2026-07-19) — 실전 감내 한계선
WARN_PERCENTILE = 0.10   # G3′/G4′ 경고선: 귀무분포 최악 10%

F_TAG, U_TAG = 'F_pbr_no_r3r4', 'U_pbr_path_ew'


def _closed_dates() -> list[str]:
    rebal_set = {d.isoformat() for d in REBALANCE_DATES}
    return rebal_set


def load_closed_periods(tag: str) -> dict[str, dict]:
    """periods CSV의 완결 구간 행 (next_date ∈ REBALANCE_DATES, n_stocks>0)."""
    rebal_set = _closed_dates()
    out = {}
    with (ABL_DIR / f'{tag}_periods.csv').open(encoding='utf-8') as f:
        for row in csv.DictReader(f):
            if row['next_date'] in rebal_set and int(row['n_stocks']) > 0:
                out[row['rebalance_date']] = row
    return out


def load_period_stock(tag: str) -> dict[str, list[tuple[str, float, float]]]:
    """holdings tape → {rebal_date: [(ticker, 1/n_valid, ret)]} (완결 구간만)."""
    rebal_set = _closed_dates()
    tape = json.loads((ABL_DIR / f'{tag}_holdings.json').read_text(encoding='utf-8'))
    out = {}
    for p in tape:
        if p['next_date'] not in rebal_set or p['n_portfolio'] == 0:
            continue
        rows = [(h['ticker'], h['ret']) for h in p['holdings'] if h.get('ret') is not None]
        if not rows:
            continue
        w = 1.0 / len(rows)
        out[p['rebalance_date']] = [(t, w, r) for t, r in rows]
    return out


def load_draws():
    draws = []
    with (ROB_DIR / 'C_pbr_path_random_draws.csv').open(encoding='utf-8') as f:
        for row in csv.DictReader(f):
            draws.append((int(row['seed']), float(row['cagr']), float(row['net_cagr'])))
    periods: dict[int, dict[str, dict]] = {}
    with gzip.open(ROB_DIR / 'C_pbr_path_random_periods.csv.gz', 'rt', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            periods.setdefault(int(row['seed']), {})[row['rebalance_date']] = row
    contrib: dict[int, dict[str, list]] = {}
    with gzip.open(ROB_DIR / 'C_pbr_path_random_contrib.csv.gz', 'rt', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            contrib.setdefault(int(row['seed']), {}).setdefault(row['rebalance_date'], []).append(
                (row['ticker'], float(row['weight_eff']), float(row['ret'])))
    return draws, periods, contrib


def main() -> None:
    f_json = json.loads((ABL_DIR / f'{F_TAG}.json').read_text(encoding='utf-8'))
    u_json = json.loads((ABL_DIR / f'{U_TAG}.json').read_text(encoding='utf-8'))
    f_periods = load_closed_periods(F_TAG)
    u_periods = load_closed_periods(U_TAG)
    common = sorted(set(f_periods) & set(u_periods))
    if len(common) != len(f_periods) or len(common) != len(u_periods):
        raise SystemExit(f'구간 집합 불일치: F={len(f_periods)} U={len(u_periods)} 공통={len(common)}')

    f_net = [float(f_periods[d]['net_return']) for d in common]
    u_net = [float(u_periods[d]['net_return']) for d in common]

    draws, draw_periods, draw_contrib = load_draws()
    draw_cagrs = [c for _, c, _ in draws]
    p95 = sorted(draw_cagrs)[int(len(draw_cagrs) * 0.95)]

    # ── 하드 게이트 ─────────────────────────────────────────────────────────
    g1 = f_json['cagr'] >= p95
    f_pctl_in_null = percentile_below(f_json['cagr'], draw_cagrs)

    g2 = f_json['net_cagr'] > u_json['net_cagr']

    nav_summary = json.loads((NAV_DIR / 'summary.json').read_text(encoding='utf-8'))
    f_daily_mdd = nav_summary['tags'][F_TAG]['net']['daily_mdd']
    g5 = f_daily_mdd > G5_MDD_LIMIT   # 얕아야 통과 (−0.45보다 큼)

    # ── G3′ 구간 의존도 (net, vs U) ────────────────────────────────────────
    f_loo_count, f_loo_idx = loo_reversal_count(f_net, u_net)
    null_loo = []
    for seed, per, _ in draws:
        rows = draw_periods[seed]
        if set(rows) != set(common):
            raise SystemExit(f'seed={seed}: 추첨 구간 집합이 F/U와 불일치')
        d_net = [float(rows[d]['net']) for d in common]
        null_loo.append(loo_reversal_count(d_net, u_net)[0])
    # 의존도(반전 수)는 높을수록 나쁨. share_more_dependent = F보다 반전 수가 큰
    # 추첨 비율(동률 절반 가중) — 이 값이 10% 미만이면 F가 귀무분포 최악 10% 안
    # (= 랜덤보다 유별나게 구간 의존적) → 경고 발화 (사전 등록 경고선).
    share_more_dependent = 1.0 - percentile_below(float(f_loo_count), [float(v) for v in null_loo])
    g3_warn = share_more_dependent < WARN_PERCENTILE

    # ── G4′ 종목 의존도 (top-k 제거 후 잔여 마진, 양쪽 동일 처리) ───────────
    f_stock = load_period_stock(F_TAG)
    u_stock = load_period_stock(U_TAG)
    if set(f_stock) != set(common) or set(u_stock) != set(common):
        raise SystemExit('holdings tape 구간 집합이 periods CSV와 불일치')
    g4 = {}
    for k in (1, 2, 3):
        f_margin_k = topk_removal_margin(f_stock, u_stock, k)
        null_margins = [topk_removal_margin(draw_contrib[seed], u_stock, k)
                        for seed, _, _ in draws]
        share_below = percentile_below(f_margin_k, null_margins)   # 낮을수록 나쁨
        g4[k] = {
            'f_margin_after_removal': f_margin_k,
            'share_of_draws_below_f': share_below,
            'warn': share_below < WARN_PERCENTILE,
        }
    g4_warn = any(v['warn'] for v in g4.values())

    # ── G6′ / G7′ 참고 ─────────────────────────────────────────────────────
    diffs = [a - b for a, b in zip(f_net, u_net)]
    pos, n_eff, p_sign = sign_test(diffs)
    half = len(common) // 2
    g7_first = margin(f_net[:half], u_net[:half])
    g7_last  = margin(f_net[half:], u_net[half:])
    g7_consistent = (g7_first > 0) == (g7_last > 0)

    results = {
        'generated_at': datetime.now().isoformat(),
        'pre_registered': '2026-07-19 (SPEC_10 §5) — 실행 후 수정 금지',
        'hard_gates': {
            'G1': {'pass': bool(g1), 'f_cagr': f_json['cagr'], 'random_p95': p95,
                   'f_percentile_in_null': f_pctl_in_null,
                   'n_draws': len(draws)},
            'G2': {'pass': bool(g2), 'f_net_cagr': f_json['net_cagr'],
                   'u_ew_net_cagr': u_json['net_cagr'],
                   'margin_pp': (f_json['net_cagr'] - u_json['net_cagr']) * 100},
            'G5': {'pass': bool(g5), 'f_daily_mdd_net': f_daily_mdd, 'limit': G5_MDD_LIMIT},
        },
        'diagnostics': {
            'G3_loo': {'f_reversal_count': f_loo_count, 'f_reversal_periods':
                       [common[i] for i in f_loo_idx],
                       'null_mean': sum(null_loo) / len(null_loo),
                       'share_of_draws_more_dependent': share_more_dependent,
                       'warn': bool(g3_warn)},
            'G4_topk': g4, 'g4_warn': bool(g4_warn),
            'G6_sign_test': {'positive': pos, 'n_effective': n_eff, 'p_value': p_sign,
                             'note': 'n=20 검정력 낮음 — 참고용 (사전 등록 명기)'},
            'G7_halves': {'first_half_margin': g7_first, 'last_half_margin': g7_last,
                          'direction_consistent': bool(g7_consistent)},
        },
        'verdict_inputs': {
            'all_hard_pass': bool(g1 and g2 and g5),
            'any_warning': bool(g3_warn or g4_warn),
        },
    }

    ROB_DIR.mkdir(parents=True, exist_ok=True)
    (ROB_DIR / 'gate_results.json').write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')

    log.info('G1 (CAGR>=random p95): %s — F=%.4f p95=%.4f (귀무분포 백분위 %.1f%%)',
             'PASS' if g1 else 'FAIL', f_json['cagr'], p95, f_pctl_in_null * 100)
    log.info('G2 (net>U_ew net):     %s — F=%.4f U=%.4f (마진 %+.2f%%p)',
             'PASS' if g2 else 'FAIL', f_json['net_cagr'], u_json['net_cagr'],
             (f_json['net_cagr'] - u_json['net_cagr']) * 100)
    log.info('G5 (일별MDD>−45%%):    %s — F=%.2f%%',
             'PASS' if g5 else 'FAIL', f_daily_mdd * 100)
    log.info('G3′ LOO 반전 %d개 (null 평균 %.2f, 더 의존적인 추첨 %.0f%%) %s',
             f_loo_count, results['diagnostics']['G3_loo']['null_mean'],
             share_more_dependent * 100, '⚠경고' if g3_warn else 'OK')
    for k, v in g4.items():
        log.info('G4′ top-%d 제거 마진 %+.4f (하위 추첨 %.0f%%) %s',
                 k, v['f_margin_after_removal'], v['share_of_draws_below_f'] * 100,
                 '⚠경고' if v['warn'] else 'OK')
    log.info('G6′ 부호검정 %d/%d p=%.3f | G7′ 전/후반 마진 %+.3f / %+.3f (%s)',
             pos, n_eff, p_sign, g7_first, g7_last,
             '일관' if g7_consistent else '불일치')
    log.info('판정 입력: 하드 전부 %s, 경고 %s → %s',
             'PASS' if results['verdict_inputs']['all_hard_pass'] else 'FAIL',
             '발화' if results['verdict_inputs']['any_warning'] else '없음',
             ROB_DIR / 'gate_results.json')


if __name__ == '__main__':
    main()
