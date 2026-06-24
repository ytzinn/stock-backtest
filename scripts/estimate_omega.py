"""
Ohlson(1995) 초과이익 지속성 ω 추정 스크립트 — 1회 실행용.

모델: ri_over_b_t = alpha + omega × ri_over_b_{t-1} + epsilon
      ri_over_b = adjROE - r  (초과ROE, 단위 통일을 위해 equity로 정규화)

결과 해석:
  CI가 0.6을 포함하면  → constants.py OMEGA=0.62 그대로 사용
  CI가 0.6을 포함 안 함 → 추정값으로 교체 또는 두 값 모두 ablation 비교
  추정 ω >= 1           → 데이터 이상. winsorize 범위 좁혀 재실행

실행:
  cd /opt/stock-backtest && venv/bin/python -m scripts.estimate_omega
"""
import sys

import numpy as np
import pandas as pd
from scipy import stats

from backtest.configs.constants import RK, RF

r = RK  # β=1.0 고정, beta_adj=0


def _load_panel(conn) -> pd.DataFrame:
    """financials_pit에서 FY·CFS 기준 NI·CFO·equity 피벗."""
    sql = """
        WITH ranked AS (
            SELECT
                ticker,
                year,
                account_nm,
                amount,
                ROW_NUMBER() OVER (
                    PARTITION BY ticker, year, account_nm
                    ORDER BY available_from DESC
                ) AS rn
            FROM financials_pit
            WHERE report_type = 'FY'
              AND fs_div      = 'CFS'
              AND year        >= 2015
              AND amount      IS NOT NULL
        )
        SELECT
            ticker,
            year,
            MAX(CASE WHEN account_nm = '당기순이익'         AND rn = 1 THEN amount END) AS ni,
            MAX(CASE WHEN account_nm = '영업활동현금흐름'   AND rn = 1 THEN amount END) AS cfo,
            COALESCE(
                MAX(CASE WHEN account_nm = '지배기업소유주지분'   AND rn = 1 THEN amount END),
                MAX(CASE WHEN account_nm = '지배기업소유주지분_1' AND rn = 1 THEN amount END),
                MAX(CASE WHEN account_nm = '자본총계'             AND rn = 1 THEN amount END)
            ) AS equity
        FROM ranked
        GROUP BY ticker, year
        HAVING
            MAX(CASE WHEN account_nm = '당기순이익'       AND rn = 1 THEN amount END) IS NOT NULL
            AND MAX(CASE WHEN account_nm = '영업활동현금흐름' AND rn = 1 THEN amount END) IS NOT NULL
    """
    return pd.read_sql(sql, conn)


def estimate_omega(winsorize_pct: float = 0.01) -> tuple[float, tuple[float, float], int]:
    from ingest.connection import get_connection

    with get_connection() as conn:
        df = _load_panel(conn)

    df = df[df['equity'].notna() & (df['equity'] > 0)].copy()
    df['adj_roe']    = (0.5 * df['ni'] + 0.5 * df['cfo']) / df['equity']
    df['ri_over_b']  = df['adj_roe'] - r

    df = df.sort_values(['ticker', 'year'])
    df['ri_over_b_lag'] = df.groupby('ticker')['ri_over_b'].shift(1)
    df = df.dropna(subset=['ri_over_b', 'ri_over_b_lag'])

    # winsorize
    for col in ['ri_over_b', 'ri_over_b_lag']:
        lo = df[col].quantile(winsorize_pct)
        hi = df[col].quantile(1 - winsorize_pct)
        df = df[(df[col] >= lo) & (df[col] <= hi)]

    n = len(df)
    if n < 50:
        print(f"[경고] 데이터 부족 (N={n}). winsorize_pct 완화 후 재실행 권장.")
        sys.exit(1)

    slope, intercept, r_val, p_val, se = stats.linregress(
        df['ri_over_b_lag'], df['ri_over_b']
    )
    omega_est = slope
    ci = (slope - 1.96 * se, slope + 1.96 * se)

    print(f"추정 ω    : {omega_est:.4f}")
    print(f"95% CI    : ({ci[0]:.4f}, {ci[1]:.4f})")
    print(f"R²        : {r_val**2:.4f}")
    print(f"N         : {n}")
    print(f"intercept : {intercept:.4f}")
    print()
    if ci[0] <= 0.62 <= ci[1]:
        print("✓ 초기값 0.62가 CI 내 포함 → constants.py OMEGA 유지")
    else:
        direction = "낮음" if omega_est < 0.62 else "높음"
        print(f"✗ 초기값 0.62가 CI 밖 → 추정값({omega_est:.4f})이 0.62보다 {direction}")
        print("  → constants.py OMEGA를 추정값으로 업데이트하거나 두 값 모두 ablation 비교 권장")

    if omega_est >= 1.0:
        print("[경고] ω >= 1: 데이터 이상 가능성. winsorize_pct를 0.005로 줄여 재실행.")

    return omega_est, ci, n


if __name__ == '__main__':
    estimate_omega()
