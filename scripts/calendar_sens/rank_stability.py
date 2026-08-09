"""
SPEC_14 §14-2 — 순위 안정성 검정: 과최적화 가설 vs 데이터품질 가설 판별.

**사전등록. 이 파일은 수치 산출 전에 커밋된다 — 문턱·그룹정의 이후 수정 금지.**
**진단 전용.** 어떤 태그도 채택 후보가 되지 않는다 (§9). 캘린더 축은 닫힌 채다
(SPEC_13 §1-3 b′) — 안 C 는 후보가 아니라 시험 도구다.

## 무엇을 판별하나

SPEC_14 B단계에서 "4·8월에서 도움이 되던 재무 룰이 5·11월에서는 방해가 된다"는
점추정 패턴이 나왔다(전부 통계적으로는 불확정). 두 설명이 **같은 예측**을 한다:

  (A) 과최적화 — 룰이 4·8월 결과를 보며 선택됐으니 다른 캘린더에서 재현 안 된다
  (B) 데이터품질 — 5·11월은 분기보고서 기반이라 재무 숫자가 더 나쁘고(무감사·
      계절성·TTM 조합 복잡), 재무 의존 룰이 그 탓에 오작동한다

둘을 가르려면 **재무를 전혀 안 쓰면서 4·8월에서 튜닝된** 대조군이 필요하다.
`ABLATION_CONFIGS` 에 이미 있다 — 재무 스택(HARD + Stability{R1,R2,R5,R6} + PBR
랭킹)을 완전히 고정하고 **모멘텀 판정기준만** 바꾼 17개 태그다. SPEC_12 §6-2 가
"그리드서치 산물"이라 경고를 붙인 바로 그 조합들이고, 4·8월 gross 가 12.09~16.12%
로 4%p 벌어져 있어 순위 검정에 쓸 만한 분산이 있다.

  (A) 가 맞으면 → 가격 전용도 4·8월에서 고른 것이므로 **순위가 흩어진다**
  (B) 가 맞으면 → 재무를 안 건드리므로 **순위가 유지된다**

## 사전등록 (2026-08-09, 사용자 확정)

| 항목 | 값 |
|---|---|
| 지표 | 엔진 **net CAGR**(완결 구간). gross 병기 |
| 통계량 | 그룹 내 (4·8월 순위, 5·11월 순위) Spearman ρ. Kendall τ 병기 |
| bootstrap | 태그 쌍을 복원추출 2,000회, seed `SPEC14:RANKSTAB` |
| **가격층 일반화** | ρ_price **≥ +0.5** |
| **가격층도 붕괴** | ρ_price **≤ 0.0** |
| 그 외 | 불확정 |
| 보조 | Δρ = ρ_price − ρ_fin 과 그 CI |

**개별 태그 수치는 판정에 쓰지 않는다.** 49개를 돌리면 우연히 좋아 보이는 게 반드시
나온다 — 보는 것은 **두 순위상관뿐**이다.

## 한계 (실행 전 명시)

- 가격 전용 17개는 **같은 (5·11월에서 이미 손상된) 재무 스택을 공유**한다. 17개
  전부가 같은 손상을 물려받은 상태에서 경쟁하므로 완벽한 격리는 아니다. 다만
  **상대 순위는 모멘텀 기준만이 좌우**하므로 검정으로는 성립한다.
- 두 캘린더의 완결 구간이 정확히 같지 않다(반기 2016-04-05~2026-04-03, 안 C
  2016-05-18~2026-05-20). 순위상관은 **그룹 내 상대 순위**만 보므로 공통 이동은
  상쇄된다 — 이 때문에 일별 NAV 공통기간 절단이 필요 없고, RIM 경로 태그의
  2016년 공백(§14-1c)도 순위 검정을 막지 않는다.
- 엔진 net 은 산술 정의(gross − tc)다. 일별 NAV 승법 net(SPEC_13 §9-1 SSOT)과
  값이 다르지만, 그룹 내 순위 비교에는 일관되게 적용되므로 문제되지 않는다.

실행:
    venv/bin/python -m scripts.calendar_sens.rank_stability
출력: experiments/calendar_sens/rank_stability.json
"""
from __future__ import annotations

import argparse
import json
import logging
import random
from datetime import datetime
from pathlib import Path

import numpy as np

from backtest.ablation import ABLATION_CONFIGS, RANDOM_TAGS
from scripts.calendar_sens.calsens_lib import ABL_DIR, OUT_DIR

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

# ── 사전등록 상수 — 수치 산출 후 수정 금지 ───────────────────────────────────
RHO_GENERALIZES = 0.5      # ρ_price ≥ 이 값 → 가격층 일반화 (데이터품질 가설 지지)
RHO_COLLAPSES   = 0.0      # ρ_price ≤ 이 값 → 가격층도 붕괴 (과최적화 가설 지지)
N_BOOT          = 2000
BOOT_SEED       = 'SPEC14:RANKSTAB'
CI_BOUNDS       = (2.5, 97.5)

PLUMBING_PAIR = ('F_pbr_ma_double_adapter', 'F_pbr_no_r3r4')
INCUMBENT_STACK = {'R1', 'R2', 'R5', 'R6'}


def classify(tag: str) -> str:
    """태그를 세 그룹으로 — **정의는 config 구조로 고정**한다 (이름 패턴 금지).

    price_only : 재무 스택이 현행안과 완전 동일(HARD + Stability{R1,R2,R5,R6} +
                 PBR 랭킹)하고 **모멘텀 판정기준만** 다른 것. 현행안 자신 포함
                 (모멘텀 20/60 레거시 경로).
    financial  : 안정성 룰 집합·R6 스위치·stability on/off 가 달라지는 것.
    other      : 그 외 (RIM 랭킹 경로, 스크리너 경로 등).
    """
    c = ABLATION_CONFIGS[tag]
    same_stack = (c.get('use_hard') and c.get('use_stability')
                  and not c.get('use_screener')
                  and c.get('rank_mode') == 'pbr'
                  and c.get('stability_rules') == INCUMBENT_STACK
                  and c.get('rim_cut') is None)
    if same_stack and (c.get('momentum_criterion') is not None or c.get('use_momentum')):
        return 'price_only'
    if (c.get('stability_rules') is not None or 'stability_r6' in c
            or not c.get('use_stability', False)):
        return 'financial'
    return 'other'


def _cagr(tag: str, calendar_suffix: str) -> tuple[float, float] | None:
    """엔진 (net, gross) CAGR. 산출물이 없으면 None."""
    path = ABL_DIR / f'{tag}{calendar_suffix}.json'
    if not path.exists():
        return None
    d = json.loads(path.read_text(encoding='utf-8'))
    return float(d.get('net_cagr', 0.0)), float(d['cagr'])


def spearman(x: list[float], y: list[float]) -> float:
    from scipy.stats import spearmanr
    return float(spearmanr(x, y).statistic)


def kendall(x: list[float], y: list[float]) -> float:
    from scipy.stats import kendalltau
    return float(kendalltau(x, y).statistic)


def boot_rho(x: list[float], y: list[float], seed: str) -> dict:
    """태그 쌍 복원추출 bootstrap — ρ 의 추정 불확실성."""
    rng = random.Random(seed)
    n = len(x)
    vals = []
    for _ in range(N_BOOT):
        idx = [rng.randrange(n) for _ in range(n)]
        xs, ys = [x[i] for i in idx], [y[i] for i in idx]
        if len(set(xs)) < 3 or len(set(ys)) < 3:
            continue                      # 중복 표본으로 순위가 무너진 추출은 버린다
        vals.append(spearman(xs, ys))
    arr = np.asarray(vals)
    lo, hi = np.percentile(arr, CI_BOUNDS)
    return {'n_valid': len(vals), 'mean': float(arr.mean()),
            'ci_low': float(lo), 'ci_high': float(hi)}


def judge(rho_price: float) -> str:
    if rho_price >= RHO_GENERALIZES:
        return 'PRICE_LAYER_GENERALIZES'      # 데이터품질 가설 지지
    if rho_price <= RHO_COLLAPSES:
        return 'PRICE_LAYER_COLLAPSES'        # 과최적화 가설 지지
    return 'INCONCLUSIVE'


def main() -> None:
    ap = argparse.ArgumentParser(description='SPEC_14 §14-2 순위 안정성 검정')
    ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 배관 양성 대조군은 현행안의 **설계상 복제품**이다 (SPEC_12 §4-5 — 소수점까지
    # 같아야 정상). 상관계수에 넣으면 완전 일치 쌍이 하나 더 생겨 ρ 를 부풀린다.
    # 게이트로만 쓰고 통계에서는 제외한다.
    det = [t for t in ABLATION_CONFIGS
           if t not in RANDOM_TAGS and t != PLUMBING_PAIR[0]]
    groups: dict[str, list[dict]] = {'price_only': [], 'financial': [], 'other': []}
    missing = []
    for t in det:
        semi, alt = _cagr(t, ''), _cagr(t, '_C')
        if semi is None or alt is None:
            missing.append(t)
            continue
        groups[classify(t)].append({
            'tag': t, 'semi_net': semi[0], 'semi_gross': semi[1],
            'alt_net': alt[0], 'alt_gross': alt[1],
        })

    if missing:
        log.warning('산출물 없는 태그 %d개 — 검정에서 제외: %s', len(missing), missing[:8])

    result: dict = {
        'spec': 'SPEC_14 §14-2 순위 안정성',
        'disclaimer': '진단 전용 — 본 실행만으로 채택 후보 없음 (§9). 캘린더 축은 닫힌 채다.',
        'generated_at': datetime.now().isoformat(),
        'pre_registered': {
            'rho_generalizes': RHO_GENERALIZES, 'rho_collapses': RHO_COLLAPSES,
            'metric': '엔진 net CAGR(완결 구간), gross 병기',
            'n_boot': N_BOOT, 'seed': BOOT_SEED,
            'note': '2026-08-09 사용자 확정 — 수치 산출 전 커밋. 개별 태그 수치는 판정 비사용.',
        },
        'excluded_missing_artifacts': missing,
        'groups': {},
    }

    for gname, rows in groups.items():
        if len(rows) < 4:
            result['groups'][gname] = {'n': len(rows), 'note': '표본 부족 — ρ 미산출',
                                       'tags': [r['tag'] for r in rows]}
            continue
        rows.sort(key=lambda r: -r['semi_net'])
        xs = [r['semi_net'] for r in rows]
        ys = [r['alt_net'] for r in rows]
        result['groups'][gname] = {
            'n': len(rows),
            'spearman_net': spearman(xs, ys), 'kendall_net': kendall(xs, ys),
            'spearman_gross': spearman([r['semi_gross'] for r in rows],
                                       [r['alt_gross'] for r in rows]),
            'bootstrap_net': boot_rho(xs, ys, f'{BOOT_SEED}:{gname}'),
            'rows': rows,
        }

    gp, gf = result['groups'].get('price_only', {}), result['groups'].get('financial', {})
    rho_p = gp.get('spearman_net')
    rho_f = gf.get('spearman_net')
    result['judgment'] = {
        'rho_price': rho_p, 'rho_financial': rho_f,
        'delta_rho': (rho_p - rho_f) if (rho_p is not None and rho_f is not None) else None,
        'verdict': judge(rho_p) if rho_p is not None else 'NO_DATA',
        'meaning': {
            'PRICE_LAYER_GENERALIZES': '가격 전용 층은 캘린더를 넘어 순위를 유지한다 '
                                       '→ 격차는 재무 입력 품질 쪽으로 설명된다 (가설 B)',
            'PRICE_LAYER_COLLAPSES':   '재무를 안 쓰는 층조차 순위가 흩어진다 '
                                       '→ 4·8월 기준 선택 자체가 일반화되지 않는다 (가설 A)',
            'INCONCLUSIVE':            '판별 불가 — 어느 가설도 지지·기각되지 않는다',
        },
    }

    # 배관 양성 대조군 — 안 C 에서도 소수점까지 같아야 한다 (SPEC_12 §4-5)
    a, b = (_cagr(PLUMBING_PAIR[0], '_C'), _cagr(PLUMBING_PAIR[1], '_C'))
    result['plumbing_control_C'] = {
        'pair': list(PLUMBING_PAIR),
        'a': a, 'b': b,
        'bit_identical': bool(a is not None and b is not None and a == b),
        'note': '불일치면 안 C 산출물 전체를 의심해야 한다',
    }

    (OUT_DIR / 'rank_stability.json').write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')

    for gname, g in result['groups'].items():
        if 'spearman_net' not in g:
            log.info('%-11s n=%d  %s', gname, g['n'], g.get('note', ''))
            continue
        bs = g['bootstrap_net']
        log.info('%-11s n=%-3d ρ(net)=%+.3f  CI95=[%+.3f, %+.3f]  τ=%+.3f  ρ(gross)=%+.3f',
                 gname, g['n'], g['spearman_net'], bs['ci_low'], bs['ci_high'],
                 g['kendall_net'], g['spearman_gross'])
    j = result['judgment']
    log.info('Δρ = %s', f"{j['delta_rho']:+.3f}" if j['delta_rho'] is not None else 'n/a')
    log.info('판정: %s', j['verdict'])
    log.info('  → %s', j['meaning'].get(j['verdict'], ''))
    log.info('배관 대조군(안 C): %s',
             'PASS 비트 일치' if result['plumbing_control_C']['bit_identical'] else '*** FAIL ***')
    log.info('저장: %s', OUT_DIR / 'rank_stability.json')


if __name__ == '__main__':
    main()
