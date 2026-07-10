"""
SPEC_08 §6 Layer 1 — 탐색 그리드. §5 축 전체 조합을 B-0 고정정책(워밍업 이후 전체를
단일 OOS로 취급, v0.3 확정)으로 실행하고 지형표 하나에 전부 깐다.

★ R8: 여기서 argmax를 뽑지 않는다. 이 표의 목적은 "어떤 축이 결과를 실제로 움직이는지"
지형을 보는 것뿐이고, 채택 여부는 walkforward.py(Layer 2)의 사전 고정 구속조건(C1~C4)
통과 여부로만 판단한다.

실행: venv/bin/python -m backtest.regime.grid
"""
from __future__ import annotations

import itertools
import logging
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.regime.config_phaseB import (
    ALT_SLEEVE_GRID,
    K_GRID,
    NORMALIZATION_GRID,
    OVERLAY_FREQ_GRID,
    PERIOD22_SHARE_WARN,
    PHASEB_RUN_ID,
    TILT_OPTION_GRID,
    VARIANTS,
)
from backtest.regime.overlay_engine import run_combo
from ingest.connection import get_connection

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger(__name__)


def all_combinations() -> list[dict]:
    """§5 축 전체 조합. scenario/variant는 VARIANTS(R5: F는 value_spread 단독)로 제약."""
    combos = []
    for scenario, variants in VARIANTS.items():
        for variant in variants:
            for tilt_option, normalization, overlay_freq, alt_sleeve, k in itertools.product(
                TILT_OPTION_GRID, NORMALIZATION_GRID, OVERLAY_FREQ_GRID, ALT_SLEEVE_GRID, K_GRID
            ):
                combos.append(dict(scenario=scenario, variant=variant, tilt_option=tilt_option,
                                    normalization=normalization, overlay_freq=overlay_freq,
                                    alt_sleeve=alt_sleeve, k=k))
    return combos


def _load_combo_returns(conn, run_id: str, cfg_hash: str, scenario: str, variant: str,
                         tilt_option: str) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT date, is_oos, episode_tag, net_port_return, net_base_return
            FROM overlay_returns
            WHERE run_id=%s AND config_hash=%s AND scenario=%s AND variant=%s
              AND tilt_option=%s AND mode='tilt'
            ORDER BY date
            """,
            (run_id, cfg_hash, scenario, variant, tilt_option),
        )
        rows = cur.fetchall()
    cols = ['date', 'is_oos', 'episode_tag', 'net_port_return', 'net_base_return']
    return pd.DataFrame(rows, columns=cols)


def summary_stats(df: pd.DataFrame) -> dict:
    """
    B-0 OOS(워밍업 이후, is_oos=True) 구간만으로 CAGR/MDD/Sharpe/Calmar +
    always-on 대비 개선 + #22 기여도(R6) 계산. overlay_freq에 따라 관측 간격이 다르므로
    (월/분기/반기) 연율화는 실제 관측 수 기준 CAGR로 통일한다.
    """
    oos = df[df['is_oos'] & df['net_base_return'].notna()]
    if oos.empty or len(oos) < 2:
        return {'n_obs': len(oos)}

    net = oos['net_port_return'].astype(float)
    base = oos['net_base_return'].astype(float)
    n_obs = len(net)
    years = (oos['date'].max() - oos['date'].min()).days / 365.25 if n_obs > 1 else np.nan

    total_ret = float((1 + net).prod() - 1)
    cagr = (1 + total_ret) ** (1 / years) - 1 if years and years > 0 else np.nan
    nav = (1 + net).cumprod()
    mdd = float((nav / nav.cummax() - 1).min())
    sharpe = float(net.mean() / net.std()) if net.std() > 0 else np.nan
    calmar = cagr / abs(mdd) if mdd not in (0, np.nan) and not np.isnan(cagr) else np.nan

    base_total_ret = float((1 + base).prod() - 1)
    base_cagr = (1 + base_total_ret) ** (1 / years) - 1 if years and years > 0 else np.nan
    base_nav = (1 + base).cumprod()
    always_on_mdd = float((base_nav / base_nav.cummax() - 1).min())

    alpha = net - base
    total_alpha = float(alpha.sum())
    period22_alpha = float(alpha[oos['episode_tag'] == 'period22'].sum())
    ex22_alpha = total_alpha - period22_alpha
    period22_share = period22_alpha / total_alpha if total_alpha != 0 else np.nan

    return {
        'n_obs': n_obs,
        'net_cagr': cagr, 'net_mdd': mdd, 'net_sharpe': sharpe, 'net_calmar': calmar,
        'always_on_cagr': base_cagr, 'always_on_mdd': always_on_mdd,
        'cagr_improve_vs_always_on': (cagr - base_cagr)
                                     if not (np.isnan(cagr) or np.isnan(base_cagr)) else np.nan,
        'mdd_improve_vs_always_on': (mdd - always_on_mdd),   # mdd는 음수 표기 — 양수면 개선(덜 나쁨)
        'total_alpha': total_alpha, 'ex22_alpha': ex22_alpha, 'period22_share': period22_share,
        'period22_share_warn': bool(not np.isnan(period22_share)
                                     and abs(period22_share) > PERIOD22_SHARE_WARN),
    }


def run(run_id: str = PHASEB_RUN_ID) -> pd.DataFrame:
    combos = all_combinations()
    log.info('Layer 1 그리드: N=%d개 조합 실행 예정 (R8: 다중검정 인지 — argmax 채택 금지)', len(combos))

    conn = get_connection()
    results = []
    try:
        for i, c in enumerate(combos, 1):
            cfg_hash = run_combo(conn, c['scenario'], c['variant'], c['tilt_option'], mode='tilt',
                                  normalization=c['normalization'], overlay_freq=c['overlay_freq'],
                                  alt_sleeve=c['alt_sleeve'], k=c['k'], run_id=run_id)
            df = _load_combo_returns(conn, run_id, cfg_hash, c['scenario'], c['variant'], c['tilt_option'])
            stats = summary_stats(df)
            results.append({**c, 'config_hash': cfg_hash, **stats})
            log.info('[%d/%d] %s', i, len(combos), {**c, **stats})
    finally:
        conn.close()

    result_df = pd.DataFrame(results)
    log.info('Layer 1 완료: N=%d개 조합 (지형 파악용 — 여기서 최댓값을 고르지 않는다, R8)', len(combos))
    return result_df


if __name__ == '__main__':
    df = run()
    out_dir = Path('experiments/runs')
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f'{date.today().isoformat()}_phaseB_grid.csv'
    df.to_csv(out_path, index=False)
    log.info('지형표 저장: %s (N=%d)', out_path, len(df))
