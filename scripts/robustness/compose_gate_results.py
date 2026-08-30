"""게이트 표 입력을 **등재 산출물에서 조립**한다 — 재계산하지 않는다.

`[검증된 사실]` CANONICAL 게이트 표가 stale 한 것은 판정이 없어서가 아니다.
판정은 이미 있다 — G1 은 `e2c2b8d`, G5·G2 는 `af529ff` 에.
문제는 `make_canonical` 이 읽는 `gate_results_{key}.json` 이 2026-08-15 산출분(폐기 세대)
이라는 것뿐이다.

**그래서 이 스크립트는 계산을 하지 않는다.** 등재 산출물 두 개를 읽어 생성기가 기대하는
모양으로 옮겨 담고, 옮기는 과정이 조용히 틀리지 않도록 fail-closed 로 검사한다.

`[Claude 의견]` `gate_analysis.py` 를 돌려 채우는 길도 있었지만 그것은 **전 구간에서 G1 을
다시 계산**한다. 사전등록된 n=18 판정과 다른 수가 나오면 그것은 새 판정이 아니라 사고다.
값을 만드는 경로와 값을 옮기는 경로를 분리해 두면, 이 세션이 조용히 판정 세션이 되는 일이
구조적으로 막힌다.

이 스크립트는 **DB 에 접속하지 않는다.**

실행:
    python -m scripts.robustness.compose_gate_results
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from scripts.analysis.period_set_lib import period_set_sha, resolve_period_set
from scripts.xsec._guard import assert_no_db_imported

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger(__name__)

G1_SRC = Path('experiments/analysis/2026.08.24._period_truncation/'
              'truncation_results_shadow_20260819.json')
G52_SRC = Path('experiments/analysis/2026.08.25._g5g2_reissue/gate_results_g5g2.json')
OUT = Path('experiments/robustness/gate_results_F_pbr_ma200_n13.json')

TAG = 'F_pbr_ma200_n13'
DRAWS_TAG = 'C_pbr_ma200_random_n13'
N_STOCKS = 13


def compose() -> dict:
    for p in (G1_SRC, G52_SRC):
        if not p.exists():
            raise SystemExit(f'등재 산출물이 없다: {p}')
    g1_all = json.loads(G1_SRC.read_text(encoding='utf-8'))
    g52 = json.loads(G52_SRC.read_text(encoding='utf-8'))

    cut = g1_all['cut18']
    ps = g52['period_set']

    # ── fail-closed 1: 두 출처가 **같은 구간 집합**을 말하는가 ──────────────
    # 다르면 두 판정이 서로 다른 대상을 서술하는 것이고, 한 표에 실으면 08-12 오귀속의
    # 재현이다. 여기서 멈춘다.
    if period_set_sha(ps['dates']) != ps['sha256']:
        raise SystemExit('G5G2 산출물의 sha256 이 자기 dates 와 불일치 — 손상')
    if cut['metrics']['n_periods'] != ps['n']:
        raise SystemExit(f"구간 수 불일치 — G1 {cut['metrics']['n_periods']} vs G5G2 {ps['n']}")
    if cut['metrics']['start'] != ps['start'] or cut['metrics']['end'] != ps['end']:
        raise SystemExit(f"구간 경계 불일치 — G1 {cut['metrics']['start']}~{cut['metrics']['end']} "
                         f"vs G5G2 {ps['start']}~{ps['end']}")

    # ── fail-closed 1-b: 이 집합이 **A-0 이 이름 붙인 그 집합**인가 ──────────
    # G5G2 산출물의 period_set 에는 id 가 없다(그 세션이 A-0 보다 먼저였다).
    # 이름을 손으로 붙이지 않고 **sha 로 역참조**한다 — 일치하지 않으면 조립하지 않는다.
    _i, t18 = resolve_period_set('SPEC15_T18_g1compat')
    if set(ps['dates']) != t18:
        raise SystemExit('G5G2 의 구간 집합이 SPEC15_T18_g1compat 과 다르다 — 조립 중단')
    ps = {**ps, 'id': 'SPEC15_T18_g1compat'}
    log.info('  구간 집합 역참조: sha %s → %s', ps['sha256'][:16], ps['id'])

    # ── fail-closed 2: **두 등재 산출물이 같은 수를 말하는가** ──────────────
    # G1 산출물의 net CAGR 과 G5G2 산출물의 G2 F net CAGR 은 같은 tape·같은 구간에서
    # 나온 같은 양이다. 서로 다른 세션이 독립 경로로 계산했으므로, 일치는 강한 대조이고
    # 불일치는 둘 중 하나가 다른 세대에서 왔다는 뜻이다.
    a = cut['metrics']['net_cagr']
    b = g52['G2']['judgment']['f_net_cagr']
    if abs(a - b) > 1e-12:
        raise SystemExit(f'두 등재 산출물의 F net CAGR 불일치 — G1측 {a!r} vs G5G2측 {b!r}. '
                         f'세대가 갈렸을 수 있다. 조립하지 않는다.')
    log.info('  교차 확인: F net CAGR 이 두 산출물에서 %.12f 로 일치', a)

    g1 = cut['G1']
    out = {
        'generated_at': '2026-08-29 (조립 — 재계산 아님)',
        'composed_from': {
            'G1': f'{G1_SRC.as_posix()} (e2c2b8d)',
            'G2': f'{G52_SRC.as_posix()} (af529ff)',
            'G5': f'{G52_SRC.as_posix()} (af529ff)',
        },
        'method': ('등재 산출물에서 읽어 옮겼다. 이 스크립트는 게이트 값을 계산하지 않는다 '
                   '— gate_analysis 를 돌리면 전 구간에서 G1 이 재계산돼 사전등록 판정과 '
                   '모순된다.'),
        'tag': TAG, 'u_tag': g52['G2']['benchmark'], 'draws_tag': DRAWS_TAG,
        'draws_n_stocks': N_STOCKS, 'n_stocks': N_STOCKS,
        'period_set': ps,
        'hard_gates': {
            'G1': {'pass': bool(g1['pass']), 'f_cagr': g1['f_cagr'],
                   'random_p95': g1['random_p95'], 'n_draws': g1['n_draws'],
                   'f_percentile_in_null': cut['null_gross'].get('f_percentile')},
            'G2': {'pass': bool(g52['G2']['pass']),
                   'f_net_cagr': g52['G2']['judgment']['f_net_cagr'],
                   'u_ew_net_cagr': g52['G2']['judgment']['u_net_cagr'],
                   'margin_pp': g52['G2']['judgment']['margin'] * 100,
                   'caveat': g52['G2']['known_limitation']},
            'G5': {'pass': bool(g52['G5']['pass']),
                   'f_daily_mdd_net': g52['G5']['judgment']['daily_mdd_net'],
                   'limit': g52['G5']['limit'],
                   'mdd_peak': g52['G5']['judgment']['mdd_peak'],
                   'mdd_trough': g52['G5']['judgment']['mdd_trough'],
                   # 단서는 산출물이 가진 사실로만 쓴다. 낙폭 구간이 언제인지는
                   # 판정 블록에 있고, "왜"는 없다 — 없는 것을 지어내지 않는다.
                   'caveat': (
                       f'낙폭 구간은 {g52["G5"]["judgment"]["mdd_peak"]} → '
                       f'{g52["G5"]["judgment"]["mdd_trough"]} 다. 저점이 2020-03 '
                       f'급락과 겹치나, 레짐 귀속은 이 산출물이 측정한 것이 아니다 '
                       f'(미해결 G5-MDD). 종목 수 축으로는 풀리지 않는 것이 실측됐다.')},
        },
    }
    return out


def main() -> None:
    assert_no_db_imported('scripts.robustness.compose_gate_results')
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    out = compose()
    hg = out['hard_gates']
    log.info('== 게이트 표 입력 조립 (등재 산출물에서 읽음, 재계산 아님) ==')
    log.info('  period_set %s  n=%d  sha256=%s', out['period_set']['id'],
             out['period_set']['n'], out['period_set']['sha256'][:16])
    log.info('  G1 %s  CAGR %.4f%% vs 귀무 p95 %.4f%%',
             'PASS' if hg['G1']['pass'] else 'FAIL',
             hg['G1']['f_cagr'] * 100, hg['G1']['random_p95'] * 100)
    log.info('  G2 %s  net %.4f%% vs U %.4f%%',
             'PASS' if hg['G2']['pass'] else 'FAIL',
             hg['G2']['f_net_cagr'] * 100, hg['G2']['u_ew_net_cagr'] * 100)
    log.info('  G5 %s  일별 net MDD %.4f%% vs 한계 %.2f%%',
             'PASS' if hg['G5']['pass'] else 'FAIL',
             hg['G5']['f_daily_mdd_net'] * 100, hg['G5']['limit'] * 100)
    if args.dry_run:
        log.info('(dry-run — 쓰지 않음)')
        return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    log.info('→ %s', OUT)


if __name__ == '__main__':
    main()
