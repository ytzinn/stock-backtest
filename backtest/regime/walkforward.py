"""
SPEC_08 §6 Layer 2 + STEP B-5 — 사전 고정 구속조건(C1~C4) 판정, D/F 시나리오 별도(3-2).

v0.3 확정 폴드 설계:
  - B-0 Fixed-policy: 파라미터가 전부 사전고정(그리드 조합만 바뀔 뿐 데이터로 튜닝하지
    않음)이므로 fold 분할이 무의미 — 워밍업 이후 전체를 단일 OOS로 취급한다(grid.py의
    is_oos 컬럼 그대로). C3("fold 부호 안정")는 여기선 "워밍업 이후 구간을 시간순 절반
    으로 나눠 alpha 부호가 전/후 동일한지"로 재정의한다.
  - B-1 Nested-policy: B-0 통과 후에만. 확장창(expanding window) fold — fold i는
    [워밍업 끝 ~ 구간 i]까지의 과거 데이터만으로 K를 선택해 구간 i+1에 적용한다(R3:
    미래 fold 절대 미참조).

실행: venv/bin/python -m backtest.regime.walkforward
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from backtest.regime.config_phaseB import (
    ALT_SLEEVE_GRID,
    K_GRID,
    NORMALIZATION_GRID,
    OVERLAY_FREQ_GRID,
    PERIOD22_SHARE_WARN,
    PHASEB_RUN_ID,
    VARIANTS,
    config_hash,
)
from backtest.regime.grid import _load_combo_returns, summary_stats
from backtest.regime.overlay_engine import run_combo
from ingest.connection import get_connection

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

# R5: F는 value_spread 단독(F_v1)만 판단 대상. D는 D_v1(주)만 — D_v2는 exploratory(3-12).
PRIMARY_VARIANT = {'D_rim_only': 'D_v1', 'F_momentum_rim': 'F_v1'}


# ── Layer 2 — C1~C4 사전 고정 구속조건 ───────────────────────────────────────

def check_c1_ex22_alpha(stats: dict) -> bool:
    """C1: #22 없이도 부가가치가 있어야 한다(R6). 이게 무너지면 강한 배분 자체를 접는다."""
    return stats.get('ex22_alpha', -1.0) > 0


def check_c2_period22_share(stats: dict) -> bool:
    """C2: 단일 에피소드(#22) 의존이 50% 미만이어야 한다(R6)."""
    share = stats.get('period22_share', np.nan)
    return bool(not np.isnan(share) and abs(share) < PERIOD22_SHARE_WARN)


def check_c3_sign_stability(oos_df: pd.DataFrame) -> bool:
    """
    C3(v0.3 재정의) — B-0엔 fold가 없으므로, 워밍업 이후 OOS 구간을 시간순 절반으로
    나눠 alpha(=net_port−net_base) 합의 부호가 전/후 반기 동일한지로 강건성을 본다.
    """
    oos = oos_df[oos_df['is_oos'] & oos_df['net_base_return'].notna()].sort_values('date')
    if len(oos) < 4:
        return False
    alpha = oos['net_port_return'] - oos['net_base_return']
    mid = len(alpha) // 2
    first_half, second_half = float(alpha.iloc[:mid].sum()), float(alpha.iloc[mid:].sum())
    return bool((first_half > 0) == (second_half > 0))


def check_c4_economic(stats: dict) -> bool:
    """
    C4(경제적 기준, [ASSUMPTION] 약증거 — 3-10) — 아래 중 하나:
      · net CAGR +0.5%p 이상, 또는
      · net CAGR −0.2%p 이내이며 MDD 2%p 이상 개선, 또는
      · Sharpe/Calmar 개선 + ex22 방향 유지
    ★ #22 제외 시 net 개선이 음수로 뒤집히면(=C1 위반) 후보 불가 — 여기선 C1이 이미 별도 체크.
    """
    d_cagr = stats.get('cagr_improve_vs_always_on', np.nan)
    if np.isnan(d_cagr):
        return False
    if d_cagr >= 0.005:
        return True
    if d_cagr >= -0.002 and stats.get('mdd_improve_vs_always_on', -1.0) >= 0.02:
        return True
    sharpe_ok = stats.get('net_sharpe', -np.inf) > 0
    calmar_ok = stats.get('net_calmar', -np.inf) > 0
    return bool((sharpe_ok or calmar_ok) and check_c1_ex22_alpha(stats))


def evaluate_candidate(oos_df: pd.DataFrame) -> dict:
    """한 조합에 대해 C1~C4를 전부 판정. 전부 True여야 '졸업 후보'."""
    stats = summary_stats(oos_df)
    c1 = check_c1_ex22_alpha(stats)
    c2 = check_c2_period22_share(stats)
    c3 = check_c3_sign_stability(oos_df)
    c4 = check_c4_economic(stats)
    return {**stats, 'C1': c1, 'C2': c2, 'C3': c3, 'C4': c4, 'is_candidate': c1 and c2 and c3 and c4}


def layer2_candidates(run_id: str = PHASEB_RUN_ID) -> pd.DataFrame:
    """
    D/F 시나리오 별도 판정(3-2) — 각 시나리오의 PRIMARY_VARIANT에 대해서만 §5 그리드 축
    (normalization x overlay_freq x alt_sleeve x tilt_option x K)을 순회하며 C1~C4를 본다.
    """
    conn = get_connection()
    results = []
    try:
        for scenario, variant in PRIMARY_VARIANT.items():
            for tilt_option in ('A_defensive', 'B_two_sided'):
                for normalization in NORMALIZATION_GRID:
                    for overlay_freq in OVERLAY_FREQ_GRID:
                        for alt_sleeve in ALT_SLEEVE_GRID:
                            for k in K_GRID:
                                cfg_hash = config_hash(K=k, NORMALIZATION=normalization,
                                                        OVERLAY_FREQ=overlay_freq, ALT_SLEEVE=alt_sleeve)
                                df = _load_combo_returns(conn, run_id, cfg_hash, scenario,
                                                          variant, tilt_option)
                                evaluation = evaluate_candidate(df)
                                results.append({
                                    'scenario': scenario, 'variant': variant,
                                    'tilt_option': tilt_option, 'normalization': normalization,
                                    'overlay_freq': overlay_freq, 'alt_sleeve': alt_sleeve, 'k': k,
                                    **evaluation,
                                })
    finally:
        conn.close()
    return pd.DataFrame(results)


# ── B-1 Nested-policy OOS (확장창 fold, B-0 통과 후에만) ─────────────────────

def expanding_folds(period_starts: list, warmup_end_idx: int) -> list[tuple[list, object]]:
    """
    확장창 fold 목록. fold i = ([warmup_end_idx .. i-1] 과거 구간, i번째 구간).
    미래 구간은 fold 안에 절대 포함되지 않는다(R3).
    """
    folds = []
    for i in range(warmup_end_idx + 1, len(period_starts)):
        folds.append((period_starts[warmup_end_idx:i], period_starts[i]))
    return folds


def select_k_from_past(past_stats_by_k: dict[float, dict]) -> float:
    """
    과거 fold의 net utility(=net_cagr − 0.5·|MDD|, MDD-adjusted) 기준으로 K 선택.
    [ASSUMPTION] utility 가중치(0.5)는 초기값 — §8류 민감도로 흔들어볼 대상.
    """
    def _utility(stats: dict) -> float:
        cagr = stats.get('net_cagr', np.nan)
        mdd = stats.get('net_mdd', np.nan)
        if np.isnan(cagr) or np.isnan(mdd):
            return -np.inf
        return cagr - 0.5 * abs(mdd)

    return max(past_stats_by_k, key=lambda k: _utility(past_stats_by_k[k]))


def run_b1_nested(scenario: str, variant: str, tilt_option: str, normalization: str,
                   overlay_freq: str, alt_sleeve: str, run_id: str = PHASEB_RUN_ID) -> pd.DataFrame:
    """
    B-1: fold별로 과거 데이터만으로 K를 고르고 다음 구간에 적용한 결과를 모은다.
    B-0(layer2_candidates)에서 이 (scenario,variant,tilt_option,normalization,overlay_freq,
    alt_sleeve) 조합이 이미 후보로 통과했을 때만 호출할 것(§6 "B-0 통과 후에만").
    """
    from backtest.regime.overlay_engine import periods  # 지연 import, 순환 참조 방지

    conn = get_connection()
    try:
        all_periods = [p[0] for p in periods()]
        warmup_end_idx = 6   # WARMUP_M(36개월) ~= 반기 6구간, config_phaseB.WARMUP_M과 정합
        folds = expanding_folds(all_periods, warmup_end_idx)

        # K별 전체 OOS 통계를 미리 계산해두고, fold별로는 "그 시점까지의" 부분만 슬라이스
        stats_by_k = {}
        dfs_by_k = {}
        for k in K_GRID:
            cfg_hash = config_hash(K=k, NORMALIZATION=normalization, OVERLAY_FREQ=overlay_freq,
                                    ALT_SLEEVE=alt_sleeve)
            df = _load_combo_returns(conn, run_id, cfg_hash, scenario, variant, tilt_option)
            df['period_start_dt'] = pd.to_datetime(df['date'])   # 근사 정렬용
            dfs_by_k[k] = df

        results = []
        for fold_periods, test_period in folds:
            past_stats_by_k = {}
            for k in K_GRID:
                df = dfs_by_k[k]
                past_mask = df['date'] < pd.Timestamp(test_period)
                past_stats_by_k[k] = summary_stats(df[past_mask])
            chosen_k = select_k_from_past(past_stats_by_k)

            test_df = dfs_by_k[chosen_k]
            test_mask = (test_df['date'] >= pd.Timestamp(test_period))
            test_stats = summary_stats(test_df[test_mask].assign(is_oos=True))
            results.append({'test_period': test_period, 'chosen_k': chosen_k, **test_stats})
    finally:
        conn.close()

    return pd.DataFrame(results)


if __name__ == '__main__':
    candidates = layer2_candidates()
    n_pass = int(candidates['is_candidate'].sum()) if not candidates.empty else 0
    log.info('Layer 2 판정 완료: %d/%d 조합이 C1~C4 전부 통과', n_pass, len(candidates))
    for scenario in PRIMARY_VARIANT:
        sub = candidates[candidates['scenario'] == scenario]
        log.info('%s: %d/%d 통과', scenario, int(sub['is_candidate'].sum()), len(sub))
