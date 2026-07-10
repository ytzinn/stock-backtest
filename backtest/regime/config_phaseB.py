"""
SPEC_08 §5 — Phase B(Signal → Tilt) 파라미터 단일 소스.
R3: tilt 파라미터는 전체표본 최적화 금지 — B-0 사전고정 → B-1 nested만.

config_hash()는 config_regime.py와 동일하게 모듈 전역의 스칼라(int/float/str/bool) 상수를
자동 수집한다. 그리드 축(list/dict 타입: *_GRID, VARIANTS)은 타입 필터에 걸려 자동 제외되므로
grid.py가 순회하는 개별 조합은 run_id/config_hash가 아니라 overlay_returns의 행 컬럼
(scenario/variant/tilt_option/mode/normalization/overlay_freq/alt_sleeve)으로 구분한다.
config_hash는 "이 Phase B 실행 전체를 규정하는 전역 스칼라 설정"이 바뀌었을 때만 달라진다
(§8 민감도처럼 Z_CAP·WARMUP_M 등을 흔드는 경우).

REGIME_* 환경변수로 덮어쓸 수 있다(config_regime.py와 동일 관례).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(f'REGIME_{name}', default))


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(f'REGIME_{name}', default))


def _env_str(name: str, default: str) -> str:
    return os.getenv(f'REGIME_{name}', default)


# 신호 소스 — v0.3 확정: 검증된 base run 고정(민감도 sweep run은 신호 소스로 쓰지 않음)
INDICATORS_RUN_ID = _env_str('PHASEB_INDICATORS_RUN_ID', 'ind_d937165660ed')
MTM_RUN_ID        = _env_str('PHASEB_MTM_RUN_ID', 'mtm_v1')
PHASEB_RUN_ID     = _env_str('PHASEB_RUN_ID', 'phaseb_v1')   # overlay_returns.run_id 공통값

# 신호 정규화 (§3-1, R4)
Z_CAP            = _env_float('Z_CAP', 2.0)
WARMUP_M         = _env_int('WARMUP_M', 36)          # 확장창 워밍업(개월) — B-0 OOS 시작점이기도 함
ROLLING_WINDOW_M = _env_int('ROLLING_WINDOW_M', 60)  # rolling_pct_60m 민감도용
NORMALIZATION_GRID = ['expanding_z', 'rolling_pct_60m']

# tilt share 매핑 (§3-2·3-3, R1·R7)
TILT_OPTION_GRID = ['A_defensive', 'B_two_sided']
S_NEUTRAL_A = _env_float('S_NEUTRAL_A', 1.0)
S_NEUTRAL_B = _env_float('S_NEUTRAL_B', 0.8)
S_MIN       = _env_float('S_MIN', 0.5)
S_MAX       = _env_float('S_MAX', 1.0)     # R7: S_MAX <= 1.0 (레버리지 금지)
K_GRID = [0.075, 0.15, 0.25]               # 실효 하한(옵션 A) 0.85 / 0.70 / 0.50
CONSERVATIVE_K = 0.075   # 보수 모드(R2) 기본값
STANDARD_K     = 0.15
AGGRESSIVE_K   = 0.25    # OOS(B-0) 통과 후에만 사용

OVERLAY_FREQ_GRID = ['monthly', 'quarterly', 'semiannual']

# 시나리오/변형 (3-2·3-12, R5)
VARIANTS = {
    'D_rim_only':     ['D_v1', 'D_v2'],   # D_v2 = value_spread + size_mom_6m, exploratory
    'F_momentum_rim': ['F_v1'],           # R5: F는 value_spread 단독만
}

# 대체 sleeve (3-8) — 연구용/실행용
ALT_SLEEVE_GRID = ['largecap_cw', 'kospi']

# 비용 — 비대칭(3-9·3-10). 소형가치는 유동성 열위라 낙관 금지.
SMALL_LEG_BPS = _env_float('SMALL_LEG_BPS', 50.0)   # 소형가치 sleeve 매매 (bp)
LARGE_LEG_BPS = _env_float('LARGE_LEG_BPS', 10.0)   # 대형/KOSPI 매매 (bp)

# 라이브 forward (3-13, B-Gate 3)
LIVE_FORWARD_MIN_PERIODS = 3

# #22 기여도 경고/차단 임계값 (R6, 3-11)
PERIOD22_SHARE_WARN = 0.50   # 초과 시 "단일 에피소드 의존" 경고


def config_hash(**combo: object) -> str:
    """
    모듈 전역 스칼라(int/float/str/bool) 자동 수집 해시 + 이 호출의 grid 조합 축(**combo).

    grid.py는 K/normalization/overlay_freq/alt_sleeve를 바꿔가며 같은 (scenario, variant,
    tilt_option, mode, date)에 여러 조합을 저장해야 하는데, overlay_returns의 PRIMARY KEY는
    이 config_hash를 포함한다(schema_phaseB.sql 참고) — 조합축을 여기 실어 보내지 않으면
    서로 다른 조합이 같은 PK로 충돌해 조용히 덮어써진다(Phase A 리뷰에서 나온 것과
    동일한 함정). 예: `config_hash(K=0.15, NORMALIZATION='expanding_z',
    OVERLAY_FREQ='monthly', ALT_SLEEVE='largecap_cw')`.
    """
    module = sys.modules[__name__]
    params = {
        name: value for name, value in vars(module).items()
        if name.isupper() and isinstance(value, (int, float, str, bool))
    }
    params.update(combo)
    blob = json.dumps(params, sort_keys=True, default=str).encode('utf-8')
    return hashlib.sha256(blob).hexdigest()[:12]
