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

from backtest.configs.constants import RF as RF_ANNUAL  # SSOT — 재선언 금지 (값 0.0263 동일)

PERIODS_PER_YEAR = 2         # 반기 리밸런싱
TRADING_DAYS_PER_YEAR = 252  # 일별 지표 연율화 (SPEC_09 §2)


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


# ── SPEC_09 — 일별 NAV 지표 (신규 추가만, 기존 함수 무수정) ──────────────────
# 기존 반기 종점 MDD·Sharpe는 characterization 원칙에 따라 유지되고,
# 리포트에 두 정의를 병기한다 (예: "MDD −30.7% (반기 종점) / −XX.X% (일별)").

def compute_daily_metrics(nav: pd.Series, benchmark: pd.Series | None = None) -> dict:
    """
    일별 NAV 시리즈(index: 날짜 오름차순, 값: NAV 수준)로부터 SPEC_09 §2 지표 계산.

    연율화·경계 규약 (계약):
      - 일별 수익률 r_t = NAV_t/NAV_{t-1} − 1 (단순수익률).
      - daily_vol_ann = std(로그수익률) × √252 (SPEC_09 §2 표 정의).
      - daily_sharpe  = (mean(r_t) × 252 − RF_ANNUAL) / daily_vol_ann.
        RF는 constants.RF SSOT.
      - 월 경계 = 캘린더 월의 마지막 관측일. 양끝 부분월도 포함한다
        (표본 보존 우선 — 부분월 수익률이 짧은 구간에서 계산됨을 리포트에 명기).
      - CVaR 5% (1M): 겹치지 않는 캘린더 월간 수익률의 하위 k개 평균,
        k = int(0.05×n). k < 3이면 k=3으로 대체하고 cvar_1m_fallback=True.
      - CVaR 5% (3M): 월말 NAV의 롤링 3개월(월 단위 스텝, **겹침 허용**) 수익률에
        동일 k 규칙 — 겹침 사용은 지표 정의의 일부다.
      - tracking_error_ann: 공통 관측일의 (전략 − 벤치마크) 일별 수익률
        std × √252. benchmark는 지수 **종가 수준** 시리즈 (수익률 아님).

    반환 dict 키: daily_mdd, mdd_peak_date, mdd_trough_date, daily_vol_ann,
      daily_sharpe, worst_month_return, worst_month, cvar_5pct_1m, cvar_1m_k,
      cvar_1m_fallback, cvar_5pct_3m, cvar_3m_k, n_days, n_months
      (+ benchmark 제공 시 tracking_error_ann).
    """
    nav = nav.dropna().astype(float).copy()
    nav.index = pd.to_datetime(nav.index)
    nav = nav.sort_index()
    if len(nav) < 2:
        raise ValueError(f'일별 NAV 관측치 부족 ({len(nav)}개) — 지표 계산 불가')

    r    = nav.pct_change().dropna()
    logr = np.log(nav).diff().dropna()

    vol_ann = float(logr.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))
    sharpe  = (
        float((r.mean() * TRADING_DAYS_PER_YEAR - RF_ANNUAL) / vol_ann)
        if vol_ann > 0 else 0.0
    )

    # 일별 MDD + 발생 구간 (peak일 ~ trough일)
    peak_series = nav.cummax()
    dd          = nav / peak_series - 1
    mdd         = float(dd.min())
    trough_date = dd.idxmin()
    peak_date   = nav.loc[:trough_date].idxmax()

    # 캘린더 월말 NAV → 월간 수익률 (첫 월은 시리즈 시작값 대비 부분월)
    month_end = nav.groupby(nav.index.to_period('M')).last()
    prev_vals = np.concatenate([[nav.iloc[0]], month_end.values[:-1]])
    monthly_ret = pd.Series(month_end.values / prev_vals - 1, index=month_end.index)

    worst_idx = monthly_ret.idxmin()

    def _cvar(series: pd.Series) -> tuple[float, int, bool]:
        n = len(series)
        k = int(n * 0.05)
        fallback = k < 3
        k = min(max(k, 3), n)
        return float(series.nsmallest(k).mean()), k, fallback

    cvar_1m, k_1m, fb_1m = _cvar(monthly_ret)

    r3m = (month_end / month_end.shift(3) - 1).dropna()
    if len(r3m) > 0:
        cvar_3m, k_3m, _ = _cvar(r3m)
    else:
        cvar_3m, k_3m = float('nan'), 0

    out = {
        'daily_mdd':          mdd,
        'mdd_peak_date':      peak_date.date(),
        'mdd_trough_date':    trough_date.date(),
        'daily_vol_ann':      vol_ann,
        'daily_sharpe':       sharpe,
        'worst_month_return': float(monthly_ret.min()),
        'worst_month':        str(worst_idx),
        'cvar_5pct_1m':       cvar_1m,
        'cvar_1m_k':          k_1m,
        'cvar_1m_fallback':   fb_1m,
        'cvar_5pct_3m':       cvar_3m,
        'cvar_3m_k':          k_3m,
        'n_days':             len(nav),
        'n_months':           len(monthly_ret),
    }

    if benchmark is not None:
        b = benchmark.dropna().astype(float).copy()
        b.index = pd.to_datetime(b.index)
        br = b.sort_index().pct_change().dropna()
        common = r.index.intersection(br.index)
        if len(common) < 2:
            raise ValueError(
                f'벤치마크 공통 관측일 부족 ({len(common)}개) — tracking error 계산 불가'
            )
        diff = r.loc[common] - br.loc[common]
        out['tracking_error_ann'] = float(diff.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))

    return out


def compute_nav_cagr(nav: pd.Series, initial_capital: float = 1.0) -> float:
    """일별 NAV 시리즈의 CAGR (SPEC_12 §5-1 SSOT).

    기준 자본은 nav.iloc[0]이 아니라 initial_capital(기본 1.0) — stitch_periods()의
    첫 net NAV는 이미 거래비용 차감 후라 1보다 작다. nav.iloc[0]을 쓰면 첫 리밸런싱
    거래비용이 CAGR에서 통째로 빠진다. 연수는 실제 캘린더 경과일수(365.25일=1년) 기준
    — compute_cagr()의 CORR-METRIC-002 수정과 동일한 정의를 daily-NAV 경로에도 적용한다.
    """
    nav = nav.dropna().astype(float)
    if len(nav) < 2:
        raise ValueError(f'일별 NAV 관측치 부족 ({len(nav)}개) — CAGR 계산 불가')
    nav.index = pd.to_datetime(nav.index)
    nav = nav.sort_index()
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    if years <= 0:
        raise ValueError(f'NAV 구간 길이가 0 이하 (years={years}) — CAGR 계산 불가')
    return float((nav.iloc[-1] / initial_capital) ** (1 / years) - 1)
