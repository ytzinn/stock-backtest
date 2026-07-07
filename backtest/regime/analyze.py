"""
SPEC_07 §7-4 — regime_indicators × strategy_returns_monthly 병합, 관계 분석.

판단은 PRIMARY_SCENARIOS(D_rim_only, F_momentum_rim) 기준. ARCHIVE는 참고 표로만.
진행 중인 구간(#23, is_closed_period=False)은 §9 게이트 판정(G1~G4) 모집단에서 제외하고
대시보드 참고 표시에만 쓴다(v0.3 확정 — period_end가 열린 stub이라 재실행마다 값이
바뀌고 h=6 리드랙 관측치도 아직 형성되지 않는다).

의존성: statsmodels(HAC). requirements.txt에 반영됨.
실행: venv/bin/python -m backtest.regime.analyze
"""
from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.stats import spearmanr

from backtest.regime.config_regime import PRIMARY_SCENARIOS
from ingest.connection import get_connection

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

HORIZONS = (1, 3, 6)
EXCLUDE_PERIOD_STARTS = {date(2025, 8, 20)}   # #22 (G2b) — #23은 is_closed_period=False로 이미 제외됨


# ── 로드 ────────────────────────────────────────────────────────────────────

def load_indicator_df(conn, run_id: str) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT date, indicator, value FROM regime_indicators WHERE run_id = %s ORDER BY date",
            (run_id,),
        )
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=['date', 'indicator', 'value'])
    if df.empty:
        return df
    df['date'] = pd.to_datetime(df['date'])   # psycopg2 DATE→datetime.date, asof()엔 DatetimeIndex 필수
    return df.pivot(index='date', columns='indicator', values='value').sort_index()


def load_monthly_returns(conn, run_id: str, scenario: str) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT date, period_start, period_end, is_closed_period,
                   port_return, largecap_cw_return, rel_vs_large
            FROM strategy_returns_monthly
            WHERE source_run_id = %s AND scenario = %s
            ORDER BY date
            """,
            (run_id, scenario),
        )
        rows = cur.fetchall()
    cols = ['date', 'period_start', 'period_end', 'is_closed_period',
            'port_return', 'largecap_cw_return', 'rel_vs_large']
    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date').sort_index()
    return df


# ── 반기 앵커(구간 레벨) 집계 ─────────────────────────────────────────────────

def period_level_returns(monthly_df: pd.DataFrame) -> pd.DataFrame:
    """월별 행을 period_start 기준으로 묶어 구간 총수익(반기 앵커)으로 압축."""
    if monthly_df.empty:
        return monthly_df

    def _agg(g: pd.DataFrame) -> pd.Series:
        port_total = float((1 + g['port_return']).prod() - 1)
        cw_total = float((1 + g['largecap_cw_return']).prod() - 1)
        return pd.Series({
            'period_end': g['period_end'].iloc[-1],
            'is_closed_period': bool(g['is_closed_period'].iloc[0]),
            'port_total': port_total,
            'cw_total': cw_total,
            'rel_vs_large': port_total - cw_total,
        })

    out = monthly_df.groupby('period_start').apply(_agg, include_groups=False)
    out.index.name = 'period_start'
    return out.sort_index()


# ── G1: 리드-랙 HAC 회귀 ─────────────────────────────────────────────────────

def _hac_regression(x: pd.Series, y: pd.Series, maxlags: int = 6) -> dict | None:
    merged = pd.concat([x.rename('x'), y.rename('y')], axis=1).dropna()
    if len(merged) < 10:
        return None
    X = sm.add_constant(merged['x'])
    model = sm.OLS(merged['y'], X).fit(cov_type='HAC', cov_kwds={'maxlags': maxlags})
    rho, pval = spearmanr(merged['x'], merged['y'])
    return {
        'n': len(merged),
        'coef': float(model.params['x']),
        'hac_tstat': float(model.tvalues['x']),   # 참고조건(G1c) — 통과 판정에 미사용
        'spearman_rho': float(rho),
        'spearman_p': float(pval),
    }


def lead_lag_table(indicator_series: pd.Series, monthly_rel: pd.Series,
                    is_closed: pd.Series, horizons=HORIZONS) -> dict[int, dict | None]:
    """
    indicator_t vs rel_vs_large_{t+h}. h개월 뒤 관측치가 진행 중인 구간(#23)에 걸치면
    그 행은 자동으로 NaN 정렬 제외된다(닫힌 구간만 대상으로 사전 필터링해서 넘길 것).

    ★ 반드시 달력 기준(월 단위) 오프셋으로 조회한다 — 행 위치 기준 shift(-h)는 안 된다.
    strategy_returns_monthly에는 반기 구간 종료 stub(월말이 아닌 관측일)이 섞여 있어
    행 하나가 항상 1개월을 의미하지 않는다(SPEC_07 §7-2 stub). asof()로 t+h월 시점
    이하 최신값을 찾으면 stub로 인한 달력-행 간 어긋남 없이 h가 실제 개월 수를 의미한다.
    """
    closed_rel = monthly_rel.where(is_closed).dropna()
    result = {}
    for h in horizons:
        shifted = pd.Series({
            d: closed_rel.asof(pd.Timestamp(d) + pd.DateOffset(months=h))
            for d in closed_rel.index
        })
        result[h] = _hac_regression(indicator_series, shifted)
    return result


# ── G1b·G3: hot/cold 분류 ────────────────────────────────────────────────────

def hot_cold_split(period_df: pd.DataFrame, quartile: float = 0.25) -> tuple[pd.Index, pd.Index]:
    """닫힌 21개 구간만 대상으로 rel_vs_large 상하위 quartile 분류."""
    closed = period_df[period_df['is_closed_period']]
    ranked = closed['rel_vs_large'].sort_values()
    n = len(ranked)
    cut = max(1, int(round(n * quartile)))
    cold = ranked.index[:cut]
    hot = ranked.index[-cut:]
    return hot, cold


def hot_cold_indicator_medians(indicator_series: pd.Series, period_starts: pd.DatetimeIndex,
                                hot: pd.Index, cold: pd.Index) -> tuple[float | None, float | None]:
    """각 구간 시작일 시점(asof, 룩어헤드 없음)의 지표값으로 hot/cold 중앙값 비교."""
    def _asof_values(idx):
        vals = [indicator_series.asof(pd.Timestamp(d)) for d in idx]
        return [v for v in vals if pd.notna(v)]

    hot_vals, cold_vals = _asof_values(hot), _asof_values(cold)
    hot_med = float(np.median(hot_vals)) if hot_vals else None
    cold_med = float(np.median(cold_vals)) if cold_vals else None
    return hot_med, cold_med


# ── G2·G2b: 반기 앵커 회귀 (+ #22 제외) ───────────────────────────────────────

def anchor_regression(indicator_series: pd.Series, period_df: pd.DataFrame,
                       exclude: set[date] = frozenset()) -> dict | None:
    closed = period_df[period_df['is_closed_period']].copy()
    if exclude:
        closed = closed[~closed.index.map(lambda d: d in exclude)]
    x = pd.Series({d: indicator_series.asof(pd.Timestamp(d)) for d in closed.index})
    merged = pd.concat([x.rename('x'), closed['rel_vs_large'].rename('y')], axis=1).dropna()
    if len(merged) < 5:
        return None
    X = sm.add_constant(merged['x'])
    model = sm.OLS(merged['y'], X).fit()
    return {'n': len(merged), 'coef': float(model.params['x']), 'tstat': float(model.tvalues['x'])}


# ── family 상관행렬 ──────────────────────────────────────────────────────────

def family_correlation(indicator_df: pd.DataFrame) -> pd.DataFrame:
    cols = [c for c in ('value_spread', 'size_val_gap', 'illiq_discount') if c in indicator_df.columns]
    return indicator_df[cols].corr()


# ── 시나리오별 종합 ──────────────────────────────────────────────────────────

def analyze_scenario(conn, indicators_run_id: str, mtm_run_id: str, scenario: str) -> dict:
    indicator_df = load_indicator_df(conn, indicators_run_id)
    monthly = load_monthly_returns(conn, mtm_run_id, scenario)
    if indicator_df.empty or monthly.empty or 'value_spread' not in indicator_df.columns:
        log.warning('%s: 지표(value_spread 포함) 또는 수익률 데이터 없음 (indicators_run_id=%s, mtm_run_id=%s)',
                     scenario, indicators_run_id, mtm_run_id)
        return {'scenario': scenario, 'ok': False}

    value_spread = indicator_df['value_spread']
    size_mom = indicator_df.get('size_mom_6m')

    period_df = period_level_returns(monthly)
    is_closed = monthly['is_closed_period'].astype(bool)

    g1 = lead_lag_table(value_spread, monthly['rel_vs_large'], is_closed)
    hot, cold = hot_cold_split(period_df)
    g1b_hot, g1b_cold = hot_cold_indicator_medians(value_spread, period_df.index, hot, cold)
    g2 = anchor_regression(value_spread, period_df)
    g2b = anchor_regression(value_spread, period_df, exclude=EXCLUDE_PERIOD_STARTS)
    g3_hot, g3_cold = (None, None)
    if size_mom is not None:
        g3_hot, g3_cold = hot_cold_indicator_medians(size_mom, period_df.index, hot, cold)

    return {
        'scenario': scenario,
        'ok': True,
        'g1_lead_lag': g1,
        'g1b_hot_median': g1b_hot,
        'g1b_cold_median': g1b_cold,
        'g1b_pass': (g1b_hot is not None and g1b_cold is not None and g1b_hot > g1b_cold),
        'g2': g2,
        'g2b_ex22': g2b,
        'g2_pass': (g2 is not None and g2['coef'] > 0),
        'g2b_pass': (g2b is not None and g2b['coef'] > 0),
        'g3_hot_median': g3_hot,
        'g3_cold_median': g3_cold,
        'g3_pass': (g3_hot is not None and g3_cold is not None and g3_hot > g3_cold),
        'n_closed_periods': int(period_df['is_closed_period'].sum()),
        'n_open_periods': int((~period_df['is_closed_period']).sum()),
    }


def run_analysis(indicators_run_id: str, mtm_run_id: str) -> dict:
    conn = get_connection()
    try:
        indicator_df = load_indicator_df(conn, indicators_run_id)
        fam_corr = family_correlation(indicator_df)
        log.info('저평가 family 상관행렬:\n%s', fam_corr)

        results = {}
        for scenario in PRIMARY_SCENARIOS:
            res = analyze_scenario(conn, indicators_run_id, mtm_run_id, scenario)
            results[scenario] = res
            if res.get('ok'):
                log.info('[%s] G1(리드랙): %s', scenario, res['g1_lead_lag'])
                log.info('[%s] G1b hot=%.4f cold=%.4f pass=%s', scenario,
                          res['g1b_hot_median'] or float('nan'), res['g1b_cold_median'] or float('nan'),
                          res['g1b_pass'])
                log.info('[%s] G2 coef=%s pass=%s / G2b(ex#22) coef=%s pass=%s', scenario,
                          res['g2'], res['g2_pass'], res['g2b_ex22'], res['g2b_pass'])
                log.info('[%s] G3(size_mom_6m) hot=%s cold=%s pass=%s', scenario,
                          res['g3_hot_median'], res['g3_cold_median'], res['g3_pass'])
        return {'family_corr': fam_corr, 'scenarios': results}
    finally:
        conn.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--indicators-run-id', required=True)
    parser.add_argument('--mtm-run-id', default='mtm_v1')
    args = parser.parse_args()
    run_analysis(args.indicators_run_id, args.mtm_run_id)
