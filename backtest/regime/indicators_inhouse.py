"""
SPEC_07 §7-3 — 인하우스 지표 6종을 월말마다 계산해 regime_indicators에 적재.

Look-ahead 금지: 모든 조회는 available_from<=t / date<=t 조건을 건다.
size_mom_6m formation은 (기본) t-6M 시점 시총으로 버킷을 고정한다(§4-3 리뷰 7번) —
"최근 오른 종목이 대형 decile로 편입되는" 기계적 왜곡을 피하기 위함. 월말 리스트에서
6개월 전 인덱스를 그대로 쓰므로(같은 달력 간격) 별도 날짜 근사가 필요 없다.

실행: venv/bin/python -m backtest.regime.indicators_inhouse
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd

from backtest.data_access import is_delisted_at
from backtest.engine import DELISTING_HAIRCUT, _last_known_price
from backtest.regime.config_regime import (
    BREADTH_MA_DAYS,
    LIQ_LOOKBACK_D,
    LIQ_QUANTILES,
    MEGACAP_TOP_N,
    MOM_FORMATION,
    MOM_LOOKBACK_M,
    PBR_QUANTILES,
    SIZE_DECILES,
    config_hash,
)
from backtest.regime.data_access_regime import (
    book_equity_batch,
    latest_close_batch,
    list_universe_tickers,
    market_cap_batch,
    month_end_dates,
    price_series_batch,
    turnover_batch,
)
from ingest.connection import get_connection

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

# t-6M formation 버퍼 확보용. 실 진단 구간은 2016-04~ (SPEC_07 §5).
LOOKBACK_BUFFER_START = date(2015, 9, 1)
WINDOW_START = date(2016, 4, 1)


# ── 월별 PBR 단면 ────────────────────────────────────────────────────────────

def _pbr_cross_section(conn, universe: list[str], t: date) -> tuple[dict[str, float], int, float]:
    """§4-1·§4-2. 반환: (pbr dict, universe_n, dropped_pct)."""
    caps = market_cap_batch(conn, universe, t)
    equities = book_equity_batch(conn, universe, t)
    pbr = {}
    for tk in universe:
        cap = caps.get(tk)
        eq = equities.get(tk)
        if cap is not None and eq is not None and cap > 0 and eq > 0:
            pbr[tk] = cap / eq
    universe_n = len(universe)
    dropped_pct = 1.0 - (len(pbr) / universe_n) if universe_n > 0 else None
    return pbr, universe_n, dropped_pct


def _quantile_labels(values: dict[str, float], n_quantiles: int) -> pd.Series | None:
    """오름차순 분위 라벨(0=최소 그룹 ... n-1=최대 그룹). 표본 부족 시 None."""
    if len(values) < n_quantiles * 3:
        return None
    s = pd.Series(values)
    try:
        return pd.qcut(s, n_quantiles, labels=False, duplicates='drop')
    except ValueError:
        return None


def _log_ratio_of_medians(pbr: dict[str, float], top_group: set[str], bottom_group: set[str]) -> float | None:
    top_vals = [pbr[t] for t in top_group if t in pbr]
    bot_vals = [pbr[t] for t in bottom_group if t in pbr]
    if len(top_vals) < 3 or len(bot_vals) < 3:
        return None
    top_med, bot_med = float(np.median(top_vals)), float(np.median(bot_vals))
    if top_med <= 0 or bot_med <= 0:
        return None
    return float(np.log(top_med / bot_med))


# ── 지표별 계산 ──────────────────────────────────────────────────────────────

def value_spread(pbr: dict[str, float]) -> float | None:
    """log(median(PBR|Q5 성장) / median(PBR|Q1 가치)). 값↑ = value 유리."""
    labels = _quantile_labels(pbr, PBR_QUANTILES)
    if labels is None:
        return None
    q1 = set(labels[labels == 0].index)
    q5 = set(labels[labels == labels.max()].index)
    return _log_ratio_of_medians(pbr, top_group=q5, bottom_group=q1)


def size_val_gap(pbr: dict[str, float], caps: dict[str, float]) -> float | None:
    """log(median(PBR|대형decile) / median(PBR|소형decile)). 값↑ = 소형이 더 쌈."""
    common = {t: caps[t] for t in pbr if t in caps}
    labels = _quantile_labels(common, SIZE_DECILES)
    if labels is None:
        return None
    small = set(labels[labels == 0].index)
    large = set(labels[labels == labels.max()].index)
    return _log_ratio_of_medians(pbr, top_group=large, bottom_group=small)


def illiq_discount(pbr: dict[str, float], turnovers: dict[str, float]) -> float | None:
    """log(median(PBR|고유동Q5) / median(PBR|저유동Q1)). 값↑ = 저유동주 할인 심화."""
    common = {t: turnovers[t] for t in pbr if t in turnovers}
    labels = _quantile_labels(common, LIQ_QUANTILES)
    if labels is None:
        return None
    illiquid = set(labels[labels == 0].index)
    liquid = set(labels[labels == labels.max()].index)
    return _log_ratio_of_medians(pbr, top_group=liquid, bottom_group=illiquid)


def check_dropped_pct_threshold(t: date, dropped_pct: float | None, threshold: float = 0.30) -> bool:
    """dropped_pct가 threshold 초과면 경고 로그. 생존편향/결측 급증 조기 감지용."""
    if dropped_pct is not None and dropped_pct > threshold:
        log.warning('%s: dropped_pct=%.1f%% > %.0f%% — 생존편향/결측 확인 필요',
                     t, dropped_pct * 100, threshold * 100)
        return True
    return False


def mega_cap_concentration(caps: dict[str, float], top_n: int = MEGACAP_TOP_N) -> float | None:
    if not caps:
        return None
    ranked = sorted(caps.values(), reverse=True)
    total = sum(ranked)
    if total <= 0:
        return None
    return float(sum(ranked[:top_n]) / total)


def breadth_ma200(conn, universe: list[str], t: date, ma_days: int = BREADTH_MA_DAYS) -> tuple[float | None, int]:
    """유니버스 중 adj_close(t) > MA200(t) 비율. 반환: (breadth, 계산에 쓰인 종목 수)."""
    start = t - timedelta(days=int(ma_days * 1.6) + 30)
    df = price_series_batch(conn, universe, start, t)
    if df.empty:
        return None, 0
    n_above = 0
    n_total = 0
    for _, g in df.groupby('ticker'):
        g = g.sort_values('date')
        if len(g) < ma_days:
            continue
        ma = g['adj_close'].tail(ma_days).mean()
        last = g['adj_close'].iloc[-1]
        n_total += 1
        if last > ma:
            n_above += 1
    if n_total == 0:
        return None, 0
    return n_above / n_total, n_total


def size_mom_6m(conn, bucket_universe: list[str], bucket_caps: dict[str, float],
                 return_start_date: date, current_date: date) -> float | None:
    """
    소형decile − 대형decile의 return_start_date→current_date 동일가중 수익 차.
    버킷 구성원(small/large)은 호출부에서 이미 결정된 시총 스냅샷(bucket_caps)으로 고정된다
    (기본: return_start_date와 동일한 t-6M. MOM_FORMATION='t_minus_1m' 민감도 시엔
    bucket_caps만 다른 날짜 것을 넘기고, 수익은 항상 return_start_date→current_date로 측정).

    ★ 상폐 종목은 latest_close_batch()만 쓰면 상폐 후 마지막 가격이 그대로 carry-forward돼
    수익률이 ~0%로 왜곡된다(mtm_monthly.py의 함정과 동일). is_delisted_at() + DELISTING_HAIRCUT로
    명시 처리한다 — 상폐가 소형주 decile에 몰리는 경향이 있어 이 지표에서 특히 중요하다.
    """
    labels = _quantile_labels(bucket_caps, SIZE_DECILES)
    if labels is None:
        return None
    small = list(labels[labels == 0].index)
    large = list(labels[labels == labels.max()].index)
    if len(small) < 3 or len(large) < 3:
        return None

    tickers = small + large
    px_form = latest_close_batch(conn, tickers, return_start_date)
    px_now = latest_close_batch(conn, tickers, current_date)

    def _exit_price(tk: str) -> float | None:
        if is_delisted_at(conn, tk, current_date):
            last = _last_known_price(conn, tk, current_date)
            return last * DELISTING_HAIRCUT if last else None
        return px_now.get(tk)

    def _ew_return(bucket: list[str]) -> float | None:
        rets = []
        for tk in bucket:
            p0, p1 = px_form.get(tk), _exit_price(tk)
            if p0 and p1 and p0 > 0:
                rets.append(p1 / p0 - 1)
        return float(np.mean(rets)) if len(rets) >= 3 else None

    small_ret, large_ret = _ew_return(small), _ew_return(large)
    if small_ret is None or large_ret is None:
        return None
    return small_ret - large_ret


# ── DB 적재 ──────────────────────────────────────────────────────────────────

def _upsert(conn, run_id: str, cfg_hash: str, t: date, indicator: str,
            value: float | None, universe_n: int, dropped_pct: float | None) -> None:
    if value is None:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO regime_indicators (run_id, config_hash, date, indicator, value, universe_n, dropped_pct)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, date, indicator) DO UPDATE SET
                config_hash = EXCLUDED.config_hash,
                value = EXCLUDED.value,
                universe_n = EXCLUDED.universe_n,
                dropped_pct = EXCLUDED.dropped_pct
            """,
            (run_id, cfg_hash, t, indicator, value, universe_n, dropped_pct),
        )


# ── 메인 ────────────────────────────────────────────────────────────────────

def run(run_id: str | None = None, end_date: date | None = None) -> None:
    cfg_hash = config_hash()
    run_id = run_id or f'ind_{cfg_hash}'
    end_date = end_date or date.today()

    conn = get_connection()
    try:
        all_month_ends = month_end_dates(conn, LOOKBACK_BUFFER_START, end_date)
        output_dates = [d for d in all_month_ends if d >= WINDOW_START]
        log.info('월말 %d개(버퍼 포함) 중 출력 대상 %d개, config_hash=%s',
                  len(all_month_ends), len(output_dates), cfg_hash)

        for t in output_dates:
            i = all_month_ends.index(t)
            universe = list_universe_tickers(conn, t)
            caps = market_cap_batch(conn, universe, t)
            turnovers = turnover_batch(conn, universe, t, window=LIQ_LOOKBACK_D)
            pbr, universe_n, dropped_pct = _pbr_cross_section(conn, universe, t)

            _upsert(conn, run_id, cfg_hash, t, 'value_spread', value_spread(pbr), universe_n, dropped_pct)
            _upsert(conn, run_id, cfg_hash, t, 'size_val_gap', size_val_gap(pbr, caps), universe_n, dropped_pct)
            _upsert(conn, run_id, cfg_hash, t, 'illiq_discount', illiq_discount(pbr, turnovers),
                    universe_n, dropped_pct)
            _upsert(conn, run_id, cfg_hash, t, 'mega_cap_concentration', mega_cap_concentration(caps),
                    universe_n, 0.0)

            breadth, n_breadth = breadth_ma200(conn, universe, t)
            _upsert(conn, run_id, cfg_hash, t, 'breadth_ma200', breadth, universe_n,
                    1.0 - n_breadth / universe_n if universe_n else None)

            form_idx = i - MOM_LOOKBACK_M if MOM_FORMATION == 't_minus_6m' else i - 1
            form_idx = max(form_idx, 0)
            formation_date = all_month_ends[form_idx]
            return_start_date = all_month_ends[max(i - MOM_LOOKBACK_M, 0)]
            bucket_universe = list_universe_tickers(conn, formation_date)
            bucket_caps = market_cap_batch(conn, bucket_universe, formation_date)
            mom = size_mom_6m(conn, bucket_universe, bucket_caps, return_start_date, t)
            _upsert(conn, run_id, cfg_hash, t, 'size_mom_6m', mom, len(bucket_universe), None)

            conn.commit()
            log.info('%s: universe=%d dropped=%.1f%% value_spread 계산 완료', t, universe_n,
                      (dropped_pct or 0) * 100)

            check_dropped_pct_threshold(t, dropped_pct)
    finally:
        conn.close()

    log.info('지표 계산 완료 (run_id=%s)', run_id)


if __name__ == '__main__':
    run()
