"""
백테스트 엔진 — 리밸런싱 루프, 수익률 계산, 성과 측정.

실행 흐름 (각 리밸런싱 구간):
  1. load_gate_passed_tickers → DQ Gate PASS + 실제 상장 종목
  2. load_pit_series_ttm      → TTM 기반 PIT 데이터 (4월=FY 3개, 8월=TTM 3개)
  3. pipeline.build_universe  → 4단계 필터 적용
  4. pipeline.score_and_rank  → RIM 적정가 계산 + 밸류에이션 필터
  5. build_portfolio          → 동일가중 포트폴리오
  6. _calc_period_return      → 구간 수익률 (상장폐지 종목 70% 청산 처리)
  7. 전체 구간 수익률 → metrics
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from backtest.data_access import (
    get_close_price,
    load_gate_passed_tickers,
    load_pit_series_ttm,
)
from backtest.metrics import compute_metrics
from backtest.pipeline import BacktestPipeline
from backtest.portfolio import build_portfolio
from ingest.connection import get_connection

log = logging.getLogger(__name__)

DELISTING_HAIRCUT = 0.70  # 상장폐지 청산 시 마지막 가격 × 70%


def _report_type(d: date) -> str:
    """8월 리밸런싱 → H1 반기보고서, 나머지 → FY 연간보고서."""
    return 'H1' if d.month == 8 else 'FY'


class BacktestEngine:
    def __init__(self, pipeline: BacktestPipeline):
        self.pipeline = pipeline

    def run(
        self,
        rebalance_dates: list[date],
        run_name:        str = '',
        ablation_tag:    str = 'F_full',
    ) -> dict:
        """
        전체 백테스트 실행.

        반환: {
            'metrics': {cagr, sharpe, mdd, alpha, robustness, ...},
            'period_results': [...],  # 구간별 상세 결과
            'run_name': str,
            'ablation_tag': str,
        }
        """
        conn = get_connection()
        try:
            period_results  = []
            kospi_returns   = []

            for i, rebal_date in enumerate(rebalance_dates):
                next_date = rebalance_dates[i + 1] if i + 1 < len(rebalance_dates) else date.today()

                log.info(f'[{i+1}/{len(rebalance_dates)}] rebal_date={rebal_date}')

                # 1-2. 데이터 로드
                rtype       = _report_type(rebal_date)
                gate_passed = load_gate_passed_tickers(conn, rebal_date, report_type=rtype)
                pit_series  = load_pit_series_ttm(conn, rebal_date, report_type=rtype)

                # 3. 유니버스 구성
                universe_result = self.pipeline.build_universe(
                    gate_passed, rebal_date, pit_series, conn
                )
                universe = universe_result['universe']
                stats    = universe_result['stats']

                log.info(
                    f'  gate={len(gate_passed)} '
                    + ' '.join(
                        f'{k}={v["passed"]}'
                        for k, v in stats.items()
                    )
                )

                # 4. 종목 랭킹
                candidates = self.pipeline.score_and_rank(universe, rebal_date, pit_series, conn)

                # 5. 포트폴리오 구성
                portfolio = build_portfolio(candidates, n_stocks=self.pipeline.n_stocks)

                # 6. 구간 수익률 계산
                period_return = _calc_period_return(conn, portfolio, rebal_date, next_date)
                kospi_return  = _calc_kospi_return(rebal_date, next_date)

                period_results.append({
                    'rebalance_date': rebal_date,
                    'next_date':      next_date,
                    'portfolio':      portfolio,
                    'period_return':  period_return,
                    'kospi_return':   kospi_return,
                    'n_gate':         len(gate_passed),
                    'n_stocks':       len(portfolio),
                    'universe_stats': stats,
                })

                kospi_returns.append(kospi_return)

        finally:
            conn.close()

        # 7. 성과 측정
        strat_ret = pd.Series(
            [r['period_return'] for r in period_results],
            index=pd.DatetimeIndex([r['rebalance_date'] for r in period_results]),
        )
        bench_ret = pd.Series(
            kospi_returns,
            index=strat_ret.index,
        )
        metrics = compute_metrics(strat_ret, bench_ret)
        log.info(
            f'백테스트 완료: CAGR={metrics["cagr"]:.1%} '
            f'Alpha={metrics["alpha"]:.1%} '
            f'MDD={metrics["mdd"]:.1%} '
            f'Sharpe={metrics["sharpe"]:.2f}'
        )

        return {
            'metrics':        metrics,
            'period_results': period_results,
            'run_name':       run_name,
            'ablation_tag':   ablation_tag,
        }


def _calc_period_return(
    conn,
    portfolio:    dict[str, float],
    start_date:   date,
    end_date:     date,
) -> float:
    """
    포트폴리오 구간 수익률. 동일가중 평균.
    상장폐지 종목: 마지막 거래가 × DELISTING_HAIRCUT 적용.
    """
    if not portfolio:
        return 0.0

    stock_returns = []
    for ticker, weight in portfolio.items():
        price_start = get_close_price(conn, ticker, start_date)
        price_end   = get_close_price(conn, ticker, end_date)

        if price_start is None or price_start <= 0:
            continue

        if price_end is None:
            # 상장폐지 처리: 마지막 가격 × 70% 청산
            price_end = _last_known_price(conn, ticker, end_date) * DELISTING_HAIRCUT

        ret = (price_end / price_start) - 1
        stock_returns.append(ret)

    if not stock_returns:
        return 0.0

    return sum(stock_returns) / len(stock_returns)


def _last_known_price(conn, ticker: str, before_date: date) -> float:
    """상장폐지 종목의 마지막 알려진 가격. 없으면 0."""
    from backtest.data_access import get_adj_close_range
    prices = get_adj_close_range(conn, ticker, before_date, lookback=1)
    return float(prices.iloc[-1]) if not prices.empty else 0.0


def _calc_kospi_return(start_date: date, end_date: date) -> float:
    """
    KOSPI 구간 수익률. FDR 'KS11' 사용 (Naver Finance/KRX 기반).

    'KRX/INDEX/KOSPI' 포맷은 FDR이 Yahoo Finance로 fallback → 500 에러.
    'KS11'은 Naver Finance 라우트 (컬럼: Close, UpDown, Comp, Change).
    pykrx get_index_ohlcv_by_date는 KRX 2024 리뉴얼 후 KeyError로 불작동.
    실패 시 0 반환.
    """
    import FinanceDataReader as fdr
    try:
        df = fdr.DataReader('KS11', str(start_date), str(end_date))
        if df is None or df.empty or len(df) < 2:
            return 0.0
        close = df['Close'].dropna()
        if len(close) < 2:
            return 0.0
        return float(close.iloc[-1] / close.iloc[0] - 1)
    except Exception as e:
        log.warning(f'KOSPI 수익률 조회 실패 ({start_date}~{end_date}): {e}')
        return 0.0
