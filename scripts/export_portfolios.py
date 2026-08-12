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
from backtest.configs.schedule import (
    CALENDAR_CHOICES,
    RebalancePoint,
    get_schedule,
    tag_suffix,
)
from backtest.data_access import (
    get_close_price,
    is_delisted_at,
    load_gate_passed_tickers,
    load_pit_series_ttm,
)
from backtest.engine import DELISTING_HAIRCUT, _last_known_price
from ingest.connection import get_connection

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

OUT_DIR = Path('experiments/ablation')
# 반기 캘린더 전용 유효 구간 — 앞 2구간(2015년, TTM 미충족으로 gate=0)과 열린 구간을
# 잘라내던 하드코딩. 안 A·안 C에는 적용하지 않는다(해당 캘린더의 유효 시작일을 새로
# 추정할 필요 없이, gate=0 구간은 n_portfolio=0으로 나와 다운스트림이 자동 스킵한다).
START_DATE = date(2016, 4, 5)   # 유효 시작 (처음 2구간 제외)
END_DATE   = date(2026, 4, 3)   # 마지막 유효 리밸런싱일


def get_stock_names(conn) -> dict[str, str]:
    cur = conn.cursor()
    cur.execute("SELECT ticker, corp_name FROM stocks")
    return {r[0]: r[1] for r in cur.fetchall()}


def extract_portfolio_periods(
    tag:              str,
    config:           dict,
    rebalance_points: list[RebalancePoint],
    date_filter:      bool = True,
    n_stocks:         int | None = None,
) -> list[dict]:
    """`n_stocks`가 None이면 `build_ablation_pipeline`의 기본값(20)을 쓴다.

    tape 은 지표(`{tag}.json`)와 **같은 n 으로** 뽑아야 한다. 어긋나면 소비처가
    조용히 다른 전략을 분석한다 (2026-08-12: n=13 운영인데 n=20 tape 만 존재해
    freeze_rebalance 의 turnover 가 종목 수 전이분을 삼켰다).
    """
    kw = {} if n_stocks is None else {'n_stocks': n_stocks}
    pipeline = build_ablation_pipeline(tag, config, seed=None, **kw)
    conn = get_connection()
    names = get_stock_names(conn)
    results = []

    for idx, rp in enumerate(rebalance_points):
        if date_filter and not (START_DATE <= rp.date <= END_DATE):
            continue
        rebal_date = rp.date
        next_date  = (rebalance_points[idx + 1].date
                      if idx + 1 < len(rebalance_points) else date.today())
        rtype      = rp.report_type

        gate_passed = load_gate_passed_tickers(
            conn, rebal_date, report_type=rtype, fiscal_year=rp.fiscal_year
        )
        pit_series  = load_pit_series_ttm(
            conn, rebal_date, report_type=rtype, fiscal_year=rp.fiscal_year
        )
        univ_result = pipeline.build_universe(gate_passed, rebal_date, pit_series, conn)
        candidates  = pipeline.score_and_rank(univ_result['universe'], rebal_date, pit_series, conn)

        from backtest.portfolio import build_portfolio
        portfolio = build_portfolio(candidates, n_stocks=pipeline.n_stocks)

        holdings = []
        for ticker in portfolio:
            entry = get_close_price(conn, ticker, rebal_date)

            # 상폐 판정은 is_delisted_at()(stock_listing_events 기준)으로 한다.
            # get_close_price()는 date<=as_of 최신값을 반환해 상폐로 가격이 끊겨도
            # None이 되지 않으므로 exit_ is None을 트리거로 쓰지 않는다 (engine.py와 동일 수정).
            delisted = is_delisted_at(conn, ticker, next_date)
            if delisted:
                last = _last_known_price(conn, ticker, next_date)
                exit_ = last * DELISTING_HAIRCUT if last else None
            else:
                exit_ = get_close_price(conn, ticker, next_date)

            ret = (exit_ / entry - 1) if (entry and exit_) else None

            holdings.append({
                'ticker':   ticker,
                'name':     names.get(ticker, ''),
                'entry':    round(entry, 0) if entry else None,
                'exit':     round(exit_, 0) if exit_ else None,
                'ret':      round(ret, 6) if ret is not None else None,
                'delisted': delisted,
            })

        period_record = {
            'rebalance_date': rebal_date.isoformat(),
            'next_date':      next_date.isoformat(),
            'n_portfolio':    len(portfolio),
            'holdings':       holdings,
        }
        # SPEC_11 M-3 최소 확장: 모멘텀 필터가 파이프라인에 있으면 탈락 종목을 보존
        # (D_pbr_no_r3r4 대조 분석의 "탈락 종목 이후 수익률" 원천). 기존 소비자는
        # 추가 키를 무시하므로 시나리오 결과 불변.
        mom_stats = univ_result['stats'].get('MomentumFilter')
        if mom_stats is not None:
            period_record['momentum_rejected'] = sorted(mom_stats['rejected'])
        results.append(period_record)
        log.info(f'[{tag}] {rebal_date} → {len(portfolio)}종목')

    conn.close()
    return results


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--tags', nargs='+', help='추출할 태그 목록 (기본: 전체 결정론적 시나리오)')
    parser.add_argument('--n-stocks', type=int, default=None,
                        help='포트폴리오 종목 수. run_ablation --n-stocks 와 **같은 값**을 '
                             '줘야 지표와 tape 의 n 이 일치한다. 지정 시 `_n{K}` 접미사.')
    parser.add_argument('--calendar', choices=CALENDAR_CHOICES, default='SEMIANNUAL',
                        help='리밸런싱 캘린더 (SPEC_13 §7). 기본 SEMIANNUAL = 기존 동작·'
                             '기존 파일명. A/C는 산출물에 _A/_C 접미사가 붙는다.')
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    det_tags = [t for t in ABLATION_CONFIGS if t not in RANDOM_TAGS]
    if args.tags:
        det_tags = [t for t in det_tags if t in args.tags]

    rebalance_points = list(get_schedule(args.calendar))
    suffix           = tag_suffix(args.calendar)
    # run_ablation 과 동일한 명명 규약 — 명시하면 항상 `_n{K}`.
    n_sfx            = '' if args.n_stocks is None else f'_n{args.n_stocks}'
    log.info(f'calendar = {args.calendar} ({len(rebalance_points)}개 앵커)')

    for tag in det_tags:
        config = ABLATION_CONFIGS[tag]
        log.info(f'=== {tag}{n_sfx}{suffix} 추출 시작 ===')
        periods = extract_portfolio_periods(
            tag, config, rebalance_points, date_filter=(args.calendar == 'SEMIANNUAL'),
            n_stocks=args.n_stocks,
        )
        out = OUT_DIR / f'{tag}{n_sfx}{suffix}_holdings.json'
        out.write_text(json.dumps(periods, ensure_ascii=False, indent=2), encoding='utf-8')
        log.info(f'  → {out}')

    log.info('전체 완료')


if __name__ == '__main__':
    main()
