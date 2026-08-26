"""SPEC_15 `S-4` — IC + Fama-MacBeth 본 측정.

**여기가 실제 신호의 `mean(IC)` 가 정당하게 노출되는 유일한 지점이다** (§6-1).
그 앞의 사전등록·방화벽·대조군은 전부 이 순간을 위한 것이었다.

사전등록 `c369401` §7-1. 착수 기록 `8aa5a55` §1-1 이 판정 분기를 **결과 보기 전에**
못박아 뒀다 — 이 파일은 계산하고, 규칙을 그대로 적용할 뿐 판단하지 않는다.

순서 강제: 선행 게이트 → E1 primary → P-2(FM) → P-3(강건성).

게이트 셋 (전부 fail-closed):
  1. `controls.json` 의 `all_pass=true` 없이는 실행 거부
     (SPEC_14 `stage_b` ↔ `integrity_gates` 선례와 같은 구조)
  2. `a2_power.json` 에 §6-1 금지 키가 있으면 실행 거부
     — `mean(IC)` 가 A-2 단계에서 새어 나왔다는 뜻이므로, 그 상태로 본 측정을
       이어가면 사전등록의 순서 증거가 이미 깨진 것이다

`[Claude 의견]` 게이트를 `S-4` 가 아니라 지금 세우는 이유는, **음성 대조로 발화를
확인해 두어야** 다음 세션이 그것을 근거로 삼을 수 있기 때문이다. 세워만 두고 발화를
확인하지 않은 검사는 이 저장소가 여러 번 겪은 "형식뿐인 검사" 가 된다.

실행:
    python -m scripts.xsec.estimate            # 본 측정
    python -m scripts.xsec.estimate --check-gates   # 게이트만
"""
from __future__ import annotations

import argparse
import json
import logging
import math
from pathlib import Path

import numpy as np

from scripts.xsec import ic as icmod
from scripts.xsec._guard import assert_no_db_imported
from scripts.xsec.firewall import FirewallBreach, assert_clean

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger(__name__)

OUT_DIR = Path('experiments/analysis/2026.08.26._xsec_controls')
CONTROLS = OUT_DIR / 'controls.json'
A2       = Path('experiments/analysis/2026.08.25._xsec_prelim') / 'a2_power.json'

#: S-3 이 **반드시 전부** 돌았어야 하는 대조 집합. 생산자(controls.py)와 소비자(여기)가
#: 서로 다른 파일에서 같은 집합을 주장한다 — 한쪽만 알면 그것은 신뢰이지 검사가 아니다.
EXPECTED_RAN = frozenset({'N1', 'N2a', 'N2b1', 'P1', 'P2'})


class GateRefused(SystemExit):
    """선행 게이트 미충족. 본 측정으로 넘어가지 않는다."""


def check_gates(controls_path: Path = CONTROLS, a2_path: Path = A2) -> dict:
    if not controls_path.exists():
        raise GateRefused(f'거부: 대조군 산출물이 없다 — {controls_path}. S-3 을 먼저 돌려라.')
    controls = json.loads(controls_path.read_text(encoding='utf-8'))
    if controls.get('all_pass') is not True:
        raise GateRefused(
            f'거부: {controls_path} 의 all_pass 가 true 가 아니다 '
            f'(현재 {controls.get("all_pass")!r}). 대조군이 서지 않으면 본 측정은 무의미하다.')
    log.info('  게이트 1 (controls.all_pass=true) 통과')

    # 게이트 1-b — **기대 집합을 소비처에 둔다.**
    # `all_pass` 는 controls.py 가 `ran` 을 정직하게 채운다는 전제 위에 있다
    # (직전 세션에 그 자리가 fail-open 이었다). 기대 집합을 여기 적어 두면 그 전제가
    # 검사로 바뀐다 — 생산자와 소비자가 서로 다른 파일에서 같은 집합을 주장해야 한다.
    ran = set(controls.get('ran') or ())
    if ran != EXPECTED_RAN:
        raise GateRefused(
            f'거부: 대조 집합 불일치 — 기대 {sorted(EXPECTED_RAN)}, 실제 {sorted(ran)}. '
            f'부족 {sorted(EXPECTED_RAN - ran)} / 초과 {sorted(ran - EXPECTED_RAN)}.')
    log.info('  게이트 1-b (ran == %s) 통과', sorted(EXPECTED_RAN))

    if not a2_path.exists():
        raise GateRefused(f'거부: A-2 산출물이 없다 — {a2_path}. S-5b 를 먼저 돌려라.')
    a2 = json.loads(a2_path.read_text(encoding='utf-8'))
    try:
        assert_clean(a2, str(a2_path))
    except FirewallBreach as e:
        raise GateRefused(f'거부(§6-1 방화벽): {e}') from e
    log.info('  게이트 2 (§6-1 금지 키 부재) 통과')
    return {'controls': controls, 'a2': a2}


def main() -> None:
    assert_no_db_imported('scripts.xsec.estimate')
    ap = argparse.ArgumentParser()
    ap.add_argument('--check-gates', action='store_true', help='게이트만 검사하고 종료')
    ap.add_argument('--controls', default=str(CONTROLS))
    ap.add_argument('--a2', default=str(A2))
    args = ap.parse_args()

    log.info('== S-4 선행 게이트 ==')
    gates = check_gates(Path(args.controls), Path(args.a2))
    if args.check_gates:
        raise SystemExit('게이트만 검사하고 종료 (--check-gates).')

    a0 = json.loads(Path('experiments/analysis/2026.08.25._xsec_prelim/a0_period_set.json')
                    .read_text(encoding='utf-8'))
    ps, ps18 = a0['period_set'], a0['period_set_t18']
    b, seed = gates['controls']['N1']['B'], gates['controls']['N1']['seed']
    log.info('  구간 집합 %s  n=%d  sha256=%s', ps['id'], ps['n'], ps['sha256'][:16])
    log.info('')

    rows = _panel_periods('L3R', ps['dates'])
    e1 = e1_primary(rows, b, seed)

    log.info('== P-2 Fama-MacBeth ==')
    fm_ctl = fama_macbeth(rows, True, b, seed)      # primary — 통제 3개 포함
    fm_raw = fama_macbeth(rows, False, b, seed)     # 병기 — 통제 없음

    p3 = p3_robustness(ps['dates'], b, seed)

    log.info('== T=18 병기 (G1 정합용, 판정 대체 아님) ==')
    e1_18 = e1_primary(_panel_periods('L3R', ps18['dates']), b, seed)

    # 사전등록 §7-1 을 **그대로** 적용한다. 판단하지 않는다.
    p1_pass = (e1['p_one_sided'] < 0.05) and e1['sign_positive']
    p2_pass = (((fm_ctl['lambda_bar'] > 0) == e1['sign_positive'])
               and fm_ctl['p_one_sided'] < 0.10)
    p3_pass = p3['all_same_sign']
    # 반대 부호 유의 — 가설 반대편 꼬리. p_one_sided 는 양의 방향 p 이므로 보수적으로 1−p.
    reverse = (not e1['sign_positive']) and ((1.0 - e1['p_one_sided']) < 0.05)

    log.info('')
    log.info('== 사전등록 §7-1 판정 ==')
    log.info('  P-1 (단측 p<0.05 且 부호 가설 방향) : %s', 'PASS' if p1_pass else 'FAIL')
    log.info('  P-2 (λ̄ 부호 일치 且 단측 p<0.10)   : %s', 'PASS' if p2_pass else 'FAIL')
    log.info('  P-3 (6조합 부호 유지)               : %s', 'PASS' if p3_pass else 'FAIL')
    log.info('  반대 부호 유의?                      : %s', reverse)
    log.info('  → E1 = %s', 'PASS' if p1_pass else 'FAIL')

    out = {'prereg': 'c369401 §7-1', 'run_record': '8aa5a55 §1-1',
           'period_set': {'id': ps['id'], 'n': ps['n'], 'sha256': ps['sha256']},
           'E1_primary': e1, 'FM_controls': fm_ctl, 'FM_no_controls': fm_raw,
           'P3': p3, 'T18_companion': e1_18,
           'verdict': {'P1': p1_pass, 'P2': p2_pass, 'P3': p3_pass,
                       'reverse_sign_significant': reverse,
                       'E1': 'PASS' if p1_pass else 'FAIL'}}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    q = OUT_DIR / 'e1_estimate.json'
    q.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    log.info('→ %s', q)


# ── 본 측정 (S-4) ───────────────────────────────────────────────────────────
def _z_within(x: np.ndarray) -> np.ndarray:
    """구간 내 rank → uniform → 역정규 (v0.4 §3-4). 패널은 원자료이므로 여기서 변환한다."""
    from statistics import NormalDist
    r = icmod.rankdata_average(x)
    u = r / (len(x) + 1.0)                       # (0,1) 개구간
    nd = NormalDist()
    return np.array([nd.inv_cdf(v) for v in u], dtype=float)


def _winsor(x: np.ndarray, lo: float = 0.01, hi: float = 0.99) -> np.ndarray:
    """1/99 pct winsorize (§3-4). trimming 을 쓰지 않는 이유는 관측을 버리면 IC 가
    낙관 쪽으로 밀리기 때문이다."""
    a, b = np.quantile(x, lo), np.quantile(x, hi)
    return np.clip(x, a, b)


def _panel_periods(layer: str, dates: list[str]):
    """계층·구간별 DataFrame. 패널 경로는 controls 와 같은 후보 규약을 쓴다."""
    import pandas as pd

    from scripts.xsec.controls import _PANEL_REL
    rel = _PANEL_REL.replace('_L3R.parquet', f'_{layer}.parquet')
    cands = [Path('experiments') / rel,
             Path('experiments/_baselines/shadow_20260819/experiments') / rel]
    path = next((c for c in cands if c.exists()), None)
    if path is None:
        raise SystemExit(f'FATAL 패널이 없다: {cands}')
    df = pd.read_parquet(path)
    out = []
    for d in dates:
        r = df[(df['rebalance_date'] == d) & df['fwd_ret'].notna() & df['inv_pbr'].notna()]
        out.append((d, r))
    return out


def e1_primary(rows, b: int, seed: int) -> dict:
    """E1 — rank IC, permutation 단측 p. **판정 대상은 이것 하나다** (v0.4 §2)."""
    per = [(r['inv_pbr'].to_numpy(float), r['fwd_ret'].to_numpy(float)) for _d, r in rows]
    ics = icmod.ic_series(per)
    T = len(ics)
    mean_ic, sd_ic = float(ics.mean()), float(ics.std(ddof=1))
    icir = mean_ic / sd_ic if sd_ic else 0.0
    t = icir * math.sqrt(T)
    null = icmod.permutation_mean_ic(per, b, seed)
    p_one = float((null >= mean_ic).mean())          # 단측 — 가설 방향은 IC > 0
    log.info('== E1 primary (L3R, T=%d, 동일가중, rank IC) ==', T)
    log.info('  mean(IC)=%.6f  sd(IC)=%.6f  ICIR=%.4f  t=%.4f', mean_ic, sd_ic, icir, t)
    log.info('  permutation 단측 p = %.4f  (B=%d, seed=%d)', p_one, b, seed)
    log.info('  구간별 IC: %s', [round(v, 4) for v in ics])
    return {'T': T, 'mean_ic': mean_ic, 'sd_ic': sd_ic, 'icir': icir, 't': t,
            'p_one_sided': p_one, 'B': b, 'seed': seed,
            'ic_by_period': {d: float(v) for (d, _r), v in zip(rows, ics)},
            'sign_positive': mean_ic > 0}


def fama_macbeth(rows, controls_on: bool, b: int, seed: int) -> dict:
    """P-2 — FM 2단 추정. 통제 3개는 **동결**이다 (C-1)."""
    lam, n_used = [], []
    for _d, r in rows:
        y = _winsor(r['fwd_ret'].to_numpy(float))
        X = [np.ones(len(y)), _z_within(r['inv_pbr'].to_numpy(float))]
        if controls_on:
            mc = r['market_cap'].to_numpy(float)
            X.append(_z_within(np.log(np.where(mc > 0, mc, np.nan))))
            X.append(_z_within(np.nan_to_num(r['mom_126'].to_numpy(float), nan=0.0)))
            tv = r['avg_turnover'].to_numpy(float)
            X.append(_z_within(np.log(np.where(tv > 0, tv, np.nan))))
        M = np.column_stack(X)
        ok = ~np.isnan(M).any(axis=1)
        beta, *_ = np.linalg.lstsq(M[ok], y[ok], rcond=None)
        lam.append(float(beta[1])); n_used.append(int(ok.sum()))
    lam = np.array(lam)
    T = len(lam)
    bar, sd = float(lam.mean()), float(lam.std(ddof=1))
    t = bar / (sd / math.sqrt(T)) if sd else 0.0

    rng = np.random.default_rng(seed)             # λ̄ 의 치환 귀무 (구간 내 신호 재배열)
    null = np.empty(b)
    for i in range(b):
        s = rng.choice([-1.0, 1.0], size=T)       # 부호 치환 — 구간 독립성 하에 λ 의 귀무
        null[i] = float((lam * s).mean())
    p_one = float((null >= bar).mean()) if bar > 0 else float((null <= bar).mean())
    tag = '통제 3개' if controls_on else '통제 없음'
    log.info('  [FM %s] λ̄=%.6f  sd(λ)=%.6f  t=%.4f (df=%d)  permutation 단측 p=%.4f',
             tag, bar, sd, t, T - 1, p_one)
    return {'controls': controls_on, 'T': T, 'lambda_bar': bar, 'sd_lambda': sd,
            't': t, 'df': T - 1, 'p_one_sided': p_one, 'n_used_median': int(np.median(n_used))}


def p3_robustness(dates: list[str], b: int, seed: int) -> dict:
    """P-3 — 6조합 **부호만**. 크기 순위를 매기지 않는다 (SPEC_13 §9-6 승계)."""
    out = []
    for layer in ('L2', 'L3R'):
        rows = _panel_periods(layer, dates)
        for wname in ('equal', 'sqrt_mktcap'):
            for wins in (True, False):
                per = []
                for _d, r in rows:
                    sig = r['inv_pbr'].to_numpy(float)
                    y = r['fwd_ret'].to_numpy(float)
                    if wins:
                        y = _winsor(y)
                    if wname == 'sqrt_mktcap':
                        # 가중 Spearman 대용: √시총 가중 rank 상관은 정의가 여럿이라
                        # **가중 재표본**으로 구현한다 — 부호만 볼 것이므로 충분하다
                        w = np.sqrt(np.clip(r['market_cap'].to_numpy(float), 0, None))
                        w = w / w.sum() if w.sum() > 0 else None
                        rng = np.random.default_rng(seed)
                        idx = rng.choice(len(y), size=len(y), replace=True, p=w)
                        sig, y = sig[idx], y[idx]
                    per.append((sig, y))
                ics = icmod.ic_series(per)
                m = float(ics.mean())
                null = icmod.permutation_mean_ic(per, b, seed)
                out.append({'layer': layer, 'weight': wname, 'winsor': wins,
                            'sign': '+' if m > 0 else ('-' if m < 0 else '0'),
                            'p_one_sided': float((null >= m).mean())})
    signs = {o['sign'] for o in out}
    log.info('== P-3 강건성 6조합 (부호만) ==')
    for o in out:
        log.info('  %-4s %-12s winsor=%-5s  부호 %s  p=%.4f', o['layer'], o['weight'],
                 o['winsor'], o['sign'], o['p_one_sided'])
    log.info('  부호 집합 = %s  -> %s', signs, 'PASS' if len(signs) == 1 else 'FAIL')
    return {'combos': out, 'signs': sorted(signs), 'all_same_sign': len(signs) == 1}


if __name__ == '__main__':
    main()
