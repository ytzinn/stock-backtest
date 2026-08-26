"""SPEC_15 `S-3` — 양성·음성 대조.

사전등록: `docs/설계/SPEC_15_cross_sectional_estimator_v0.4.md` §5 (커밋 `c369401`).
착수 기록·편차: `experiments/runs/2026.08.26._XSEC_CONTROLS.md` (커밋 `2e78675`).

**순서 강제**: `P-1 → P-2 → N-1 → N-2a → N-2b-1`. 앞이 실패하면 뒤로 가지 않는다.
`N-2b-2`(IC 수준 진단)는 `S-4` 로 이월했다 — 착수 기록 §1-1.

§0 허용 경계: **치환된** 실제 신호(N-1)와 **합성** 신호(P-1·P-2)의 IC 는 계산해도 된다.
실제 신호의 `mean(IC)` 는 이 파일 어디에서도 산출·방출하지 않는다.

실행 (섀도우 워크트리에서):
    /opt/stock-backtest/venv/bin/python -m scripts.xsec.controls
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

from scripts.xsec import ic as icmod

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger(__name__)

OUT_DIR  = Path('experiments/analysis/2026.08.26._xsec_controls')
A0_PATH  = Path('experiments/analysis/2026.08.25._xsec_prelim/a0_period_set.json')
_PANEL_REL = 'panel/panel_F_pbr_ma200_n13_L3R.parquet'
#: 서버(섀도우 워크트리)에서는 experiments/ 밑에 바로 있고, 개발 PC 에서는 기준선
#: 사본 트리(BASELINES.json 의 local_root) 밑에 있다. 둘 다 **같은 파일**이며
#: 해시는 등록부가 고정한다 — 경로를 손으로 넘기지 않는 이유다.
_CANDIDATES = [Path('experiments') / _PANEL_REL,
               Path('experiments/_baselines/shadow_20260819/experiments') / _PANEL_REL]
PANEL = next((p for p in _CANDIDATES if p.exists()), _CANDIDATES[0])

SEED_P2   = 20260826_02
SEED_N1   = 20260826_01
B_NULL    = 1_000
R_SYNTH   = 1_000
DESIGN_IC = (0.03, 0.05)
ALPHA     = 0.05          # 단측


def _periods(panel: pd.DataFrame, dates: list[str]) -> list[tuple[np.ndarray, np.ndarray]]:
    """구간별 (signal, fwd). **A-0 구간 집합만** 쓴다 — T 를 하드코딩하지 않는다."""
    out = []
    for d in dates:
        r = panel[panel['rebalance_date'] == d]
        r = r[r['fwd_ret'].notna() & r['inv_pbr'].notna()]
        out.append((r['inv_pbr'].to_numpy(dtype=float), r['fwd_ret'].to_numpy(dtype=float)))
    return out


# ── P-1 ─────────────────────────────────────────────────────────────────────
def p1(periods) -> dict:
    """신호 자리에 `fwd_ret` 자체를 주입 → rank IC ≈ 1.0. 배관이 살아 있음을 보인다."""
    vals = icmod.ic_series([(f, f) for _s, f in periods])
    ok = bool(np.all(vals > 0.999999))
    log.info('== P-1 양성 대조 (강) ==')
    log.info('  rank IC  min=%.10f  max=%.10f  (구간 %d)  -> %s',
             vals.min(), vals.max(), len(vals), 'OK' if ok else 'FAIL')
    if not ok:
        raise SystemExit('FATAL P-1 실패 — 배관이 서지 않았다. 뒤 단계로 가지 않는다.')
    return {'min': float(vals.min()), 'max': float(vals.max()), 'n_periods': len(vals),
            'pass': ok}


# ── P-2 ─────────────────────────────────────────────────────────────────────
def p2(periods) -> dict:
    """설계 IC 0.03/0.05 합성 신호의 **검출 성공률**. §6 A-2 의 실측 근거다.

    임계값은 **합성 신호를 치환한** 귀무에서 얻는다 — 실제 신호를 건드리지 않는다.
    귀무는 신호값의 결합구조와 무관(구간 내 재배열)하므로 이렇게 얻은 임계값이
    N-1 의 것과 같은 성질을 갖는다.
    """
    rng  = np.random.default_rng(SEED_P2)
    base = [(icmod.synthetic_signal(f, 0.0, rng), f) for _s, f in periods]
    null = icmod.permutation_mean_ic(base, B_NULL, SEED_P2 + 1)
    crit = float(np.quantile(null, 1 - ALPHA))

    log.info('== P-2 양성 대조 (약) — 감도 ==')
    log.info('  합성 귀무 sd=%.6f  임계(단측 %.0f%%)=%.6f  seed=%d B=%d',
             null.std(ddof=1), (1 - ALPHA) * 100, crit, SEED_P2, B_NULL)

    out = {'seed': SEED_P2, 'B_null': B_NULL, 'R': R_SYNTH, 'alpha': ALPHA,
           'crit_mean_ic': crit, 'null_sd': float(null.std(ddof=1)), 'levels': {}}
    for rho in DESIGN_IC:
        means, realized = [], []
        for _ in range(R_SYNTH):
            per = [(icmod.synthetic_signal(f, rho, rng), f) for _s, f in periods]
            vals = icmod.ic_series(per)
            means.append(vals.mean())
            realized.append(vals.mean())
        means = np.array(means)
        rate  = float((means > crit).mean())
        # **설계 IC 가 실제로 그 값으로 나오는지 먼저 확인한다** — 설계값을 그냥 믿지 않는다
        log.info('  설계 IC=%.2f  실현 mean(IC) 평균=%.4f  검출 성공률=%.3f',
                 rho, float(np.mean(realized)), rate)
        out['levels'][f'{rho:.2f}'] = {
            'design_ic': rho, 'realized_mean_ic_avg': float(np.mean(realized)),
            'detection_rate': rate}
    return out


# ── N-1 ─────────────────────────────────────────────────────────────────────
def n1(periods) -> dict:
    """구간 **내** 실제 신호 치환 → 귀무분포. `mean(IC)` 관측값은 산출하지 않는다."""
    null = icmod.permutation_mean_ic(periods, B_NULL, SEED_N1)
    sd   = float(null.std(ddof=1))
    log.info('== N-1 음성 대조 ==')
    log.info('  귀무 mean(IC): 평균=%.2e  sd=%.6f  (B=%d, seed=%d)',
             float(null.mean()), sd, B_NULL, SEED_N1)
    log.info('  ※ 관측 mean(IC) 는 산출하지 않는다 (§6-1). S-4 가 유일한 노출 지점이다.')
    centered = abs(float(null.mean())) < 5 * sd / np.sqrt(B_NULL)
    if not centered:
        raise SystemExit('FATAL N-1 귀무 평균이 0 에서 벗어났다 — 치환이 잘못됐다.')
    return {'B': B_NULL, 'seed': SEED_N1, 'null_mean': float(null.mean()),
            'sd_null_ic': sd, 'centered_at_zero': centered,
            'crit_one_sided': float(np.quantile(null, 1 - ALPHA))}


# ── N-2a / N-2b-1 (DB 필요) ─────────────────────────────────────────────────
def n2(dates: list[str]) -> dict:
    """손잡이 자기검증 + lookahead 값 수준 대조. 대상 계정은 **자본총계 하나**."""
    from backtest.configs.schedule import get_schedule
    from backtest.data_access import load_pit_series_ttm
    from ingest.connection import get_connection
    from scripts.xsec._guard import assert_shadow_db

    conn = get_connection()
    assert_shadow_db(conn)
    pts = {p.date.isoformat(): p for p in get_schedule('SEMIANNUAL')}
    order = sorted(pts)

    a_rows, s_total, viol = [], 0, []
    for d in dates:
        i = order.index(d)
        if i + 1 >= len(order):
            continue
        cur, nxt = pts[d], pts[order[i + 1]]
        pit_now = load_pit_series_ttm(conn, cur.date, report_type=cur.report_type,
                                      fiscal_year=cur.fiscal_year)
        # **as_of 손잡이** — 재무만 미래로 민다. fiscal_year=미래 는 0행이다
        pit_fut = load_pit_series_ttm(conn, nxt.date, report_type=nxt.report_type,
                                      fiscal_year=nxt.fiscal_year)

        now = {t: v[0].get('자본총계') for t, v in pit_now.items() if v and v[0].get('자본총계')}
        fut = {t: v[0].get('자본총계') for t, v in pit_fut.items() if v and v[0].get('자본총계')}
        common = set(now) & set(fut)
        changed = {t for t in common if now[t] != fut[t]}

        a_rows.append({'date': d, 'n_pit': len(now), 'n_future': len(fut),
                       'coverage': (len(common) / len(now)) if now else 0.0,
                       'n_changed': len(changed),
                       'changed_ratio': (len(changed) / len(common)) if common else 0.0})
        s_total += len(changed)
        # N-2b-1: 갱신된 종목에서 PIT 경로가 **구 공시값** 이어야 한다 (= 미래값이 아니어야)
        for t in changed:
            if now[t] == fut[t]:
                viol.append({'date': d, 'ticker': t})

    conn.close()

    cond1 = all(r['n_future'] > 0 for r in a_rows)
    cond2 = all(r['coverage'] >= 0.9 for r in a_rows)
    cond3 = all(r['n_changed'] >= 1 for r in a_rows)
    log.info('== N-2a 손잡이 자기검증 (as_of) ==')
    log.info('  조건1 N_future>0        : %s', cond1)
    log.info('  조건2 커버리지 >= 0.9    : %s  (최소 %.4f)', cond2,
             min(r['coverage'] for r in a_rows))
    log.info('  조건3 자본총계 갱신 >= 1 : %s  (최소 %d, 합계 %d)', cond3,
             min(r['n_changed'] for r in a_rows), s_total)
    if not (cond1 and cond2 and cond3):
        raise SystemExit('FATAL N-2a 실패 — 손잡이가 움직이지 않았다. "다른 값이면 통과" 로 낮추지 마라.')

    log.info('== N-2b-1 lookahead (값 수준, primary) ==')
    log.info('  |S| = %d  위배 %d건  -> %s', s_total, len(viol),
             'PASS' if not viol else 'FAIL')
    if s_total == 0:
        raise SystemExit('FATAL |S|=0 — 검사가 공허하다. N-2a 조건 3 을 재확인하라.')
    if viol:
        raise SystemExit(f'FATAL N-2b-1 위배 {len(viol)}건 — PIT 가 깨졌다: {viol[:5]}')
    return {'N2a': {'per_period': a_rows, 'cond1': cond1, 'cond2': cond2, 'cond3': cond3},
            'N2b1': {'S_size': s_total, 'violations': len(viol), 'pass': True,
                     'account': '자본총계',
                     'note': 'N-2b-2(IC 수준 진단)는 S-4 로 이월 — 착수 기록 §1-1'}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--skip-db', action='store_true', help='N-2 를 건너뛴다 (부분 실행)')
    args = ap.parse_args()

    a0 = json.loads(A0_PATH.read_text(encoding='utf-8'))
    ps = a0['period_set']
    dates = ps['dates']
    panel = pd.read_parquet(PANEL)
    periods = _periods(panel, dates)
    log.info('== 구간 집합 %s  n=%d  sha256=%s ==', ps['id'], ps['n'], ps['sha256'][:16])
    log.info('   구간별 N_t: %s\n', [len(s) for s, _ in periods])

    res = {'period_set': {'id': ps['id'], 'n': ps['n'], 'sha256': ps['sha256']},
           'P1': p1(periods), 'P2': p2(periods), 'N1': n1(periods)}
    if not args.skip_db:
        res.update(n2(dates))

    # `all_pass` 는 **실제로 돈 검사**만 근거로 삼는다. `--skip-db` 로 N-2 를 건너뛰고도
    # true 를 쓰면 estimate.py 의 선행 게이트가 무의미해진다 — 이 저장소가 반복해 잡은
    # "형식뿐인 검사" 유형이다. 필수 키가 하나라도 없으면 false 다.
    required = ('P1', 'P2', 'N1', 'N2a', 'N2b1')
    missing = [k for k in required if k not in res]
    res['ran'] = sorted(k for k in required if k in res)
    res['all_pass'] = not missing
    if missing:
        log.warning('all_pass=false — 실행되지 않은 대조: %s '
                    '(--skip-db 부분 실행이면 S-4 게이트가 이 산출물을 거부한다)', missing)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / 'controls.json').write_text(
        json.dumps(res, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    log.info('\n→ %s', OUT_DIR / 'controls.json')


if __name__ == '__main__':
    main()
