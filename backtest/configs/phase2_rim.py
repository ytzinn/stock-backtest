"""
Phase 2 기본 파이프라인 조립.

파이프라인 구성:
  HardFilter → StabilityFilter → FactorScreener → MomentumFilter → RIMModel

Phase 2 튜닝 파라미터 4개 (MASTER.md §3-7):
  beta_adj      초기 0.0,  범위 [-0.02, +0.02]
  rim_threshold 초기 0.05, 범위 [-0.10, +0.20]
  top_pct       초기 0.20, 범위 [0.10, 0.40]
  n_stocks      초기 20,   범위 [10, 30]

Phase 2 고정값 (튜닝 제외):
  모멘텀 파라미터 4개 / 업종 집중 25% / 거래대금 1억 / 팩터 가중치 (동일가중 고정)
"""
from backtest.filters.factor_screener  import FactorScreener
from backtest.filters.hard_filter      import HardFilter
from backtest.filters.momentum_filter  import MomentumFilter
from backtest.filters.stability_filter import StabilityFilter
from backtest.models.rim               import RIMModel
from backtest.pipeline                 import BacktestPipeline


def build_phase2_pipeline(
    beta_adj:      float = 0.0,
    rim_threshold: float = 0.05,
    top_pct:       float = 0.20,
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
            FactorScreener(
                weights={
                    'rev_yoy': 1 / 6,
                    'op_yoy':  1 / 6,
                    'gpa':     1 / 3,
                    'inv_pbr': 1 / 3,
                },
                top_pct=top_pct,
            ),
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


# 기본 인스턴스 (Ablation Test 기준선 F_full)
PHASE2_PIPELINE = build_phase2_pipeline()
