"""SPEC_15 `A-0` — primary 구간 집합(T) 확정.

사전등록: `docs/설계/SPEC_15_cross_sectional_estimator_v0.4.md` §3-6 (커밋 `c369401`).
**T=19 를 가정하지 않는다.** 규칙 4단계를 순서대로 적용하고 단계별 잔존 수를 기록한다.

이 스크립트는 **DB 에 접속하지 않는다** — 산출물 파일만 읽는다 (`_guard` 참조).

실행:
    python -m scripts.xsec.period_set
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
from pathlib import Path

from scripts.analysis.baseline_registry import local_root, verify
from scripts.analysis.coverage_slice import (
    COVERAGE_CSV,
    PREREG_THRESHOLD,
    load_rule_coverage,
)
from scripts.xsec._guard import assert_no_db_imported

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger(__name__)

OUT_DIR = Path('experiments/analysis/2026.08.25._xsec_prelim')

DEFAULT_BASELINE = 'shadow_20260819'
F_PERIODS = 'experiments/ablation/F_pbr_ma200_n13_periods.csv'

#: SPEC_15 §3-6 4단계가 쓰는 규칙 집합. 채택 설정(CANONICAL)의 active_rules 와 같다.
RULES = frozenset({'R1', 'R2', 'R5', 'R6'})

#: 직전 세션(G5·G2 재산출)의 판정 구간 집합 — 교차 대조 상대.
#: **독립 경로로 만들어졌다**: 저쪽은 복리 연쇄 연속화(contiguous_tail)를 거쳤고,
#: 이쪽은 §3-6 4단계를 순서대로 적용한다. 그래서 일치가 대조로서 의미를 갖는다.
N18_ARTIFACT = Path('experiments/analysis/2026.08.25._g5g2_reissue/gate_results_g5g2.json')


def sha_of(dates: list[str]) -> str:
    """직전 세션 `period_set.sha256` 과 **동일 규약** — 규약이 다르면 이후 대조가 불가능하다."""
    return hashlib.sha256('\n'.join(sorted(dates)).encode()).hexdigest()


def derive(baseline: str = DEFAULT_BASELINE) -> dict:
    root = local_root(baseline)
    rows = list(csv.DictReader((root / F_PERIODS).open(encoding='utf-8')))
    anchors = [r['rebalance_date'] for r in rows]
    anchor_set = set(anchors)
    cov = load_rule_coverage()

    steps, cur = [], list(anchors)
    steps.append({'step': 1, 'rule': '전 앵커', 'remaining': len(cur), 'dropped': []})

    nxt = {r['rebalance_date']: r['next_date'] for r in rows}
    keep = [a for a in cur if nxt[a] in anchor_set]
    steps.append({'step': 2, 'rule': '완결 (next_date 가 앵커)',
                  'remaining': len(keep), 'dropped': sorted(set(cur) - set(keep))})
    cur = keep

    keep = [a for a in cur if a in cov]
    steps.append({'step': 3, 'rule': '커버리지 산출물에 행 존재',
                  'remaining': len(keep), 'dropped': sorted(set(cur) - set(keep))})
    cur = keep

    keep = [a for a in cur if all(cov[a].get(r, 0.0) > 0.0 for r in RULES)]
    steps.append({'step': 4, 'rule': f'{sorted(RULES)} 전부 판정가능 > 0',
                  'remaining': len(keep), 'dropped': sorted(set(cur) - set(keep))})
    cur = keep

    for s in steps:
        log.info('  step %d  %-34s 잔존 %2d  탈락 %s',
                 s['step'], s['rule'], s['remaining'], s['dropped'] or '—')

    end = nxt[cur[-1]]
    return {'dates': cur, 'end': end, 'derivation': steps,
            'threshold_note': f'판정가능 > 0 (임계 {PREREG_THRESHOLD:.0%} 는 G1 절단 규칙이며 '
                              f'§3-6 4단계는 0 초과만 요구한다)'}


def cross_check(t19: list[str]) -> dict:
    """`T19 − n18 == {2016-04-05}` 그리고 `n18 − T19 == ∅`. 어긋나면 중단."""
    if not N18_ARTIFACT.exists():
        raise SystemExit(f'FATAL 교차 대조 상대가 없다: {N18_ARTIFACT}')
    ps = json.loads(N18_ARTIFACT.read_text(encoding='utf-8'))['period_set']
    n18 = ps['dates']
    if sha_of(n18) != ps['sha256']:
        raise SystemExit('FATAL n18 산출물의 sha256 이 자기 dates 와 불일치 — 산출물 손상')

    added   = sorted(set(t19) - set(n18))
    removed = sorted(set(n18) - set(t19))
    ok = (added == ['2016-04-05']) and (removed == [])
    log.info('')
    log.info('== 교차 대조 (독립 경로: G1 절단 산출 vs §3-6 4단계) ==')
    log.info('  n18 sha256 = %s  (자기정합 확인)', ps['sha256'])
    log.info('  T19 − n18  = %s   (기대 [\'2016-04-05\'])', added)
    log.info('  n18 − T19  = %s   (기대 [])', removed)
    log.info('  -> %s', 'PASS' if ok else 'FAIL')
    if not ok:
        raise SystemExit(
            'FATAL 교차 대조 실패. **규칙을 고치거나 결과에 맞추지 마라.** '
            '두 집합은 독립 경로로 만들어졌으므로 어긋난다면 어느 한쪽이 틀린 것이고, '
            '어느 쪽인지는 이 세션에서 판단하지 말고 보고하라.')
    return {'added': added, 'removed': removed, 'pass': ok,
            'n18_sha256': ps['sha256'], 'n18_n': ps['n']}


def main() -> None:
    assert_no_db_imported('scripts.xsec.period_set')
    ap = argparse.ArgumentParser()
    ap.add_argument('--baseline', default=DEFAULT_BASELINE)
    args = ap.parse_args()

    prov = verify(args.baseline, [F_PERIODS], extra=[COVERAGE_CSV.as_posix()])
    log.info('== 기준선 %s · commit=%s · db_port=%s · 공식자격=%s ==',
             prov['baseline'], prov['commit'], prov['db_port'],
             prov['valid_for_official_numbers'])
    log.info('== A-0 구간 집합 유도 (SPEC_15 §3-6, T 를 가정하지 않는다) ==')

    d   = derive(args.baseline)
    t19 = d['dates']
    xc  = cross_check(t19)

    # G1 정합 병기용 — T19 에서 선두 하나를 뺀 연속 꼬리
    t18 = [a for a in t19 if a != '2016-04-05']

    out = {
        'prereg': 'docs/설계/SPEC_15_cross_sectional_estimator_v0.4.md (c369401) §3-6',
        'provenance': prov,
        'sha256_convention': 'sha256("\\n".join(sorted(dates))) — 직전 세션과 동일',
        'period_set': {
            'id': 'SPEC15_T19_primary', 'n': len(t19), 'dates': t19,
            'sha256': sha_of(t19), 'start': t19[0], 'end': d['end'],
            'derivation': d['derivation'],
            'cross_check_vs_n18': xc,
            'note': d['threshold_note'],
        },
        'period_set_t18': {
            'id': 'SPEC15_T18_g1compat', 'n': len(t18), 'dates': t18,
            'sha256': sha_of(t18), 'start': t18[0], 'end': d['end'],
            'note': 'G1(e2c2b8d)·G5·G2(2026-08-25)와 같은 집합. 병기 전용, primary 아님.',
        },
    }
    log.info('')
    log.info('  primary  %s  n=%d  %s ~ %s', out['period_set']['id'],
             len(t19), t19[0], d['end'])
    log.info('    sha256 %s', out['period_set']['sha256'])
    log.info('  병기     %s  n=%d  sha256 %s', out['period_set_t18']['id'],
             len(t18), out['period_set_t18']['sha256'])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / 'a0_period_set.json'
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    log.info('→ %s', p)


if __name__ == '__main__':
    main()
