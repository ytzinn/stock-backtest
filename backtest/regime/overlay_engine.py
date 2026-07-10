"""
SPEC_08 §3~4, STEP B-3 — always_on/tilt 모드 overlay 재조립.

★ always_on 모드는 Phase A가 이미 계산해둔 strategy_returns_monthly를 그대로 블렌딩만
한다(순수 산술, DB 가격 재조회 없음). 이게 §3-14 게이트가 검증하는 대상이다 — 옵션 A의
s_neutral=1.0이면 always_on은 정의상 port_return과 완전히 같아야 한다(0·alt+1·port).

★ tilt / tilt_conservative 모드만 signal_date≠execution_date 지연을 정밀 반영한다(v0.3 §3-1):
`next_trading_day()`로 execution_date를 구하고, `nav_path()`(mtm_monthly.py 공개 별칭)를
월말 대신 지연 반영 execution_date 시퀀스로 호출해 소형가치/대체 sleeve 구간 수익률을
다시 계산한다. 게이트를 통과하기 전에는(always_on 검증 실패 시) tilt 계산을 진행하지 않는다.

실행: venv/bin/python -m backtest.regime.overlay_engine
"""
from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd

from backtest.regime.config_phaseB import (
    CONSERVATIVE_K,
    INDICATORS_RUN_ID,
    LARGE_LEG_BPS,
    MTM_RUN_ID,
    PHASEB_RUN_ID,
    S_MAX,
    S_MIN,
    S_NEUTRAL_A,
    S_NEUTRAL_B,
    SMALL_LEG_BPS,
    VARIANTS,
    WARMUP_M,
    Z_CAP,
    ROLLING_WINDOW_M,
    config_hash,
)
from backtest.regime.data_access_regime import kospi_return, month_end_dates, next_trading_day
from backtest.regime.mtm_monthly import build_largecap_sleeve, load_period_holdings, nav_path, periods
from backtest.regime.tilt import compute_z, effective_k, effective_tilt_option, share_from_z
from ingest.connection import get_connection

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

PERIOD22_START = date(2025, 8, 20)   # #22 — analyze.py의 EXCLUDE_PERIOD_STARTS와 동일 기준

S_NEUTRAL_BY_OPTION = {'A_defensive': S_NEUTRAL_A, 'B_two_sided': S_NEUTRAL_B}
ALT_RETURN_COLUMN = {'largecap_cw': 'largecap_cw_return', 'kospi': 'kospi_return'}

# ── grid.py 216 조합 재계산 캐시 ─────────────────────────────────────────────
# §5 그리드 축(tilt_option/normalization/overlay_freq/alt_sleeve/K) 중 상당수는 이 값들에
# 영향을 주지 않는데도 조합마다 매번 새로 DB에서 읽고 nav_path()를 다시 돌리고 있었다
# (216 조합 × 21구간 기준 실측 ~1분/조합, 전체 그리드 3~4시간 추정). 프로세스 하나가
# grid.py 실행 동안 계속 살아있는 걸 이용해 조합-불변 구간만 메모이즈한다:
#   - load_base_monthly/load_indicator_series/compute_z: scenario·indicator·normalization에만 의존
#   - 결정일/집행일: (rebal_date,next_date,overlay_freq)에만 의존(K/정규화/시나리오 무관)
#   - 소형가치 NAV: (scenario,rebal_date,overlay_freq)에만 의존(K/정규화/alt_sleeve 무관 —
#     D_v1/D_v2가 같은 tag를 쓰므로 variant끼리도 공유됨)
#   - 대체 sleeve NAV: (alt_sleeve,rebal_date,overlay_freq)에만 의존(시나리오조차 무관)
_MONTHLY_DF_CACHE: dict[tuple, pd.DataFrame] = {}
_INDICATOR_SERIES_CACHE: dict[tuple, pd.Series] = {}
_Z_CACHE: dict[tuple, pd.Series] = {}
_DECISION_DATES_CACHE: dict[tuple, tuple | None] = {}
_SMALL_NAV_CACHE: dict[tuple, list[float] | None] = {}
_ALT_NAV_CACHE: dict[tuple, list[float]] = {}


def clear_caches() -> None:
    """테스트/재실행 격리용. grid.py 프로세스 하나가 끝나면 어차피 다 날아가지만,
    같은 프로세스에서 다른 indicators_run_id/mtm_run_id로 다시 돌릴 땐 명시적으로 불러야 한다."""
    for cache in (_MONTHLY_DF_CACHE, _INDICATOR_SERIES_CACHE, _Z_CACHE,
                  _DECISION_DATES_CACHE, _SMALL_NAV_CACHE, _ALT_NAV_CACHE):
        cache.clear()


# ── Phase A 데이터 로드 ──────────────────────────────────────────────────────

def load_base_monthly(conn, mtm_run_id: str, scenario: str) -> pd.DataFrame:
    """
    analyze.py::load_monthly_returns()의 확장판 — always_on 블렌딩에 필요한
    largecap_ew_return/kospi_return까지 SELECT한다(analyze.py는 일부만 가져옴).
    scenario당 한 번만 조회하면 되므로 캐시한다(grid.py가 216번 반복 호출).
    """
    cache_key = (mtm_run_id, scenario)
    if cache_key in _MONTHLY_DF_CACHE:
        return _MONTHLY_DF_CACHE[cache_key]
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT date, period_start, period_end, is_closed_period,
                   port_return, largecap_cw_return, largecap_ew_return, kospi_return
            FROM strategy_returns_monthly
            WHERE source_run_id = %s AND scenario = %s
            ORDER BY date
            """,
            (mtm_run_id, scenario),
        )
        rows = cur.fetchall()
    cols = ['date', 'period_start', 'period_end', 'is_closed_period',
            'port_return', 'largecap_cw_return', 'largecap_ew_return', 'kospi_return']
    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'])
        # period_start는 raw datetime.date(object dtype)로 남아있으면 pd.Timestamp와 비교 시
        # 전부 False가 되는 함정이 있다(같은 날짜인데도 매칭 안 됨) — 실제 서버에서 always_on
        # 행이 조용히 0건만 저장되던 원인. date 컬럼과 동일하게 datetime으로 통일한다.
        df['period_start'] = pd.to_datetime(df['period_start'])
        df['period_end'] = pd.to_datetime(df['period_end'])
        df = df.set_index('date').sort_index()
    _MONTHLY_DF_CACHE[cache_key] = df
    return df


def load_indicator_series(conn, indicators_run_id: str, indicator: str) -> pd.Series:
    """regime_indicators에서 지표 하나의 시계열(date-indexed)만 로드. indicator당 캐시."""
    cache_key = (indicators_run_id, indicator)
    if cache_key in _INDICATOR_SERIES_CACHE:
        return _INDICATOR_SERIES_CACHE[cache_key]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT date, value FROM regime_indicators WHERE run_id = %s AND indicator = %s ORDER BY date",
            (indicators_run_id, indicator),
        )
        rows = cur.fetchall()
    if not rows:
        result = pd.Series(dtype=float)
    else:
        dates, values = zip(*rows)
        result = pd.Series(values, index=pd.to_datetime(dates), dtype=float).sort_index()
    _INDICATOR_SERIES_CACHE[cache_key] = result
    return result


# ── always_on (순수 산술, §3-14 게이트) ──────────────────────────────────────

def compute_always_on_series(monthly_df: pd.DataFrame, alt_sleeve: str, s_neutral: float) -> pd.Series:
    """always_on(s_t≡s_neutral 고정) 월별 수익률 = s_neutral·port + (1−s_neutral)·alt."""
    alt_col = ALT_RETURN_COLUMN[alt_sleeve]
    return s_neutral * monthly_df['port_return'] + (1 - s_neutral) * monthly_df[alt_col]


def check_always_on_gate(monthly_df: pd.DataFrame, alt_sleeve: str, tol: float = 1e-9) -> None:
    """
    §3-14 — s_neutral=1.0(옵션 A)일 때 always_on은 정의상 port_return과 완전히 같아야 한다.
    다르면 블렌딩 공식(부호·컬럼 매칭)에 결함이 있다는 뜻 — 진행 중단.
    """
    always_on_a = compute_always_on_series(monthly_df, alt_sleeve, s_neutral=1.0)
    diff = (always_on_a - monthly_df['port_return']).abs().max()
    if pd.notna(diff) and diff > tol:
        raise RuntimeError(
            f'[STEP B-3 always_on 게이트 실패] alt_sleeve={alt_sleeve}: '
            f'always_on(s_neutral=1.0) != port_return (최대 오차 {diff:.2e} > {tol}). '
            f'블렌딩 공식 확인 필요 — tilt 계산 진행 중단.'
        )
    closed = monthly_df[monthly_df['is_closed_period']]
    cumulative = (1 + closed['port_return']).prod() - 1
    log.info('[B-3 게이트 통과] alt_sleeve=%s always_on(s=1.0)==port_return 확인, '
              '닫힌구간 누적수익=%.4f%%', alt_sleeve, cumulative * 100)


# ── tilt/tilt_conservative (지연 반영 재계산) ────────────────────────────────

def _decision_dates_for_period(conn, rebal_date: date, next_date: date, overlay_freq: str) -> list[date]:
    """
    이 구간에서 s_t를 재평가하는 신호일(signal_date) 목록. ★ 반드시 rebal_date로 시작한다 —
    첫 구간([exec(rebal_date), exec(첫 월말)])의 s_t는 포트폴리오 형성 시점(rebal_date)에
    알려진 신호로 결정돼야 하며, 이게 빠지면 nav_path()의 buy_date가 첫 월말의 집행일이
    되어버려 그 사이 수익이 통째로 누락된다(구현 중 실제 서버에서 발견된 버그).

    `[ASSUMPTION]` 반기 구간 길이가 일정하지 않아(4~8개월) quarterly는 "매 3번째 월말"로
    근사한다(달력상 정확한 분기 경계가 아님) — §8 민감도 대상.
    """
    if overlay_freq == 'semiannual':
        return [rebal_date]
    month_ends = month_end_dates(conn, rebal_date, next_date)
    if overlay_freq == 'monthly':
        return [rebal_date] + month_ends
    if overlay_freq == 'quarterly':
        return [rebal_date] + (month_ends[::3] or month_ends[-1:])
    raise ValueError(f'알 수 없는 overlay_freq: {overlay_freq}')


def _kospi_nav_path(start_date: date, obs_dates: list[date]) -> list[float]:
    """kospi_return()으로 nav_path()와 동일한 형태의 누적 NAV 리스트를 만든다(conn 불필요)."""
    nav = 1.0
    navs = []
    prev = start_date
    for d in obs_dates:
        nav *= (1 + kospi_return(prev, d))
        navs.append(nav)
        prev = d
    return navs


def _combined_z(value_spread_z: float | None, size_mom_z: float | None) -> float | None:
    """
    D_v2(exploratory, §3-12) — value_spread와 size_mom_6m의 z를 단순 평균한다.
    `[ASSUMPTION]` 정식 결합 가중치는 미정 — D_v2는 판단 신호가 아니라 탐색용이라
    가장 단순한 평균으로 시작하고, 필요시 §8류 민감도로 가중치를 흔든다.
    """
    vals = [z for z in (value_spread_z, size_mom_z) if pd.notna(z)]
    if not vals:
        return None
    return float(np.mean(vals))


def _get_decision_exec_obs_dates(conn, rebal_date: date, next_date: date,
                                  overlay_freq: str) -> tuple | None:
    """
    (rebal_date, next_date, overlay_freq)에만 의존 — K/normalization/scenario/alt_sleeve와
    무관하므로 grid.py의 216 조합 중 겹치는 조합끼리 재사용한다(21구간×3빈도=63개면 충분).
    """
    cache_key = (rebal_date, next_date, overlay_freq)
    if cache_key in _DECISION_DATES_CACHE:
        return _DECISION_DATES_CACHE[cache_key]

    decision_dates = _decision_dates_for_period(conn, rebal_date, next_date, overlay_freq)
    exec_dates = [next_trading_day(conn, d) for d in decision_dates]
    period_end_exec = next_trading_day(conn, next_date)

    # 진행 중인 구간(#23류, is_closed=False)은 next_date=오늘이라 그 이후 거래일이 아직
    # 없어 next_trading_day()가 None을 반환할 수 있다 — None이 nav_path의 가격조회에 섞이면
    # 그 시점 가격이 통째로 빈 딕셔너리가 되어 NAV가 0으로 떨어지고(다음 구간에서 0으로
    # 나누는 사고로 이어짐, 실제 서버에서 재현됨) is_closed=False는 어차피 §9 게이트
    # 모집단에서 제외되므로 여기서도 건너뛴다.
    if None in exec_dates or period_end_exec is None:
        _DECISION_DATES_CACHE[cache_key] = None
        return None

    obs_dates = exec_dates[1:] + [period_end_exec]
    result = (decision_dates, exec_dates, obs_dates)
    _DECISION_DATES_CACHE[cache_key] = result
    return result


def _get_small_navs(conn, tag: str, rebal_date: date, exec_dates: list, obs_dates: list) -> list[float] | None:
    """(tag,rebal_date,overlay_freq)에만 의존 — D_v1/D_v2가 tag를 공유하므로 variant끼리도 재사용."""
    cache_key = (tag, rebal_date, tuple(exec_dates), tuple(obs_dates))
    if cache_key in _SMALL_NAV_CACHE:
        return _SMALL_NAV_CACHE[cache_key]
    period = load_period_holdings(tag, rebal_date)
    tickers = [h['ticker'] for h in period['holdings']]
    result = None
    if tickers:
        weights = {t: 1.0 / len(tickers) for t in tickers}
        result = nav_path(conn, weights, exec_dates[0], obs_dates)
    _SMALL_NAV_CACHE[cache_key] = result
    return result


def _get_alt_navs(conn, alt_sleeve: str, rebal_date: date, exec_dates: list, obs_dates: list) -> list[float]:
    """(alt_sleeve,rebal_date,overlay_freq)에만 의존 — 시나리오/variant조차 무관하게 재사용."""
    cache_key = (alt_sleeve, rebal_date, tuple(exec_dates), tuple(obs_dates))
    if cache_key in _ALT_NAV_CACHE:
        return _ALT_NAV_CACHE[cache_key]
    if alt_sleeve == 'kospi':
        result = _kospi_nav_path(exec_dates[0], obs_dates)
    else:
        cw_weights, _ = build_largecap_sleeve(conn, rebal_date)
        result = nav_path(conn, cw_weights, exec_dates[0], obs_dates)
    _ALT_NAV_CACHE[cache_key] = result
    return result


def _period_tilt_rows(conn, tag: str, rebal_date: date, next_date: date, is_closed: bool,
                       overlay_freq: str, alt_sleeve: str, value_spread_z: pd.Series,
                       size_mom_z: pd.Series | None, variant: str, s_neutral: float, k: float,
                       s_min: float, s_max: float) -> list[dict]:
    """한 구간(반기) 내 tilt 모드 행들을 계산(지연 반영 execution_date 시퀀스 재사용)."""
    dates_result = _get_decision_exec_obs_dates(conn, rebal_date, next_date, overlay_freq)
    if dates_result is None:
        log.warning('%s %s~%s: execution_date 미확정(진행 중인 구간) — tilt 계산 건너뜀',
                     tag, rebal_date, next_date)
        return []
    decision_dates, exec_dates, obs_dates = dates_result

    small_navs = _get_small_navs(conn, tag, rebal_date, exec_dates, obs_dates)
    if small_navs is None:
        return []
    alt_navs = _get_alt_navs(conn, alt_sleeve, rebal_date, exec_dates, obs_dates)

    rows = []
    nav_prev_small = nav_prev_alt = 1.0
    prev_s = s_neutral
    episode_tag = 'period22' if rebal_date == PERIOD22_START else 'normal'
    for i, signal_date in enumerate(decision_dates):
        vs_z_raw = value_spread_z.asof(pd.Timestamp(signal_date)) if not value_spread_z.empty else None
        # .asof()/.loc()는 np.float64를 반환한다 — isinstance(x, float)는 플랫폼에 따라
        # np.float64를 못 잡을 수 있어(numpy가 builtin float를 상속하지 않는 빌드) pd.notna()로
        # 통일하고 항상 순수 float로 변환한다. 안 그러면 psycopg2가 numpy 스칼라를 SQL에
        # raw 텍스트로 박아 넣어 `schema "np" does not exist` 에러가 난다(실제 서버에서 재현됨).
        vs_z = float(vs_z_raw) if pd.notna(vs_z_raw) else None
        if variant.endswith('_v2') and size_mom_z is not None and not size_mom_z.empty:
            sm_z_raw = size_mom_z.asof(pd.Timestamp(signal_date))
            sm_z = float(sm_z_raw) if pd.notna(sm_z_raw) else None
            z_t = _combined_z(vs_z, sm_z)
        else:
            sm_z = None
            z_t = vs_z

        s_t = share_from_z(z_t, s_neutral, k, s_min, s_max)
        small_ret = small_navs[i] / nav_prev_small - 1
        alt_ret = alt_navs[i] / nav_prev_alt - 1
        port_ret = s_t * small_ret + (1 - s_t) * alt_ret
        turnover = abs(s_t - prev_s)
        cost = turnover * (SMALL_LEG_BPS + LARGE_LEG_BPS) / 10000.0
        net_ret = port_ret - cost

        rows.append({
            'signal_date': signal_date, 'execution_date': exec_dates[i],
            'date': obs_dates[i], 's_t': s_t, 'z_t': z_t, 'size_mom_z': sm_z,
            'port_return': port_ret, 'alt_return': alt_ret,
            'overlay_turnover': turnover, 'overlay_cost': cost,
            'net_port_return': net_ret, 'is_oos': z_t is not None,
            'episode_tag': episode_tag,
        })
        nav_prev_small, nav_prev_alt, prev_s = small_navs[i], alt_navs[i], s_t
    return rows


# ── DB 적재 ──────────────────────────────────────────────────────────────────

def _upsert(conn, run_id: str, cfg_hash: str, scenario: str, variant: str, tilt_option: str,
            mode: str, normalization: str, overlay_freq: str, alt_sleeve: str, row: dict,
            period_start: date, period_end: date, base_return: float | None,
            net_base_return: float | None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO overlay_returns (
                run_id, config_hash, scenario, variant, tilt_option, mode, normalization,
                overlay_freq, alt_sleeve, signal_date, execution_date, period_start, period_end,
                date, s_t, z_t, size_mom_z, port_return, base_return, alt_return,
                overlay_turnover, overlay_cost, net_port_return, net_base_return, is_oos, episode_tag
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            )
            ON CONFLICT (run_id, config_hash, scenario, variant, tilt_option, mode, date) DO UPDATE SET
                s_t = EXCLUDED.s_t, z_t = EXCLUDED.z_t, size_mom_z = EXCLUDED.size_mom_z,
                port_return = EXCLUDED.port_return, base_return = EXCLUDED.base_return,
                alt_return = EXCLUDED.alt_return, overlay_turnover = EXCLUDED.overlay_turnover,
                overlay_cost = EXCLUDED.overlay_cost, net_port_return = EXCLUDED.net_port_return,
                net_base_return = EXCLUDED.net_base_return, is_oos = EXCLUDED.is_oos,
                episode_tag = EXCLUDED.episode_tag
            """,
            (run_id, cfg_hash, scenario, variant, tilt_option, mode, normalization, overlay_freq,
             alt_sleeve, row['signal_date'], row['execution_date'], period_start, period_end,
             row['date'], row['s_t'], row['z_t'], row.get('size_mom_z'), row['port_return'],
             base_return, row['alt_return'], row['overlay_turnover'], row['overlay_cost'],
             row['net_port_return'], net_base_return, row['is_oos'], row['episode_tag']),
        )


def _get_z_series(conn, indicator: str, normalization: str) -> pd.Series:
    """(indicator,normalization)에만 의존 — K/overlay_freq/alt_sleeve/scenario와 무관하므로
    216 조합 중 정규화 축(2종)만큼만 계산하면 된다."""
    cache_key = (INDICATORS_RUN_ID, indicator, normalization)
    if cache_key in _Z_CACHE:
        return _Z_CACHE[cache_key]
    raw = load_indicator_series(conn, INDICATORS_RUN_ID, indicator)
    z = compute_z(raw, normalization, WARMUP_M, Z_CAP, ROLLING_WINDOW_M)
    _Z_CACHE[cache_key] = z
    return z


# ── 조합 실행 (grid.py가 반복 호출) ──────────────────────────────────────────

def run_combo(conn, scenario: str, variant: str, tilt_option: str, mode: str,
              normalization: str, overlay_freq: str, alt_sleeve: str, k: float,
              run_id: str = PHASEB_RUN_ID) -> str:
    """
    한 그리드 조합을 전체 구간에 대해 계산·적재한다. mode='always_on'이면 순수 블렌딩만,
    'tilt'/'tilt_conservative'면 지연 반영 재계산. 반환값은 이 조합의 config_hash(호출부에서
    walkforward.py/grid.py가 재조회할 때 재사용).
    """
    tag = scenario
    s_neutral = S_NEUTRAL_BY_OPTION[tilt_option]
    k_eff = effective_k(mode, k, CONSERVATIVE_K)
    tilt_option_eff = effective_tilt_option(mode, tilt_option)
    s_neutral_eff = S_NEUTRAL_BY_OPTION[tilt_option_eff]
    cfg_hash = config_hash(K=k, NORMALIZATION=normalization, OVERLAY_FREQ=overlay_freq,
                            ALT_SLEEVE=alt_sleeve)

    monthly_df = load_base_monthly(conn, MTM_RUN_ID, scenario)
    check_always_on_gate(monthly_df, alt_sleeve)
    base_series = compute_always_on_series(monthly_df, alt_sleeve, s_neutral_eff)

    value_spread_z = _get_z_series(conn, 'value_spread', normalization)
    size_mom_z = None
    if variant.endswith('_v2'):
        size_mom_z = _get_z_series(conn, 'size_mom_6m', normalization)

    for rebal_date, next_date, is_closed in periods():
        if mode == 'always_on':
            period_rows = monthly_df[monthly_df['period_start'] == pd.Timestamp(rebal_date)]
            for d, r in period_rows.iterrows():
                port_ret = float(base_series.loc[d])
                alt_ret = float(r[ALT_RETURN_COLUMN[alt_sleeve]])
                row = {
                    'signal_date': d.date(), 'execution_date': d.date(), 'date': d.date(),
                    's_t': s_neutral_eff, 'z_t': None, 'size_mom_z': None,
                    'port_return': port_ret, 'alt_return': alt_ret,
                    'overlay_turnover': 0.0, 'overlay_cost': 0.0, 'net_port_return': port_ret,
                    'is_oos': False,
                    'episode_tag': 'period22' if rebal_date == PERIOD22_START else 'normal',
                }
                _upsert(conn, run_id, cfg_hash, scenario, variant, tilt_option_eff, mode,
                        normalization, overlay_freq, alt_sleeve, row, rebal_date, next_date,
                        base_return=port_ret, net_base_return=port_ret)
        else:
            rows = _period_tilt_rows(conn, tag, rebal_date, next_date, is_closed, overlay_freq,
                                      alt_sleeve, value_spread_z, size_mom_z, variant,
                                      s_neutral_eff, k_eff, S_MIN, S_MAX)
            for row in rows:
                d = row['date']
                base_ret_raw = base_series.asof(pd.Timestamp(d)) if not base_series.empty else None
                base_ret = float(base_ret_raw) if pd.notna(base_ret_raw) else None
                net_base = base_ret
                _upsert(conn, run_id, cfg_hash, scenario, variant, tilt_option_eff, mode,
                        normalization, overlay_freq, alt_sleeve, row, rebal_date, next_date,
                        base_return=base_ret, net_base_return=net_base)
        conn.commit()

    log.info('조합 완료: scenario=%s variant=%s tilt_option=%s mode=%s norm=%s freq=%s alt=%s K=%s '
              '(config_hash=%s)', scenario, variant, tilt_option, mode, normalization, overlay_freq,
              alt_sleeve, k, cfg_hash)
    return cfg_hash


if __name__ == '__main__':
    conn = get_connection()
    try:
        for scenario, variants in VARIANTS.items():
            for variant in variants:
                for tilt_option in ('A_defensive', 'B_two_sided'):
                    run_combo(conn, scenario, variant, tilt_option, mode='always_on',
                              normalization='expanding_z', overlay_freq='monthly',
                              alt_sleeve='largecap_cw', k=0.0)
                    run_combo(conn, scenario, variant, tilt_option, mode='tilt',
                              normalization='expanding_z', overlay_freq='monthly',
                              alt_sleeve='largecap_cw', k=0.15)
    finally:
        conn.close()
