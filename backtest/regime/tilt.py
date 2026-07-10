"""
SPEC_08 §3 — 신호 정규화(PIT) + 실행 시차(lag) + share 매핑.

★ 룩어헤드 금지(R4): expanding 통계는 t 이하 history만 쓴다. WARMUP_M(개월) 이전엔 z_t를
비활성(NaN) 처리 — 어린 분포에서 나온 z-score를 신뢰하지 않고 s_neutral로 고정한다.

★ signal_date != execution_date(§3-1, v0.3 정밀 반영): 같은 종가로 신호를 보고 그 종가에
체결하는 룩어헤드를 막기 위해 반드시 execution_date = signal_date 이후 첫 거래일을 쓴다.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from backtest.regime.data_access_regime import next_trading_day


def expanding_z(series: pd.Series, warmup_m: int, z_cap: float) -> pd.Series:
    """
    t 시점 z = (v_t − mean(v_0..v_t)) / std(v_0..v_t), [−z_cap, z_cap]로 clamp.
    처음 warmup_m개 관측치는 NaN(비활성) — 어린 분포를 신뢰하지 않는다.
    """
    mean = series.expanding().mean()
    std = series.expanding().std()
    z = ((series - mean) / std).clip(lower=-z_cap, upper=z_cap)
    z.iloc[:warmup_m] = np.nan
    return z


def rolling_pct_z(series: pd.Series, window_m: int, z_cap: float) -> pd.Series:
    """
    §8 민감도 대안 — 롤링 window_m개월 내 백분위 순위를 [−z_cap, z_cap]로 선형 매핑한다
    (percentile 0.5 → z=0, 1.0 → z=+z_cap, 0.0 → z=−z_cap). expanding_z와 달리 오래된
    히스토리를 버리므로 최근 분포 변화에 더 민감하다. rolling()이라 t 이하 history만 쓴다(PIT).
    """
    def _pct_rank(window: np.ndarray) -> float:
        return float((window[:-1] < window[-1]).sum()) / max(len(window) - 1, 1)

    pct = series.rolling(window_m, min_periods=window_m).apply(_pct_rank, raw=True)
    return ((pct - 0.5) * 2 * z_cap).clip(lower=-z_cap, upper=z_cap)


def compute_z(series: pd.Series, normalization: str, warmup_m: int, z_cap: float,
              rolling_window_m: int) -> pd.Series:
    if normalization == 'expanding_z':
        return expanding_z(series, warmup_m, z_cap)
    if normalization == 'rolling_pct_60m':
        return rolling_pct_z(series, rolling_window_m, z_cap)
    raise ValueError(f'알 수 없는 normalization: {normalization}')


def effective_k(mode: str, k_requested: float, conservative_k: float) -> float:
    """R2 보수 모드 — WALKFORWARD 미통과 시(mode='tilt_conservative') K는 CONSERVATIVE_K로 강제."""
    return conservative_k if mode == 'tilt_conservative' else k_requested


def effective_tilt_option(mode: str, tilt_option_requested: str) -> str:
    """R2 보수 모드 — 옵션 B(양방향) 비활성, A(방어형)만 허용."""
    if mode == 'tilt_conservative' and tilt_option_requested == 'B_two_sided':
        return 'A_defensive'
    return tilt_option_requested


def share_from_z(z_t: float | None, s_neutral: float, k: float, s_min: float, s_max: float) -> float:
    """
    s_t = clamp(s_neutral + K·z_t, S_MIN, S_MAX). (R1: 연속·유계 함수만.)
    z_t가 None/NaN(워밍업 이전·비활성)이면 s_neutral 그대로 — 신뢰 못 하는 신호로
    임의 진입/이탈하지 않는다.
    """
    if z_t is None or pd.isna(z_t):
        return s_neutral
    return float(np.clip(s_neutral + k * z_t, s_min, s_max))


def signal_execution_dates(conn, signal_dates: list[date]) -> list[tuple[date, date]]:
    """
    각 signal_date(월말 등)에 대해 (signal_date, execution_date) 쌍을 만든다.
    execution_date = signal_date 이후 첫 거래일(§3-1) — 같은 종가로 신호를 보고 그
    종가에 체결하는 룩어헤드를 막기 위한 지연.
    """
    return [(d, next_trading_day(conn, d)) for d in signal_dates]
