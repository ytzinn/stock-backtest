"""
SPEC_09 — 일별 NAV 엔진 (고정수량 NAV 경로의 SSOT).

계약 (CONTRACT-NAV-001~005, 2026-07-19 사용자 확정):
  NAV-001 거래정지: 직전 종가 forward-fill — 정지 기간 동안 NAV 기여 동결.
    [검토된 대안: NaN 제외 후 잔여 종목 재정규화 — 정지 해제 시 재편입 규칙이
     추가로 필요해져 비채택]
  NAV-002 구간 중 상장폐지: 최초 감지 관측일에 직전 종가 × DELISTING_HAIRCUT
    (engine.py SSOT) 적용 후 구간말까지 그 가격에 동결(무수익 현금성 — 반복 청산
    금지, 기존 백테스트의 1회성 청산 가정과 동일). [대안: 구간말 일괄 haircut —
    구간 수익률과는 일치하나 일별 경로가 비현실적이라 비채택]
  NAV-003 거래비용: 리밸런싱일에 승법 차감 NAV × (1 − turnover×(COST_SELL+COST_BUY)).
    구간 복리 net = gross − tc − gross×tc 로, 엔진의 산술 정의(net = gross − tc)와
    교차항 gross×tc 만큼 차이난다 — 버그가 아니라 정의 차이이며 reconciliation
    리포트에 정량 명기한다 (게이트 G-NAV-4가 상한 검증). [대안: 매수/매도 분리
    차감 — Phase 3+, 산술 정합 강제(차감계수 tc/(1+gross) 소급 보정) — 비채택]
  NAV-004 배당: 미반영 (Phase 2 전 구간과 일관 — 저PBR 포트폴리오 특성상 절대
    수익률 과소평가 개연성 있음, 리포트에 항상 명기).
  NAV-005 체결가: 리밸런싱일 adj_close 종가 체결 가정 (기존 엔진과 동일).
    일별 NAV의 리밸런싱일 관측치는 체결 직후 상태다.

★ 반드시 '초기 수량 고정 NAV' 방식이다. 관측일마다 목표비중으로 되돌리면
  전략 자체가 바뀐다 (regime/mtm_monthly.py와 동일 불변식).

★ NAV 경로 로직 SSOT (2026-07-19 사용자 확정): 이 모듈의 순수 함수
  (build_price_panel / compute_nav_path — DB 무접촉)가 유일한 구현이고,
  regime/mtm_monthly._nav_path()는 nav_path_db()에 위임한다 (산식 복제 금지).
  관측일 리스트가 월말이면 월별 MTM, 전체 거래일이면 일별 NAV가 된다.

★ 가격은 DB adj_close 직접 사용 — holdings tape의 entry/exit는 정수 반올림값이라
  게이트 G-NAV-1(1e-6)을 만족할 수 없다 (export_portfolios.py round(entry, 0)).
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from backtest.engine import DELISTING_HAIRCUT

log = logging.getLogger(__name__)


# ── 순수 함수 (DB 무접촉 — oracle 테스트 대상) ──────────────────────────────

def build_price_panel(
    raw_prices:   dict[str, pd.Series],
    entry_prices: dict[str, float],
    entry_date:   date,
    obs_dates:    list[date],
) -> pd.DataFrame:
    """
    관측일 × 종목 가격 패널. 각 셀은 "해당 관측일 이하 최신 adj_close"
    (= get_close_price/latest_close_batch의 date <= as_of 계약과 동일).

    거래정지·휴장으로 행이 없는 날은 직전 종가로 forward-fill 된다 (CONTRACT-NAV-001).
    entry_date의 진입가를 시드로 깔기 때문에 관측일 값은 결측이 될 수 없다
    (진입가 자체가 없는 종목은 entry_prices에서 미리 제외하고 호출).

    raw_prices: {ticker: Series(date → adj_close)} — (entry_date, 마지막 관측일]
                구간의 실제 거래일 가격 (결측일 없음, 있는 날만).
    """
    idx = pd.Index(obs_dates)
    cols = {}
    for ticker, p0 in entry_prices.items():
        s = raw_prices.get(ticker)
        seed = pd.Series({entry_date: float(p0)})
        s = pd.concat([seed, s]).sort_index() if s is not None and not s.empty else seed
        s = s[~s.index.duplicated(keep='last')]
        cols[ticker] = s.reindex(idx, method='ffill')
    panel = pd.DataFrame(cols, index=idx)
    if panel.isna().any().any():
        missing = panel.columns[panel.isna().any()].tolist()
        raise ValueError(
            f'가격 패널 결측 (진입가 시드가 있으면 불가능한 상태 — 로더 버그): {missing}'
        )
    return panel


def compute_nav_path(
    weights:       dict[str, float],
    entry_prices:  dict[str, float],
    panel:         pd.DataFrame,
    delisted_from: dict[str, date] | None = None,
) -> tuple[pd.Series, pd.DataFrame]:
    """
    weights(합≈1.0)로 진입가에 매수 후 panel의 각 관측일에 재평가한 NAV 경로.
    반환: (nav Series[관측일], 종목별 포지션 가치 DataFrame[관측일 × 종목]) —
    진입 시점 NAV = 1.0 기준의 상대 경로.

    - 진입가 없음(≤0 포함) 종목은 제외하고 잔여 종목으로 비중 재정규화
      (engine._calc_period_return / mtm_monthly._nav_path와 동일 관례 —
      그냥 빼기만 하면 그 비중만큼 자본이 증발해 NAV가 1.0 미만에서 출발).
    - delisted_from[t] <= 관측일이면 그 관측일의 패널 가격 × DELISTING_HAIRCUT로
      1회 동결, 이후 무수익 (CONTRACT-NAV-002). is_delisted_at()의
      delisted_date <= as_of 판정과 동일 의미.
    - 유효 종목이 하나도 없으면 전 관측일 0.0 (mtm_monthly 관례 유지).
    """
    delisted_from = delisted_from or {}
    obs_dates = list(panel.index)

    valid = {
        t: w for t, w in weights.items()
        if entry_prices.get(t) is not None and entry_prices[t] > 0
    }
    total_w = sum(valid.values())
    if total_w <= 0:
        zero = pd.Series(0.0, index=panel.index)
        return zero, pd.DataFrame(index=panel.index)

    shares = {t: (w / total_w) / entry_prices[t] for t, w in valid.items()}

    values = pd.DataFrame(index=panel.index, columns=list(shares), dtype=float)
    for t, sh in shares.items():
        px = panel[t].copy()
        dl = delisted_from.get(t)
        if dl is not None:
            frozen_mask = pd.Series([d >= dl for d in obs_dates], index=panel.index)
            if frozen_mask.any():
                first_idx = frozen_mask.idxmax()
                frozen_px = px.loc[first_idx] * DELISTING_HAIRCUT
                px.loc[frozen_mask] = frozen_px
        values[t] = sh * px

    return values.sum(axis=1), values


def stitch_periods(periods: list[dict]) -> pd.DataFrame:
    """
    구간별 상대 NAV 경로를 전 구간 연속 gross/net NAV로 결합.

    periods 원소: {
        'rebalance_date':   date,        # 체결일 (경로 기준점, NAV 상대값 1.0)
        'obs_dates':        list[date],  # (rebalance_date, next_date] 관측일
        'nav_path':         list[float], # obs_dates별 상대 NAV (compute_nav_path)
        'transaction_cost': float,       # engine 기록 tc = turnover×(COST_SELL+COST_BUY)
    }  (rebalance_date 오름차순)

    net은 각 리밸런싱일에 누적 NAV × (1 − tc) 승법 차감 (CONTRACT-NAV-003).
    구간 경계일(직전 구간의 마지막 관측일 == 다음 구간의 리밸런싱일)은
    리밸런싱 **후** 상태(비용 차감 반영)의 한 행만 남긴다.

    반환: DataFrame(index=date, columns=[nav_gross, nav_net])
    """
    rows: dict[date, tuple[float, float]] = {}
    g = n = 1.0
    for p in periods:
        n *= (1.0 - p['transaction_cost'])
        rows[p['rebalance_date']] = (g, n)          # 체결 직후 (경계일 덮어쓰기)
        for d, v in zip(p['obs_dates'], p['nav_path']):
            rows[d] = (g * v, n * v)
        last = p['nav_path'][-1]
        g, n = g * last, n * last

    idx = sorted(rows)
    return pd.DataFrame(
        {'nav_gross': [rows[d][0] for d in idx], 'nav_net': [rows[d][1] for d in idx]},
        index=pd.Index(idx),
    )


# ── DB 로더 + 위임 진입점 ────────────────────────────────────────────────────

def load_delisted_from(conn, tickers: list[str]) -> dict[str, date]:
    """종목별 최초 delisted_date (stock_listing_events). is_delisted_at()과 동일 기준."""
    if not tickers:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ticker, MIN(delisted_date)
            FROM stock_listing_events
            WHERE ticker = ANY(%s) AND delisted_date IS NOT NULL
            GROUP BY ticker
            """,
            (tickers,),
        )
        return {r[0]: r[1] for r in cur.fetchall()}


def load_raw_prices(
    conn, tickers: list[str], start: date, end: date
) -> dict[str, pd.Series]:
    """(start, end] 구간의 실제 거래일 adj_close. {ticker: Series(date → price)}."""
    if not tickers:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ticker, date, adj_close
            FROM price_history
            WHERE ticker = ANY(%s) AND date > %s AND date <= %s
              AND adj_close IS NOT NULL
            ORDER BY ticker, date
            """,
            (tickers, start, end),
        )
        rows = cur.fetchall()
    out: dict[str, list] = {}
    for t, d, px in rows:
        out.setdefault(t, []).append((d, float(px)))
    return {
        t: pd.Series({d: px for d, px in pairs}) for t, pairs in out.items()
    }


def trading_dates(conn, start: date, end: date) -> list[date]:
    """(start, end] 전체 거래일 — price_history DISTINCT date (CLAUDE.md 관례)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT date FROM price_history WHERE date > %s AND date <= %s ORDER BY date",
            (start, end),
        )
        return [r[0] for r in cur.fetchall()]


def nav_path_db(
    conn,
    weights:    dict[str, float],
    rebal_date: date,
    obs_dates:  list[date],
) -> list[float]:
    """
    mtm_monthly._nav_path()의 일반화 구현 (반환 계약 동일 — 관측일별 NAV 리스트).
    관측일이 월말이면 월별 MTM, 전체 거래일이면 일별 NAV.
    진입가는 rebal_date 이하 최신 adj_close (latest_close_batch 계약).
    """
    from backtest.regime.data_access_regime import latest_close_batch

    tickers = list(weights.keys())
    p0 = latest_close_batch(conn, tickers, rebal_date)
    entry_prices = {t: p0[t] for t in tickers if p0.get(t) and p0[t] > 0}
    dropped = [t for t in tickers if t not in entry_prices]
    if dropped:
        dropped_frac = 1.0 - (
            sum(weights[t] for t in entry_prices) / sum(weights.values())
        )
        log.warning(
            'rebal_date=%s: 진입가 없어 제외된 종목 %d개(비중 %.2f%%, 잔여 종목으로 재정규화) %s',
            rebal_date, len(dropped), dropped_frac * 100, dropped[:5],
        )
    if not entry_prices:
        return [0.0 for _ in obs_dates]

    raw   = load_raw_prices(conn, list(entry_prices), rebal_date, obs_dates[-1])
    panel = build_price_panel(raw, entry_prices, rebal_date, obs_dates)
    delisted = load_delisted_from(conn, list(entry_prices))

    nav, _ = compute_nav_path(weights, entry_prices, panel, delisted)
    return [float(v) for v in nav]


def daily_nav_for_period(
    conn,
    weights:    dict[str, float],
    rebal_date: date,
    next_date:  date,
) -> tuple[list[date], pd.Series, pd.DataFrame]:
    """
    한 구간의 일별 NAV. 반환: (관측일, nav Series, 종목별 가치 DataFrame).
    관측일 = (rebal_date, next_date] 전체 거래일 + 구간 종료 stub
    (next_date가 거래일이 아니어도 마지막 관측에 포함 — mtm _obs_dates와 동일 관례).
    """
    from backtest.regime.data_access_regime import latest_close_batch

    obs = trading_dates(conn, rebal_date, next_date)
    if not obs or obs[-1] != next_date:
        obs.append(next_date)

    tickers = list(weights.keys())
    p0 = latest_close_batch(conn, tickers, rebal_date)
    entry_prices = {t: p0[t] for t in tickers if p0.get(t) and p0[t] > 0}
    if not entry_prices:
        return obs, pd.Series(0.0, index=pd.Index(obs)), pd.DataFrame(index=pd.Index(obs))

    raw      = load_raw_prices(conn, list(entry_prices), rebal_date, obs[-1])
    panel    = build_price_panel(raw, entry_prices, rebal_date, obs)
    delisted = load_delisted_from(conn, list(entry_prices))
    nav, values = compute_nav_path(weights, entry_prices, panel, delisted)
    return obs, nav, values
