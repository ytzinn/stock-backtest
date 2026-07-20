"""
SPEC_11 §4 — D_pbr_no_r3r4 대조 진단 4종 + SPEC_10 §4-6 잔여(Jaccard·스피어만).

진단 (F = F_pbr_no_r3r4, D = F에서 모멘텀만 제거):
  1. 구간별 paired return 차이 (gross·net, 부호 승률)
  2. turnover·구간별 종목 수 (일별 MDD는 daily_nav summary에서 병기)
  3. 모멘텀에 의해 탈락한 종목(D 편입 ∩ F tape momentum_rejected)의 해당 구간
     수익률 — 거부권이 실제 가치를 더했는지, 후보 폭만 줄였는지
  4. 모멘텀 통과 vs 탈락 종목의 평균/중위 PBR — 가치-모멘텀 상충 여부
     (통과 풀 = robustness/pools.json, 탈락 = F tape momentum_rejected — 같은
      필터 스택이므로 통과∪탈락 = stability 생존 집합)
  + §4-6: Jaccard(F vs F_no_r3r4 — RIM 쌍대 기록용, F vs D — 모멘텀 유무 중복도),
     스피어만(1/PBR 랭킹 vs U_pbr_path_ew 내 실현수익 순위, 구간별)

실행 (서버, D·F tape 재생성 후): venv/bin/python -m scripts.analysis.momentum_decomposition
출력: experiments/analysis/momentum_decomposition.json + 콘솔 요약
"""
from __future__ import annotations

import csv
import json
import logging
from datetime import date, datetime
from pathlib import Path

from backtest.ablation import _PBRRankPipeline
from backtest.configs.rebalance_dates import REBALANCE_DATES
from backtest.data_access import load_pit_series_ttm
from backtest.engine import _report_type
from ingest.connection import get_connection
from scripts.analysis.analysis_lib import jaccard, spearman

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

ABL = Path('experiments/ablation')
OUT = Path('experiments/analysis')
F_TAG, D_TAG, U_TAG, RIM_TAG = ('F_pbr_no_r3r4', 'D_pbr_no_r3r4',
                                'U_pbr_path_ew', 'F_no_r3r4')

REBAL_SET = {d.isoformat() for d in REBALANCE_DATES}


def load_tape(tag: str) -> dict[str, dict]:
    tape = json.loads((ABL / f'{tag}_holdings.json').read_text(encoding='utf-8'))
    return {p['rebalance_date']: p for p in tape
            if p['next_date'] in REBAL_SET and p['n_portfolio'] > 0}


def load_periods(tag: str) -> dict[str, dict]:
    out = {}
    with (ABL / f'{tag}_periods.csv').open(encoding='utf-8') as f:
        for row in csv.DictReader(f):
            if row['next_date'] in REBAL_SET and int(row['n_stocks']) > 0:
                out[row['rebalance_date']] = row
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    f_tape, d_tape, u_tape, rim_tape = (load_tape(F_TAG), load_tape(D_TAG),
                                        load_tape(U_TAG), load_tape(RIM_TAG))
    f_csv, d_csv = load_periods(F_TAG), load_periods(D_TAG)
    pools = {k: set(v) for k, v in json.loads(
        Path('experiments/robustness/pools.json').read_text(encoding='utf-8')).items()}
    common = sorted(set(f_tape) & set(d_tape) & set(f_csv) & set(d_csv))

    # 1. paired 차이
    paired = []
    for dstr in common:
        fg, dg = float(f_csv[dstr]['period_return']), float(d_csv[dstr]['period_return'])
        fn, dn = float(f_csv[dstr]['net_return']), float(d_csv[dstr]['net_return'])
        paired.append({'rebalance_date': dstr, 'f_gross': fg, 'd_gross': dg,
                       'diff_gross': fg - dg, 'f_net': fn, 'd_net': dn, 'diff_net': fn - dn,
                       'f_turnover': float(f_csv[dstr]['turnover']),
                       'd_turnover': float(d_csv[dstr]['turnover']),
                       'f_n': int(f_csv[dstr]['n_stocks']), 'd_n': int(d_csv[dstr]['n_stocks'])})
    n = len(paired)
    f_wins_net = sum(1 for r in paired if r['diff_net'] > 0)

    # 3. 모멘텀 탈락 종목의 해당 구간 수익률 + 6. Jaccard
    victims_rows, jac_rim, jac_d = [], [], []
    for dstr in common:
        f_hold = {h['ticker'] for h in f_tape[dstr]['holdings']}
        d_hold = {h['ticker']: h for h in d_tape[dstr]['holdings']}
        mom_rej = set(f_tape[dstr].get('momentum_rejected', []))
        victims = sorted(set(d_hold) & mom_rej)
        vic_rets = [d_hold[t]['ret'] for t in victims if d_hold[t]['ret'] is not None]
        victims_rows.append({
            'rebalance_date': dstr, 'n_victims': len(victims),
            'victim_mean_ret': sum(vic_rets) / len(vic_rets) if vic_rets else None,
            'f_period_gross': float(f_csv[dstr]['period_return']),
            'victims': victims,
        })
        jac_d.append(jaccard(f_hold, set(d_hold)))
        if dstr in rim_tape:
            jac_rim.append(jaccard(f_hold, {h['ticker'] for h in rim_tape[dstr]['holdings']}))

    vic_valid = [r for r in victims_rows if r['victim_mean_ret'] is not None]
    vic_underperf = sum(1 for r in vic_valid if r['victim_mean_ret'] < r['f_period_gross'])

    # 4. 모멘텀 통과 vs 탈락 PBR + 5. 스피어만 (DB 스코어링 — 구간별 1회)
    ranker = _PBRRankPipeline(filters=[])
    pbr_rows, spearman_rows = [], []
    conn = get_connection()
    try:
        for dstr in common:
            d0 = date.fromisoformat(dstr)
            pit = load_pit_series_ttm(conn, d0, report_type=_report_type(d0))
            passed  = pools.get(dstr, set())
            rejected = set(f_tape[dstr].get('momentum_rejected', []))
            scored = ranker.score_and_rank(sorted(passed | rejected), d0, pit, conn)
            inv_pbr = {it['ticker']: it['upside_pct'] for it in scored}   # 1/PBR

            def _pbrs(group: set[str]) -> list[float]:
                return sorted(1.0 / inv_pbr[t] for t in group if inv_pbr.get(t))

            p_pbrs, r_pbrs = _pbrs(passed), _pbrs(rejected)
            pbr_rows.append({
                'rebalance_date': dstr,
                'passed_n': len(p_pbrs), 'rejected_n': len(r_pbrs),
                'passed_mean_pbr': sum(p_pbrs) / len(p_pbrs) if p_pbrs else None,
                'rejected_mean_pbr': sum(r_pbrs) / len(r_pbrs) if r_pbrs else None,
                'passed_median_pbr': p_pbrs[len(p_pbrs) // 2] if p_pbrs else None,
                'rejected_median_pbr': r_pbrs[len(r_pbrs) // 2] if r_pbrs else None,
            })

            if dstr in u_tape:
                xs, ys = [], []
                for h in u_tape[dstr]['holdings']:
                    if h['ret'] is not None and inv_pbr.get(h['ticker']):
                        xs.append(inv_pbr[h['ticker']])
                        ys.append(h['ret'])
                spearman_rows.append({'rebalance_date': dstr, 'n': len(xs),
                                      'spearman_invpbr_ret': spearman(xs, ys)})
            log.info('%s PBR·스피어만 완료', dstr)
    finally:
        conn.close()

    sp_vals = [r['spearman_invpbr_ret'] for r in spearman_rows]
    cheaper_rejected = sum(
        1 for r in pbr_rows
        if r['rejected_median_pbr'] is not None and r['passed_median_pbr'] is not None
        and r['rejected_median_pbr'] < r['passed_median_pbr'])

    result = {
        'generated_at': datetime.now().isoformat(),
        'n_periods': n,
        'paired': {
            'rows': paired,
            'mean_diff_gross': sum(r['diff_gross'] for r in paired) / n,
            'mean_diff_net': sum(r['diff_net'] for r in paired) / n,
            'f_wins_net': f_wins_net, 'f_win_rate_net': f_wins_net / n,
            'f_avg_turnover': sum(r['f_turnover'] for r in paired) / n,
            'd_avg_turnover': sum(r['d_turnover'] for r in paired) / n,
        },
        'momentum_victims': {
            'rows': victims_rows,
            'periods_with_victims': len(vic_valid),
            'victims_underperformed_f': vic_underperf,
            'mean_victim_ret': (sum(r['victim_mean_ret'] for r in vic_valid) / len(vic_valid))
                               if vic_valid else None,
            'mean_f_gross_same_periods': (sum(r['f_period_gross'] for r in vic_valid)
                                          / len(vic_valid)) if vic_valid else None,
        },
        'value_momentum_conflict': {
            'rows': pbr_rows,
            'periods_rejected_cheaper_median': cheaper_rejected,
        },
        'jaccard': {
            'f_vs_rim_counterpart_mean': sum(jac_rim) / len(jac_rim) if jac_rim else None,
            'f_vs_rim_min': min(jac_rim) if jac_rim else None,
            'f_vs_d_mean': sum(jac_d) / len(jac_d),
            'f_vs_d_min': min(jac_d),
        },
        'spearman_u_ew': {
            'rows': spearman_rows,
            'mean': sum(sp_vals) / len(sp_vals) if sp_vals else None,
            'positive_periods': sum(1 for v in sp_vals if v > 0),
        },
    }
    (OUT / 'momentum_decomposition.json').write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')

    log.info('=== 요약 ===')
    log.info('1. F−D net 평균 %+.4f, F 승률 %d/%d', result['paired']['mean_diff_net'], f_wins_net, n)
    log.info('2. avg turnover F=%.0f%% D=%.0f%%',
             result['paired']['f_avg_turnover'] * 100, result['paired']['d_avg_turnover'] * 100)
    mv = result['momentum_victims']
    log.info('3. 모멘텀 탈락 종목: %d개 구간, F 대비 언더퍼폼 %d구간, 평균수익 %s vs F %s',
             mv['periods_with_victims'], mv['victims_underperformed_f'],
             f"{mv['mean_victim_ret']:.2%}" if mv['mean_victim_ret'] is not None else 'n/a',
             f"{mv['mean_f_gross_same_periods']:.2%}"
             if mv['mean_f_gross_same_periods'] is not None else 'n/a')
    log.info('4. 탈락군 중위 PBR이 더 싼 구간: %d/%d (가치-모멘텀 상충 지표)',
             cheaper_rejected, len(pbr_rows))
    log.info('6. Jaccard F vs RIM쌍대 평균 %.3f | F vs D 평균 %.3f',
             result['jaccard']['f_vs_rim_counterpart_mean'] or -1,
             result['jaccard']['f_vs_d_mean'])
    log.info('5. 스피어만(1/PBR vs U_ew 실현수익) 평균 %.3f (양수 %d/%d)',
             result['spearman_u_ew']['mean'] or 0,
             result['spearman_u_ew']['positive_periods'], len(sp_vals))
    log.info('저장: %s', OUT / 'momentum_decomposition.json')


if __name__ == '__main__':
    main()
