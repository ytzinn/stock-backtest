RF = 0.0263  # 무위험수익률 (3Y KTB)
RK = 0.0873  # 요구수익률 (CAPM 기반 시장 기대수익률)

# Ohlson(1995) 지속성 RIM 파라미터
OMEGA  = 0.62  # 초과이익 지속성 [0,1). Phase 2 초기값. scripts/estimate_omega.py로 검증 후 갱신.
VB_CAP = 5.0   # V/B 상한 세니티 캡
