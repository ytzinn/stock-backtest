"""
SPEC_14 B단계 — 룰×캘린더 상호작용 δ(j) + bootstrap CI + 3분류 + §8 2축 판정.

**진단 전용 — 본 실행만으로 채택 후보 없음 (§9).**

B단계는 **항상 실행**한다 (§6). 생략 가능한 유일한 경우는 §6-4 무결성 게이트 실패이며,
그때는 이 스크립트가 애초에 시작을 거부한다.

산출 (§7·§8, 전부 사전등록 — 결과 열람 후 기준 수정 금지):
  e(j,c) = g(variant_j, c) − g(incumbent, c)      공통 기간 연율 로그수익률, net 1차
  δ(j)   = e(j, 안C) − e(j, 반기)                  이중차분 = 룰×캘린더 상호작용
  Δ_EW   = g(EW_안C) − g(EW_반기)                  Q1 판정값 (룰 선별 제거)
  J1 방향 유지율 · J3 명확한 방향 반전 수 (단일축만) · J2 순위상관 (8개, 참고)

**단일축 분모는 5다** (C_R1·C_R2·C_R5·C_R6·C_MOM). v0.3 §6-3 은 `C_RANK` 를 포함해
6으로 등록했으나, `F_no_r3r4` 는 랭킹 외에 RIM 밸류에이션 컷까지 함께 바뀌어
§6-2 가 배제한 것과 같은 오염이다 — 보조 표로 강등했다 (`calsens_lib` 주석 참조).
Q2-D 문턱은 원 사전등록이 **비율이 아니라 개수**였으므로 그대로 둔다.

실행 (동결 스냅샷 워크트리에서, 크론 시간대 밖):
    venv/bin/python -m scripts.calendar_sens.integrity_gates
    venv/bin/python -m scripts.calendar_sens.stage_b
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone

import numpy as np

from scripts.calendar_sens.calsens_lib import (
    BLOCK_DAYS_PRIMARY,
    BLOCK_DAYS_SENSITIVITY,
    CALENDARS,
    CI_EQUIV,
    CI_MAIN,
    COMMON_E,
    COMMON_S,
    EPSILON,
    EW_TAG,
    INCUMBENT_TAG,
    JUDGMENT_CONTRASTS,
    MULTI_AXIS_CONTRASTS,
    N_RESAMPLES,
    OUT_DIR,
    Q1_EQUIV_BOUND,
    Q1_LARGE_ABS,
    Q2D_NEUTRAL_MAX,
    Q2D_REVERSAL_LARGE,
    Q2M_ABS,
    REQUIRED_TAGS,
    RNG_SEED,
    SIGN_PROB_THRESHOLD,
    SINGLE_AXIS_CONTRASTS,
    DIR_NEUTRAL,
    DIR_REVERSED,
    DIR_HELD,
    annualized_log_return,
    block_starts,
    bootstrap_g,
    cagr_from_g,
    calendar_tag,
    ci,
    classify_contrast,
    classify_sign,
    common_period_years,
    excludes_zero,
    expand_starts,
    index_digest,
    load_nav,
    log_returns,
    within,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

SEMI, ALT = 'SEMIANNUAL', 'C'


def _abort_if_cron_window() -> None:
    """DRIFT-INGEST-001: 크론 시간대(UTC 10:00~10:45 = KST 19:00~19:45) 실행 금지."""
    now = datetime.now(timezone.utc)
    minutes = now.hour * 60 + now.minute
    if 10 * 60 <= minutes < 10 * 60 + 45:
        raise SystemExit('DRIFT-INGEST-001: 크론 시간대(UTC 10:00~10:45) — 실행 금지.')


def _require_gates() -> dict:
    path = OUT_DIR / 'integrity_gates.json'
    if not path.exists():
        raise SystemExit(
            f'{path} 없음 — `python -m scripts.calendar_sens.integrity_gates` 를 '
            f'먼저 통과시켜라 (§6-4: 성과 산출 전 필수).'
        )
    gates = json.loads(path.read_text(encoding='utf-8'))
    if not gates.get('all_pass'):
        failed = [g['gate'] for g in gates.get('gates', []) if not g.get('pass')]
        raise SystemExit(f'무결성 게이트 불통과 {failed} — 성과 수치 미발행, 즉시 중단 (§6-4)')
    return gates


# ── 시리즈 적재 ──────────────────────────────────────────────────────────────

def load_series() -> tuple[dict[tuple[str, str], int], np.ndarray, np.ndarray, list]:
    """전 태그 × 전 캘린더의 공통 기간 일별 로그수익률 행렬.

    반환: (키→행 index, net 행렬, gross 행렬, 관측일 리스트)
    """
    keys, net_rows, gross_rows = [], [], []
    ref_index = None
    for calendar in CALENDARS:
        for tag in REQUIRED_TAGS:
            nav = load_nav(tag, calendar)
            lr_net   = log_returns(nav['nav_net'],   COMMON_S, COMMON_E)
            lr_gross = log_returns(nav['nav_gross'], COMMON_S, COMMON_E)
            if ref_index is None:
                ref_index = lr_net.index
            elif not lr_net.index.equals(ref_index) or not lr_gross.index.equals(ref_index):
                raise SystemExit(
                    f'[관측일 불일치] {calendar_tag(tag, calendar)} — §10 동일 달력일 '
                    f'pairing 이 성립하지 않는다 (G-CAL-5 재확인 필요)'
                )
            keys.append((calendar, tag))
            net_rows.append(lr_net.to_numpy(dtype=float))
            gross_rows.append(lr_gross.to_numpy(dtype=float))

    idx_map = {k: i for i, k in enumerate(keys)}
    dates = [d.date().isoformat() for d in ref_index]
    return idx_map, np.vstack(net_rows), np.vstack(gross_rows), dates


# ── 효과 계산 ────────────────────────────────────────────────────────────────

def _effects(g_vec: np.ndarray, idx_map: dict) -> dict:
    """한 벌의 g(시리즈별) 로부터 e(j,c)·δ(j)·Δ_EW 를 계산.

    `g_vec` 은 1차원(원표본) 또는 2차원 `(n_series, B)`(bootstrap) 둘 다 받는다 —
    numpy 브로드캐스트로 동일 코드가 성립한다.
    """
    def g(tag: str, calendar: str):
        return g_vec[idx_map[(calendar, tag)]]

    out = {'e': {}, 'delta': {}}
    for c in JUDGMENT_CONTRASTS:
        # baseline 은 contrast 마다 다를 수 있다 — 2×2 의 "컷 켠 상태 랭킹 비교"는
        # 인컴번트가 아니라 F_pbr_no_r3r4_rimcut 을 기준으로 재야 1축이 된다 (§14-1).
        e_semi = g(c.variant_tag, SEMI) - g(c.baseline, SEMI)
        e_alt  = g(c.variant_tag, ALT)  - g(c.baseline, ALT)
        out['e'][c.contrast_id] = {SEMI: e_semi, ALT: e_alt}
        out['delta'][c.contrast_id] = e_alt - e_semi
    out['delta_ew'] = g(EW_TAG, ALT) - g(EW_TAG, SEMI)
    return out


def _judge_q1(point: float, ci95: tuple, ci90: tuple) -> str:
    """§8-1 — 큼은 경제적 크기 AND 통계적 배제를, 작음은 equivalence 를 요구한다."""
    if abs(point) >= Q1_LARGE_ABS and excludes_zero(ci95):
        return 'Q1_LARGE'
    if within(ci90, Q1_EQUIV_BOUND):
        return 'Q1_SMALL'
    return 'Q1_INCONCLUSIVE'


def _judge_q2d(n_reversal: int, n_neutral: int) -> str:
    """§8-2 — 개별 δ(j) 의 유의성은 쓰지 않는다 (무보정 다중검정 회피)."""
    if n_reversal >= Q2D_REVERSAL_LARGE:
        return 'Q2D_LARGE'
    if n_reversal == 0 and n_neutral <= Q2D_NEUTRAL_MAX:
        return 'Q2D_SMALL'
    return 'Q2D_INCONCLUSIVE'


def _judge_q2m(single_rows: list[dict]) -> str:
    """§8-2 — '전 contrast' 는 J 분모와 동일하게 **단일축**으로 해석한다."""
    large = any(abs(r['delta_point']) >= Q2M_ABS and excludes_zero(tuple(r['delta_ci95']))
                for r in single_rows)
    small = all(within(tuple(r['delta_ci95']), Q2M_ABS) for r in single_rows)
    if large:
        return 'Q2M_LARGE'
    if small:
        return 'Q2M_SMALL'
    return 'Q2M_INCONCLUSIVE'


def _action(q1: str, q2d: str, q2m: str) -> dict:
    """§8-3 조치표 — 전 조합 포괄. Q2-D 우선."""
    if q2d == 'Q2D_LARGE':
        return {'action': 'INCUMBENT_CONFIDENCE_DOWNGRADE',
                'text': '인컴번트 룰 신뢰도 하향 — §8-4 발동 (MASTER·SPEC_05~11 경고 삽입, '
                        '#24 이후 라이브 관측 가중 상향, 대체 룰 선정 금지)'}
    if q2d == 'Q2D_INCONCLUSIVE':
        return {'action': 'NO_AUTOMATIC_ACTION',
                'text': '자동 조치 없음 — 수치 병기 후 사용자 결정. 라이브 OOS 대기'}
    # 이하 Q2-D 작음
    if q2m == 'Q2M_LARGE':
        return {'action': 'EFFECT_SIZE_INSTABILITY_WARNING',
                'text': '룰 방향은 견고하나 기대효과 크기가 캘린더 민감 — "효과 크기 불안정" '
                        '경고를 SPEC_05~11에 기재. 신뢰도 하향은 미발동'}
    if q2m == 'Q2M_INCONCLUSIVE':
        return {'action': 'NO_AUTOMATIC_ACTION',
                'text': 'Q2-D 작음이나 Q2-M 판별 불가 — 수치 병기 후 사용자 결정'}
    mapping = {
        'Q1_LARGE': ('CALENDAR_LEVEL_EFFECT_RECORDED',
                     '룰 선택 견고. 격차 원인은 캘린더·유니버스 수준효과로 기록. '
                     'SPEC_13 §9-7 해석 확정'),
        'Q1_SMALL': ('NO_EVIDENCE_CLOSE',
                     '캘린더 민감성 증거 없음. 기록 후 종결'),
        'Q1_INCONCLUSIVE': ('RULES_ROBUST_Q1_UNDETERMINED',
                            '룰은 견고. Q1 은 판별 불가로 기록'),
    }
    key, text = mapping[q1]
    return {'action': key, 'text': text}


# ── 실행 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description='SPEC_14 B단계 판정')
    ap.add_argument('--resamples', type=int, default=N_RESAMPLES,
                    help='bootstrap 반복 수 (사전등록 2000 — 변경 시 비공표 실행)')
    ap.add_argument('--skip-sensitivity', action='store_true',
                    help='§10-1 block 길이 민감도(10·63) 생략')
    args = ap.parse_args()

    _abort_if_cron_window()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gates = _require_gates()
    log.info('무결성 게이트 통과 확인 (%s)', gates['generated_at'])

    idx_map, net_mat, gross_mat, dates = load_series()
    n = net_mat.shape[1]
    years = common_period_years()
    log.info('공통 기간 %s~%s | 관측일 %d개 → 일별 로그수익률 %d개 | years=%.6f',
             dates[0], dates[-1], n + 1, n, years)

    # 원표본 g
    g_net   = np.array([annualized_log_return(row, years) for row in net_mat])
    g_gross = np.array([annualized_log_return(row, years) for row in gross_mat])
    point_net, point_gross = _effects(g_net, idx_map), _effects(g_gross, idx_map)

    # bootstrap — 전역 단일 RNG, 반복마다 block index 1개를 전 셀에 동일 적용 (§10)
    log.info('bootstrap: block=%d거래일, %d회, seed=%r (전역 단일)',
             BLOCK_DAYS_PRIMARY, args.resamples, RNG_SEED)
    starts = block_starts(n, BLOCK_DAYS_PRIMARY, args.resamples)
    idx    = expand_starts(starts, BLOCK_DAYS_PRIMARY, n)
    digest = index_digest(idx)
    boot_net   = _effects(bootstrap_g(net_mat,   idx, years), idx_map)
    boot_gross = _effects(bootstrap_g(gross_mat, idx, years), idx_map)

    # ── contrast 별 결과 ─────────────────────────────────────────────────────
    rows = []
    for c in JUDGMENT_CONTRASTS:
        b_e = boot_net['e'][c.contrast_id]
        sign_semi, p_pos_semi, p_neg_semi = classify_sign(b_e[SEMI])
        sign_alt,  p_pos_alt,  p_neg_alt  = classify_sign(b_e[ALT])
        d_boot = boot_net['delta'][c.contrast_id]
        rows.append({
            'contrast_id': c.contrast_id, 'variant_tag': c.variant_tag,
            'baseline_tag': c.baseline, 'group': c.group,
            'composition': c.composition, 'axis': c.axis, 'n_axes': c.n_axes,
            'single_axis': c.single_axis, 'note': c.note,
            'semiannual_gross_ref_full_period': c.semi_gross_ref,

            'e_semiannual_net': float(point_net['e'][c.contrast_id][SEMI]),
            'e_altC_net':       float(point_net['e'][c.contrast_id][ALT]),
            'e_semiannual_gross': float(point_gross['e'][c.contrast_id][SEMI]),
            'e_altC_gross':       float(point_gross['e'][c.contrast_id][ALT]),
            'e_semiannual_ci95': list(ci(b_e[SEMI])),
            'e_altC_ci95':       list(ci(b_e[ALT])),
            'p_e_gt_eps_semiannual': p_pos_semi, 'p_e_lt_neg_eps_semiannual': p_neg_semi,
            'p_e_gt_eps_altC':       p_pos_alt,  'p_e_lt_neg_eps_altC':       p_neg_alt,
            'sign_semiannual': sign_semi, 'sign_altC': sign_alt,
            'direction_class': classify_contrast(sign_semi, sign_alt),

            'delta_point':       float(point_net['delta'][c.contrast_id]),
            'delta_point_gross': float(point_gross['delta'][c.contrast_id]),
            'delta_ci95':        list(ci(d_boot)),
            'delta_ci95_excludes_zero': bool(excludes_zero(ci(d_boot))),
            'p_delta_gt_0':      float(np.mean(d_boot > 0.0)),
        })

    rule    = [r for r in rows if r['group'] == 'rule']
    rankcut = [r for r in rows if r['group'] == 'rank_cut']
    single  = [r for r in rule if r['single_axis']]      # J 분모 — 룰 단일축만
    multi   = [r for r in rule if not r['single_axis']]

    n_rev     = sum(1 for r in single if r['direction_class'] == DIR_REVERSED)
    n_held    = sum(1 for r in single if r['direction_class'] == DIR_HELD)
    n_neutral = sum(1 for r in single if r['direction_class'] == DIR_NEUTRAL)
    j1 = (n_held / (n_held + n_rev)) if (n_held + n_rev) else None

    from scipy.stats import kendalltau, spearmanr
    e_s = [r['e_semiannual_net'] for r in rule]
    e_a = [r['e_altC_net'] for r in rule]
    j2 = {'spearman': float(spearmanr(e_s, e_a).statistic),
          'kendall':  float(kendalltau(e_s, e_a).statistic),
          'n_contrasts': len(rule),
          'status': '참고만 — 효과크기 격차가 크면 자동으로 높아진다 (§7-3). '
                    '룰 contrast 7개 기준 (랭킹×컷 2×2 제외)'}

    # ── Q1 / Q2 ──────────────────────────────────────────────────────────────
    dew_boot = boot_net['delta_ew']
    dew = {
        'point_net':   float(point_net['delta_ew']),
        'point_gross': float(point_gross['delta_ew']),
        'ci95': list(ci(dew_boot, CI_MAIN)), 'ci90': list(ci(dew_boot, CI_EQUIV)),
        'g_ew_semiannual_net': float(g_net[idx_map[(SEMI, EW_TAG)]]),
        'g_ew_altC_net':       float(g_net[idx_map[(ALT,  EW_TAG)]]),
        'cagr_ew_semiannual_net': cagr_from_g(float(g_net[idx_map[(SEMI, EW_TAG)]])),
        'cagr_ew_altC_net':       cagr_from_g(float(g_net[idx_map[(ALT,  EW_TAG)]])),
        'name': '룰 선별을 제거한 캘린더·유니버스 수준효과 — "순수 캘린더 효과"가 아니다 (§5 A-1)',
    }
    q1  = _judge_q1(dew['point_net'], tuple(dew['ci95']), tuple(dew['ci90']))
    q2d = _judge_q2d(n_rev, n_neutral)
    q2m = _judge_q2m(single)

    # ── §10-1 block 길이 민감도 (판정 비사용) ────────────────────────────────
    sensitivity = {}
    if not args.skip_sensitivity:
        for blk in BLOCK_DAYS_SENSITIVITY:
            if blk == BLOCK_DAYS_PRIMARY:
                sens_boot = boot_net
            else:
                s2 = block_starts(n, blk, args.resamples, seed=f'{RNG_SEED}:SENS:{blk}')
                sens_boot = _effects(
                    bootstrap_g(net_mat, expand_starts(s2, blk, n), years), idx_map)
            sensitivity[str(blk)] = {
                'delta_ew_ci95': list(ci(sens_boot['delta_ew'])),
                'delta_ew_excludes_zero': bool(excludes_zero(ci(sens_boot['delta_ew']))),
                'delta_excludes_zero': {
                    cid: bool(excludes_zero(ci(sens_boot['delta'][cid])))
                    for cid in (c.contrast_id for c in JUDGMENT_CONTRASTS)
                },
            }
        base = sensitivity[str(BLOCK_DAYS_PRIMARY)]
        sensitivity['flips_vs_block21'] = {
            str(blk): sorted(
                [cid for cid, v in sensitivity[str(blk)]['delta_excludes_zero'].items()
                 if v != base['delta_excludes_zero'][cid]]
                + (['Δ_EW'] if sensitivity[str(blk)]['delta_ew_excludes_zero']
                   != base['delta_ew_excludes_zero'] else [])
            )
            for blk in BLOCK_DAYS_SENSITIVITY if blk != BLOCK_DAYS_PRIMARY
        }
        sensitivity['status'] = '판정 비사용 — CI 0 배제 여부의 변화만 병기 (§10-1)'

    result = {
        'spec': 'SPEC_14 B단계',
        'disclaimer': '진단 전용 — 본 실행만으로 채택 후보 없음 (§9)',
        'generated_at': datetime.now().isoformat(),
        'common_period': {'S': COMMON_S.isoformat(), 'E': COMMON_E.isoformat(),
                          'n_obs': n + 1, 'n_daily_log_returns': n, 'years': years},
        'pre_registered': {
            'epsilon_net_log': EPSILON, 'sign_prob_threshold': SIGN_PROB_THRESHOLD,
            'q1_large_abs': Q1_LARGE_ABS, 'q1_equiv_bound': Q1_EQUIV_BOUND,
            'q2m_abs': Q2M_ABS, 'q2d_reversal_large': Q2D_REVERSAL_LARGE,
            'q2d_neutral_max': Q2D_NEUTRAL_MAX,
            'block_days': BLOCK_DAYS_PRIMARY, 'n_resamples': args.resamples,
            'rng_seed': RNG_SEED,
            'g_definition': 'sum(log(1+r_t)) / years, years=(E−S).days/365.25 — CAGR=exp(g)−1',
            'single_axis_contrasts': [c.contrast_id for c in SINGLE_AXIS_CONTRASTS],
            'multi_axis_contrasts':  [c.contrast_id for c in MULTI_AXIS_CONTRASTS],
            'note': '§12 N1~N5 [확정 2026-08-06, 사용자]. C_RANK 는 구현 단계에서 다축 강등.',
        },
        'bootstrap_provenance': {
            'block_index_digest_sha256': digest,
            'starts_file': 'block_starts_21.npz',
            'expansion_rule': 'idx = concat_k((starts[b,k] + arange(block)) % n)[:n]',
        },
        'contrasts_single_axis': single,
        'contrasts_multi_axis':  multi,
        'contrasts_rank_cut_2x2': {
            'cells': rankcut,
            'design': {
                '(1/PBR, 컷없음)': INCUMBENT_TAG + '  ← 인컴번트 = 2×2 원점',
                '(RIM,   컷없음)': 'F_rimrank_no_r3r4',
                '(1/PBR, 컷있음)': 'F_pbr_no_r3r4_rimcut',
                '(RIM,   컷있음)': 'F_no_r3r4',
            },
            'read_as': {
                '세트1 (컷 끈 상태의 랭킹 효과)': 'C_RANK_NOCUT',
                '세트2 (컷 켠 상태의 랭킹 효과)': 'C_RANK_CUT',
                '컷 효과 (랭킹 고정)':            'C_RIMCUT',
                '결합 (두 축 동시)':              'C_RANK',
            },
            'status': 'J1·J3·Q2-D·Q2-M 분모 제외 — J 계열은 "어느 룰의 방향이 '
                      '뒤집혔나"를 재는 지표라 랭킹·컷 축을 섞으면 룰 견고성 판정이 '
                      '희석된다 (§7-3, §14-1). 방향 분류는 룰과 같은 규칙으로 산출.',
        },
        'delta_ew': dew,
        'summary_metrics': {
            'J1_direction_hold_rate': j1, 'J1_denominator': n_held + n_rev,
            'J3_clear_reversals': n_rev, 'n_direction_held': n_held,
            'n_neutral_inconclusive': n_neutral,
            'J2_rank_correlation': j2,
        },
        'judgment': {
            'Q1': q1, 'Q2_D': q2d, 'Q2_M': q2m, **_action(q1, q2d, q2m),
        },
        'block_length_sensitivity': sensitivity,
    }

    (OUT_DIR / 'stage_b.json').write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    np.savez_compressed(OUT_DIR / f'block_starts_{BLOCK_DAYS_PRIMARY}.npz',
                        starts=starts, n=n, block=BLOCK_DAYS_PRIMARY,
                        seed=RNG_SEED, digest=digest)
    (OUT_DIR / 'stage_b_tables.md').write_text(_markdown(result), encoding='utf-8')

    # ── 콘솔 ────────────────────────────────────────────────────────────────
    log.info('── 단일축 %d개 (판정용) ──', len(single))
    for r in single:
        log.info('  %-7s e(반기)=%+.4f%%p e(안C)=%+.4f%%p → δ=%+.4f%%p CI95=[%+.4f, %+.4f] %s',
                 r['contrast_id'], r['e_semiannual_net'] * 100, r['e_altC_net'] * 100,
                 r['delta_point'] * 100, r['delta_ci95'][0] * 100, r['delta_ci95'][1] * 100,
                 r['direction_class'])
    log.info('── 다축 %d개 (보조, 판정 비사용) ──', len(multi))
    for r in multi:
        log.info('  %-7s δ=%+.4f%%p CI95=[%+.4f, %+.4f] %s',
                 r['contrast_id'], r['delta_point'] * 100,
                 r['delta_ci95'][0] * 100, r['delta_ci95'][1] * 100, r['direction_class'])
    log.info('── 랭킹×컷 2×2 %d셀 (J 분모 제외) ──', len(rankcut))
    for r in rankcut:
        log.info('  %-13s %-22s vs %-22s δ=%+.4f%%p CI95=[%+.4f, %+.4f] %s',
                 r['contrast_id'], r['variant_tag'], r['baseline_tag'],
                 r['delta_point'] * 100, r['delta_ci95'][0] * 100,
                 r['delta_ci95'][1] * 100, r['direction_class'])
    log.info('Δ_EW = %+.4f%%p  CI95=[%+.4f, %+.4f]  CI90=[%+.4f, %+.4f]',
             dew['point_net'] * 100, dew['ci95'][0] * 100, dew['ci95'][1] * 100,
             dew['ci90'][0] * 100, dew['ci90'][1] * 100)
    log.info('J1=%s  J3(반전)=%d  중립·불확정=%d  J2 spearman=%.3f',
             f'{j1:.3f}' if j1 is not None else 'n/a', n_rev, n_neutral, j2['spearman'])
    log.info('판정: %s | %s | %s → %s', q1, q2d, q2m, result['judgment']['action'])
    log.info('%s', result['judgment']['text'])
    log.info('저장: %s', OUT_DIR / 'stage_b.json')


def _markdown(r: dict) -> str:
    """§11 "표를 세 개로 분리 발행" — 보고서에 그대로 붙일 표 조각."""
    pct = lambda x: f'{x * 100:+.4f}'   # noqa: E731

    def table(rows: list[dict]) -> str:
        head = ('| contrast | variant | baseline | 축 | e(반기) | e(안C) | δ | δ 95% CI | 분류 |\n'
                '|---|---|---|---|---|---|---|---|---|\n')
        body = ''.join(
            f"| `{x['contrast_id']}` | `{x['variant_tag']}` | `{x['baseline_tag']}` | {x['axis']} "
            f"| {pct(x['e_semiannual_net'])} | {pct(x['e_altC_net'])} | **{pct(x['delta_point'])}** "
            f"| [{pct(x['delta_ci95'][0])}, {pct(x['delta_ci95'][1])}] "
            f"| {x['direction_class']} |\n" for x in rows)
        return head + body

    d, j, s = r['delta_ew'], r['judgment'], r['summary_metrics']
    return (
        '> **진단 전용 — 본 실행만으로 채택 후보 없음** (SPEC_14 §9)\n\n'
        f"단위 %p, net 연율 로그수익률. 공통 기간 {r['common_period']['S']}~"
        f"{r['common_period']['E']} ({r['common_period']['n_obs']}관측일).\n\n"
        '## ① 룰 단일축 (판정용)\n\n' + table(r['contrasts_single_axis']) +
        '\n## ② 룰 다축 (보조 — J1·J3 분모 제외)\n\n' + table(r['contrasts_multi_axis']) +
        '\n## ③ 랭킹 × 밸류에이션컷 2×2 (J 분모 제외)\n\n'
        '```\n          컷 없음                 컷 있음\n'
        '1/PBR     F_pbr_no_r3r4(인컴번트)   F_pbr_no_r3r4_rimcut\n'
        'RIM       F_rimrank_no_r3r4        F_no_r3r4\n```\n\n'
        + table(r['contrasts_rank_cut_2x2']['cells']) +
        '\n## ④ 탐색 셀\n\n(없음 — 추가 시 `exploratory=true`, 추가 시점·사유 병기, §7-4)\n'
        '\n## Q1 — Δ_EW\n\n'
        f"| 지표 | 값 |\n|---|---|\n"
        f"| Δ_EW (net) | **{pct(d['point_net'])}%p** |\n"
        f"| 95% CI | [{pct(d['ci95'][0])}, {pct(d['ci95'][1])}] |\n"
        f"| 90% CI (equivalence 판정용) | [{pct(d['ci90'][0])}, {pct(d['ci90'][1])}] |\n"
        f"| EW 반기 / 안C net CAGR | {d['cagr_ew_semiannual_net'] * 100:.4f}% / "
        f"{d['cagr_ew_altC_net'] * 100:.4f}% |\n"
        '\n## 판정\n\n'
        f"| 축 | 결과 |\n|---|---|\n"
        f"| Q1 (캘린더·유니버스 수준효과) | **{j['Q1']}** |\n"
        f"| Q2-D (방향 견고성) | **{j['Q2_D']}** — 반전 {s['J3_clear_reversals']}개 / "
        f"중립·불확정 {s['n_neutral_inconclusive']}개 (단일축 5) |\n"
        f"| Q2-M (크기 민감성) | **{j['Q2_M']}** |\n"
        f"| J1 방향 유지율 | {s['J1_direction_hold_rate']} (분모 {s['J1_denominator']}) |\n"
        f"| J2 순위상관 (참고) | spearman {s['J2_rank_correlation']['spearman']:.3f} |\n"
        f"\n**조치**: {j['text']}\n"
    )


if __name__ == '__main__':
    main()
