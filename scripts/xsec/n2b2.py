"""SPEC_15 `N-2b-2` — lookahead **IC 수준 진단**. `S-3` 에서 `S-4` 로 이월된 항목.

이월 사유: `IC(PIT)` 가 곧 실제 신호의 IC 라 `S-3` 에서 돌리면 §6-1 방화벽이 막으려는
값을 방출하게 된다 (착수 기록 `2e78675` §1-1). 본 측정이 끝난 지금은 그 제약이 없다.

> **판정 비사용이다.** v0.4 §5-3 이 `N-2b-1`(값 수준)을 primary 로, 이것을 진단으로
> 지정했고, §8 금지사항 9 가 **이 결과로 `N-2b-1` 판정을 뒤집는 것**을 금지한다.
> `N-2b-1` 은 이미 통과했다 (|S|=198, 룩어헤드 0).
>
> **`IC(future) ≈ IC(PIT)` 여도 FAIL 이 아니다.** 교락 때문이다 — (a) PIT 가 깨졌거나
> (b) 미래 재무가 예측력을 더 주지 않거나, IC 비교만으로는 둘을 가를 수 없다.
> 그래서 v0.4 가 값 수준 검사를 primary 로 올렸다.

**산출물을 `estimate.py` 와 분리한다** — 판정 비사용임을 산출물 수준에서도 드러낸다.

실행 (섀도우 워크트리):
    /opt/stock-backtest/venv/bin/python -m scripts.xsec.n2b2
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.xsec import ic as icmod
from scripts.xsec.controls import PANEL
from scripts.xsec._guard import assert_shadow_db

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger(__name__)

OUT_DIR = Path('experiments/analysis/2026.08.26._xsec_controls')
A0_PATH = Path('experiments/analysis/2026.08.25._xsec_prelim/a0_period_set.json')


def main() -> None:
    from backtest.configs.schedule import get_schedule
    from backtest.data_access import load_pit_series_ttm
    from ingest.connection import get_connection

    conn = get_connection()
    assert_shadow_db(conn)

    ps = json.loads(A0_PATH.read_text(encoding='utf-8'))['period_set']
    panel = pd.read_parquet(PANEL)
    pts = {p.date.isoformat(): p for p in get_schedule('SEMIANNUAL')}
    order = sorted(pts)

    pit_per, fut_per, rows = [], [], []
    for d in ps['dates']:
        i = order.index(d)
        if i + 1 >= len(order):
            continue
        nxt = pts[order[i + 1]]
        r = panel[(panel['rebalance_date'] == d) & panel['fwd_ret'].notna()
                  & panel['inv_pbr'].notna()]
        # 미래 재무만 민다. **시가총액은 rebal_date 고정** — 분모까지 밀면
        # "미래 재무" 가 아니라 "다른 시점의 다른 지표" 가 된다.
        pf = load_pit_series_ttm(conn, nxt.date, report_type=nxt.report_type,
                                 fiscal_year=nxt.fiscal_year)
        fut_eq = {t: v[0].get('자본총계') for t, v in pf.items() if v and v[0].get('자본총계')}

        keep = r[r['ticker'].isin(fut_eq)]
        if len(keep) < 2:
            continue
        y = keep['fwd_ret'].to_numpy(float)
        sig_pit = keep['inv_pbr'].to_numpy(float)
        mc = keep['market_cap'].to_numpy(float)
        sig_fut = np.array([fut_eq[t] for t in keep['ticker']], dtype=float) / mc

        pit_per.append((sig_pit, y))
        fut_per.append((sig_fut, y))
        rows.append({'date': d, 'n': int(len(keep)),
                     'ic_pit': icmod.rank_ic(sig_pit, y),
                     'ic_future': icmod.rank_ic(sig_fut, y)})
    conn.close()

    ic_pit = float(np.mean([r['ic_pit'] for r in rows]))
    ic_fut = float(np.mean([r['ic_future'] for r in rows]))
    log.info('== N-2b-2 (IC 수준 진단 — **판정 비사용**) ==')
    log.info('  대조 구간 %d개 (공통 종목만)', len(rows))
    log.info('  mean IC(PIT)    = %.6f', ic_pit)
    log.info('  mean IC(future) = %.6f', ic_fut)
    log.info('  방향: IC(future) %s IC(PIT)   차이 %+.6f',
             '>' if ic_fut > ic_pit else ('<' if ic_fut < ic_pit else '='), ic_fut - ic_pit)
    log.info('  ※ 역전·동일이어도 FAIL 이 아니다 — 교락 때문이다.')
    log.info('    primary 는 N-2b-1(값 수준)이고 이미 통과했다. 이 결과로 뒤집지 않는다.')

    out = {'role': '진단 (판정 비사용)', 'primary_is': 'N-2b-1 (값 수준, S-3 에서 PASS)',
           'deferred_from': 'S-3 → S-4 (착수 기록 2e78675 §1-1)',
           'n_periods': len(rows), 'mean_ic_pit': ic_pit, 'mean_ic_future': ic_fut,
           'difference': ic_fut - ic_pit,
           'direction': 'future > pit' if ic_fut > ic_pit else 'future <= pit',
           'per_period': rows,
           'caveat': ('IC(future) ≈ IC(PIT) 는 (a) PIT 파손과 (b) 미래 재무 무예측력을 '
                      '가르지 못한다. 이 교락 때문에 v0.4 가 값 수준을 primary 로 올렸다.'),
           'note_prohibition': 'v0.4 §8 금지 9 — 이 결과로 N-2b-1 판정을 뒤집지 않는다'}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / 'n2b2_diagnostic.json'
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    log.info('→ %s', p)


if __name__ == '__main__':
    main()
