"""
레짐 진단 파라미터 단일 소스 (SPEC_07 §7-1). §8 민감도에서 이 값만 흔든다.
config_hash = 이 파라미터 집합의 해시 → regime_indicators.config_hash 로 기록.

STEP A-7 민감도 스윕은 config_regime.py를 매번 편집하는 대신 환경변수로 덮어쓴다
(REGIME_ 접두사) — 파일 상태를 스윕마다 되돌릴 필요 없이
`REGIME_PBR_QUANTILES=4 venv/bin/python -m backtest.regime.indicators_inhouse`처럼
바로 실행 가능. config_hash()가 모듈 전역을 자동 수집하므로 덮어쓴 값도 자동으로
해시에 반영되어 base run과 겹치지 않는 run_id가 나온다.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(f'REGIME_{name}', default))


def _env_str(name: str, default: str) -> str:
    return os.getenv(f'REGIME_{name}', default)


PBR_QUANTILES   = _env_int('PBR_QUANTILES', 5)
SIZE_DECILES    = _env_int('SIZE_DECILES', 10)
LIQ_QUANTILES   = _env_int('LIQ_QUANTILES', 5)
MOM_LOOKBACK_M  = _env_int('MOM_LOOKBACK_M', 6)         # size_mom_6m
MOM_FORMATION   = _env_str('MOM_FORMATION', 't_minus_6m')   # §8 민감도: 't_minus_1m'도 확인
BREADTH_MA_DAYS = _env_int('BREADTH_MA_DAYS', 200)
LIQ_LOOKBACK_D  = _env_int('LIQ_LOOKBACK_D', 20)
MEGACAP_TOP_N   = _env_int('MEGACAP_TOP_N', 10)
MONTH_END_RULE  = _env_str('MONTH_END_RULE', 'last_trading_day')

# 상폐 청산 가정은 별도 상수를 두지 않고 backtest.engine.DELISTING_HAIRCUT를 그대로 import해
# 쓴다(단일 소스 유지, §7-2 복제 게이트 전제조건).

# FactorScreener 폐기(2026-07-05) 반영 — Phase B 판단은 PRIMARY 기준으로만.
PRIMARY_SCENARIOS = ['D_rim_only', 'F_momentum_rim']
ARCHIVE_SCENARIOS = ['E_screener_rim', 'G_full', 'H_no_stability']

# STEP A-2 이전에 experiments/ablation/{tag}_holdings.json 이 최신(상폐 haircut 수정 반영)
# 상태로 존재해야 하는 태그 전체. mtm_monthly.py 시작 시 존재 여부를 확인한다.
REQUIRED_HOLDINGS_TAGS = PRIMARY_SCENARIOS + ARCHIVE_SCENARIOS

# §9 게이트 판정 — 진행 중인 반기(#23)는 회귀·hot/cold 분류에서 제외, 참고 표시만.
GATE_CUTOFF_DATE = date(2026, 4, 3)  # 이 날짜 이후 시작 구간(#23)은 게이트 모집단에서 제외

# config_hash()가 해시에서 제외하는 이름들 — 튜닝 파라미터가 아니라 시나리오/게이트 메타데이터.
# 새 튜닝 파라미터를 추가할 땐 이 목록에 넣지 않는 한 자동으로 해시에 포함된다(아래 config_hash 참고).
_NON_TUNABLE_NAMES = frozenset({'PRIMARY_SCENARIOS', 'ARCHIVE_SCENARIOS',
                                 'REQUIRED_HOLDINGS_TAGS', 'GATE_CUTOFF_DATE'})


def config_hash() -> str:
    """
    이 모듈의 튜닝 파라미터 집합 해시. 민감도 run 구분용(regime_indicators.config_hash).

    ★ 수기 나열 대신 모듈 전역의 대문자 상수를 자동 수집한다 — 손으로 나열하면 새
    파라미터를 추가하고 여기 반영을 깜빡했을 때 서로 다른 두 설정이 같은 해시(=같은
    run_id)로 충돌해 regime_indicators를 조용히 덮어쓰는 사고로 이어진다(§6 run_id/
    config_hash 도입 취지 자체가 이걸 막기 위함). _NON_TUNABLE_NAMES에 없는
    int/float/str/bool 전역 상수는 전부 자동 포함된다.
    """
    module = sys.modules[__name__]
    params = {
        name: value for name, value in vars(module).items()
        if name.isupper() and name not in _NON_TUNABLE_NAMES
        and isinstance(value, (int, float, str, bool))
    }
    blob = json.dumps(params, sort_keys=True, default=str).encode('utf-8')
    return hashlib.sha256(blob).hexdigest()[:12]
