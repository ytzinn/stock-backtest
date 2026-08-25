"""
G5·G2 재산출 (섀도우 세대) — 2026-08-25.

사전등록: `experiments/runs/2026.08.25._G5G2_REISSUE.md` (커밋 629885e).
**문턱·정의를 바꾸지 않는다.** 정정된 데이터로 같은 기준을 다시 잴 뿐이다.

산식은 전부 SSOT 재사용 — 복제 금지.
  구간 지표   : backtest.metrics.compute_*
  일별 지표   : backtest.metrics.compute_daily_metrics  (G5 = daily_mdd, net)
  G5 문턱     : scripts.robustness.gate_analysis.G5_MDD_LIMIT
  구간 집합   : scripts.analysis.coverage_slice + period_truncation.load_f_periods

단계 (순서 강제 — 양성 대조가 본 측정보다 먼저):
    --stage controls   PC-4  MDD 계산기 양성/음성 대조 + G5 비교방향 대조   (DB·tape 불필요)
    --stage tape       PC-1/2/3  세대 판별·전 구간 대조·편입 집합 대조
    --stage gates      period_set + G5 + G2 판정

실행 (섀도우 워크트리에서):
    /opt/stock-backtest/venv/bin/python -m scripts.analysis.g5g2_reissue --stage controls
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
from datetime import date
from pathlib import Path

import pandas as pd

from backtest.metrics import compute_cagr, compute_daily_metrics
from scripts.analysis.coverage_slice import (
    PREREG_THRESHOLD,
    contiguous_tail,
    load_rule_coverage,
    selected_anchors,
)
from scripts.analysis.period_truncation import RULES, load_f_periods
from scripts.robustness.gate_analysis import G5_MDD_LIMIT

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger(__name__)

ABL_DIR   = Path('experiments/ablation')
NAV_DIR   = Path('experiments/daily_nav')
OUT_DIR   = Path('experiments/analysis/2026.08.25._g5g2_reissue')

F_TAG     = 'F_pbr_ma200_n13'
F_TAG_N20 = 'F_pbr_ma200_n20'      # PC-3 독립 멤버십 참조물 (기존 섀도우 자산)
U_TAG     = 'U_pbr_path_ew'        # G2 사전등록 벤치마크 (SPEC_10 §5-1) — 교체 금지

#: 착수 기록(§1-3)에 **결과 산출 전** 박아 둔 값. 어긋나면 중단한다.
#: 이 상수가 사전등록과 실행을 잇는 유일한 끈이다 — 재계산 결과를 믿지 말고 이것과 맞춰라.
PREREG_PERIOD_N      = 18
PREREG_PERIOD_SHA256 = 'd318d475e89c438dce62873a6c38d1b422c2d042c52cf99eadccb7797495d383'

#: tape 의 `ret` 은 round(..., 6) 이므로 6자리 반올림 반폭. 등가중 평균도 같은 상한을 갖는다.
TOL_TAPE_RET = 5e-7


# ── 구간 집합 ───────────────────────────────────────────────────────────────
def period_set(root: Path = Path('.')) -> dict:
    """사전등록 규칙의 기계적 적용. **숫자를 옮겨 적지 않는다** — SSOT 재적용."""
    f_rows      = load_f_periods(root)
    all_anchors = [r['rebalance_date'] for r in f_rows]
    cov         = load_rule_coverage()
    sel  = [a for a in selected_anchors(RULES, PREREG_THRESHOLD, cov) if a in set(all_anchors)]
    keep = contiguous_tail(sel, all_anchors)

    sha = hashlib.sha256('\n'.join(sorted(keep)).encode()).hexdigest()
    end = next(r['next_date'] for r in f_rows if r['rebalance_date'] == keep[-1])

    log.info('== 구간 집합 (사전등록 규칙 재적용) ==')
    log.info('  완결 구간            : %d', len(all_anchors))
    log.info('  임계 탈락 (tau=%.0f%%) : %s', PREREG_THRESHOLD * 100,
             sorted(set(all_anchors) - set(sel)))
    log.info('  연속화 상실          : %s',
             [a for a in all_anchors if a in set(sel) and a not in set(keep)])
    log.info('  채택                 : n=%d  %s ~ %s', len(keep), keep[0], end)
    log.info('  sha256               : %s', sha)

    # fail-closed: 사전등록값과 어긋나면 실행하지 않는다
    if len(keep) != PREREG_PERIOD_N or sha != PREREG_PERIOD_SHA256:
        raise SystemExit(
            f'FATAL 구간 집합이 사전등록과 다르다 — n={len(keep)}(등록 {PREREG_PERIOD_N}), '
            f'sha256={sha}(등록 {PREREG_PERIOD_SHA256}). 중단한다.'
        )
    return {
        'n': len(keep), 'dates': keep, 'sha256': sha,
        'source': 'e2c2b8d 절단 집합',
        'start': keep[0], 'end': end,
        'full_closed_n': len(all_anchors),
    }


# ── PC-4: MDD 계산기 대조 (DB·tape 불필요) ──────────────────────────────────

def _d(v) -> str:
    """compute_daily_metrics 는 date 를, pandas 는 Timestamp 를 준다. 둘 다 받는다."""
    return str(v.date() if hasattr(v, 'date') and not isinstance(v, date) else v)

def stage_controls() -> dict:
    """G5 가 쓰는 **바로 그 계산기**(compute_daily_metrics)를 합성 계열로 검증한다.

    0건·이상값 판정 전에 계측이 산다는 것부터 보인다 (CLOSEOUT §8 교훈 1).
    음성 대조를 함께 둔다 — 양성만 있으면 "항상 그 값을 뱉는 계산기"를 통과시킨다.
    """
    idx = pd.bdate_range('2020-01-01', periods=400)

    def _nav(values: list[float]) -> pd.Series:
        pad = [values[-1]] * (len(idx) - len(values))
        return pd.Series(values + pad, index=idx[:len(values) + len(pad)], dtype=float)

    results = []

    # 양성 — 고점 100 → 저점 70 (정확히 −30%) → 회복. peak/trough 날짜까지 확인한다.
    up      = [100.0] * 100
    down    = [100.0 - 30.0 * i / 50 for i in range(1, 51)]     # 100 → 70
    recover = [70.0 + 30.0 * i / 50 for i in range(1, 51)]      # 70 → 100
    nav_pos = _nav(up + down + recover)
    m = compute_daily_metrics(nav_pos)
    results.append({
        'id': 'PC-4a', 'kind': '양성 — 설계 MDD −30%',
        'expected': -0.30, 'observed': m['daily_mdd'],
        'ok': abs(m['daily_mdd'] - (-0.30)) < 1e-12,
        'peak': _d(m['mdd_peak_date']), 'trough': _d(m['mdd_trough_date']),
    })

    # 음성 — 단조 증가. 낙폭이 없으므로 0 이어야 한다.
    nav_neg = _nav([100.0 * (1.0002 ** i) for i in range(400)])
    m2 = compute_daily_metrics(nav_neg)
    results.append({
        'id': 'PC-4b', 'kind': '음성 — 단조 증가 (낙폭 없음)',
        'expected': 0.0, 'observed': m2['daily_mdd'],
        'ok': abs(m2['daily_mdd']) < 1e-12,
    })

    # 양성 — G5 문턱 근방. 비교 방향(얕아야 통과)이 실제로 그런지 본다.
    for depth, want_pass in ((0.4499, True), (0.4501, False)):
        nav = _nav([100.0] * 100 + [100.0 * (1 - depth * i / 50) for i in range(1, 51)])
        mdd = compute_daily_metrics(nav)['daily_mdd']
        verdict = mdd > G5_MDD_LIMIT          # gate_analysis.py:185 와 같은 부등호
        results.append({
            'id': f'PC-4c(-{depth:.2%})', 'kind': 'G5 비교 방향',
            'expected': want_pass, 'observed': verdict,
            'ok': verdict == want_pass, 'mdd': mdd, 'limit': G5_MDD_LIMIT,
        })

    for r in results:
        log.info('  [%s] %-28s %s  %s', 'OK ' if r['ok'] else 'FAIL', r['id'], r['kind'],
                 {k: v for k, v in r.items() if k not in ('id', 'kind', 'ok')})
    if not all(r['ok'] for r in results):
        raise SystemExit('FATAL PC-4 실패 — 계측이 서지 않았다. 검출기를 완화하지 말고 원인을 특정하라.')
    log.info('  PC-4 전건 통과 — MDD 계산기·G5 비교 방향 정상\n')
    return {'all_pass': True, 'checks': results, 'g5_limit': G5_MDD_LIMIT}


# ── PC-1/2/3: tape 대조 ─────────────────────────────────────────────────────
def _load_tape(tag: str, root: Path = Path('.')) -> list[dict]:
    return json.loads((root / ABL_DIR / f'{tag}_holdings.json').read_text(encoding='utf-8'))


def _load_periods(tag: str, root: Path = Path('.')) -> dict[str, dict]:
    with (root / ABL_DIR / f'{tag}_periods.csv').open(encoding='utf-8') as f:
        return {r['rebalance_date']: r for r in csv.DictReader(f)}


def _tape_ew_return(period: dict) -> float:
    """tape 구간의 등가중 수익률.

    engine._aggregate_period_return 은 **유효 종목**의 weight 합으로 재정규화한다.
    tape 의 ret=None 이 곧 그 무효 종목이므로, None 을 뺀 평균이 엔진 정의와 같다.
    """
    rets = [h['ret'] for h in period['holdings'] if h['ret'] is not None]
    if not rets:
        return 0.0
    return sum(rets) / len(rets)


def stage_tape(root: Path = Path('.')) -> dict:
    """PC-1 세대 판별 / PC-2 전 구간 대조 / PC-3 편입 집합 대조."""
    tape    = _load_tape(F_TAG, root)
    periods = _load_periods(F_TAG, root)

    # PC-1 — 세대 판별. **기대값을 문서에서 인용하지 않는다.** periods.csv 에서 읽는다.
    probe   = '2017-04-05'
    p_tape  = next(p for p in tape if p['rebalance_date'] == probe)
    obs     = _tape_ew_return(p_tape)
    exp     = float(periods[probe]['period_return'])
    pc1_ok  = abs(obs - exp) <= TOL_TAPE_RET
    log.info('== PC-1 세대 판별 (%s) ==', probe)
    log.info('  tape 등가중  = %.10f', obs)
    log.info('  섀도우 csv   = %.10f   (직접 읽음 — 문서 인용값 아님)', exp)
    log.info('  |diff| = %.3e  tol = %.1e  -> %s\n', abs(obs - exp), TOL_TAPE_RET,
             'OK' if pc1_ok else 'FAIL')
    if not pc1_ok:
        raise SystemExit('FATAL PC-1 실패 — 세대가 다르다. 즉시 중단.')

    # PC-2 — 전 구간 수익률 대조
    rows, worst = [], 0.0
    for p in tape:
        d = p['rebalance_date']
        if d not in periods:
            raise SystemExit(f'FATAL PC-2: {d} 가 periods.csv 에 없다')
        o, e = _tape_ew_return(p), float(periods[d]['period_return'])
        diff = abs(o - e)
        worst = max(worst, diff)
        rows.append({'date': d, 'tape': o, 'csv': e, 'diff': diff,
                     'ok': diff <= TOL_TAPE_RET})
    pc2_ok = all(r['ok'] for r in rows)
    log.info('== PC-2 전 구간 대조 (n=%d) ==', len(rows))
    log.info('  최대 |diff| = %.3e  tol = %.1e  -> %s', worst, TOL_TAPE_RET,
             'OK' if pc2_ok else 'FAIL')
    for r in rows:
        if not r['ok']:
            log.error('  불일치 %s: tape %.10f vs csv %.10f (%.3e)',
                      r['date'], r['tape'], r['csv'], r['diff'])
    if not pc2_ok:
        raise SystemExit('FATAL PC-2 실패 — 검출기를 완화하지 말고 기대치 산술을 먼저 의심하라.')

    # PC-3 — 편입 집합. 독립 참조물 = 기존 섀도우 n=20 tape 의 상위 13.
    #        랭킹이 같고 supplement_to_minimum 이 PBR 경로에서 무동작이라 top-13 ⊂ top-20.
    pc3 = {'ran': False}
    n20_path = root / ABL_DIR / f'{F_TAG_N20}_holdings.json'
    if n20_path.exists():
        t20 = {p['rebalance_date']: p for p in _load_tape(F_TAG_N20, root)}
        mism, checked = [], 0
        for p in tape:
            d = p['rebalance_date']
            if d not in t20 or t20[d]['n_portfolio'] < p['n_portfolio']:
                continue
            got  = [h['ticker'] for h in p['holdings']]
            want = [h['ticker'] for h in t20[d]['holdings']][:len(got)]
            checked += 1
            if set(got) != set(want):
                mism.append({'date': d, 'only_n13': sorted(set(got) - set(want)),
                             'only_n20_top': sorted(set(want) - set(got))})
        pc3 = {'ran': True, 'checked': checked, 'mismatches': mism, 'ok': not mism}
        log.info('== PC-3 편입 집합 (독립 참조 = n=20 tape 상위 13) ==')
        log.info('  대조 구간 %d개, 불일치 %d개  -> %s (허용오차 없음)',
                 checked, len(mism), 'OK' if not mism else 'FAIL')
        for m in mism:
            log.error('  %s  n13만 %s / n20상위만 %s', m['date'], m['only_n13'], m['only_n20_top'])
        if mism:
            raise SystemExit('FATAL PC-3 실패 — 편입 집합 불일치.')
    else:
        log.warning('== PC-3 건너뜀 — %s 없음 ==', n20_path)

    log.info('')
    return {
        'all_pass': True,
        'PC-1': {'probe': probe, 'tape': obs, 'csv': exp, 'diff': abs(obs - exp), 'ok': pc1_ok},
        'PC-2': {'n': len(rows), 'max_diff': worst, 'tol': TOL_TAPE_RET, 'ok': pc2_ok},
        'PC-3': pc3,
    }


# ── G5 / G2 ─────────────────────────────────────────────────────────────────
def _span(rows: list[dict]) -> dict:
    return {'start_date': date.fromisoformat(rows[0]['rebalance_date']),
            'end_date':   date.fromisoformat(rows[-1]['next_date'])}


def _net_cagr(rows: list[dict]) -> float:
    idx = pd.DatetimeIndex([r['rebalance_date'] for r in rows])
    return compute_cagr(pd.Series([float(r['net_return']) for r in rows], index=idx),
                        **_span(rows))


def _g5(nav_csv: Path, dates: list[str], end: str, label: str) -> dict:
    """일별 net MDD. 구간 집합으로 **슬라이싱한 뒤** 계산한다 (tape 은 전 구간)."""
    nav = pd.read_csv(nav_csv, parse_dates=['date']).set_index('date')
    sl  = nav.loc[pd.Timestamp(dates[0]):pd.Timestamp(end)]
    if sl.empty:
        raise SystemExit(f'FATAL G5({label}): 슬라이스가 비었다 {dates[0]}~{end}')
    m = compute_daily_metrics(sl['nav_net'])
    log.info('  [%s] n_days=%d  %s ~ %s  일별 net MDD = %.4f%%', label, len(sl),
             sl.index[0].date(), sl.index[-1].date(), m['daily_mdd'] * 100)
    return {
        'label': label, 'n_days': len(sl),
        'first_day': str(sl.index[0].date()), 'last_day': str(sl.index[-1].date()),
        'daily_mdd_net': m['daily_mdd'],
        'mdd_peak': _d(m['mdd_peak_date']), 'mdd_trough': _d(m['mdd_trough_date']),
        'net_sharpe': m['daily_sharpe'],
    }


def stage_gates(root: Path = Path('.')) -> dict:
    ps = period_set(root)
    keep, end = set(ps['dates']), ps['end']

    f_all = load_f_periods(root)
    f_cut = [r for r in f_all if r['rebalance_date'] in keep]

    u_rows_all = list(csv.DictReader(
        (root / ABL_DIR / f'{U_TAG}_periods.csv').open(encoding='utf-8')))
    u_cut = [r for r in u_rows_all if r['rebalance_date'] in keep]
    if len(u_cut) != len(f_cut):
        raise SystemExit(f'FATAL G2: 벤치마크 구간 수 불일치 F={len(f_cut)} U={len(u_cut)}')

    # ── G5 ──
    log.info('== G5 (일별 net MDD, 문턱 %.0f%%) ==', G5_MDD_LIMIT * 100)
    nav_csv = root / NAV_DIR / f'{F_TAG}_daily_nav.csv'
    g5_cut  = _g5(nav_csv, ps['dates'], end, '판정 n=18')
    g5_full = _g5(nav_csv, [f_all[0]['rebalance_date']], f_all[-1]['next_date'],
                  f'진단 전구간 n={len(f_all)}')
    g5_pass = g5_cut['daily_mdd_net'] > G5_MDD_LIMIT      # gate_analysis.py:185 동일 부등호
    log.info('  판정: %s  (%.4f%% vs 한계 %.2f%%)\n',
             'PASS' if g5_pass else 'FAIL', g5_cut['daily_mdd_net'] * 100, G5_MDD_LIMIT * 100)

    # ── G2 ──
    log.info('== G2 (F net CAGR > %s net CAGR) ==', U_TAG)
    f_net_cut,  u_net_cut  = _net_cagr(f_cut),  _net_cagr(u_cut)
    f_net_full, u_net_full = _net_cagr(f_all),  _net_cagr(u_rows_all)
    g2_pass = f_net_cut > u_net_cut
    log.info('  판정 n=18   F %.4f%%  vs  U %.4f%%   마진 %+.4f%%p  -> %s',
             f_net_cut * 100, u_net_cut * 100, (f_net_cut - u_net_cut) * 100,
             'PASS' if g2_pass else 'FAIL')
    log.info('  진단 전구간 F %.4f%%  vs  U %.4f%%   마진 %+.4f%%p  (판정 비사용)\n',
             f_net_full * 100, u_net_full * 100, (f_net_full - u_net_full) * 100)

    out = {
        'generated_for': 'G5·G2 재산출 (섀도우 세대)',
        'prereg': 'experiments/runs/2026.08.25._G5G2_REISSUE.md',
        'baseline': {'branch': 'shadow/fs-div-fallback', 'db_port': 5436,
                     'artifact_root': '/home/milmelmul/stock-backtest-shadow/experiments'},
        'period_set': ps,
        'G5': {'pass': bool(g5_pass), 'limit': G5_MDD_LIMIT,
               'judgment': g5_cut, 'diagnostic_full': g5_full},
        'G2': {'pass': bool(g2_pass), 'benchmark': U_TAG,
               'judgment': {'f_net_cagr': f_net_cut, 'u_net_cagr': u_net_cut,
                            'margin': f_net_cut - u_net_cut, 'n': len(f_cut)},
               'diagnostic_full': {'f_net_cagr': f_net_full, 'u_net_cagr': u_net_full,
                                   'margin': f_net_full - u_net_full, 'n': len(f_all)},
               'known_limitation': (
                   f'{U_TAG} 의 모멘텀은 MA 20/60, 채택안은 MA200 — 다른 유니버스의 '
                   '동일가중과 견준다. SPEC_10 미해결 항목, dashboard/claims.KNOWN 등록분. '
                   '벤치마크 교체는 사전등록 게이트 정의 변경이라 이 세션에서 하지 않았다.')},
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / 'gate_results_g5g2.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    log.info('→ %s', OUT_DIR / 'gate_results_g5g2.json')
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--stage', required=True, choices=['controls', 'tape', 'gates'])
    ap.add_argument('--root', default='.')
    args = ap.parse_args()
    root = Path(args.root)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    res = {'controls': stage_controls, 'tape': lambda: stage_tape(root),
           'gates': lambda: stage_gates(root)}[args.stage]()
    (OUT_DIR / f'{args.stage}.json').write_text(
        json.dumps(res, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


if __name__ == '__main__':
    main()
