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


class BenchmarkDataUnavailable(RuntimeError):
    """벤치마크 지수 조회 실패. 조용한 0.0 반환 대신 이 예외로 실행을 중단한다.

    네트워크 장애가 '벤치마크 0% 수익'으로 둔갑하면 alpha·robustness가 오염된 채
    백테스트가 성공 상태로 끝난다 (CORR-BENCH-001). 재시도는 호출자(CLI) 책임이다.
    """


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
        valuation_date:  date | None = None,
    ) -> dict:
        """
        전체 백테스트 실행.

        valuation_date: 마지막(열린) 구간의 평가 기준일. **주입 필수** — 엔진은 재현성을
          위해 내부에서 date.today()를 호출하지 않는다 (CORR-ENGINE-003). 호출부(CLI)가
          "오늘"을 원하면 date.today()를 명시적으로 넘겨라.
          price_history 최신일보다 미래면 경고를 남긴다 (CORR-FRESH-001) — 열린 구간은
          어차피 최신 보유 가격까지만 반영된다.

        성과 지표(metrics)는 **완결 구간(closed periods)만으로 계산**한다 — 마지막 열린
        구간은 period_results에 is_open_period=True로 포함되지만 공식 지표에서 제외된다.
        (열린 구간을 포함하면 실행 날짜에 따라 지표가 달라지고, CAGR 연수 계산도 부분
        연도로 왜곡된다 — CORR-ENGINE-003/METRIC-002 동시 소거, AUDIT IMPACT_MATRIX §5.)
        CAGR는 완결 구간의 실제 캘린더 경과일수 기준이다.

        반환: {
            'metrics': {cagr, sharpe, mdd, alpha, robustness, ...},  # closed 기준
            'period_results': [...],  # 구간별 상세 (열린 구간 포함, is_open_period 플래그)
            'run_name': str,
            'ablation_tag': str,
            'valuation_date': date,
            'price_data_max_date': date | None,
        }
        """
        if valuation_date is None:
            raise ValueError(
                'valuation_date 주입 필수 — 엔진은 date.today()를 내부 호출하지 않는다 '
                '(재현성, CORR-ENGINE-003). 예: engine.run(dates, valuation_date=date.today())'
            )

        conn = get_connection()
        try:
            from backtest.data_access import get_max_price_date
            price_max = get_max_price_date(conn)
            if price_max is not None and valuation_date > price_max:
                log.warning(
                    f'[신선도] valuation_date={valuation_date} > price_history 최신 {price_max} '
                    f'— 열린 구간 수익률은 {price_max}까지의 가격으로만 계산된다 (CORR-FRESH-001)'
                )

            period_results: list[dict]       = []
            kospi_returns:  list[float]       = []
            prev_portfolio: dict[str, float]  = {}

            for i, rebal_date in enumerate(rebalance_dates):
                is_open   = i + 1 >= len(rebalance_dates)
                next_date = rebalance_dates[i + 1] if not is_open else valuation_date

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
                    'is_open_period':     is_open,
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

        # 7. 성과 측정 — **완결 구간(closed) 공식 기준**
        # TTM 미충족으로 gate=0인 빈 구간(2015-04, 2015-08)을 제외하고,
        # 마지막 열린 구간(is_open_period)도 공식 지표에서 제외한다 (docstring 참조).
        # 벤치마크(KOSPI/KOSDAQ)도 동일 구간 기준으로 적용 → 공정 비교 보장.
        # period_results 자체는 전체(빈 구간·열린 구간 포함)를 유지해 상세 리포트에 표시.
        active = [r for r in period_results if r['n_gate'] > 0]
        closed = [r for r in active if not r['is_open_period']]
        if len(active) < len(period_results):
            log.info(
                f'  [TTM 미충족] {len(period_results) - len(active)}개 빈 구간 제외'
            )
        if len(closed) < len(active):
            log.info(
                f'  [열린 구간 제외] 공식 지표는 완결 {len(closed)}개 구간 기준 '
                f'(열린 구간 {len(active) - len(closed)}개는 open_period_* 참고 지표로 별도 보고)'
            )

        idx        = pd.DatetimeIndex([r['rebalance_date'] for r in closed])
        strat_ret  = pd.Series([r['period_return']      for r in closed], index=idx)
        net_ret_s  = pd.Series([r['net_return']         for r in closed], index=idx)
        opt_ret_s  = pd.Series(
            [r['period_return'] + r['delisting_opt_adj']  for r in closed], index=idx
        )
        cons_ret_s = pd.Series(
            [r['period_return'] + r['delisting_cons_adj'] for r in closed], index=idx
        )
        bench_ret  = pd.Series([r['kospi_return']  for r in closed], index=idx)
        kosdaq_ret = pd.Series([r['kosdaq_return'] for r in closed], index=idx)

        # CAGR 연수 = 완결 구간의 실제 캘린더 경과일수 (CORR-METRIC-002)
        span = dict(start_date=closed[0]['rebalance_date'],
                    end_date=closed[-1]['next_date']) if closed else {}

        metrics = compute_metrics(strat_ret, bench_ret, **span)
        kosdaq_cagr = compute_metrics(strat_ret, kosdaq_ret, **span)['benchmark_cagr']
        metrics['kosdaq_cagr']       = kosdaq_cagr
        metrics['alpha_kosdaq']      = metrics['cagr'] - kosdaq_cagr
        metrics['net_cagr']          = compute_cagr(net_ret_s, **span)
        metrics['net_sharpe']        = compute_sharpe(net_ret_s)
        metrics['cagr_optimistic']   = compute_cagr(opt_ret_s, **span)
        metrics['cagr_conservative'] = compute_cagr(cons_ret_s, **span)
        metrics['avg_turnover']      = float(
            sum(r['turnover'] for r in closed) / max(len(closed), 1)
        )
        # 열린 구간은 참고 지표로만 (실행 날짜·가격 신선도에 종속 — 공식 비교 금지)
        open_periods = [r for r in active if r['is_open_period']]
        metrics['open_period_return'] = (
            open_periods[-1]['period_return'] if open_periods else None
        )
        log.info(
            f'백테스트 완료 (완결 {len(closed)}구간 기준): '
            f'CAGR={metrics["cagr"]:.1%} (net={metrics["net_cagr"]:.1%}) '
            f'Alpha(KOSPI)={metrics["alpha"]:.1%} Alpha(KOSDAQ)={metrics["alpha_kosdaq"]:.1%} '
            f'MDD={metrics["mdd"]:.1%} Sharpe={metrics["sharpe"]:.2f} '
            f'Turnover(avg)={metrics["avg_turnover"]:.0%}'
        )

        return {
            'metrics':             metrics,
            'period_results':      period_results,
            'run_name':            run_name,
            'ablation_tag':        ablation_tag,
            'valuation_date':      valuation_date,
            'price_data_max_date': price_max,
        }


def _calc_turnover(prev: dict[str, float], curr: dict[str, float]) -> float:
    """
    turnover = 0.5 × Σ_{t∈prev∪curr} |w_new[t] − w_old[t]|  — 재조정 규모의 표준 정의.
    첫 구간(prev 없음)은 전액 신규 매수 관례로 1.0.

    종전 산식 sold/max(len(prev),len(curr),1)은 이탈 종목 **수**만 세어 비중 변화를
    무시했다 — 종목 수가 같고 등가중이면 항등식으로 우연히 일치했지만, 종목 수가
    바뀌는 구간(예: 5→20종목)에서 재조정 규모를 크게 과소평가해 거래비용·net 수익률을
    오염시켰다 (CORR-METRIC-001, P0-A). 검증: tests/oracle/test_turnover_oracle.py.
    """
    if not prev:
        return 1.0 if curr else 0.0
    tickers = set(prev) | set(curr)
    return 0.5 * sum(abs(curr.get(t, 0.0) - prev.get(t, 0.0)) for t in tickers)


def _calc_period_return(
    conn,
    portfolio:    dict[str, float],
    start_date:   date,
    end_date:     date,
) -> tuple[float, float, float]:
    """
    포트폴리오 구간 수익률 (기준/낙관/보수 조정값). **build_portfolio()가 준 weight를
    소비하는 가중 수익률** — 유효 종목의 weight 합으로 재정규화한다 (등가중 1/N이면
    단순평균과 동일값).

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

    # 1-pass: 가격·상폐 판정만 수집 (계산 없음). 2-pass: 확정된 유효 weight 합으로 계산.
    # 분모를 순회 중에 줄이면서 상폐 조정 가중치를 쓰면 가격결측 종목이 상폐 종목보다
    # 앞/뒤 어디에 있느냐로 opt/cons가 달라진다 — 순회 순서는 RIM 정렬 순서라서 tie-break만
    # 바뀌어도 편입이 같은데 숫자가 바뀌는 결함(CORR-ENGINE-002)이었다. 결과는 반드시
    # 편입 집합(과 weight)에만 의존해야 한다.
    valid = _period_stock_data(conn, portfolio, start_date, end_date)

    if not valid:
        return 0.0, 0.0, 0.0

    return _aggregate_period_return(valid)


def _aggregate_period_return(
    valid: list[tuple[str, float, float, float, float | None]],
) -> tuple[float, float, float]:
    """
    _period_stock_data() 결과의 2-pass 집계 (SSOT — 구간 수익률 산식의 유일한 정의).
    가격결측으로 탈락한 종목의 weight는 유효 종목에 비례 재배분 (합 1.0 재정규화).
    등가중 1/N 포트폴리오에서는 종전 단순평균(sum/len)과 정확히 같은 값이다.
    SPEC_10 fast-path가 풀 단위 prefetch 데이터로 이 함수를 직접 호출한다.
    """
    total_w = sum(w for _, w, *_ in valid)
    gross    = 0.0
    opt_adj  = 0.0
    cons_adj = 0.0
    for _ticker, weight, price_start, price_end, last in valid:
        w = weight / total_w
        gross += w * (price_end / price_start - 1)
        if last is not None:
            opt_adj  += w * last * (1.0 - DELISTING_HAIRCUT) / price_start
            cons_adj -= w * last * DELISTING_HAIRCUT / price_start

    return gross, opt_adj, cons_adj


def _period_stock_data(
    conn,
    portfolio:  dict[str, float],
    start_date: date,
    end_date:   date,
) -> list[tuple[str, float, float, float, float | None]]:
    """
    구간 수익률의 종목별 원천 데이터 (1-pass 수집, SSOT — _calc_period_return 산식의
    유일한 가격·상폐·유효집합 판정 지점). 반환 원소:
      (ticker, weight, price_start, price_end, last_if_delisted)
    상폐 종목의 price_end는 last × DELISTING_HAIRCUT 적용값. 진입가 결측(≤0 포함)·
    청산가 결측 종목은 제외 — 호출자가 유효 weight 합으로 재정규화한다.

    SPEC_10 robustness fast-path(scripts/robustness/)가 종목별 기여 산출에 재사용한다
    — 별도 가격 조회 로직 복제 금지.
    """
    valid: list[tuple[str, float, float, float, float | None]] = []
    for ticker, weight in portfolio.items():
        price_start = get_close_price(conn, ticker, start_date)

        if price_start is None or price_start <= 0:
            continue

        if is_delisted_at(conn, ticker, end_date):
            last = _last_known_price(conn, ticker, end_date)
            valid.append((ticker, weight, price_start, last * DELISTING_HAIRCUT, last))
        else:
            price_end = get_close_price(conn, ticker, end_date)
            if price_end is None:
                continue
            valid.append((ticker, weight, price_start, price_end, None))
    return valid


def _last_known_price(conn, ticker: str, before_date: date) -> float:
    """상장폐지 종목의 마지막 알려진 가격. 없으면 0."""
    from backtest.data_access import get_adj_close_range
    prices = get_adj_close_range(conn, ticker, before_date, lookback=1)
    return float(prices.iloc[-1]) if not prices.empty else 0.0


def _calc_index_return(symbol: str, name: str, start_date: date, end_date: date) -> float:
    """
    지수 구간 수익률 (FDR, Naver Finance 라우트).

    계약: 조회 실패·데이터 부족 시 **BenchmarkDataUnavailable을 던진다** — 0.0을
    반환하지 않는다 (CORR-BENCH-001: 장애가 '정상 수익률 0%'로 둔갑해 alpha·robustness를
    조용히 오염시키고 백테스트가 성공 상태로 끝나는 것을 차단).
    """
    import FinanceDataReader as fdr
    try:
        df = fdr.DataReader(symbol, str(start_date), str(end_date))
    except Exception as e:
        raise BenchmarkDataUnavailable(
            f'{name}({symbol}) 조회 실패 ({start_date}~{end_date}): {e}'
        ) from e

    close = df['Close'].dropna() if df is not None and not df.empty else None
    if close is None or len(close) < 2:
        raise BenchmarkDataUnavailable(
            f'{name}({symbol}) 데이터 부족 ({start_date}~{end_date}): '
            f'{0 if close is None else len(close)}행'
        )
    return float(close.iloc[-1] / close.iloc[0] - 1)


def _calc_kosdaq_return(start_date: date, end_date: date) -> float:
    """KOSDAQ 구간 수익률 (KQ11). 실패 시 BenchmarkDataUnavailable — 0.0 반환 금지."""
    return _calc_index_return('KQ11', 'KOSDAQ', start_date, end_date)


def _calc_kospi_return(start_date: date, end_date: date) -> float:
    """
    KOSPI 구간 수익률 (KS11, Naver Finance 라우트). 실패 시 BenchmarkDataUnavailable.

    'KRX/INDEX/KOSPI' 포맷은 FDR이 Yahoo Finance로 fallback → 500 에러.
    pykrx get_index_ohlcv_by_date는 KRX 2024 리뉴얼 후 KeyError로 불작동.
    """
    return _calc_index_return('KS11', 'KOSPI', start_date, end_date)
