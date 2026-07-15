"""
Pass 3 배포 검증 — 수정 전(기존 baseline tape) vs 수정 후(재실행) 비교.

사용자 지정 필수 산출물: PIT-AMEND 수정 전후로 ablation 결과가 바뀌는지 확인.
selection(편입 종목)과 aggregate(지표)를 분리해 보고한다 —
  selection 불변 + aggregate 변경 → 산술 수정의 효과
  selection 변경                  → 필터/데이터 수정의 효과 (룩어헤드 제거·상장필터 부활 등)

실행 (서버, 수정된 코드 + 재빌드된 DB 상태에서):
    PYTHONPATH=/opt/stock-backtest venv/bin/python scripts/audit/compare_before_after.py \
        --valuation-date 2026-07-11 --tags F_no_r2r3 D_rim_only D_no_r2 D_no_r3 D_no_stability

--valuation-date는 기존 baseline 캡처 시점(2026-07-11)과 맞춰 열린 구간을 동일 조건으로 둔다.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from backtest.ablation import ABLATION_CONFIGS, build_ablation_pipeline
from backtest.configs.rebalance_dates import REBALANCE_DATES
from backtest.engine import BacktestEngine

BASE = Path('tests/baselines')


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def run_after(tag: str, valuation_date: date) -> dict:
    pipeline = build_ablation_pipeline(tag, ABLATION_CONFIGS[tag], seed=None)
    result = BacktestEngine(pipeline).run(
        REBALANCE_DATES, run_name=tag, ablation_tag=tag, valuation_date=valuation_date
    )
    return result


def compare(tag: str, valuation_date: date) -> dict:
    before_sel = json.loads((BASE / 'selection' / f'{tag}.json').read_text(encoding='utf-8'))
    before_agg = json.loads((BASE / 'aggregate' / f'{tag}.json').read_text(encoding='utf-8'))
    before_holdings = {
        p['rebalance_date']: {h['ticker'] for h in p['holdings']} for p in before_sel['periods']
    }
    before_periods = {p['rebalance_date']: p for p in before_agg['periods']}
    before_metrics = before_agg['overall_metrics_all_periods_including_open']

    after = run_after(tag, valuation_date)
    rows = []
    changed_periods = 0
    for p in after['period_results']:
        rd = p['rebalance_date'].isoformat()
        after_set = set(p['portfolio'])
        before_set = before_holdings.get(rd, set())
        j = jaccard(before_set, after_set)
        if j < 1.0:
            changed_periods += 1
        b = before_periods.get(rd, {})
        rows.append({
            'rebalance_date': rd,
            'is_open': p['is_open_period'],
            'n_before': len(before_set),
            'n_after': len(after_set),
            'jaccard': round(j, 4),
            'added': sorted(after_set - before_set),
            'removed': sorted(before_set - after_set),
            'gross_before': b.get('gross_return'),
            'gross_after': p['period_return'],
            'net_before': b.get('net_return'),
            'net_after': p['net_return'],
            'turnover_before': b.get('turnover'),
            'turnover_after': p['turnover'],
            'n_gate_before': b.get('n_gate'),
            'n_gate_after': p['n_gate'],
        })

    return {
        'tag': tag,
        'changed_periods': changed_periods,
        'total_periods': len(rows),
        'metrics_before(구 엔진 기준, 열린구간 포함)': before_metrics,
        'metrics_after(신 엔진 기준, 완결구간)': after['metrics'],
        'periods': rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--tags', nargs='+', required=True)
    ap.add_argument('--valuation-date', required=True)
    ap.add_argument('--out', default='experiments/runs/pass3_before_after.json')
    args = ap.parse_args()

    vd = date.fromisoformat(args.valuation_date)
    results = {}
    for tag in args.tags:
        print(f'=== {tag} 재실행 중 ===')
        r = compare(tag, vd)
        results[tag] = r
        mb = r['metrics_before(구 엔진 기준, 열린구간 포함)']
        ma = r['metrics_after(신 엔진 기준, 완결구간)']
        print(f"  편입 변경 구간: {r['changed_periods']}/{r['total_periods']}")
        print(f"  CAGR   {mb['cagr']:.4%} → {ma['cagr']:.4%}")
        print(f"  net    {mb['net_cagr']:.4%} → {ma['net_cagr']:.4%}")
        print(f"  Sharpe {mb['sharpe']:.3f} → {ma['sharpe']:.3f}   "
              f"MDD {mb['mdd']:.2%} → {ma['mdd']:.2%}")
        for p in r['periods']:
            if p['jaccard'] < 1.0:
                print(f"    [{p['rebalance_date']}] J={p['jaccard']:.3f} "
                      f"gate {p['n_gate_before']}→{p['n_gate_after']} "
                      f"n {p['n_before']}→{p['n_after']} "
                      f"-{p['removed']} +{p['added']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1, ensure_ascii=False, default=str))
    print(f'\n저장: {out}')


if __name__ == '__main__':
    main()
