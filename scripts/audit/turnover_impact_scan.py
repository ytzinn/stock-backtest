"""
AUDIT Pass 0C — Pass 0B selection tape 기반 실데이터 영향 스캔. DB 미접속, 읽기 전용.

1) CORR-METRIC-001 (turnover): 구간별 기록된 turnover(현행 산식) vs 올바른 정의
   0.5×Σ|Δw| 를 대조하고, 거래비용 차이가 net 수익률에 주는 왜곡을 정량화한다.
2) CORR-ENGINE-002 (순서 의존): 같은 구간에 "진입가 결측 종목"과 "상폐 종목"이
   동시에 존재하는지(=순서 의존 버그가 실데이터에서 발화 가능한 조건) 스캔한다.

실행: python scripts/audit/turnover_impact_scan.py
"""
from __future__ import annotations

import json
from pathlib import Path

from backtest.configs.constants import COST_BUY, COST_SELL

BASE = Path(__file__).resolve().parents[2] / 'tests' / 'baselines'


def turnover_true(prev: dict[str, float], curr: dict[str, float]) -> float:
    if not prev:
        return 1.0 if curr else 0.0
    tickers = set(prev) | set(curr)
    return 0.5 * sum(abs(curr.get(t, 0.0) - prev.get(t, 0.0)) for t in tickers)


def scan_tag(tag: str) -> None:
    selection = json.loads((BASE / 'selection' / f'{tag}.json').read_text(encoding='utf-8'))
    aggregate = json.loads((BASE / 'aggregate' / f'{tag}.json').read_text(encoding='utf-8'))
    agg_by_date = {p['rebalance_date']: p for p in aggregate['periods']}

    print(f'\n=== {tag} ===')
    unit_cost = COST_SELL + COST_BUY

    prev_w: dict[str, float] = {}
    total_recorded_tc = 0.0
    total_true_tc     = 0.0
    for period in selection['periods']:
        curr_w = {h['ticker']: h['weight'] for h in period['holdings']}
        rd = period['rebalance_date']
        recorded = agg_by_date[rd]['turnover']
        true_val = turnover_true(prev_w, curr_w)

        if curr_w or prev_w:
            total_recorded_tc += recorded * unit_cost
            total_true_tc     += true_val * unit_cost

        if abs(true_val - recorded) > 1e-9:
            print(f'  [turnover 불일치] {rd}: 기록 {recorded:.4f} vs 올바른 정의 {true_val:.4f} '
                  f'(net 왜곡 {((true_val - recorded) * unit_cost) * 100:+.3f}%p, '
                  f'{len(prev_w)}→{len(curr_w)}종목)')

        # CORR-ENGINE-002 발화 조건: 진입가 결측 + 상폐 동시 존재
        missing  = [h['ticker'] for h in period['holdings']
                    if h['entry_price'] is None or (h['entry_price'] or 0) <= 0]
        delisted = [h['ticker'] for h in period['holdings'] if h['is_delisted']]
        exit_missing = [h['ticker'] for h in period['holdings']
                        if not h['is_delisted'] and h['exit_price'] is None
                        and h['entry_price'] is not None]
        if delisted and (missing or exit_missing):
            print(f'  [순서의존 발화조건] {rd}: 상폐 {delisted} + 진입결측 {missing} '
                  f'+ 정상인데 청산가결측 {exit_missing}')
        elif delisted:
            print(f'  [상폐만 존재 — 순서 무관] {rd}: {delisted}')

        prev_w = curr_w

    print(f'  누적 거래비용: 기록 {total_recorded_tc*100:.3f}%p vs 올바른 정의 '
          f'{total_true_tc*100:.3f}%p (차이 {((total_true_tc-total_recorded_tc))*100:+.3f}%p, 단순합)')


def main() -> None:
    tags = sorted(p.stem for p in (BASE / 'selection').glob('*.json'))
    for tag in tags:
        scan_tag(tag)


if __name__ == '__main__':
    main()
