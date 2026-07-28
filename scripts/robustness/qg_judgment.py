"""
SPEC_13 Q-H — 캘린더 후보 공식 판정 (QG1·QG2·QG3·QG5-PROD + evidence_label).

**SPEC_10의 `gate_analysis.py`와 별개 스크립트다.** 그쪽은 반기 인컴번트의 G1/G2/G5
판정(`gate_results.json`)을 만드는 다른 실험의 공식 산출물이고 반기 전용인 채로 유효하다.
여기에 SPEC_13 로직을 얹으면 두 실험의 판정이 한 파일에 섞이므로 분리한다.
공통 진단 함수(`percentile_below` 등)는 `robustness_lib`에서 재사용한다 — 복제 금지.

사전등록 기준 (결과 열람 후 수정 금지):
  §9-2  QG1 후보 net CAGR > 랜덤 p95 | QG2 > EW net CAGR | QG3 ≥ 인컴번트 + 0.1%p
        QG5-PROD 일별 net MDD > −45% (얕을 것). **전부 공통 기간 (S, E] 기준**
        (§9-2 `[확정 2026-07-28, 사용자]`).
  §9-2b 공통 기간 = warm-up 후 NAV(S)=1.0 정규화, S에서 강제 매수 금지.
        → `metrics.slice_common_period` + `compute_nav_cagr(initial_capital=NAV(S))`.
  §9-3a paired moving block bootstrap (월간, 블록 12개월, B=10,000, seed=13).
  §9-4a evidence_label: SUPERIOR = 마진 ≥ +0.3%p AND P(ΔCAGR>0) ≥ 0.90.

랜덤 대조군만 **구간 단위** 절단을 쓴다 — §9-1이 "랜덤은 일별 경로를 만들 필요 없이
terminal NAV만 승법으로"라고 사전등록했고, S가 안 A·안 C **둘 다의 실제 앵커**라
구간 경계와 정확히 맞아 근사가 아니다. (인컴번트는 S가 앵커가 아니므로 반드시 일별
NAV 절단을 쓴다.) 이 전제는 실행 시 단언으로 검증한다.

실행 (동결 스냅샷 워크트리에서):
    python -m scripts.robustness.qg_judgment --calendar A --start 2016-05-18 --end 2026-04-03
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import logging
from datetime import date
from pathlib import Path

import pandas as pd

from backtest.metrics import compute_daily_metrics, compute_nav_cagr, slice_common_period
from scripts.robustness.robustness_lib import (
    paired_block_bootstrap_cagr_diff,
    percentile_below,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

NAV_DIR = Path('experiments/daily_nav')
ROB_DIR = Path('experiments/robustness')

INCUMBENT_TAG = 'F_pbr_no_r3r4'          # 반기 (무접미사)
CANDIDATE_TAG = 'F_pbr_no_r3r4'          # 캘린더 접미사가 붙는다
EW_TAG        = 'U_pbr_path_ew'
RANDOM_TAG    = 'C_pbr_path_random'

# 사전등록 상수 — 수정 금지
DELTA_PP         = 0.001    # QG3 δ = +0.1%p (§9-2)
QG5_MDD_LIMIT    = -0.45    # §9-2 QG5-PROD
SUPERIOR_MARGIN  = 0.003    # §9-4a: +0.3%p
SUPERIOR_P_GT_0  = 0.90     # §9-4a


def _load_nav(tag: str) -> pd.Series:
    """{tag}_daily_nav.csv 의 nav_net 시리즈 (index=date)."""
    path = NAV_DIR / f'{tag}_daily_nav.csv'
    if not path.exists():
        raise FileNotFoundError(f'{path} 없음 — run_daily_nav 먼저 실행할 것')
    df = pd.read_csv(path, index_col='date', parse_dates=True)
    return df['nav_net']


def _common_period_metrics(tag: str, start: date, end: date) -> dict:
    """공통 기간 (S, E] 의 net CAGR·MDD·Sharpe (§9-2b)."""
    nav    = _load_nav(tag)
    sliced = slice_common_period(nav, start, end)
    daily  = compute_daily_metrics(sliced)
    return {
        'tag':        tag,
        'net_cagr':   compute_nav_cagr(sliced, initial_capital=float(sliced.iloc[0])),
        'daily_mdd':  daily['daily_mdd'],
        'daily_sharpe': daily['daily_sharpe'],
        'n_obs':      len(sliced),
        'first_obs':  sliced.index[0].date().isoformat(),
        'last_obs':   sliced.index[-1].date().isoformat(),
        'monthly':    _monthly_returns(sliced),
    }


def _monthly_returns(nav: pd.Series) -> list[float]:
    """월말 기준 월간 수익률 — compute_daily_metrics 의 월 경계 규약과 동일
    (양끝 부분월 포함, 첫 월은 시리즈 시작값 대비)."""
    month_end = nav.groupby(nav.index.to_period('M')).last()
    prev = [float(nav.iloc[0])] + [float(v) for v in month_end.values[:-1]]
    return [float(cur) / p - 1.0 for cur, p in zip(month_end.values, prev)]


def _random_net_cagrs(calendar: str, start: date, end: date) -> tuple[list[float], dict]:
    """랜덤 1,000회의 공통 기간 net CAGR 분포 (구간 단위 승법 누적, §9-1).

    seed별로 (S, E] 안의 구간만 골라 net_growth = Π(1+net_period) 를 누적하고
    실제 캘린더 경과일로 연율화한다. 구간 경계가 S·E와 정확히 맞는지 단언한다.
    """
    path = ROB_DIR / f'{RANDOM_TAG}_{calendar}_periods.csv.gz'
    if not path.exists():
        raise FileNotFoundError(f'{path} 없음 — run_random_pool 먼저 실행할 것')

    by_seed: dict[int, list[tuple[date, float]]] = {}
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            d = date.fromisoformat(row['rebalance_date'])
            if start <= d < end:                      # 구간 시작이 [S, E) 안
                by_seed.setdefault(int(row['seed']), []).append((d, float(row['net'])))

    if not by_seed:
        raise ValueError(f'{calendar}: 공통 기간 안에 랜덤 구간이 없다')

    starts = sorted({d for rows in by_seed.values() for d, _ in rows})
    # §9-1 전제 검증: S가 이 캘린더의 실제 앵커여야 구간 단위 절단이 근사가 아니다
    if starts[0] != start:
        raise SystemExit(
            f'[전제 위반] 공통 시작일 {start}이 캘린더 {calendar}의 앵커가 아니다 '
            f'(첫 구간 시작 {starts[0]}) — 랜덤 구간 단위 절단은 이 경우 근사가 되므로 중단'
        )
    years = (end - start).days / 365.25
    cagrs = []
    for seed in sorted(by_seed):
        growth = 1.0
        for _d, net in sorted(by_seed[seed]):
            growth *= (1.0 + net)
        cagrs.append(growth ** (1.0 / years) - 1.0)

    meta = {
        'n_draws':        len(cagrs),
        'n_periods_used': len(starts),
        'first_period':   starts[0].isoformat(),
        'last_period':    starts[-1].isoformat(),
        'years':          years,
    }
    return sorted(cagrs), meta


def _percentile(values: list[float], q: float) -> float:
    """정렬된 리스트의 q 분위 (run_random_pool 의 p95 산출과 동일한 index 규약)."""
    return values[int(len(values) * q)]


def main() -> None:
    ap = argparse.ArgumentParser(description='SPEC_13 Q-H 캘린더 후보 판정')
    ap.add_argument('--calendar', choices=['A', 'C'], required=True)
    ap.add_argument('--start', required=True, help='공통 시작일 S (§9-2b 사전등록)')
    ap.add_argument('--end',   required=True, help='공통 종료일 E (§9-2b 사전등록)')
    args = ap.parse_args()

    S, E = date.fromisoformat(args.start), date.fromisoformat(args.end)
    cal  = args.calendar
    log.info('=== SPEC_13 Q-H 판정: 캘린더 %s, 공통 기간 (%s, %s] ===', cal, S, E)

    cand = _common_period_metrics(f'{CANDIDATE_TAG}_{cal}', S, E)
    inc  = _common_period_metrics(INCUMBENT_TAG, S, E)
    ew   = _common_period_metrics(f'{EW_TAG}_{cal}', S, E)

    # 절단 정합성 — 한쪽만 다른 구간을 보고 있으면 비교 자체가 무효
    for other in (inc, ew):
        if (other['first_obs'], other['last_obs']) != (cand['first_obs'], cand['last_obs']):
            raise SystemExit(
                f'[절단 불일치] {cand["tag"]} {cand["first_obs"]}~{cand["last_obs"]} vs '
                f'{other["tag"]} {other["first_obs"]}~{other["last_obs"]} — 비교 무효'
            )
    log.info('절단 정합성 OK — 전 태그 %s~%s, %d관측일',
             cand['first_obs'], cand['last_obs'], cand['n_obs'])

    rnd_cagrs, rnd_meta = _random_net_cagrs(cal, S, E)
    rnd_p95 = _percentile(rnd_cagrs, 0.95)

    # ── 하드 게이트 (§9-2) ────────────────────────────────────────────────────
    qg1 = cand['net_cagr'] > rnd_p95
    qg2 = cand['net_cagr'] > ew['net_cagr']
    margin = cand['net_cagr'] - inc['net_cagr']
    qg3 = margin >= DELTA_PP
    qg5 = cand['daily_mdd'] > QG5_MDD_LIMIT

    boot = paired_block_bootstrap_cagr_diff(cand['monthly'], inc['monthly'])

    # ── evidence_label (§9-4a) ───────────────────────────────────────────────
    if margin < 0:
        evidence = 'INFERIOR'
    elif margin >= SUPERIOR_MARGIN and boot['p_gt_0'] >= SUPERIOR_P_GT_0:
        evidence = 'SUPERIOR'
    else:
        evidence = 'INCONCLUSIVE'

    result = {
        'spec': 'SPEC_13 Q-H', 'calendar': cal,
        'common_period': {'S': S.isoformat(), 'E': E.isoformat(),
                          'n_obs': cand['n_obs'],
                          'first_obs': cand['first_obs'], 'last_obs': cand['last_obs']},
        'pre_registered': {
            'delta_pp': DELTA_PP, 'qg5_mdd_limit': QG5_MDD_LIMIT,
            'superior_margin': SUPERIOR_MARGIN, 'superior_p_gt_0': SUPERIOR_P_GT_0,
            'note': '§9-2·§9-2b·§9-3a·§9-4a — 결과 열람 후 수정 금지',
        },
        'metrics_common_period': {
            'candidate':  {k: v for k, v in cand.items() if k != 'monthly'},
            'incumbent':  {k: v for k, v in inc.items()  if k != 'monthly'},
            'ew':         {k: v for k, v in ew.items()   if k != 'monthly'},
            'random':     {'p95_net_cagr': rnd_p95,
                           'median_net_cagr': _percentile(rnd_cagrs, 0.5),
                           'p5_net_cagr': _percentile(rnd_cagrs, 0.05), **rnd_meta},
        },
        'hard_gates': {
            'QG1': {'pass': bool(qg1), 'candidate_net_cagr': cand['net_cagr'],
                    'random_p95': rnd_p95,
                    'percentile_in_null': percentile_below(cand['net_cagr'], rnd_cagrs)},
            'QG2': {'pass': bool(qg2), 'candidate_net_cagr': cand['net_cagr'],
                    'ew_net_cagr': ew['net_cagr'],
                    'margin_pp': (cand['net_cagr'] - ew['net_cagr']) * 100},
            'QG3': {'pass': bool(qg3), 'candidate_net_cagr': cand['net_cagr'],
                    'incumbent_net_cagr': inc['net_cagr'],
                    'threshold': inc['net_cagr'] + DELTA_PP,
                    'margin_pp': margin * 100},
            'QG5_PROD': {'pass': bool(qg5), 'candidate_daily_mdd_net': cand['daily_mdd'],
                         'limit': QG5_MDD_LIMIT},
        },
        'bootstrap': boot,
        'QG3_SCREEN_PASS': bool(qg3),
        'evidence_label': evidence,
        'all_hard_gates_pass': bool(qg1 and qg2 and qg3 and qg5),
    }

    out = ROB_DIR / f'qg_results_{cal}.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')

    log.info('후보 net CAGR   %.4f%%  (공통 기간)', cand['net_cagr'] * 100)
    log.info('인컴번트         %.4f%%   → QG3 문턱 %.4f%%',
             inc['net_cagr'] * 100, (inc['net_cagr'] + DELTA_PP) * 100)
    log.info('EW               %.4f%%   랜덤 p95 %.4f%%', ew['net_cagr'] * 100, rnd_p95 * 100)
    log.info('QG1 %s | QG2 %s | QG3 %s (마진 %+.4f%%p) | QG5-PROD %s (MDD %.2f%%)',
             'PASS' if qg1 else 'FAIL', 'PASS' if qg2 else 'FAIL',
             'PASS' if qg3 else 'FAIL', margin * 100,
             'PASS' if qg5 else 'FAIL', cand['daily_mdd'] * 100)
    log.info('bootstrap: P(Δ>0)=%.3f  P(Δ>0.1%%p)=%.3f  95%%CI=[%+.4f%%p, %+.4f%%p]',
             boot['p_gt_0'], boot['p_gt_delta'],
             boot['ci_low'] * 100, boot['ci_high'] * 100)
    log.info('→ QG3_SCREEN_PASS=%s  evidence_label=%s  전체 하드게이트=%s',
             result['QG3_SCREEN_PASS'], evidence,
             'PASS' if result['all_hard_gates_pass'] else 'FAIL')
    log.info('저장: %s', out)


if __name__ == '__main__':
    main()
