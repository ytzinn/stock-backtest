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

from backtest.configs.constants import COST_BUY, COST_SELL, MIN_STOCKS_WARN
from backtest.data_access import (
    get_close_price,
    is_delisted_at,
    load_gate_passed_tickers,
    load_pit_series_ttm,
)
from backtest.metrics import compute_cagr, compute_metrics, compute_sharpe
from backtest.pipeline import BacktestPipeline
from backtest.portfolio import build_portfolio
from ingest.connection import get_connection

log = logging.getLogger(__name__)

DELISTING_HAIRCUT = 0.70  # 상장폐지 청산 시 마지막 가격 × 70% (기준 시나리오)


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
            period_results: list[dict]       = []
            kospi_returns:  list[float]       = []
            prev_portfolio: dict[str, float]  = {}

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
                    + ' '.join(f'{k}={v["passed"]}' for k, v in stats.items())
                )

                # 4. 종목 랭킹
                candidates = self.pipeline.score_and_rank(universe, rebal_date, pit_series, conn)

                # 5. 포트폴리오 구성
                portfolio = build_portfolio(candidates, n_stocks=self.pipeline.n_stocks)

                # 최소 편입 종목 수 경고 (DesignBug-3)
                if 0 < len(portfolio) < MIN_STOCKS_WARN:
                    log.warning(
                        f'  [최소종목미달] {rebal_date}: {len(portfolio)}종목 < {MIN_STOCKS_WARN}'
                    )

                # 6. 구간 수익률 계산 — 상폐 3종 조정값 포함 (Gap-3)
                gross_ret, opt_adj, cons_adj = _calc_period_return(
                    conn, portfolio, rebal_date, next_date
                )
                kospi_return  = _calc_kospi_return(rebal_date, next_date)
                kosdaq_return = _calc_kosdaq_return(rebal_date, next_date)

                # 거래비용 계산 (Gap-4·6)
                turnover = _calc_turnover(prev_portfolio, portfolio)
                tc       = turnover * (COST_SELL + COST_BUY)
                net_ret  = gross_ret - tc

                period_results.append({
                    'rebalance_date':     rebal_date,
                    'next_date':          next_date,
                    'portfolio':          portfolio,
                    'period_return':      gross_ret,
                    'net_return':         net_ret,
                    'turnover':           turnover,
                    'transaction_cost':   tc,
                    'delisting_opt_adj':  opt_adj,
                    'delisting_cons_adj': cons_adj,
                    'kospi_return':       kospi_return,
                    'kosdaq_return':      kosdaq_return,
                    'n_gate':             len(gate_passed),
                    'n_stocks':           len(portfolio),
                    'universe_stats':     stats,
                })

                kospi_returns.append(kospi_return)
                prev_portfolio = portfolio

        finally:
            conn.close()

        # 7. 성과 측정
        # TTM 미충족으로 gate=0인 빈 구간(2015-04, 2015-08)을 성과 지표에서 제외.
        # 벤치마크(KOSPI/KOSDAQ)도 동일 구간 기준으로 적용 → 공정 비교 보장.
        # period_results 자체는 전체(빈 구간 포함)를 유지해 구간 상세 리포트에 표시.
        active = [r for r in period_results if r['n_gate'] > 0]
        if len(active) < len(period_results):
            log.info(
                f'  [TTM 미충족] {len(period_results) - len(active)}개 빈 구간 제외 → '
                f'유효 {len(active)}개 구간 기준으로 성과 측정'
            )

        idx        = pd.DatetimeIndex([r['rebalance_date'] for r in active])
        strat_ret  = pd.Series([r['period_return']      for r in active], index=idx)
        net_ret_s  = pd.Series([r['net_return']         for r in active], index=idx)
        opt_ret_s  = pd.Series(
            [r['period_return'] + r['delisting_opt_adj']  for r in active], index=idx
        )
        cons_ret_s = pd.Series(
            [r['period_return'] + r['delisting_cons_adj'] for r in active], index=idx
        )
        bench_ret  = pd.Series([r['kospi_return']  for r in active], index=idx)
        kosdaq_ret = pd.Series([r['kosdaq_return'] for r in active], index=idx)

        metrics = compute_metrics(strat_ret, bench_ret)
        kosdaq_cagr = compute_metrics(strat_ret, kosdaq_ret)['benchmark_cagr']
        metrics['kosdaq_cagr']       = kosdaq_cagr
        metrics['alpha_kosdaq']      = metrics['cagr'] - kosdaq_cagr
        metrics['net_cagr']          = compute_cagr(net_ret_s)
        metrics['net_sharpe']        = compute_sharpe(net_ret_s)
        metrics['cagr_optimistic']   = compute_cagr(opt_ret_s)
        metrics['cagr_conservative'] = compute_cagr(cons_ret_s)
        metrics['avg_turnover']      = float(
            sum(r['turnover'] for r in active) / max(len(active), 1)
        )
        log.info(
            f'백테스트 완료: CAGR={metrics["cagr"]:.1%} (net={metrics["net_cagr"]:.1%}) '
            f'Alpha(KOSPI)={metrics["alpha"]:.1%} Alpha(KOSDAQ)={metrics["alpha_kosdaq"]:.1%} '
            f'MDD={metrics["mdd"]:.1%} Sharpe={metrics["sharpe"]:.2f} '
            f'Turnover(avg)={metrics["avg_turnover"]:.0%}'
        )

        return {
            'metrics':        metrics,
            'period_results': period_results,
            'run_name':       run_name,
            'ablation_tag':   ablation_tag,
        }


def _calc_turnover(prev: dict[str, float], curr: dict[str, float]) -> float:
    """
    단방향 회전율 (0~1). 매도된 종목의 비중 합계.
    첫 구간(prev 빈 경우)은 1.0 (전액 신규 매수).
    """
    if not prev or not curr:
        return 1.0 if not prev and curr else 0.0
    n = max(len(prev), len(curr), 1)
    sold = len(set(prev) - set(curr))
    return sold / n


def _calc_period_return(
    conn,
    portfolio:    dict[str, float],
    start_date:   date,
    end_date:     date,
) -> tuple[float, float, float]:
    """
    포트폴리오 구간 수익률 (기준/낙관/보수 조정값). 동일가중 평균.

    반환: (gross_return, opt_adj, cons_adj)
      gross_return : 기준 수익률 (상폐 × DELISTING_HAIRCUT=0.70)
      opt_adj      : 낙관 조정 (+, 상폐 haircut 없앨 경우 추가 수익)
      cons_adj     : 보수 조정 (-, 상폐 전액 손실 가정 시 추가 손실)

    상폐 판정은 `is_delisted_at()`(stock_listing_events 기준)으로 한다. `get_close_price()`는
    `date <= as_of` 최신값을 반환해 상폐로 가격이 끊겨도 절대 None이 되지 않으므로
    (2026-07-05 확인된 버그 — haircut 분기가 도달 불가능했음), `price_end is None`을
    트리거로 쓰지 않는다.

    계약: 결과는 편입 종목 **집합**에만 의존한다 — dict 순회 순서(=RIM 정렬 순서)와 무관.
    검증: tests/oracle/test_engine_return_oracle.py (순서 독립성 O-3).
    """
    if not portfolio:
        return 0.0, 0.0, 0.0

    # 1-pass: 가격·상폐 판정만 수집 (계산 없음). 2-pass: 확정된 유효 종목 수(n)로 계산.
    # 분모 n을 순회 중에 줄이면서 상폐 조정 가중치(1/n)를 쓰면 가격결측 종목이 상폐 종목보다
    # 앞/뒤 어디에 있느냐로 opt/cons가 달라진다 — 순회 순서는 RIM 정렬 순서라서 tie-break만
    # 바뀌어도 편입이 같은데 숫자가 바뀌는 결함(CORR-ENGINE-002)이었다. 결과는 반드시
    # 편입 집합에만 의존해야 한다.
    valid: list[tuple[float, float, float | None]] = []  # (price_start, price_end, last_if_delisted)

    for ticker in portfolio:
        price_start = get_close_price(conn, ticker, start_date)

        if price_start is None or price_start <= 0:
            continue

        if is_delisted_at(conn, ticker, end_date):
            last = _last_known_price(conn, ticker, end_date)
            valid.append((price_start, last * DELISTING_HAIRCUT, last))
        else:
            price_end = get_close_price(conn, ticker, end_date)
            if price_end is None:
                continue
            valid.append((price_start, price_end, None))

    if not valid:
        return 0.0, 0.0, 0.0

    n = len(valid)
    stock_returns = []
    opt_adj  = 0.0
    cons_adj = 0.0
    for price_start, price_end, last in valid:
        stock_returns.append(price_end / price_start - 1)
        if last is not None:
            w = 1.0 / n
            opt_adj  += w * last * (1.0 - DELISTING_HAIRCUT) / price_start
            cons_adj -= w * last * DELISTING_HAIRCUT / price_start

    return sum(stock_returns) / n, opt_adj, cons_adj


def _last_known_price(conn, ticker: str, before_date: date) -> float:
    """상장폐지 종목의 마지막 알려진 가격. 없으면 0."""
    from backtest.data_access import get_adj_close_range
    prices = get_adj_close_range(conn, ticker, before_date, lookback=1)
    return float(prices.iloc[-1]) if not prices.empty else 0.0


def _calc_kosdaq_return(start_date: date, end_date: date) -> float:
    """
    KOSDAQ 구간 수익률. FDR 'KQ11' 사용 (Naver Finance/KRX 기반).
    실패 시 0 반환.
    """
    import FinanceDataReader as fdr
    try:
        df = fdr.DataReader('KQ11', str(start_date), str(end_date))
        if df is None or df.empty or len(df) < 2:
            return 0.0
        close = df['Close'].dropna()
        if len(close) < 2:
            return 0.0
        return float(close.iloc[-1] / close.iloc[0] - 1)
    except Exception as e:
        log.warning(f'KOSDAQ 수익률 조회 실패 ({start_date}~{end_date}): {e}')
        return 0.0


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
