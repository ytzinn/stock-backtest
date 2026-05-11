"""
BacktestPipeline — 필터 목록과 적정가 모델을 주입받아 유니버스 구성과 종목 랭킹을 수행한다.
Phase별 파이프라인 조립은 backtest/configs/ 에서 관리한다.
"""
from __future__ import annotations

from datetime import date

from backtest.interfaces import UniverseFilter, ValuationModel


class BacktestPipeline:
    def __init__(
        self,
        filters:         list[UniverseFilter],
        valuation_model: ValuationModel,
        rim_threshold:   float = 0.05,   # Bayesian 튜닝: [-0.10, +0.20]
        n_stocks:        int   = 20,     # Bayesian 튜닝: [10, 30]
        top_pct:         float = 0.20,   # FactorScreener에 전달하지 않음 (filter 자체 관리)
    ):
        self.filters       = filters
        self.model         = valuation_model
        self.rim_threshold = rim_threshold
        self.n_stocks      = n_stocks
        self.top_pct       = top_pct

    def build_universe(
        self,
        gate_passed:    list[str],
        rebalance_date: date,
        pit_series:     dict[str, list[dict]],
        conn,
    ) -> dict:
        """
        filters를 순서대로 적용. 단계별 탈락 수 반환.

        반환: {
            'universe': [ticker, ...],
            'stats': {
                'HardFilter':       {'passed': N, 'rejected': {ticker: reason}},
                'StabilityFilter':  {'passed': N, 'rejected': {ticker: reason_list}},
                'FactorScreener':   {'passed': N, 'rejected': {ticker: reason}},
                'MomentumFilter':   {'passed': N, 'rejected': {ticker: reason}},
            }
        }
        """
        tickers = list(gate_passed)
        stats   = {}
        for f in self.filters:
            tickers, rejected = f.apply(tickers, rebalance_date, pit_series, conn)
            stats[f.__class__.__name__] = {
                'passed':   len(tickers),
                'rejected': rejected,
            }
        return {'universe': tickers, 'stats': stats}

    def score_and_rank(
        self,
        universe:       list[str],
        rebalance_date: date,
        pit_series:     dict[str, list[dict]],
        conn,
    ) -> list[dict]:
        """
        valuation_model로 적정가 계산 → 밸류에이션 필터 → upside% 내림차순 정렬.

        반환 리스트 원소: {
            'ticker': str,
            'upside_pct': float,
            'model': str,
            'fair_value': float,
            'price': float,
        }
        """
        from backtest.data_access import get_shares_outstanding, get_close_price

        result = []
        for ticker in universe:
            pit0   = pit_series.get(ticker, [{}])[0]
            shares = get_shares_outstanding(conn, ticker, rebalance_date)
            fv     = self.model.fair_value(ticker, pit0, shares or 0, beta=1.0)
            price  = get_close_price(conn, ticker, rebalance_date)

            if fv is None or price is None or price <= 0:
                continue

            upside = (fv / price - 1) * 100

            # 고평가 제외: 현재가 > 적정가 × (1 + rim_threshold)
            if price > fv * (1 + self.rim_threshold):
                continue

            result.append({
                'ticker':     ticker,
                'upside_pct': upside,
                'model':      self.model.name,
                'fair_value': fv,
                'price':      price,
            })

        return sorted(result, key=lambda x: x['upside_pct'], reverse=True)
