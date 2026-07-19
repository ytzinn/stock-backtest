"""
SPEC_09 N-3 — 일별 NAV 엔진 oracle 테스트 (합성 케이스 손계산 대조).

daily_nav의 순수 함수(build_price_panel / compute_nav_path / stitch_periods)와
metrics.compute_daily_metrics를 DB 없이 검증한다. 상수는 전부 SSOT import —
테스트 내 재하드코딩 금지 (CLAUDE.md 영구 규칙).

구 tests/regime/test_mtm_monthly.py의 NAV 불변식 3건(고정수량·haircut 1회 동결·
066110 실측)은 SPEC_09 SSOT 이관에 따라 이 파일로 이식됐다 (2026-07-19).
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from backtest.daily_nav import build_price_panel, compute_nav_path, stitch_periods
from backtest.engine import DELISTING_HAIRCUT
from backtest.metrics import compute_daily_metrics, compute_mdd


def _dates(n: int, start_day: int = 1, year: int = 2020, month: int = 6) -> list[date]:
    return [date(year, month, start_day + i) for i in range(n)]


def _panel(entry_date, obs_dates, raw, entry_prices):
    return build_price_panel(
        {t: pd.Series(s) for t, s in raw.items()}, entry_prices, entry_date, obs_dates
    )


# ── 케이스 1: 3종목 × 10일 손계산 (NAV·MDD) ─────────────────────────────────

def test_three_stocks_ten_days_hand_calculated():
    d0 = date(2020, 6, 1)
    obs = _dates(10, start_day=2)
    entry = {'A': 100.0, 'B': 200.0, 'C': 50.0}
    raw = {
        'A': dict(zip(obs, [110, 120, 130, 120, 110, 100, 90, 100, 110, 120])),
        'B': dict(zip(obs, [190, 180, 170, 180, 190, 200, 210, 220, 210, 200])),
        'C': dict(zip(obs, [55, 60, 50, 45, 40, 50, 55, 60, 65, 70])),
    }
    weights = {'A': 1 / 3, 'B': 1 / 3, 'C': 1 / 3}
    panel = _panel(d0, obs, raw, entry)

    nav, values = compute_nav_path(weights, entry, panel)

    # 손계산: NAV_t = (A_t/100 + B_t/200 + C_t/50) / 3
    expected = [1.05, 1.1, 1.05, 1.0, 0.95, 1.0, 3.05 / 3, 1.1, 1.15, 1.2]
    for i, e in enumerate(expected):
        assert nav.iloc[i] == pytest.approx(e, abs=1e-12)

    # 종목별 기여 (d1): A 0.011/주 × 110... = w/3 반영 값
    assert values['A'].iloc[0] == pytest.approx(1.1 / 3, abs=1e-12)
    assert values['B'].iloc[0] == pytest.approx(0.95 / 3, abs=1e-12)

    # 일별 MDD 손계산: peak 1.1(d2) → trough 0.95(d5), dd = 0.95/1.1 − 1 = −3/22
    full = pd.concat([pd.Series({d0: 1.0}), nav])
    m = compute_daily_metrics(full)
    assert m['daily_mdd'] == pytest.approx(0.95 / 1.1 - 1, abs=1e-12)
    assert m['mdd_peak_date'] == obs[1]
    assert m['mdd_trough_date'] == obs[4]


# ── 케이스 2: 구간 중 상폐 — haircut 적용 시점·금액 손계산 ───────────────────

def test_mid_period_delisting_haircut_and_freeze():
    """
    CONTRACT-NAV-002: 상폐 감지 관측일에 직전 종가 × DELISTING_HAIRCUT 적용 후
    구간말까지 동결. 상폐 이후의 잘못된 가격 행(스트레이)이 있어도 동결값 유지
    (구 mtm 테스트의 '반복 청산 금지'를 더 강한 형태로 검증).
    """
    d0 = date(2020, 6, 1)
    obs = _dates(5, start_day=2)
    entry = {'A': 100.0, 'B': 100.0}
    raw = {
        'A': {d: 100.0 for d in obs},
        # B: d1=90, d2=80 거래 후 d3부터 상폐. d4에 스트레이 행(999) 주입.
        'B': {obs[0]: 90.0, obs[1]: 80.0, obs[3]: 999.0},
    }
    weights = {'A': 0.5, 'B': 0.5}
    panel = _panel(d0, obs, raw, entry)

    nav, _ = compute_nav_path(weights, entry, panel, delisted_from={'B': obs[2]})

    assert nav.iloc[0] == pytest.approx(0.5 + 0.5 * 0.90, abs=1e-12)      # 0.95
    assert nav.iloc[1] == pytest.approx(0.5 + 0.5 * 0.80, abs=1e-12)      # 0.90
    frozen = 0.5 + 0.5 * 0.80 * DELISTING_HAIRCUT                          # 0.78
    assert nav.iloc[2] == pytest.approx(frozen, abs=1e-12)
    assert nav.iloc[3] == pytest.approx(frozen, abs=1e-12)   # 스트레이 999 무시
    assert nav.iloc[4] == pytest.approx(frozen, abs=1e-12)

    # 구간말 값 == engine._calc_period_return의 기준 시나리오 손계산 (G-NAV-1 취지)
    engine_gross = 0.5 * (100 / 100 - 1) + 0.5 * (80 * DELISTING_HAIRCUT / 100 - 1)
    assert nav.iloc[-1] - 1.0 == pytest.approx(engine_gross, abs=1e-12)


def test_delisted_haircut_matches_066110_real_case():
    """(구 mtm 이식) 066110 한프 실측: 진입 69 → 청산 69×0.70=48.3 → −30.00%."""
    d0 = date(2022, 4, 5)
    obs = [date(2022, 8, 18)]
    panel = _panel(d0, obs, {}, {'066110': 69.0})
    nav, _ = compute_nav_path({'066110': 1.0}, {'066110': 69.0}, panel,
                              delisted_from={'066110': date(2022, 6, 1)})
    assert nav.iloc[-1] - 1.0 == pytest.approx(-0.30, abs=1e-6)


# ── 케이스 3: 거래정지 forward-fill ──────────────────────────────────────────

def test_suspension_forward_fill():
    """CONTRACT-NAV-001: 정지 기간(가격 행 없음)은 직전 종가로 동결."""
    d0 = date(2020, 6, 1)
    obs = _dates(5, start_day=2)
    raw = {'A': {obs[0]: 110.0, obs[4]: 120.0}}   # d2~d4 정지 (행 없음)
    panel = _panel(d0, obs, raw, {'A': 100.0})
    nav, _ = compute_nav_path({'A': 1.0}, {'A': 100.0}, panel)
    assert list(nav.round(12)) == [1.1, 1.1, 1.1, 1.1, 1.2]


# ── 케이스 4: 고정수량 불변식 (구 mtm 이식) ──────────────────────────────────

def test_nav_path_is_fixed_shares_not_rebalanced_per_observation():
    """
    ★ 핵심 회귀 (SPEC_07 §7-2 이식) — '초기 수량 고정' 검증.
    A: 100→200→100 (+100%, −50%), B: 100 고정. 고정수량 50/50이면 원금 복귀(0%).
    관측일마다 재조정했다면 1.5×(1+0.5×(−0.5))=1.125로 달랐을 것.
    """
    d0 = date(2020, 4, 3)
    obs = [date(2020, 5, 31), date(2020, 8, 20)]
    entry = {'A': 100.0, 'B': 100.0}
    raw = {'A': {obs[0]: 200.0, obs[1]: 100.0}, 'B': {obs[0]: 100.0, obs[1]: 100.0}}
    panel = _panel(d0, obs, raw, entry)
    nav, _ = compute_nav_path({'A': 0.5, 'B': 0.5}, entry, panel)

    assert nav.iloc[0] == pytest.approx(1.5)
    assert nav.iloc[1] == pytest.approx(1.0, abs=1e-9)          # 원금 복귀
    assert nav.iloc[1] - 1.0 != pytest.approx(0.125)            # 재조정 반례와 상이


def test_missing_entry_price_renormalizes_remaining_weights():
    """진입가 없는 종목 제외 시 잔여 종목 재정규화 — NAV 1.0 미만 출발 금지."""
    d0 = date(2020, 6, 1)
    obs = _dates(2, start_day=2)
    entry = {'A': 100.0}                       # B는 진입가 없음 → 사전 제외 상태
    raw = {'A': {obs[0]: 100.0, obs[1]: 110.0}}
    panel = _panel(d0, obs, raw, entry)
    nav, _ = compute_nav_path({'A': 0.5, 'B': 0.5}, entry, panel)
    assert nav.iloc[0] == pytest.approx(1.0, abs=1e-12)         # 자본 증발 없음
    assert nav.iloc[1] == pytest.approx(1.1, abs=1e-12)


# ── 케이스 5: 리밸런싱 경계 비용 차감 (stitch, G-NAV-2/4 축소판) ─────────────

def test_stitch_deducts_cost_multiplicatively_at_rebalance():
    r1, d1, d2, d3 = date(2020, 1, 2), date(2020, 1, 3), date(2020, 4, 1), date(2020, 4, 2)
    periods = [
        {'rebalance_date': r1, 'obs_dates': [d1, d2], 'nav_path': [1.1, 1.2],
         'transaction_cost': 0.01},
        {'rebalance_date': d2, 'obs_dates': [d3], 'nav_path': [1.05],
         'transaction_cost': 0.02},
    ]
    df = stitch_periods(periods)

    # gross: 경로 그대로 복리
    assert df.loc[r1, 'nav_gross'] == pytest.approx(1.0)
    assert df.loc[d2, 'nav_gross'] == pytest.approx(1.2)
    assert df.loc[d3, 'nav_gross'] == pytest.approx(1.26)

    # net: 리밸런싱일 승법 차감 (CONTRACT-NAV-003)
    assert df.loc[r1, 'nav_net'] == pytest.approx(0.99)
    assert df.loc[d1, 'nav_net'] == pytest.approx(0.99 * 1.1)
    # 경계일(d2)은 리밸런싱 후 상태 한 행: 0.99×1.2×(1−0.02)
    assert df.loc[d2, 'nav_net'] == pytest.approx(0.99 * 1.2 * 0.98, abs=1e-15)
    assert df.loc[d3, 'nav_net'] == pytest.approx(0.99 * 1.2 * 0.98 * 1.05, abs=1e-15)

    # G-NAV-2 축소판: 경계 차감 비율 == 기록 tc
    net_before = df.loc[d1, 'nav_net'] / 1.1 * 1.2
    assert 1.0 - df.loc[d2, 'nav_net'] / net_before == pytest.approx(0.02, abs=1e-12)

    # G-NAV-4 계약: 구간 복리 net = gross − tc − gross×tc (교차항 명시)
    p2_net = 1.05 * (1 - 0.02) - 1
    assert p2_net == pytest.approx(0.05 - 0.02 - 0.05 * 0.02, abs=1e-15)

    # 경계일 중복 행 없음
    assert df.index.is_unique


# ── 케이스 6: 일별 MDD ≥ 반기 종점 MDD 불변식 (G-NAV-3) ─────────────────────

def test_daily_mdd_at_least_endpoint_mdd():
    d0 = date(2020, 6, 1)
    obs = _dates(10, start_day=2)
    entry = {'A': 100.0, 'B': 200.0, 'C': 50.0}
    raw = {
        'A': dict(zip(obs, [110, 120, 130, 120, 110, 100, 90, 100, 110, 120])),
        'B': dict(zip(obs, [190, 180, 170, 180, 190, 200, 210, 220, 210, 200])),
        'C': dict(zip(obs, [55, 60, 50, 45, 40, 50, 55, 60, 65, 70])),
    }
    panel = _panel(d0, obs, raw, entry)
    nav, _ = compute_nav_path({'A': 1 / 3, 'B': 1 / 3, 'C': 1 / 3}, entry, panel)
    full = pd.concat([pd.Series({d0: 1.0}), nav])

    # "반기 종점"을 d5·d10으로 잡은 축소판: 구간수익률 [−5%, +26.3%]
    endpoint_returns = pd.Series(
        [nav.iloc[4] - 1.0, nav.iloc[-1] / nav.iloc[4] - 1.0],
        index=pd.DatetimeIndex([obs[4], obs[-1]]),
    )
    endpoint_mdd = compute_mdd(endpoint_returns)
    daily_mdd = compute_daily_metrics(full)['daily_mdd']
    assert daily_mdd <= endpoint_mdd  # 둘 다 음수 — 일별이 같거나 더 깊어야 함


# ── compute_daily_metrics 규약 (월 경계·CVaR fallback) ───────────────────────

def test_daily_metrics_monthly_conventions():
    # 1~3월: 1월말 1.1, 2월말 0.9, 3월말 1.0 (일별 2관측/월)
    navs = pd.Series({
        date(2020, 1, 2): 1.00, date(2020, 1, 31): 1.10,
        date(2020, 2, 14): 1.00, date(2020, 2, 28): 0.90,
        date(2020, 3, 16): 0.95, date(2020, 3, 31): 1.00,
    })
    m = compute_daily_metrics(navs)

    # 최악 월간: 2월 = 0.9/1.1 − 1
    assert m['worst_month_return'] == pytest.approx(0.9 / 1.1 - 1, abs=1e-12)
    assert m['worst_month'] == '2020-02'
    assert m['n_months'] == 3

    # 표본 3개월 → k=int(0.15)=0 < 3 → fallback k=3 (하위 3개 = 전체 평균)
    assert m['cvar_1m_fallback'] is True
    assert m['cvar_1m_k'] == 3
    monthly = pd.Series([0.10, 0.9 / 1.1 - 1, 1.0 / 0.9 - 1])
    assert m['cvar_5pct_1m'] == pytest.approx(monthly.mean(), abs=1e-12)


def test_daily_metrics_requires_min_observations():
    with pytest.raises(ValueError, match='관측치 부족'):
        compute_daily_metrics(pd.Series({date(2020, 1, 2): 1.0}))
