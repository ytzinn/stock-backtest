"""
백테스트 성과 측정.

지표 정의 (MASTER.md §3-4):
  - 알파: 전략 CAGR − KOSPI CAGR (단순 차이, 배당 미반영)
  - Robustness: 21개 구간 중 KOSPI 대비 Alpha 양수 비율
  - MDD: 최대 낙폭 (누적 수익률 기준)
  - Sharpe: (전략 수익률 − RF) / 표준편차 × sqrt(기간수/년)
  - 수익률 기준: adj_close 수정주가, 배당 미반영
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RF_ANNUAL = 0.0263   # 무위험수익률 연간
PERIODS_PER_YEAR = 2  # 반기 리밸런싱


def compute_period_returns(
    period_results: list[dict],
) -> pd.Series:
    """리밸런싱 구간별 수익률 Series (인덱스: rebalance_date)."""
    dates   = [r['rebalance_date'] for r in period_results]
    returns = [r['period_return']  for r in period_results]
    return pd.Series(returns, index=pd.DatetimeIndex(dates))


def compute_cagr(
    returns: pd.Series,
    periods_per_year: int = PERIODS_PER_YEAR,
    *,
    start_date=None,
    end_date=None,
) -> float:
    """
    연복리 수익률(CAGR).

    start_date·end_date(구간 전체의 실제 캘린더 경계)가 주어지면 **실제 경과일수 기준**
    (365.25일 = 1년)으로 연수를 계산한다 — 리밸런싱 구간은 4월→8월(≈4.5개월)과
    8월→4월(≈7.5개월)로 균등하지 않으므로 이것이 정확한 정의다 (CORR-METRIC-002).
    날짜 미제공 시 구간수 ÷ periods_per_year 근사(레거시 관례)로 동작한다.
    """
    if returns.empty:
        return 0.0
    total = (1 + returns).prod()
    if start_date is not None and end_date is not None:
        years = (end_date - start_date).days / 365.25
    else:
        years = len(returns) / periods_per_year
    return float(total ** (1 / years) - 1) if years > 0 else 0.0


def compute_sharpe(returns: pd.Series, periods_per_year: int = PERIODS_PER_YEAR) -> float:
    """연환산 Sharpe Ratio."""
    if returns.empty or returns.std() == 0:
        return 0.0
    rf_per_period = RF_ANNUAL / periods_per_year
    excess = returns - rf_per_period
    return float(excess.mean() / excess.std() * np.sqrt(periods_per_year))


def compute_mdd(returns: pd.Series) -> float:
    """최대 낙폭(MDD). 음수 값."""
    if returns.empty:
        return 0.0
    cum = (1 + returns).cumprod()
    rolling_max = cum.cummax()
    dd  = cum / rolling_max - 1
    return float(dd.min())


def compute_robustness(strategy_returns: pd.Series, benchmark_returns: pd.Series) -> float:
    """구간별 Alpha 양수 비율 (Robustness)."""
    if strategy_returns.empty:
        return 0.0
    alpha_per_period = strategy_returns.values - benchmark_returns.reindex(
        strategy_returns.index, fill_value=0.0
    ).values
    return float((alpha_per_period > 0).mean())


def compute_metrics(
    strategy_returns:  pd.Series,
    benchmark_returns: pd.Series,
    periods_per_year:  int = PERIODS_PER_YEAR,
    *,
    start_date=None,
    end_date=None,
) -> dict:
    """
    전략 성과 지표 종합 계산.

    반환: {cagr, sharpe, mdd, alpha, robustness, benchmark_cagr}
    start_date/end_date 제공 시 CAGR는 실제 캘린더 경과일수 기준 (compute_cagr 참조).
    """
    strat_cagr = compute_cagr(strategy_returns, periods_per_year,
                              start_date=start_date, end_date=end_date)
    bench_cagr = compute_cagr(benchmark_returns, periods_per_year,
                              start_date=start_date, end_date=end_date)

    return {
        'cagr':            strat_cagr,
        'sharpe':          compute_sharpe(strategy_returns, periods_per_year),
        'mdd':             compute_mdd(strategy_returns),
        'alpha':           strat_cagr - bench_cagr,
        'robustness':      compute_robustness(strategy_returns, benchmark_returns),
        'benchmark_cagr':  bench_cagr,
        'n_periods':       len(strategy_returns),
    }
