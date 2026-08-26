"""SPEC_15 `S-5a`(A-1) — CAGR 추정량의 최소검출효과(MDE).

사전등록: `docs/설계/SPEC_15_cross_sectional_estimator_v0.4.md` §1-2 (커밋 `c369401`).

**임무는 §0-1(a) 전제의 재확인이다.** "18~19구간 CAGR 로 1~2%p 를 구분할 수 없다" 가
SPEC_15 의 착수 근거인데, 그것이 실측으로 뒤집히면 SPEC_15 는 착수하지 않는다.
**통과를 전제하지 않는다.**

이 스크립트는 **DB 에 접속하지 않는다** — 산출물 파일만 읽는다 (`_guard`).

── 산식 유도 (SPEC 문서에서 베끼지 않았다. 오라클이 고정한다) ──────────────────

`g` 의 정의는 `calsens_lib.annualized_log_return` 이 SSOT 다:

    g = Σ_t x_t / years,     x_t = log(1 + r_t),   years = 실제 캘린더 경과연수

`x_t` 를 iid(평균 μ, 분산 σ²)로 보면 `Σx` 의 분산은 `T·σ²` 이므로

    Var(g) = T·σ² / years²        →        SE(g) = σ·√T / years

`[Claude 의견]` 설계서 v0.2~v0.4 는 이것을 `2σ/√T` 로 적었다. 반기 2회/년이면
`years = T/2` 라 `√T/years = 2/√T` 가 되어 **같은 값이지만, 그것은 근사다.**
실제 `years` 는 캘린더 경과일수라 정확히 `T/2` 가 아니다 (CORR-METRIC-002 와 같은 이유).
여기서는 `years` 를 그대로 쓴다 — 상수 2 를 쓰지 않는다.

`[검증된 사실]` v0.1 은 이 자리에 `√2` 를 적었고(합의 변동성 승수, 평균의 것이 아니다),
그 식이 검증 없이 스크립트로 복사돼 SE 를 √2배 과소추정했다. 그래서 승수는
`tests/oracle/test_xsec_power_oracle.py` 가 **몬테카를로로** 고정한다 — 산식 대 산식이
아니라 산식 대 추정량의 실제 표집분포다.

실행:
    python -m scripts.xsec.power --stage a1
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
from datetime import date
from pathlib import Path
from statistics import NormalDist

import numpy as np

from scripts.analysis.baseline_registry import local_root, verify
from scripts.calendar_sens.calsens_lib import (
    annualized_log_return,
    cagr_from_g,
    common_period_years,
)
from scripts.xsec._guard import assert_no_db_imported

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger(__name__)

OUT_DIR = Path('experiments/analysis/2026.08.25._xsec_prelim')
A0_PATH = OUT_DIR / 'a0_period_set.json'

DEFAULT_BASELINE = 'shadow_20260819'
F_PERIODS = 'experiments/ablation/F_pbr_ma200_n13_periods.csv'

BOOT_B    = 200_000
BOOT_SEED = 20260825

ALPHA = 0.05      # 단측
POWER = 0.80

#: 해석적·부트스트랩 SE 합치 밴드 (§2-1). 중심은 √((T−1)/T), 폭은 MC 표준오차의 3σ 여유.
RATIO_BAND = 5e-4


# ── 순수 함수 (오라클 대상) ─────────────────────────────────────────────────
def g_vec(logr_matrix: np.ndarray, years: float) -> np.ndarray:
    """재표본 행렬 → g 벡터. `annualized_log_return` 의 벡터화판 (오라클이 동치를 고정)."""
    return logr_matrix.sum(axis=1) / years


def se_g_analytic(logr: np.ndarray, years: float) -> float:
    """SE(g) = σ·√T / years.  σ 는 표본 sd(ddof=1)."""
    T = len(logr)
    return float(np.std(logr, ddof=1) * math.sqrt(T) / years)


def se_g_bootstrap(logr: np.ndarray, years: float, b: int = BOOT_B,
                   seed: int = BOOT_SEED) -> tuple[float, np.ndarray]:
    """재표본(복원추출) g 분포의 sd. seed 고정 — 같은 seed 면 비트 동일."""
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(logr), size=(b, len(logr)))
    gs  = g_vec(logr[idx], years)
    return float(gs.std(ddof=1)), gs


def mde_one_sided(se: float, alpha: float = ALPHA, power: float = POWER) -> float:
    """단측 MDE = (z_{1−α} + z_{power}) · SE.  z 는 stdlib 정규분포에서 — 상수 베끼지 않는다."""
    nd = NormalDist()
    return (nd.inv_cdf(1 - alpha) + nd.inv_cdf(power)) * se


def annual_vol(logr: np.ndarray, years: float) -> float:
    """연율 변동성 = σ · √(구간/년).  SPEC_15 §9-3 `[VERIFY]` 입력."""
    return float(np.std(logr, ddof=1) * math.sqrt(len(logr) / years))


# ── A-1 ─────────────────────────────────────────────────────────────────────
def _load_rows(baseline: str) -> list[dict]:
    root = local_root(baseline)
    return list(csv.DictReader((root / F_PERIODS).open(encoding='utf-8')))


def _slice(rows: list[dict], dates: list[str]) -> tuple[np.ndarray, np.ndarray, float, str, str]:
    """선택 구간의 (net logr, gross logr, years, start, end)."""
    keep = [r for r in rows if r['rebalance_date'] in set(dates)]
    keep.sort(key=lambda r: r['rebalance_date'])
    if len(keep) != len(dates):
        raise SystemExit(f'FATAL 구간 수 불일치 {len(keep)} != {len(dates)}')
    s, e = keep[0]['rebalance_date'], keep[-1]['next_date']
    years = common_period_years(date.fromisoformat(s), date.fromisoformat(e))
    net   = np.log1p(np.array([float(r['net_return']) for r in keep]))
    gross = np.log1p(np.array([float(r['period_return']) for r in keep]))
    return net, gross, years, s, e


def _one(logr: np.ndarray, years: float, label: str) -> dict:
    T   = len(logr)
    g   = annualized_log_return(logr, years)          # SSOT
    an  = se_g_analytic(logr, years)
    bs, _ = se_g_bootstrap(logr, years)
    # 합치 조건 — **밴드**다. 부트스트랩 SE 의 기댓값이 해석적 SE × √((T−1)/T) 이므로
    # (부트스트랩은 모집단 sd `÷n`, 해석식은 표본 sd `÷n−1`) 그 값은 하한이 아니라 **중심**이다.
    #
    # `[정정 2026-08-25]` 최초 구현은 단측 하한(`비 ≥ √((T−1)/T)`)이었다. 하한이 곧
    # 기댓값이면 MC 잡음이 위아래로 균등해 **약 50% 확률로 실패하는 동전 던지기**가 된다
    # — 검사가 아니다. 직전 세션이 통과한 것은 위쪽이 나왔기 때문일 뿐이다(+2.6e-5).
    # B=200,000 에서 부트스트랩 SE 의 MC 표준오차 ≈ SE/√(2B) ≈ 1.3e-4 이므로 ±5e-4 는
    # 3σ보다 넉넉하면서 진짜 산식 오류(×√2 = 41% 차이)는 확실히 잡는다.
    ratio, floor = bs / an, math.sqrt((T - 1) / T)
    ok = abs(ratio - floor) < RATIO_BAND
    mde = mde_one_sided(bs)
    log.info('  [%s] T=%d  years=%.4f  g=%.6f  CAGR=%.4f%%', label, T, years, g,
             cagr_from_g(g) * 100)
    log.info('      SE 해석적=%.6f  부트스트랩=%.6f  비(bs/an)=%.6f  하한 √((T−1)/T)=%.6f  -> %s',
             an, bs, ratio, floor, 'OK' if ok else 'FAIL')
    log.info('      |비 − 중심| = %.3e  (밴드 %.0e)', abs(ratio - floor), RATIO_BAND)
    log.info('      MDE(단측 α=%.2f, 검정력 %.2f) = %.4f%%p (로그)  →  CAGR 환산 %.4f%%p',
             ALPHA, POWER, mde * 100, (math.exp(mde) - 1) * 100)
    log.info('      연율 변동성 = %.4f%%', annual_vol(logr, years) * 100)
    if not ok:
        raise SystemExit('FATAL 해석적·부트스트랩 SE 불합치 — 중단. 문턱을 완화하지 마라.')
    return {'label': label, 'T': T, 'years': years, 'g': g, 'cagr': cagr_from_g(g),
            'se_analytic': an, 'se_bootstrap': bs, 'ratio': ratio, 'ratio_center': floor,
            'agree': ok, 'ratio_band': RATIO_BAND,
            'ratio_dev_from_center': abs(ratio - floor), 'mde_log': mde, 'mde_cagr_equiv': math.exp(mde) - 1,
            'annual_vol': annual_vol(logr, years),
            'alpha': ALPHA, 'power': POWER, 'tail': 'one-sided',
            'boot_b': BOOT_B, 'boot_seed': BOOT_SEED}


def stage_a1(baseline: str = DEFAULT_BASELINE) -> dict:
    if not A0_PATH.exists():
        raise SystemExit(f'FATAL A-0 산출물이 없다: {A0_PATH}. period_set 을 먼저 돌려라.')
    a0 = json.loads(A0_PATH.read_text(encoding='utf-8'))
    rows = _load_rows(baseline)

    # primary = n=18 (G1 이 쓴 추정량이 대상이다. SPEC_15 의 T=19 가 아니다)
    t18 = a0['period_set_t18']['dates']
    t19 = a0['period_set']['dates']

    log.info('== A-1 (primary = n=18, G1 이 쓴 추정량 · net) ==')
    net18, gross18, y18, s18, e18 = _slice(rows, t18)
    primary = _one(net18, y18, 'primary net n=18')
    primary.update(period_set_id=a0['period_set_t18']['id'],
                   period_set_sha256=a0['period_set_t18']['sha256'], start=s18, end=e18)

    log.info('')
    log.info('== 진단 병기 (§0-1(a) 판단에 쓰지 않는다) ==')
    net19, _gross19, y19, s19, e19 = _slice(rows, t19)
    diag = {
        'gross_n18': _one(gross18, y18, '진단 gross n=18'),
        'net_n19':   _one(net19, y19, '진단 net n=19'),
    }
    diag['net_n19'].update(period_set_id=a0['period_set']['id'],
                           period_set_sha256=a0['period_set']['sha256'],
                           start=s19, end=e19)

    # 재현성 — 같은 seed 는 비트 동일, 다른 seed 는 소수 셋째 자리 이내
    a, _ = se_g_bootstrap(net18, y18, seed=BOOT_SEED)
    b, _ = se_g_bootstrap(net18, y18, seed=BOOT_SEED)
    c, _ = se_g_bootstrap(net18, y18, seed=BOOT_SEED + 1)
    repro = {'same_seed_identical': a == b, 'same_seed_value': a,
             'alt_seed_value': c, 'abs_diff_alt_seed': abs(a - c),
             'alt_seed_within_1e-3': abs(a - c) < 1e-3}
    log.info('')
    log.info('== 재현성 ==')
    log.info('  같은 seed 2회 비트 동일 = %s (%.12f)', repro['same_seed_identical'], a)
    log.info('  다른 seed |차| = %.3e  (< 1e-3 = %s)', repro['abs_diff_alt_seed'],
             repro['alt_seed_within_1e-3'])
    if not repro['same_seed_identical'] or not repro['alt_seed_within_1e-3']:
        raise SystemExit('FATAL 재현성 실패')

    # §0-1(a) 판정 — MDE 가 1~2%p 를 구분할 만큼 작으면 SPEC_15 미착수
    mde_pp = primary['mde_cagr_equiv'] * 100
    resolves_1to2 = mde_pp <= 2.0
    verdict = 'HALT — SPEC_15 미착수' if resolves_1to2 else 'PASS — 전제 유지, SPEC_15 계속'
    log.info('')
    log.info('== §0-1(a) 판정 ==')
    log.info('  MDE(CAGR 환산) = %.4f%%p  vs  구분 대상 1~2%%p  →  %s', mde_pp, verdict)

    return {
        'prereg': 'SPEC_15 v0.4 (c369401) §1-2 / §0-1(a)',
        'note_footnote': ('이 MDE 는 1표본 근사다. G1 은 실제로 같은 풀 무작위 추첨 p95 '
                          '비교이므로 검정 구조가 다르다. **결론이 같아도 다른 계산이다.**'),
        'primary': primary, 'diagnostic': diag, 'reproducibility': repro,
        'verdict_0_1_a': {'mde_cagr_pp': mde_pp, 'target_pp': [1.0, 2.0],
                          'resolves_1to2pp': resolves_1to2, 'verdict': verdict},
    }


def main() -> None:
    assert_no_db_imported('scripts.xsec.power')
    ap = argparse.ArgumentParser()
    ap.add_argument('--stage', required=True, choices=['a1', 'a2'])
    ap.add_argument('--baseline', default=DEFAULT_BASELINE)
    args = ap.parse_args()

    if args.stage == 'a2':
        stage_a2()
        return
    prov = verify(args.baseline, [F_PERIODS])
    log.info('== 기준선 %s · commit=%s · 공식자격=%s ==', prov['baseline'], prov['commit'],
             prov['valid_for_official_numbers'])
    res = stage_a1(args.baseline)
    res['provenance'] = prov
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / f'{args.stage}_power.json'
    p.write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    log.info('→ %s', p)




# ── A-2 (SPEC_15 §6, S-5b) ──────────────────────────────────────────────────
#
# 유도 (SPEC 에서 베끼지 않았다. 오라클이 몬테카를로로 고정한다):
#   mean(IC) 의 표준오차가 두 가지로 잡힌다 — 무엇을 귀무로 두느냐가 다르다.
#     MDE_perm : 분모 = permutation 귀무의 sd. 이미 **mean(IC) 의 sd** 다
#                (permutation_mean_ic 가 재표본마다 mean 을 돌려주므로).
#                → SE = sd_null_ic          → MDE = z · sd_null_ic
#     MDE_ICIR : 분모 = **실현** sd(IC_t). 구간별 계열의 sd 이므로 평균의 SE 로
#                바꾸려면 √T 로 나눈다.
#                → SE = sd_realized_ic/√T   → MDE = z · sd_realized_ic/√T
#   z = z_{1−α} + z_{power}  (단측). A-1 과 같은 `mde_one_sided` 를 쓴다.
#
# `[Claude 의견]` 실현 sd 가 귀무 sd 보다 크면 MDE_ICIR 가 더 크다 — 참 IC 가 시간에
# 따라 변하기 때문이다. permutation 은 그 시변성을 귀무에 넣지 않아 낙관적이다.
# §7-3 분기가 보수적인 쪽(MDE_ICIR)을 쓰는 이유다.

def se_mean_ic_from_null(sd_null_ic: float) -> float:
    """permutation 귀무는 이미 mean(IC) 의 분포다 — √T 로 다시 나누지 않는다."""
    return float(sd_null_ic)


def se_mean_ic_from_series(sd_realized_ic: float, t: int) -> float:
    """구간별 IC 계열의 sd → 평균의 SE."""
    return float(sd_realized_ic / math.sqrt(t))


def predicted_detection_rate(design_ic: float, se_mean_ic: float,
                             alpha: float = ALPHA, power_unused: float = 0.0) -> float:
    """정규근사 검출 성공률 — P-2 실측과 교차확인할 예측값."""
    nd = NormalDist()
    return float(nd.cdf(design_ic / se_mean_ic - nd.inv_cdf(1 - alpha)))


def stage_a2() -> dict:
    """A-2 — MDE_perm · MDE_ICIR.

    ⚠ **§6-1 방화벽**: 실현 IC 계열을 계산하지만 `sd` 만 남긴다.
    `mean(IC)`·`ICIR`·`t`·`p` 를 변수로도 만들지 않고, 산출물·로그 어디에도 넣지 않는다.
    S-4(`estimate.py`)가 유일한 노출 지점이다.
    """
    import pandas as pd

    from scripts.xsec import ic as icmod
    from scripts.xsec.controls import PANEL, _periods
    from scripts.xsec.firewall import assert_whitelisted

    a0 = json.loads(A0_PATH.read_text(encoding='utf-8'))
    ps = a0['period_set']
    ctl = json.loads(Path('experiments/analysis/2026.08.26._xsec_controls/controls.json')
                     .read_text(encoding='utf-8'))
    if ctl.get('all_pass') is not True:
        raise SystemExit('FATAL 대조군이 서지 않았다 — A-2 는 N-1·P-2 를 재료로 쓴다.')

    periods = _periods(pd.read_parquet(PANEL), ps['dates'])
    T = len(periods)

    # 실현 sd 만 취한다. 아래 한 줄이 §6-1 이 지키는 지점이다.
    sd_realized = float(icmod.ic_series(periods).std(ddof=1))

    sd_null  = float(ctl['N1']['sd_null_ic'])
    se_perm  = se_mean_ic_from_null(sd_null)
    se_icir  = se_mean_ic_from_series(sd_realized, T)
    mde_perm = mde_one_sided(se_perm)
    mde_icir = mde_one_sided(se_icir)

    log.info('== A-2 (SPEC_15 §6) ==')
    log.info('  T=%d  sd_null_ic=%.6f  sd_realized_ic=%.6f  (실현/귀무 = %.3f배)',
             T, sd_null, sd_realized, se_icir / se_perm)
    log.info('  MDE_perm  = %.6f   (분모 = permutation 귀무)', mde_perm)
    log.info('  MDE_ICIR  = %.6f   (분모 = 실현 sd(IC_t)/√T) ← §7-3 분기는 이쪽', mde_icir)

    # 교차확인 — P-2 실측 검출률 vs 정규근사 예측
    log.info('  교차확인 (P-2 실측 vs 정규근사):')
    worst = 0.0
    for k, lv in ctl['P2']['levels'].items():
        pred = predicted_detection_rate(lv['design_ic'], se_perm)
        d = abs(pred - lv['detection_rate']); worst = max(worst, d)
        log.info('    설계 IC=%s  실측=%.3f  예측=%.3f  |차|=%.3f',
                 k, lv['detection_rate'], pred, d)
    # 허용오차 0.05 의 근거 (§4-1): R=1,000 반복에서 검출률 p̂ 의 SE = √(p(1−p)/R) 이고
    # p=0.5 에서 최대 0.0158 이다. 0.05 는 그 3σ 남짓이며, 근사가 √2 배쯤 어긋나는 진짜
    # 오류(예: SE 차원 혼동)는 검출률을 0.2 이상 움직이므로 확실히 걸린다.
    # a2 산출물에는 적지 않는다 — §6-1 화이트리스트를 근거 한 줄 때문에 열지 않는다.
    log.info('  최대 |차| = %.3f  (허용 0.05 = 검출률 SE 의 약 3σ)  -> %s',
             worst, 'OK' if worst < 0.05 else 'FAIL')
    if worst >= 0.05:
        raise SystemExit('FATAL P-2 실측과 정규근사가 어긋난다 — 근사 쪽을 의심하라. 문턱을 낮추지 마라.')

    # §4-1 경계 대비 위치 — **기록만 한다. 판정은 S-6.**
    ic_econ, ic_lit = 0.5 / math.sqrt(26), 0.03
    log.info('  경계 대비 (기록만, 판정 아님): IC*_econ=%.4f  IC*_lit=%.4f', ic_econ, ic_lit)
    log.info('    MDE_ICIR %s IC*_lit,  %s IC*_econ',
             '<' if mde_icir < ic_lit else '>=', '<' if mde_icir < ic_econ else '>=')

    out = {
        'period_set': {'id': ps['id'], 'n': ps['n'], 'sha256': ps['sha256']},
        'T': T, 'sd_null_ic': sd_null, 'sd_realized_ic': sd_realized,
        'mde_perm': mde_perm, 'mde_icir': mde_icir,
        'seed': ctl['N1']['seed'], 'B': ctl['N1']['B'],
    }
    assert_whitelisted(out, 'a2_power.json')      # 화이트리스트 + 금지 키 이중 검사
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / 'a2_power.json'
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    log.info('→ %s', p)
    return out


if __name__ == '__main__':
    main()
