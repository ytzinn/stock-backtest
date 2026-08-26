"""SPEC_15 `S-4` — IC + Fama-MacBeth 본 측정.

**이 세션(S-3 + S-5b)에서는 측정을 구현하지 않는다.** 지금 들어 있는 것은
**입력 게이트뿐**이며, 게이트를 통과해도 측정 없이 종료한다.

게이트 둘 (전부 fail-closed):
  1. `controls.json` 의 `all_pass=true` 없이는 실행 거부
     (SPEC_14 `stage_b` ↔ `integrity_gates` 선례와 같은 구조)
  2. `a2_power.json` 에 §6-1 금지 키가 있으면 실행 거부
     — `mean(IC)` 가 A-2 단계에서 새어 나왔다는 뜻이므로, 그 상태로 본 측정을
       이어가면 사전등록의 순서 증거가 이미 깨진 것이다

`[Claude 의견]` 게이트를 `S-4` 가 아니라 지금 세우는 이유는, **음성 대조로 발화를
확인해 두어야** 다음 세션이 그것을 근거로 삼을 수 있기 때문이다. 세워만 두고 발화를
확인하지 않은 검사는 이 저장소가 여러 번 겪은 "형식뿐인 검사" 가 된다.

실행:
    python -m scripts.xsec.estimate --check-gates
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from scripts.xsec._guard import assert_no_db_imported
from scripts.xsec.firewall import FirewallBreach, assert_clean

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger(__name__)

OUT_DIR = Path('experiments/analysis/2026.08.26._xsec_controls')
CONTROLS = OUT_DIR / 'controls.json'
A2       = Path('experiments/analysis/2026.08.25._xsec_prelim') / 'a2_power.json'

#: S-3 이 **반드시 전부** 돌았어야 하는 대조 집합. 생산자(controls.py)와 소비자(여기)가
#: 서로 다른 파일에서 같은 집합을 주장한다 — 한쪽만 알면 그것은 신뢰이지 검사가 아니다.
EXPECTED_RAN = frozenset({'N1', 'N2a', 'N2b1', 'P1', 'P2'})


class GateRefused(SystemExit):
    """선행 게이트 미충족. 본 측정으로 넘어가지 않는다."""


def check_gates(controls_path: Path = CONTROLS, a2_path: Path = A2) -> dict:
    if not controls_path.exists():
        raise GateRefused(f'거부: 대조군 산출물이 없다 — {controls_path}. S-3 을 먼저 돌려라.')
    controls = json.loads(controls_path.read_text(encoding='utf-8'))
    if controls.get('all_pass') is not True:
        raise GateRefused(
            f'거부: {controls_path} 의 all_pass 가 true 가 아니다 '
            f'(현재 {controls.get("all_pass")!r}). 대조군이 서지 않으면 본 측정은 무의미하다.')
    log.info('  게이트 1 (controls.all_pass=true) 통과')

    # 게이트 1-b — **기대 집합을 소비처에 둔다.**
    # `all_pass` 는 controls.py 가 `ran` 을 정직하게 채운다는 전제 위에 있다
    # (직전 세션에 그 자리가 fail-open 이었다). 기대 집합을 여기 적어 두면 그 전제가
    # 검사로 바뀐다 — 생산자와 소비자가 서로 다른 파일에서 같은 집합을 주장해야 한다.
    ran = set(controls.get('ran') or ())
    if ran != EXPECTED_RAN:
        raise GateRefused(
            f'거부: 대조 집합 불일치 — 기대 {sorted(EXPECTED_RAN)}, 실제 {sorted(ran)}. '
            f'부족 {sorted(EXPECTED_RAN - ran)} / 초과 {sorted(ran - EXPECTED_RAN)}.')
    log.info('  게이트 1-b (ran == %s) 통과', sorted(EXPECTED_RAN))

    if not a2_path.exists():
        raise GateRefused(f'거부: A-2 산출물이 없다 — {a2_path}. S-5b 를 먼저 돌려라.')
    a2 = json.loads(a2_path.read_text(encoding='utf-8'))
    try:
        assert_clean(a2, str(a2_path))
    except FirewallBreach as e:
        raise GateRefused(f'거부(§6-1 방화벽): {e}') from e
    log.info('  게이트 2 (§6-1 금지 키 부재) 통과')
    return {'controls': controls, 'a2': a2}


def main() -> None:
    assert_no_db_imported('scripts.xsec.estimate')
    ap = argparse.ArgumentParser()
    ap.add_argument('--check-gates', action='store_true',
                    help='게이트만 검사하고 종료 (본 측정 미구현)')
    ap.add_argument('--controls', default=str(CONTROLS))
    ap.add_argument('--a2', default=str(A2))
    args = ap.parse_args()

    log.info('== S-4 선행 게이트 ==')
    check_gates(Path(args.controls), Path(args.a2))
    raise SystemExit(
        'S-4 본 측정은 아직 구현하지 않았다 (S-3 + S-5b 세션 범위 밖). '
        '게이트만 검사하고 종료한다.')


if __name__ == '__main__':
    main()
