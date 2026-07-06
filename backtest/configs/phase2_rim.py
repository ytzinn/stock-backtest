"""
Phase 2 기본 파이프라인 조립 — 채택 파이프라인.

파이프라인 구성:
  HardFilter → StabilityFilter(R1,R4,R5,R6만 — R2/R3 제거) → MomentumFilter → RIMModel

FactorScreener는 폐기(2026-07-05). StabilityFilter는 R2(차입금비율, R1과 완전 중복 —
leave-one-out 검증 결과 어떤 조합에서도 결과에 영향 없음 확인)·R3(매출역성장, 역효과로
확인)를 제거(2026-07-07). 변경 이력·수치 근거는 MASTER.md 버전이력 v5.2~v5.4 참조.

Phase 2 튜닝 파라미터 3개 (MASTER.md §3-7):
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
            StabilityFilter(active_rules={'R1', 'R4', 'R5', 'R6'}),
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
