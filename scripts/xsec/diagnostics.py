"""SPEC_15 `S-6` 진단 병기 — **전부 판정 비사용** (v0.4 §11).

판정은 `estimate.py` 의 E1 하나이고, 여기 있는 것은 그 결과를 읽는 사람이
맥락을 잃지 않도록 붙이는 것이다. **어느 것도 판정을 바꾸지 않는다.**

DB 에 접속하지 않는다 — 패널만 읽는다.

실행:
    python -m scripts.xsec.diagnostics
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.xsec import ic as icmod
from scripts.xsec._guard import assert_no_db_imported
from scripts.xsec.controls import _PANEL_REL

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger(__name__)

OUT_DIR = Path('experiments/analysis/2026.08.26._xsec_controls')
A0_PATH = Path('experiments/analysis/2026.08.25._xsec_prelim/a0_period_set.json')


def _panel(layer: str) -> pd.DataFrame:
    rel = _PANEL_REL.replace('_L3R.parquet', f'_{layer}.parquet')
    for c in (Path('experiments') / rel,
              Path('experiments/_baselines/shadow_20260819/experiments') / rel):
        if c.exists():
            return pd.read_parquet(c)
    raise SystemExit(f'FATAL 패널 없음: {rel}')


def _mean_ic(df: pd.DataFrame, dates: list[str]) -> tuple[float, list[int]]:
    per, ns = [], []
    for d in dates:
        r = df[(df['rebalance_date'] == d) & df['fwd_ret'].notna() & df['inv_pbr'].notna()]
        if len(r) < 2:
            continue
        per.append((r['inv_pbr'].to_numpy(float), r['fwd_ret'].to_numpy(float)))
        ns.append(len(r))
    return float(icmod.ic_series(per).mean()), ns


def main() -> None:
    assert_no_db_imported('scripts.xsec.diagnostics')
    dates = json.loads(A0_PATH.read_text(encoding='utf-8'))['period_set']['dates']
    out: dict = {'role': '진단 병기 — 전부 판정 비사용 (v0.4 §11)'}

    # 1. 계층별 IC
    log.info('== 계층별 IC (판정 비사용) ==')
    layers = {}
    for lyr in ('L0', 'L1', 'L2', 'L3', 'L3R'):
        m, ns = _mean_ic(_panel(lyr), dates)
        layers[lyr] = {'mean_ic': m, 'N_t_min': min(ns), 'N_t_median': int(np.median(ns)),
                       'N_t_max': max(ns)}
        log.info('  %-4s mean IC=%+.6f   N_t min/median/max = %d/%d/%d',
                 lyr, m, min(ns), int(np.median(ns)), max(ns))
    out['layer_ic'] = layers
    log.info('  ※ 계층을 내려갈수록 필터가 남긴 것과 랭킹이 고른 것이 섞인다')
    log.info('    (v0.4 §2 교락). primary 는 L3R 하나다.')

    # 2. 유동성 3분위 IC — **이미 절단된 분포 안**이다
    log.info('== 유동성 3분위 IC (판정 비사용) ==')
    p = _panel('L3R')
    terc: dict = {'q1_low': [], 'q2_mid': [], 'q3_high': []}
    for d in dates:
        r = p[(p['rebalance_date'] == d) & p['fwd_ret'].notna() & p['inv_pbr'].notna()
              & p['avg_turnover'].notna()]
        if len(r) < 9:
            continue
        q = pd.qcut(r['avg_turnover'], 3, labels=['q1_low', 'q2_mid', 'q3_high'])
        for k in terc:
            s = r[q == k]
            if len(s) >= 2:
                terc[k].append(icmod.rank_ic(s['inv_pbr'].to_numpy(float),
                                             s['fwd_ret'].to_numpy(float)))
    out['liquidity_tercile_ic'] = {k: {'mean_ic': float(np.mean(v)), 'n_periods': len(v)}
                                   for k, v in terc.items()}
    for k, v in terc.items():
        log.info('  %-8s mean IC=%+.6f  (%d구간)', k, float(np.mean(v)), len(v))
    log.info('  ⚠ **이미 절단된 분포다** — HardFilter 가 L1 에서 일평균 거래대금 1억원 컷을')
    log.info('    건다(hard_filter.py:32). 컷 아래 구간이 패널에 없으므로 "초소형주에서')
    log.info('    IC 가 나오는가" 를 이 설계로는 온전히 답할 수 없다 (v0.4 §9-4).')
    out['liquidity_caveat'] = ('HardFilter 일평균 거래대금 1억원 컷(L1) 아래가 패널에 없다. '
                               '절단된 분포 안에서의 비교이며 판정에 쓰지 않는다.')

    # 3. 구간별 coverage
    l3, l3r = _panel('L3'), _panel('L3R')
    cov = []
    for d in dates:
        a = int((l3['rebalance_date'] == d).sum())
        bb = int((l3r['rebalance_date'] == d).sum())
        cov.append({'date': d, 'L3': a, 'L3R': bb, 'excluded': a - bb})
    out['coverage_by_period'] = cov
    log.info('== 구간별 coverage ==')
    log.info('  L3→L3R 제외 합계 = %d행 (사유는 exclusion_report 참조: '
             'equity_nonpositive 14 / 나머지 0)', sum(c['excluded'] for c in cov))

    # 4. 월간 확장 — **미실행**
    out['monthly_extension'] = {
        'executed': False,
        'reason': ('월간 캘린더가 존재하지 않는다 — CALENDAR_CHOICES = (SEMIANNUAL, A, C). '
                   '월간 패널을 만들려면 캘린더 인프라 신설 + 패널 재생성이 필요하다. '
                   'v0.4 P-1b 에 따라 판정 비사용이므로 판정에는 영향이 없다.'),
        'prohibition': 'P-1b — 반기 primary 가 FAIL 인데 월간이 PASS 인 경우를 PASS 로 보고하지 않는다',
    }
    log.info('== 월간 확장: **미실행** ==')
    log.info('  월간 캘린더가 없다 (CALENDAR_CHOICES = SEMIANNUAL, A, C).')
    log.info('  판정 비사용 항목이라 판정에는 영향이 없다.')

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    q = OUT_DIR / 'diagnostics.json'
    q.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    log.info('→ %s', q)


if __name__ == '__main__':
    main()
