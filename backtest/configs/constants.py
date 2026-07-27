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
# characterization 값. run_random_pool(§9-1 Q-G 승법 재작성)·freeze_rebalance(#24)·
# scripts/audit 가 각자 스케줄에서 신모델로 옮기기 전까지만 사용한다.
# 신규 코드는 반드시 BUY_COST / sell_cost() 를 쓴다 — 이 두 상수를 새로 참조하지 마라.
COST_SELL = 0.00505  # 구 TAX(0.33%) + COMMISSION/2 + SLIPPAGE/2
COST_BUY  = 0.00175  # COMMISSION/2 + SLIPPAGE/2

# 포트폴리오 최소 편입 종목 수 경고 임계값
MIN_STOCKS_WARN = 15
