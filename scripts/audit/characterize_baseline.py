"""
AUDIT_01 Pass 0B — 특성화(characterization) baseline 캡처.

이 값은 "정답"이 아니라 "기존 구현의 동작 기록"이다. 버그 수정 시 정당하게 깨진다.
깨졌다고 자동으로 되돌리지 마라.

프로덕션 코드는 한 줄도 수정하지 않는다. backtest.engine/ablation/pipeline/portfolio의
기존 함수를 그대로 import해 호출한다 — 재구현하지 않는다(재구현 시 실제 동작과
미묘하게 어긋날 위험이 있어 원칙 3 위반).

산출:
  tests/baselines/selection/{scenario}.json   — 구간별 원시 float (반올림 없음)
  tests/baselines/aggregate/{scenario}.json   — engine.run() 실제 출력 그대로

closed_period(#23 열린 구간 제외) / live_snapshot(valuation_date 고정) 이층 분리는
selection/aggregate 파일 안에 각각 담는다 (AUDIT_01 §Pass 0B 지시).

실행 (서버, 운영 DB 읽기전용 조회):
  PYTHONPATH=/opt/stock-backtest venv/bin/python /tmp/characterize_baseline.py --tags F_no_r2r3
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from backtest.ablation import ABLATION_CONFIGS, RANDOM_TAGS, build_ablation_pipeline
from backtest.configs.rebalance_dates import REBALANCE_DATES
from backtest.data_access import get_close_price, is_delisted_at
from backtest.engine import BacktestEngine, DELISTING_HAIRCUT, _last_known_price
from ingest.connection import get_connection

OUT_SELECTION = Path('tests/baselines/selection')
OUT_AGGREGATE = Path('tests/baselines/aggregate')

# 마지막 리밸런싱 날짜(REBALANCE_DATES[-1])는 next_date가 date.today()로 결정되는
# 열린 구간이다 (CORR-ENGINE-003, engine.py:69). closed_period baseline은 이를 제외한다.
OPEN_PERIOD_REBAL_DATE = REBALANCE_DATES[-1]


def _get_delisted_date(conn, ticker: str, as_of: date) -> str | None:
    """stock_listing_events에서 상폐일 조회. data_access.is_delisted_at()과 동일 조건, 읽기전용."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT delisted_date FROM stock_listing_events
            WHERE ticker = %s AND delisted_date IS NOT NULL AND delisted_date <= %s
            ORDER BY delisted_date DESC LIMIT 1
            """,
            (ticker, as_of),
        )
        row = cur.fetchone()
        return row[0].isoformat() if row else None


def capture_selection_tape(conn, tag: str, period_results: list[dict]) -> list[dict]:
    """
    engine.run()이 이미 만든 period_results(portfolio dict 포함)를 입력으로,
    종목별 원시 entry/exit price를 추가 조회해 selection tape을 만든다.
    파이프라인·유니버스 구성을 재실행하지 않는다 — engine.run()이 실제로 쓴
    portfolio를 그대로 쓴다 (재실행 시 동일 DB라도 두 번째 호출 시점에 PIT 데이터가
    바뀌었을 극단적 경우 selection과 aggregate가 서로 다른 유니버스를 참조하게 될
    위험을 원천 차단).
    """
    tape = []
    for period in period_results:
        rebal_date = period['rebalance_date']
        next_date  = period['next_date']
        portfolio  = period['portfolio']

        holdings = []
        for ticker, weight in portfolio.items():
            entry_price = get_close_price(conn, ticker, rebal_date)
            delisted    = is_delisted_at(conn, ticker, next_date)

            if delisted:
                last = _last_known_price(conn, ticker, next_date)
                exit_price = last * DELISTING_HAIRCUT if last else None
                delisting_date = _get_delisted_date(conn, ticker, next_date)
                last_known_price_at_delisting = last
            else:
                exit_price = get_close_price(conn, ticker, next_date)
                delisting_date = None
                last_known_price_at_delisting = None

            holdings.append({
                'ticker':                        ticker,
                'weight':                         weight,
                'entry_price':                    entry_price,
                'entry_price_date':                rebal_date.isoformat(),
                'exit_price':                     exit_price,
                'exit_price_date':                 next_date.isoformat(),
                'is_delisted':                    delisted,
                'delisting_date':                 delisting_date,
                'last_known_price_at_delisting':  last_known_price_at_delisting,
            })

        tape.append({
            'rebalance_date': rebal_date.isoformat(),
            'end_date':       next_date.isoformat(),
            'is_open_period': period['is_open_period'],
            'n_gate':         period['n_gate'],
            'n_stocks':       period['n_stocks'],
            'holdings':       holdings,
        })
    return tape


def build_aggregate_tape(tag: str, result: dict, captured_at: str) -> dict:
    """engine.run() 실제 출력을 그대로 aggregate baseline으로 기록. 재계산하지 않는다."""
    period_agg = []
    for p in result['period_results']:
        period_agg.append({
            'rebalance_date':      p['rebalance_date'].isoformat(),
            'end_date':            p['next_date'].isoformat(),
            'is_open_period':      p['is_open_period'],
            'gross_return':        p['period_return'],
            'net_return':          p['net_return'],
            'turnover':            p['turnover'],
            'transaction_cost':    p['transaction_cost'],
            'delisting_opt_adj':   p['delisting_opt_adj'],
            'delisting_cons_adj':  p['delisting_cons_adj'],
            'kospi_return':        p['kospi_return'],
            'kosdaq_return':       p['kosdaq_return'],
            'n_gate':              p['n_gate'],
            'n_stocks':            p['n_stocks'],
        })

    return {
        'tag':          tag,
        'run_name':     result['run_name'],
        'ablation_tag': result['ablation_tag'],
        'captured_at':  captured_at,
        'periods':      period_agg,
        'overall_metrics_closed_periods': result['metrics'],
        'valuation_date':      str(result['valuation_date']),
        'price_data_max_date': str(result['price_data_max_date']),
        'note': (
            "overall_metrics_closed_periods는 engine.run()이 산출한 값 그대로다 — 2026-07 감사 "
            "(CORR-ENGINE-003/METRIC-002/FRESH-001) 이후 엔진의 공식 지표는 완결 구간만으로 "
            "계산되며 CAGR 연수는 실제 캘린더 경과일수 기준이다. 열린 마지막 구간(#23)은 "
            "periods에 is_open_period=True로 남지만 공식 지표에서는 제외된다."
        ),
    }


def run_one(tag: str) -> None:
    config = ABLATION_CONFIGS[tag]
    if tag in RANDOM_TAGS:
        raise SystemExit(
            f'[{tag}] RANDOM 시나리오는 이 스크립트 범위 밖이다 (seed 고정 여부는 GAPS.md 참조, '
            f'이번 Pass 0B에서 characterize 대상에서 제외).'
        )

    pipeline = build_ablation_pipeline(tag, config, seed=None)
    engine   = BacktestEngine(pipeline)

    print(f'[{tag}] engine.run() 실행 중 (23개 구간)...')
    result = engine.run(REBALANCE_DATES, run_name=tag, ablation_tag=tag,
                        valuation_date=date.today())
    captured_at = datetime.now().isoformat()

    conn = get_connection()
    try:
        selection_tape = capture_selection_tape(conn, tag, result['period_results'])
    finally:
        conn.close()

    # 자체 검증: selection tape의 원시 가격으로 재계산한 gross_return이
    # engine.run()이 실제로 산출한 period_return과 일치하는지 확인 (동일 로직 재사용
    # 여부를 스크립트 스스로 교차 검증 — 다르면 이 스크립트 자체가 버그다).
    mismatches = []
    for tape_period, real_period in zip(selection_tape, result['period_results']):
        holdings = tape_period['holdings']
        if not holdings:
            continue
        # engine._calc_period_return과 동일 계약: 유효 종목 weight 합으로 재정규화한 가중합
        valid = [h for h in holdings
                 if h['entry_price'] not in (None, 0) and h['entry_price'] > 0
                 and h['exit_price'] is not None]
        total_w = sum(h['weight'] for h in valid)
        recomputed = (
            sum((h['weight'] / total_w) * (h['exit_price'] / h['entry_price'] - 1) for h in valid)
            if valid and total_w > 0 else 0.0
        )
        real = real_period['period_return']
        if abs(recomputed - real) > 1e-9:
            mismatches.append({
                'rebalance_date': tape_period['rebalance_date'],
                'recomputed': recomputed,
                'engine_actual': real,
                'diff': recomputed - real,
            })

    if mismatches:
        print(f'[{tag}] ⚠ selection tape 재계산이 engine 실제값과 {len(mismatches)}개 구간에서 불일치:')
        for m in mismatches:
            print(f'    {m}')
    else:
        print(f'[{tag}] ✅ selection tape 재계산 == engine 실제값 (전 구간 일치, tol=1e-9)')

    OUT_SELECTION.mkdir(parents=True, exist_ok=True)
    OUT_AGGREGATE.mkdir(parents=True, exist_ok=True)

    sel_path = OUT_SELECTION / f'{tag}.json'
    sel_path.write_text(json.dumps({
        'tag': tag,
        'captured_at': captured_at,
        'cross_check_mismatches': mismatches,
        'periods': selection_tape,
    }, indent=2, ensure_ascii=False))

    agg_path = OUT_AGGREGATE / f'{tag}.json'
    agg_path.write_text(json.dumps(
        build_aggregate_tape(tag, result, captured_at), indent=2, ensure_ascii=False
    ))

    print(f'[{tag}] 저장 완료: {sel_path} / {agg_path}')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--tags', nargs='+', required=True)
    args = parser.parse_args()

    for tag in args.tags:
        if tag not in ABLATION_CONFIGS:
            raise SystemExit(f'알 수 없는 태그: {tag}')
        run_one(tag)


if __name__ == '__main__':
    main()
