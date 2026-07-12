"""
[O-5] CAGR / Sharpe / MDD 오라클.

CAGR — 진실 기준: **실제 캘린더 경과일수**.
  CAGR = (Π(1+r_i))^(365.25 / 경과일수) − 1
  현행 compute_cagr()는 연수를 len(returns)/2 (구간수÷2)로 계산한다. 리밸런싱 구간은
  4월→8월 ≈ 4.5개월, 8월→4월 ≈ 7.5개월로 균등하지 않으므로 이 관례는 수학적으로 틀렸다.
  (시그니처가 날짜를 받지도 않는다 — 계약 결함.) → CORR-METRIC-002, 의도적 실패 상태.

Sharpe — 규약 명시(이 규약대로면 현행 구현은 옳다):
  excess_i = r_i − RF_ANNUAL/periods_per_year   (산술 분할, 기하 분할 아님)
  Sharpe   = mean(excess) / std(excess, ddof=1) × sqrt(periods_per_year)
  ddof=1(표본 표준편차, pandas 기본값)이 규약이다.

MDD — 규약 명시: **반기 리밸런싱 시점 기준** 누적곡선의 최대 낙폭.
  구간 내부(월별 MTM)의 낙폭은 포착하지 않는다 — 월별 MTM 기준 MDD는 이보다 깊거나 같다.
  (별도 구현 backtest/regime/mtm_monthly.py가 월별 기준을 담당 — 두 정의는 다른 값이다.)
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from backtest.metrics import (
    PERIODS_PER_YEAR,
    RF_ANNUAL,
    compute_cagr,
    compute_mdd,
    compute_sharpe,
)


# ── CAGR ────────────────────────────────────────────────────────────────────────

def test_cagr_uses_actual_calendar_days():
    """
    두 구간: 2020-04-03→2020-08-20 (+10%), 2020-08-20→2021-04-05 (+10%).
    실제 경과 = 2020-04-03 → 2021-04-05 = 367일.
    진실: 1.21^(365.25/367) − 1  (≈ 20.93%)
    현행: 구간수 2 ÷ 2 = 1년 → 21.00% — 경과일수를 무시한다.

    ⚠ 의도적 실패 (CORR-METRIC-002 증거). 수정 전 영향 추정용 차이 기록은
      GAPS.md Pass 0C 절 참조. 고치지 말고 그대로 둘 것.
    """
    period_dates = [date(2020, 4, 3), date(2020, 8, 20)]
    end_of_last  = date(2021, 4, 5)
    returns = pd.Series([0.10, 0.10], index=pd.DatetimeIndex(period_dates))

    elapsed_days = (end_of_last - period_dates[0]).days
    true_cagr = float((1.21) ** (365.25 / elapsed_days) - 1)

    assert compute_cagr(returns) == pytest.approx(true_cagr, abs=1e-6)


def test_cagr_two_equal_periods_current_convention_sanity():
    """
    현행 관례의 산술 자체는 자기일관적이다: 2구간 = 1년 → (1.1×1.1)^1 − 1 = 21%.
    이 테스트는 '관례가 옳다'가 아니라 '관례의 산술이 구현대로인가'를 고정한다.
    (관례 자체의 시비는 위 test_cagr_uses_actual_calendar_days가 가린다.)
    """
    returns = pd.Series([0.10, 0.10],
                        index=pd.DatetimeIndex([date(2020, 4, 3), date(2020, 8, 20)]))
    assert compute_cagr(returns) == pytest.approx(0.21, abs=1e-12)


def test_cagr_empty_returns_zero():
    assert compute_cagr(pd.Series(dtype=float)) == 0.0


# ── Sharpe ──────────────────────────────────────────────────────────────────────

def test_sharpe_follows_stated_convention():
    """규약(모듈 docstring)대로 독립 계산한 값과 일치해야 한다. RF_ANNUAL은 SSOT import."""
    rets = [0.10, -0.05, 0.08, 0.02]
    returns = pd.Series(rets)

    rf_per_period = RF_ANNUAL / PERIODS_PER_YEAR
    excess = np.array(rets) - rf_per_period
    expected = excess.mean() / excess.std(ddof=1) * np.sqrt(PERIODS_PER_YEAR)

    assert compute_sharpe(returns) == pytest.approx(float(expected), rel=1e-12)


def test_sharpe_zero_variance_returns_zero():
    assert compute_sharpe(pd.Series([0.05, 0.05, 0.05])) == 0.0


# ── MDD ─────────────────────────────────────────────────────────────────────────

def test_mdd_hand_computed_case():
    """
    수익률 [+10%, −20%, +5%] → 누적 [1.10, 0.88, 0.924] → 최대낙폭 = 0.88/1.10 − 1 = −0.20.
    """
    returns = pd.Series([0.10, -0.20, 0.05])
    assert compute_mdd(returns) == pytest.approx(-0.20, abs=1e-12)


def test_mdd_monotonic_up_is_zero():
    """계속 오르면 낙폭 0. (compute_mdd 반환 계약: 음수 또는 0)"""
    returns = pd.Series([0.10, 0.05, 0.02])
    assert compute_mdd(returns) == 0.0


def test_mdd_is_semiannual_not_intraperiod():
    """
    규약 문서화: 구간 수익률이 [+50%, −33.333…%]이면 반기 기준 MDD는 −33.33%지만,
    구간 '내부'에서 무슨 일이 있었는지는 이 함수가 알 수 없다.
    이 테스트는 반기 시점 기준이라는 **정의 자체**를 고정한다.
    """
    returns = pd.Series([0.50, -1.0 / 3.0])
    assert compute_mdd(returns) == pytest.approx(-1.0 / 3.0, abs=1e-9)
