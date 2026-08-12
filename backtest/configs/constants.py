RF = 0.0263  # 무위험수익률 (3Y KTB)
RK = 0.0873  # 요구수익률 (CAPM 기반 시장 기대수익률)

# Ohlson(1995) 지속성 RIM 파라미터
OMEGA  = 0.62  # 초과이익 지속성 [0,1). Phase 2 초기값. scripts/estimate_omega.py로 검증 후 갱신.
VB_CAP = 5.0   # V/B 상한 세니티 캡

# ── 거래비용 (SPEC_04 §9-2, CORR-COST-001 — 매수/매도·시장 분리) ──────────────
# 편도 비용. 매수는 양시장 공통, 매도는 시장별 증권거래세 차이만큼 다르다.
# 수수료·슬리피지는 매수·매도 각각 전액 부과한다 (구 모델의 절반 배분은 오류였음).
TAX_KOSPI  = 0.0033  # 증권거래세 0.18% + 농어촌특별세 0.15%
TAX_KOSDAQ = 0.0018  # 증권거래세 0.18% (농특세 없음)
COMMISSION = 0.0015  # 수수료 (매수·매도 각각)
SLIPPAGE   = 0.0020  # 슬리피지 (매수·매도 각각)
BUY_COST         = COMMISSION + SLIPPAGE               # 0.35% (양시장 공통)
SELL_COST_KOSPI  = COMMISSION + TAX_KOSPI + SLIPPAGE   # 0.68%
SELL_COST_KOSDAQ = COMMISSION + TAX_KOSDAQ + SLIPPAGE  # 0.53%


def sell_cost(market: str | None) -> float:
    """시장별 편도 매도비용. 미상/기타는 KOSPI(보수적 상한)로 처리."""
    return SELL_COST_KOSDAQ if market == 'KOSDAQ' else SELL_COST_KOSPI


# ── [LEGACY] CORR-COST-001 이전 combined 모델 (왕복 0.68%, 시장 미구분) ────────
# characterization 값. 남은 소비처는 freeze_rebalance(#24 라이브)·
# scripts/audit/turnover_impact_scan·tests/oracle/test_turnover_oracle 뿐이며,
# 각자 스케줄에서 신모델로 옮기기 전까지만 사용한다.
# (run_random_pool은 SPEC_13 Q-G에서 신모델로 이전 완료 — 2026-07-28.)
# 신규 코드는 반드시 BUY_COST / sell_cost() 를 쓴다 — 이 두 상수를 새로 참조하지 마라.
COST_SELL = 0.00505  # 구 TAX(0.33%) + COMMISSION/2 + SLIPPAGE/2
COST_BUY  = 0.00175  # COMMISSION/2 + SLIPPAGE/2

# `[폐기 2026-08-12, 사용자 결정]` MIN_STOCKS_WARN = 15 삭제.
# 2026-08-11 에 운영 종목 수를 13 으로 채택하면서 이 임계값은 **매 구간 발화**하게 됐다.
# 늘 켜져 있는 경고는 무시되고, 무시되는 경고는 진짜 이상을 가린다.
# 목표 미달 감지는 engine 이 파이프라인의 목표 종목 수와 **상대 비교**로 대체한다
# (고정값이 아니라 n 을 따라가므로 다시 낡지 않는다).
