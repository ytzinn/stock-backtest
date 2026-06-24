RF = 0.0263  # 무위험수익률 (3Y KTB)
RK = 0.0873  # 요구수익률 (CAPM 기반 시장 기대수익률)

# Ohlson(1995) 지속성 RIM 파라미터
OMEGA  = 0.62  # 초과이익 지속성 [0,1). Phase 2 초기값. scripts/estimate_omega.py로 검증 후 갱신.
VB_CAP = 5.0   # V/B 상한 세니티 캡

# 거래비용 (SPEC_04 §9-2)
TAX        = 0.0033  # 증권거래세 (매도 시)
COMMISSION = 0.0015  # 거래수수료 (매수·매도 합산)
SLIPPAGE   = 0.0020  # 슬리피지 추정 (매수·매도 합산)
COST_SELL  = TAX + COMMISSION / 2 + SLIPPAGE / 2   # 매도 측 단방향 비용 ≈ 0.505%
COST_BUY   = COMMISSION / 2 + SLIPPAGE / 2         # 매수 측 단방향 비용 ≈ 0.175%

# 포트폴리오 최소 편입 종목 수 경고 임계값
MIN_STOCKS_WARN = 15
