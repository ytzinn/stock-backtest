"""
Ablation Test — 7개 시나리오 (A_random ~ G_full).
Phase 2 필수 실행. 레이어별 Alpha 기여도 분해.

판정 기준:
  C > B  : 재무안정성 필터가 Alpha에 기여 (단순 종목 수 축소 이상의 효과)
  D > C  : RIM이 랜덤 대비 Alpha를 냄 → RIM 유효성 확인 (핵심 관문)
  E > D  : 팩터 스크리닝이 추가 Alpha를 냄
  F > D  : 모멘텀이 추가 Alpha를 냄
  G ≈ E 또는 G ≈ F : 팩터 스크리닝·모멘텀 중 하나가 중복 → 제거 검토
"""
from __future__ import annotations

import random

from backtest.configs.constants        import OMEGA
from backtest.filters.factor_screener  import FactorScreener
from backtest.filters.hard_filter      import HardFilter
from backtest.filters.momentum_filter  import MomentumFilter
from backtest.filters.stability_filter import StabilityFilter
from backtest.models.rim               import RIMModel
from backtest.pipeline                 import BacktestPipeline

ABLATION_CONFIGS: dict[str, dict] = {
    'A_random':            {'use_hard': False, 'use_stability': False, 'use_screener': False,
                            'use_momentum': False, 'use_rim_filter': False, 'random_n': 20},
    'B_hard_random':       {'use_hard': True,  'use_stability': False, 'use_screener': False,
                            'use_momentum': False, 'use_rim_filter': False, 'random_n': 20},
    'C_stability_random':  {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': False, 'use_rim_filter': False, 'random_n': 20},
    'C_no_r6':             {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': False, 'use_rim_filter': False, 'random_n': 20,
                            'stability_r6': False},
    'D_rim_only':          {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': False, 'use_rim_filter': True},
    'D_no_r6':             {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': False, 'use_rim_filter': True,  'stability_r6': False},
    'D_pbr_only':          {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': False, 'use_rim_filter': False, 'stability_r6': False,
                            'rank_mode': 'pbr'},
    'E_screener_rim':      {'use_hard': True,  'use_stability': True,  'use_screener': True,
                            'use_momentum': False, 'use_rim_filter': True},
    'E_no_r6':             {'use_hard': True,  'use_stability': True,  'use_screener': True,
                            'use_momentum': False, 'use_rim_filter': True,  'stability_r6': False},
    'F_momentum_rim':      {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': True,  'use_rim_filter': True},
    'F_no_r6':             {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': True,  'use_rim_filter': True,  'stability_r6': False},
    'G_full':              {'use_hard': True,  'use_stability': True,  'use_screener': True,
                            'use_momentum': True,  'use_rim_filter': True},
    'G_no_r6':             {'use_hard': True,  'use_stability': True,  'use_screener': True,
                            'use_momentum': True,  'use_rim_filter': True,  'stability_r6': False},
    'H_no_stability':      {'use_hard': True,  'use_stability': False, 'use_screener': True,
                            'use_momentum': True,  'use_rim_filter': True},
}

RANDOM_TAGS    = frozenset({'A_random', 'B_hard_random', 'C_stability_random', 'C_no_r6'})
RANDOM_REPEATS = 500


class _RandomSelectPipeline(BacktestPipeline):
    """
    필터 통과 종목 중 무작위 N개 선택. 랜덤 시나리오(A/B/C) 전용.

    seed × rebalance_date 복합 시드 → 구간마다 다른 무작위 선택.
    500회 반복 실행 시 각 run_seed로 독립적인 분포 생성.
    """

    def __init__(self, filters: list, n_stocks: int = 20, seed: int | None = None):
        super().__init__(filters=filters, valuation_model=RIMModel(), n_stocks=n_stocks)
        self._seed = seed

    def score_and_rank(self, universe, rebalance_date, pit_series, conn) -> list[dict]:
        rng = random.Random(f"{self._seed}:{rebalance_date.isoformat()}")
        shuffled = list(universe)
        rng.shuffle(shuffled)
        return [
            {'ticker': t, 'upside_pct': 0.0, 'model': 'RANDOM',
             'fair_value': 0.0, 'price': 0.0}
            for t in shuffled
        ]


class _PBRRankPipeline(BacktestPipeline):
    """
    필터 통과 종목을 1/PBR(inv_pbr) 내림차순으로 랭킹해 상위 N개 선택.

    STEP 3 신호분리용 대조군 — D_no_r6(RIM 업사이드 랭킹)와 필터 구성을 동일하게 두고
    랭킹 기준만 "RIM V/B" → "순수 1/PBR"로 바꿔, RIM 알파가 사실상 저PBR 재포장인지
    확인한다. equity 정의는 factor_screener._compute_factors의 inv_pbr과 동일하게
    자본총계 기준(비교 가능성 우선, RIM의 지배주주지분 우선순위와는 다름).
    """

    def __init__(self, filters: list, n_stocks: int = 20):
        super().__init__(filters=filters, valuation_model=RIMModel(), n_stocks=n_stocks)

    def score_and_rank(self, universe, rebalance_date, pit_series, conn) -> list[dict]:
        from backtest.data_access import get_market_cap, get_close_price

        scored = []
        for ticker in universe:
            pit0   = pit_series.get(ticker, [{}])[0]
            equity = pit0.get('자본총계')
            mktcap = get_market_cap(conn, ticker, rebalance_date)
            price  = get_close_price(conn, ticker, rebalance_date)

            if not equity or equity <= 0 or not mktcap or mktcap <= 0 or price is None:
                continue

            pbr = mktcap / equity
            if pbr <= 0:
                continue

            scored.append({
                'ticker':     ticker,
                'upside_pct': 1.0 / pbr,   # inv_pbr 스코어(랭킹용, 업사이드 % 아님)
                'model':      'PBR_ONLY',
                'fair_value': None,
                'price':      price,
            })

        return sorted(scored, key=lambda x: x['upside_pct'], reverse=True)


def build_ablation_pipeline(
    tag:           str,
    config:        dict,
    seed:          int | None = None,
    beta_adj:      float = 0.0,
    omega:         float = OMEGA,
    rim_threshold: float = 0.05,
    top_pct:       float = 0.20,
    n_stocks:      int   = 20,
) -> BacktestPipeline:
    """config 플래그에 따라 파이프라인 조립. 랜덤 시나리오는 _RandomSelectPipeline 반환."""
    filters: list = []

    if config.get('use_hard', False):
        filters.append(HardFilter(min_turnover=100_000_000, min_listed_months=6))
    if config.get('use_stability', False):
        use_r6 = config.get('stability_r6', True)
        filters.append(StabilityFilter(r2_exception=True, use_r6=use_r6))
    if config.get('use_screener', False):
        filters.append(FactorScreener(
            weights={'rev_yoy': 1/6, 'op_yoy': 1/6, 'gpa': 1/3, 'inv_pbr': 1/3},
            top_pct=top_pct,
        ))
    if config.get('use_momentum', False):
        filters.append(MomentumFilter(
            ma_short=20, ma_long=60, confirm_days=5, slope_lookback=20,
        ))

    if config.get('rank_mode') == 'pbr':
        return _PBRRankPipeline(filters=filters, n_stocks=n_stocks)

    if not config.get('use_rim_filter', True):
        return _RandomSelectPipeline(
            filters=filters,
            n_stocks=config.get('random_n', n_stocks),
            seed=seed,
        )

    return BacktestPipeline(
        filters=filters,
        valuation_model=RIMModel(beta_adj=beta_adj, omega=omega),
        rim_threshold=rim_threshold,
        n_stocks=n_stocks,
    )
