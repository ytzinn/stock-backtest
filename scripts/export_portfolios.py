"""
구간별 편입 종목 + 거래가격 추출 스크립트.

각 결정론적 시나리오(D/E/F/G/H + no_r6 변형)에 대해
리밸런싱 구간별 편입 종목, 종목명, 진입가(rebalance_date 종가),
청산가(next_date 종가), 구간수익률을 추출해 JSON으로 저장.
이후 make_excel.py가 이 JSON을 읽어 Excel 생성.

실행:
  venv/bin/python -m scripts.export_portfolios
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from backtest.ablation import ABLATION_CONFIGS, RANDOM_TAGS, build_ablation_pipeline
from backtest.configs.rebalance_dates import REBALANCE_DATES
from backtest.data_access import get_close_price, load_gate_passed_tickers, load_pit_series_ttm
from backtest.engine import DELISTING_HAIRCUT, _last_known_price, _report_type
from ingest.connection import get_connection

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

OUT_DIR = Path('experiments/ablation')
START_DATE = date(2016, 4, 5)   # 유효 시작 (처음 2구간 제외)
END_DATE   = date(2026, 4, 3)   # 마지막 유효 리밸런싱일


def get_stock_names(conn) -> dict[str, str]:
    cur = conn.cursor()
    cur.execute("SELECT ticker, corp_name FROM stocks")
    return {r[0]: r[1] for r in cur.fetchall()}


def extract_portfolio_periods(tag: str, config: dict) -> list[dict]:
    pipeline = build_ablation_pipeline(tag, config, seed=None)
    conn = get_connection()
    names = get_stock_names(conn)
    results = []

    dates = [d for d in REBALANCE_DATES if START_DATE <= d <= END_DATE]

    for i, rebal_date in enumerate(dates):
        idx = REBALANCE_DATES.index(rebal_date)
        next_date = REBALANCE_DATES[idx + 1] if idx + 1 < len(REBALANCE_DATES) else date.today()
        rtype     = _report_type(rebal_date)

        gate_passed = load_gate_passed_tickers(conn, rebal_date, report_type=rtype)
        pit_series  = load_pit_series_ttm(conn, rebal_date, report_type=rtype)
        univ_result = pipeline.build_universe(gate_passed, rebal_date, pit_series, conn)
        candidates  = pipeline.score_and_rank(univ_result['universe'], rebal_date, pit_series, conn)

        from backtest.portfolio import build_portfolio
        portfolio = build_portfolio(candidates, n_stocks=pipeline.n_stocks)

        holdings = []
        for ticker in portfolio:
            entry = get_close_price(conn, ticker, rebal_date)
            exit_ = get_close_price(conn, ticker, next_date)

            delisted = False
            if exit_ is None:
                last = _last_known_price(conn, ticker, next_date)
                exit_ = last * DELISTING_HAIRCUT if last else None
                delisted = True

            ret = (exit_ / entry - 1) if (entry and exit_) else None

            holdings.append({
                'ticker':   ticker,
                'name':     names.get(ticker, ''),
                'entry':    round(entry, 0) if entry else None,
                'exit':     round(exit_, 0) if exit_ else None,
                'ret':      round(ret, 6) if ret is not None else None,
                'delisted': delisted,
            })

        results.append({
            'rebalance_date': rebal_date.isoformat(),
            'next_date':      next_date.isoformat(),
            'n_portfolio':    len(portfolio),
            'holdings':       holdings,
        })
        log.info(f'[{tag}] {rebal_date} → {len(portfolio)}종목')

    conn.close()
    return results


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    det_tags = [t for t in ABLATION_CONFIGS if t not in RANDOM_TAGS]

    for tag in det_tags:
        config = ABLATION_CONFIGS[tag]
        log.info(f'=== {tag} 추출 시작 ===')
        periods = extract_portfolio_periods(tag, config)
        out = OUT_DIR / f'{tag}_holdings.json'
        out.write_text(json.dumps(periods, ensure_ascii=False, indent=2), encoding='utf-8')
        log.info(f'  → {out}')

    log.info('전체 완료')


if __name__ == '__main__':
    main()
