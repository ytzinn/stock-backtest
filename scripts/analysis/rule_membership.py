"""
SPEC_11 §2-1 — R1/R2(/R4)/R5/R6 룰별 멤버십 분해 (읽기 전용, 결정론적 집합 판정).

성과 실험 없이 집합 수준에서 판정한다:
  - 핵심 1: 채택 룰셋 {R1,R2,R5,R6}에서 R2를 제거해도 최종 편입 후보(1/PBR 상위 20)가
    전 리밸런싱일에서 동일한가 → 동일이면 R2 결정론적 삭제 가능 (CAGR 실험 불필요)
  - 핵심 2 (부수 확인): R4를 추가하면 최종 후보가 달라지는가 → MASTER §3-6 활성 룰
    표기({R1,R4,R5,R6})와 채택안({R1,R2,R5,R6}) 불일치 해소 재료

방법론 주석: 풀 기준은 "HARD + 모멘텀 통과"다. 프로덕션 순서는 HARD → Stability →
모멘텀이지만 세 필터 모두 종목별 독립 판정이라 최종 생존 집합은 적용 순서와 무관
(FactorScreener 같은 집합 의존 필터는 채택 경로에 없음 — 2026-07-19 구현 검토 확인).
필터 파라미터는 build_ablation_pipeline(F_pbr_no_r3r4)에서 그대로 가져온다 (SSOT).

실행: venv/bin/python -m scripts.analysis.rule_membership
출력: experiments/analysis/rule_membership.json + 콘솔 요약
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

from backtest.ablation import ABLATION_CONFIGS, _PBRRankPipeline, build_ablation_pipeline
from backtest.configs.rebalance_dates import REBALANCE_DATES
from backtest.data_access import load_gate_passed_tickers, load_pit_series_ttm
from backtest.engine import _report_type
from backtest.filters.stability_filter import StabilityFilter
from backtest.portfolio import build_portfolio
from ingest.connection import get_connection

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

OUT_DIR = Path('experiments/analysis')
RULESET_FULL = frozenset({'R1', 'R2', 'R5', 'R6'})   # 채택 후보 룰셋
SINGLE_RULES = ['R1', 'R2', 'R4', 'R5', 'R6']


def _stability_survivors(base: list[str], rules: frozenset, d: date, pit, conn) -> set[str]:
    f = StabilityFilter(r2_exception=True, active_rules=set(rules))
    passed, _ = f.apply(base, d, pit, conn)
    return set(passed)


def _top20(ranked: list[str], survivors: set[str]) -> list[str]:
    """base 전체 랭킹(1/PBR 내림차순)에서 survivors에 속한 상위 20 (점수는 집합 무관)."""
    return [t for t in ranked if t in survivors][:20]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pipe = build_ablation_pipeline('F_pbr_no_r3r4', ABLATION_CONFIGS['F_pbr_no_r3r4'])
    by_name = {f.__class__.__name__: f for f in pipe.filters}
    hard, mom = by_name['HardFilter'], by_name['MomentumFilter']
    ranker = _PBRRankPipeline(filters=[], n_stocks=20)

    closed = [(REBALANCE_DATES[i], REBALANCE_DATES[i + 1])
              for i in range(len(REBALANCE_DATES) - 1)]

    per_date = []
    r2_diff_dates, r4_diff_dates = [], []

    conn = get_connection()
    try:
        for d, _nxt in closed:
            rtype = _report_type(d)
            gate  = load_gate_passed_tickers(conn, d, report_type=rtype)
            if not gate:
                continue
            pit = load_pit_series_ttm(conn, d, report_type=rtype)

            hard_passed, _ = hard.apply(gate, d, pit, conn)
            base, _        = mom.apply(hard_passed, d, pit, conn)
            base_set       = set(base)

            single_pass = {R: _stability_survivors(base, frozenset({R}), d, pit, conn)
                           for R in SINGLE_RULES}
            fail = {R: base_set - single_pass[R] for R in SINGLE_RULES}

            surv_full  = _stability_survivors(base, RULESET_FULL, d, pit, conn)
            surv_noR2  = _stability_survivors(base, RULESET_FULL - {'R2'}, d, pit, conn)
            surv_addR4 = _stability_survivors(base, RULESET_FULL | {'R4'}, d, pit, conn)

            scored = ranker.score_and_rank(base, d, pit, conn)
            ranked = [item['ticker'] for item in scored]

            top_full  = _top20(ranked, surv_full)
            top_noR2  = _top20(ranked, surv_noR2)
            top_addR4 = _top20(ranked, surv_addR4)

            r2_same = top_full == top_noR2
            r4_same = top_full == top_addR4
            if not r2_same:
                r2_diff_dates.append(d.isoformat())
            if not r4_same:
                r4_diff_dates.append(d.isoformat())

            row = {
                'rebalance_date': d.isoformat(),
                'pool_hard_momentum': len(base_set),
                'fail_counts': {R: len(fail[R]) for R in SINGLE_RULES},
                'r1_only_fail': len(fail['R1'] - fail['R2']),
                'r2_only_fail': len(fail['R2'] - fail['R1']),
                'r1_and_r2_fail': len(fail['R1'] & fail['R2']),
                'r2_only_fail_tickers': sorted(fail['R2'] - fail['R1']),
                'newcomers_if_removed': {
                    R: len(_stability_survivors(base, RULESET_FULL - {R}, d, pit, conn)
                           - surv_full)
                    for R in sorted(RULESET_FULL)
                },
                'survivors_full': len(surv_full),
                'top20_same_without_r2': r2_same,
                'top20_diff_without_r2': sorted(set(top_noR2) ^ set(top_full)),
                'top20_same_with_r4': r4_same,
                'top20_diff_with_r4': sorted(set(top_addR4) ^ set(top_full)),
            }
            per_date.append(row)
            log.info('%s pool=%d 생존=%d | R2단독탈락=%d R2제거후 top20 %s | R4추가후 top20 %s',
                     d, len(base_set), len(surv_full), row['r2_only_fail'],
                     '동일' if r2_same else f'상이({len(row["top20_diff_without_r2"])})',
                     '동일' if r4_same else f'상이({len(row["top20_diff_with_r4"])})')
    finally:
        conn.close()

    verdict = {
        'r2_deterministically_removable': not r2_diff_dates,
        'r2_diff_dates': r2_diff_dates,
        'r4_addition_changes_top20': bool(r4_diff_dates),
        'r4_diff_dates': r4_diff_dates,
    }
    out = {'generated_at': datetime.now().isoformat(),
           'ruleset_full': sorted(RULESET_FULL), 'verdict': verdict, 'per_date': per_date}
    (OUT_DIR / 'rule_membership.json').write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding='utf-8')

    log.info('=== 판정 ===')
    log.info('R2 결정론적 삭제 가능: %s %s', verdict['r2_deterministically_removable'],
             f'(상이 구간: {r2_diff_dates})' if r2_diff_dates else '(전 구간 top20 동일)')
    log.info('R4 추가 시 top20 변화: %s %s', verdict['r4_addition_changes_top20'],
             f'(변화 구간: {r4_diff_dates})' if r4_diff_dates else '(전 구간 동일)')
    log.info('저장: %s', OUT_DIR / 'rule_membership.json')


if __name__ == '__main__':
    main()
