"""
Phase 2 기본 파이프라인 조립 — F_momentum_rim 구조 (채택 파이프라인).

파이프라인 구성:
  HardFilter → StabilityFilter → MomentumFilter → RIMModel

2026-07-05: FactorScreener 폐기로 제거. Ablation 결과 D_rim_only(11.99%) > E_screener_rim
(6.29%)로 스크리닝이 RIM 알파를 구조적으로 훼손함을 확인(SPEC_05 §11 STEP 3B). 이 파일이
FactorScreener를 포함해 조립하던 이전 버전은 실제로는 G_full 구조였고 F_full(현재는
F_momentum_rim)로 잘못 라벨링돼 있었음 — 채택된 최적 파이프라인(F_momentum_rim, +14.63% CAGR)
구조로 교체.

Phase 2 튜닝 파라미터 3개 (MASTER.md §3-7, 2026-07-05 이전엔 4개 — top_pct 제거):
  beta_adj      초기 0.0,  범위 [-0.02, +0.02]
  rim_threshold 초기 0.05, 범위 [-0.10, +0.20]
  n_stocks      초기 20,   범위 [10, 30]

Phase 2 고정값 (튜닝 제외):
  모멘텀 파라미터 4개 / 업종 집중 25% / 거래대금 1억
"""
from backtest.filters.hard_filter      import HardFilter
from backtest.filters.momentum_filter  import MomentumFilter
from backtest.filters.stability_filter import StabilityFilter
from backtest.models.rim               import RIMModel
from backtest.pipeline                 import BacktestPipeline


def build_phase2_pipeline(
    beta_adj:      float = 0.0,
    rim_threshold: float = 0.05,
    n_stocks:      int   = 20,
) -> BacktestPipeline:
    """
    Phase 2 파이프라인 인스턴스 생성.
    Bayesian 튜닝 시 이 함수를 호출해 파라미터를 주입한다.
    """
    return BacktestPipeline(
        filters=[
            HardFilter(
                min_turnover=100_000_000,
                min_listed_months=6,
            ),
            StabilityFilter(r2_exception=True),
            MomentumFilter(
                ma_short=20,
                ma_long=60,
                confirm_days=5,
                slope_lookback=20,
            ),
        ],
        valuation_model=RIMModel(beta_adj=beta_adj),
        rim_threshold=rim_threshold,
        n_stocks=n_stocks,
    )


# 기본 인스턴스 (채택 파이프라인 F_momentum_rim 구조)
PHASE2_PIPELINE = build_phase2_pipeline()
