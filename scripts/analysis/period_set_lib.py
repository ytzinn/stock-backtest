"""`period_set` 규약 — **한 곳에만 둔다.**

sha256 규약이 산출물마다 갈리면 자동 대조가 깨진다. `a0_period_set.json`,
`gate_results_g5g2.json`, `gate_results_{tag}.json` 이 같은 함수를 공유해야 한다.

**이 모듈은 DB 를 import 하지 않는다.** 원래 `gate_analysis` 안에 있었는데, 그 모듈은
`run_ablation → backtest.engine → psycopg2` 를 끌고 오므로 순수 함수를 쓰려는 소비처까지
DB 의존을 물려받았다 (`assert_no_db_imported` 가 그것을 잡았다). 순수한 것은 순수한 곳에 둔다.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

#: A-0 산출물 — `period_set` id 를 날짜 집합으로 푸는 유일한 출처.
A0_PATH = Path('experiments/analysis/2026.08.25._xsec_prelim/a0_period_set.json')


def period_set_sha(dates) -> str:
    """정렬된 날짜 목록을 LF 로 join 한 것의 sha256. **규약 본문이다.**

    정렬하는 이유는 입력 순서가 해시에 새지 않게 하려는 것이다 — 같은 집합이면
    어떤 순서로 넘겨도 같은 해시가 나와야 대조가 성립한다.
    """
    return hashlib.sha256('\n'.join(sorted(dates)).encode()).hexdigest()


def resolve_period_set(spec: str, a0_path: Path = A0_PATH) -> tuple[str, set[str] | None]:
    """`full` 또는 A-0 산출물의 id → (id, 유지할 날짜 집합).

    `full` 은 절단 없음(`None`)이다. **미지정은 여기서 처리하지 않는다** —
    호출부가 인자를 required 로 강제해야 한다. 기본값을 두면 그것이 곧 사고 경로다.
    """
    if spec == 'full':
        return 'full', None
    if not a0_path.exists():
        raise SystemExit(f'period_set {spec!r} 을 해석할 산출물이 없다: {a0_path}')
    a0 = json.loads(a0_path.read_text(encoding='utf-8'))
    for key in ('period_set', 'period_set_t18'):
        blk = a0.get(key) or {}
        if blk.get('id') == spec:
            if period_set_sha(blk['dates']) != blk['sha256']:
                raise SystemExit(f'{spec}: 산출물의 sha256 이 자기 dates 와 불일치 — 손상')
            return spec, set(blk['dates'])
    raise SystemExit(f'알 수 없는 period_set: {spec!r}')
