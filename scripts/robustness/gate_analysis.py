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

import argparse
import csv
import gzip
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from backtest.configs.schedule import REBALANCE_POINTS
from scripts.run_ablation import DEFAULT_N_STOCKS
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

# `[정정 2026-08-12]` F_TAG 는 상수가 아니라 인자다.
# 종전에는 'F_pbr_no_r3r4' 로 하드코딩돼 있었다. 2026-08-10 채택안이 F_pbr_ma200 으로,
# 08-11 종목 수가 20→13 으로 바뀐 뒤에도 이 상수가 그대로라, 산출물
# `gate_results.json` 은 **판정 대상이 아닌 전략의 성적표**였다. 그 파일을 읽은 곳마다
# 오귀속이 전파됐다 (freeze_rebalance 의 strategy_version 문자열 포함).
DEFAULT_U_TAG = 'U_pbr_path_ew'
DEFAULT_DRAWS_TAG = 'C_pbr_path_random'


def _n_from_tag(tag: str, default: int) -> int:
    """태그 이름의 `_n{K}` 접미사에서 종목 수를 읽는다.

    종목 수는 오랫동안 **산출물에 기록되지 않고 이름에만** 있었다. 신규 실행은
    `{tag}.json` 에 `n_stocks` 를 남기므로 그쪽이 우선이고, 이 함수는 구 산출물용
    폴백이다.
    """
    m = re.search(r'_n(\d+)$', tag)
    return int(m.group(1)) if m else default


def _closed_dates() -> list[str]:
    rebal_set = {rp.date.isoformat() for rp in REBALANCE_POINTS}
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


def load_draws(draws_tag: str = DEFAULT_DRAWS_TAG):
    draws = []
    with (ROB_DIR / f'{draws_tag}_draws.csv').open(encoding='utf-8') as f:
        for row in csv.DictReader(f):
            draws.append((int(row['seed']), float(row['cagr']), float(row['net_cagr'])))
    periods: dict[int, dict[str, dict]] = {}
    with gzip.open(ROB_DIR / f'{draws_tag}_periods.csv.gz', 'rt', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            periods.setdefault(int(row['seed']), {})[row['rebalance_date']] = row
    contrib: dict[int, dict[str, list]] = {}
    with gzip.open(ROB_DIR / f'{draws_tag}_contrib.csv.gz', 'rt', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            contrib.setdefault(int(row['seed']), {}).setdefault(row['rebalance_date'], []).append(
                (row['ticker'], float(row['weight_eff']), float(row['ret'])))
    return draws, periods, contrib


def main() -> None:
    p = argparse.ArgumentParser(description='SPEC_10 §5 하드 게이트 + 진단 산출')
    p.add_argument('--f-tag', required=True,
                   help='판정 대상 태그. 운영 설정과 같아야 한다 '
                        '(예: F_pbr_ma200_n13). 종전 하드코딩 F_pbr_no_r3r4 는 '
                        '2026-08-10 이후 채택안이 아니다.')
    p.add_argument('--u-tag', default=DEFAULT_U_TAG,
                   help='G2 벤치마크. 필터 통과 전 종목 동일가중이라 n 개념이 없다.')
    p.add_argument('--draws-tag', default=DEFAULT_DRAWS_TAG,
                   help='G1 귀무분포 추첨 태그. **판정 대상과 같은 n 으로 추첨된 것**이어야 한다.')
    args = p.parse_args()
    F_TAG, U_TAG = args.f_tag, args.u_tag

    f_json = json.loads((ABL_DIR / f'{F_TAG}.json').read_text(encoding='utf-8'))
    u_json = json.loads((ABL_DIR / f'{U_TAG}.json').read_text(encoding='utf-8'))
    f_periods = load_closed_periods(F_TAG)
    u_periods = load_closed_periods(U_TAG)
    common = sorted(set(f_periods) & set(u_periods))
    if len(common) != len(f_periods) or len(common) != len(u_periods):
        raise SystemExit(f'구간 집합 불일치: F={len(f_periods)} U={len(u_periods)} 공통={len(common)}')

    f_net = [float(f_periods[d]['net_return']) for d in common]
    u_net = [float(u_periods[d]['net_return']) for d in common]

    draws, draw_periods, draw_contrib = load_draws(args.draws_tag)
    draw_cagrs = [c for _, c, _ in draws]
    p95 = sorted(draw_cagrs)[int(len(draw_cagrs) * 0.95)]

    # ── G1 전제: 판정 대상과 귀무분포의 종목 수가 같아야 한다 ──────────────
    # 종목 수가 다르면 분산이 달라 p95(합격선) 자체가 달라진다. 2026-08-12 이전에는
    # n=13 전략을 n=20 귀무분포로 판정하고 있었고, 그 방향은 **합격선을 낮추는** 쪽
    # (종목이 많을수록 분산이 작아 p95 가 낮다)이라 게이트가 관대해져 있었다.
    f_n = f_json.get('n_stocks') or _n_from_tag(F_TAG, DEFAULT_N_STOCKS)
    # `random_summary` 파일명은 종전에 **`_n{K}` 접미사만** 담았다 (풀 태그가 하나뿐
    # 이라 가능했던 규약). 2026-08-15 에 풀 태그가 인자가 됐으므로, 기본 태그가 아니면
    # 태그를 포함한 이름을 먼저 본다 — 안 그러면 MA200 풀을 판정하면서 MA 20/60 풀의
    # 요약을 읽는다. `run_random_pool.py` 의 `side_sfx` 와 같은 규칙이다.
    m_sfx = re.search(r'(_n\d+)$', args.draws_tag)
    rs_path = ROB_DIR / f'random_summary_{args.draws_tag}.json'
    if not rs_path.exists():
        rs_path = ROB_DIR / f'random_summary{m_sfx.group(1) if m_sfx else ""}.json'
    draws_n = None
    if rs_path.exists():
        draws_n = json.loads(rs_path.read_text(encoding='utf-8')).get('n_stocks')
    if draws_n is None:
        draws_n = _n_from_tag(args.draws_tag, DEFAULT_N_STOCKS)

    # ── 하드 게이트 ─────────────────────────────────────────────────────────
    n_mismatch = (f_n != draws_n)
    if n_mismatch:
        log.error('G1 미산출 — 종목 수 불일치: 판정 대상 %s n=%s vs 귀무분포 %s n=%s. '
                  '같은 n 으로 추첨한 풀이 필요하다 '
                  '(run_random_pool --n-pick %s).', F_TAG, f_n, args.draws_tag, draws_n, f_n)
        g1 = None
    else:
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
        # 어떤 전략의 성적표인지 파일 자체에 남긴다. 종전 gate_results.json 에는
        # 이 필드가 없어, 태그가 바뀐 뒤 누가 봐도 어느 전략 것인지 알 수 없었다.
        'tag': F_TAG, 'n_stocks': f_n,
        'u_tag': U_TAG, 'draws_tag': args.draws_tag, 'draws_n_stocks': draws_n,
        'hard_gates': {
            'G1': {'pass': None if g1 is None else bool(g1),
                   'not_computed_reason': (
                       f'귀무분포 종목 수 불일치 (대상 n={f_n}, 추첨 n={draws_n}) — '
                       f'같은 n 의 풀로 재추첨 필요' if n_mismatch else None),
                   'f_cagr': f_json['cagr'], 'random_p95': p95,
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
            # G1 미산출이면 "전부 통과"라고 말할 수 없다 — None 을 False 로 뭉개지 않는다.
            'all_hard_pass': (None if g1 is None else bool(g1 and g2 and g5)),
            'any_warning': bool(g3_warn or g4_warn),
        },
    }

    ROB_DIR.mkdir(parents=True, exist_ok=True)
    out_path = ROB_DIR / f'gate_results_{F_TAG}.json'
    out_path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')

    log.info('G1 (CAGR>=random p95): %s — F=%.4f p95=%.4f (귀무분포 백분위 %.1f%%)',
             '미산출(n 불일치)' if g1 is None else ('PASS' if g1 else 'FAIL'),
             f_json['cagr'], p95, f_pctl_in_null * 100)
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
    ahp = results['verdict_inputs']['all_hard_pass']
    log.info('판정 입력: 하드 전부 %s, 경고 %s → %s',
             '미확정' if ahp is None else ('PASS' if ahp else 'FAIL'),
             '발화' if results['verdict_inputs']['any_warning'] else '없음', out_path)


if __name__ == '__main__':
    main()
