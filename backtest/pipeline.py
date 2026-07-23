"""
BacktestPipeline — 필터 목록과 적정가 모델을 주입받아 유니버스 구성과 종목 랭킹을 수행한다.
Phase별 파이프라인 조립은 backtest/configs/ 에서 관리한다.
"""
from __future__ import annotations

import logging
from datetime import date

from backtest.interfaces import UniverseFilter, ValuationModel
from backtest.portfolio import MIN_PORTFOLIO_STOCKS

log = logging.getLogger(__name__)


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
            # SPEC_12 §4-1: stats_key로 위장 가능(MomentumCriterionFilter가 기존
            # 'MomentumFilter' 키를 유지해야 export_portfolios.py 조회가 안 깨진다).
            key = getattr(f, 'stats_key', f.__class__.__name__)
            stats[key] = {
                'passed':   len(tickers),
                'rejected': rejected,
            }
            if hasattr(f, 'last_diagnostics'):
                stats[key]['diagnostics'] = f.last_diagnostics
        return {'universe': tickers, 'stats': stats}

    def score_and_rank(
        self,
        universe:       list[str],
        rebalance_date: date,
        pit_series:     dict[str, list[dict]],
        conn,
    ) -> list[dict]:
        """
        valuation_model로 총액 적정가 계산 → 밸류에이션 필터 → upside% 내림차순 정렬.

        upside = (FV_total / 시가총액 − 1) × 100 — **총액 비교** (BASIS-RIM-001,
        2026-07-17). 종전의 주당 FV ÷ 수정주가 비교는 폐기: price_history의
        수정주가는 현재 기준으로 리베이스되고 market_cap_history의 PIT 주식수는
        당시 기준이라, 분할·무상증자 종목에서 upside가 배수로 틀어진다. 총액 비교는
        주식수·수정주가가 식에서 사라져 기저 문제가 없다. 시가총액은
        get_market_cap(as_of 이하 최신, PIT 안전) — 없으면 해당 종목 제외.

        RIM 컷 후 MIN_PORTFOLIO_STOCKS 미달 시 고평가 종목을 upside 순으로 보완.
        (FV 계산 불가 종목은 어떤 경우에도 제외)

        반환 리스트 원소: {
            'ticker': str,
            'upside_pct': float,
            'model': str,
            'fair_value': float,   # FV_total (KRW, 총액)
            'market_cap': float,   # 비교에 사용한 시가총액 (KRW)
            'price': float,        # rebalance_date 종가 (참고용, 비교에 미사용)
        }
        """
        from backtest.data_access import get_market_cap, get_close_price

        passed   = []  # RIM 컷 통과
        rejected = []  # RIM 컷 탈락 (고평가)

        for ticker in universe:
            pit0   = pit_series.get(ticker, [{}])[0]
            fv     = self.model.fair_value_total(ticker, pit0, beta=1.0)
            mktcap = get_market_cap(conn, ticker, rebalance_date)
            price  = get_close_price(conn, ticker, rebalance_date)

            if fv is None or not mktcap or mktcap <= 0 or price is None or price <= 0:
                continue

            upside = (fv / mktcap - 1) * 100
            item = {
                'ticker':     ticker,
                'upside_pct': upside,
                'model':      self.model.name,
                'fair_value': fv,
                'market_cap': mktcap,
                'price':      price,
            }

            if mktcap <= fv * (1 + self.rim_threshold):
                passed.append(item)
            else:
                rejected.append(item)

        # RIM 컷 통과 종목이 최소 기준 미달 → 고평가 종목 중 upside 상위부터 보완
        if len(passed) < MIN_PORTFOLIO_STOCKS and rejected:
            need = MIN_PORTFOLIO_STOCKS - len(passed)
            supplement = sorted(rejected, key=_rank_key)[:need]
            log.info(
                f'[{rebalance_date}] RIM 컷 통과 {len(passed)}개 < 최소 {MIN_PORTFOLIO_STOCKS}개 '
                f'→ 고평가 보완 {len(supplement)}개 추가'
            )
            passed.extend(supplement)

        return sorted(passed, key=_rank_key)


def _rank_key(item: dict) -> tuple[float, str]:
    """
    랭킹 정렬 키: upside_pct 내림차순, 동률 시 ticker 오름차순 (tie-break 명시 고정).

    종전에는 tie-break가 파이썬 안정 정렬 + 유니버스 순서(load_gate_passed_tickers의
    ORDER BY ticker)에 암묵적으로 의존했다 — 동작은 같지만 계약이 아니었다 (CORR-SORT-001).
    n_stocks 경계의 동률 종목 편입과 상폐 조정값이 정렬 구현에 흔들리지 않도록 여기 고정한다.
    """
    return (-item['upside_pct'], item['ticker'])
