"""종목 수 곡선 — holdings tape 을 상위 n 개로 **절단**해 n=1..N 성과를 산출한다.

## 왜 있는가

`docs/CANONICAL.md` 의 미해결 과제 `G5-MDD` 는 "구간간 표준편차가 n=1 에서 n=20 까지
거의 줄지 않는다"를 근거로 든다. 그런데 **그 수치가 어떤 산출물에도 없었다** —
`scripts/live/freeze_rebalance.py` 주석과 검토 문서 산문에만 있었고 재현 스크립트도
없었다(2026-08-14 확인). 수치를 산문에 박으면 낡고, 낡은 걸 읽는 사람은 낡은 줄 모른다.
`make_canonical` 을 만든 이유가 그것이었다. 이 스크립트는 그 근거를 산출물로 되돌린다.

## 재실행이 아니라 절단이다

`build_portfolio` 는 `candidates[:n_stocks]` 인 **순수 접두어 슬라이스**라, 상위 n 개
포트폴리오는 백테스트를 다시 돌리지 않고 기존 tape 에서 잘라낼 수 있다. 그래서 이
스크립트는 **DB 도 엔진도 쓰지 않는다** — 백테스트 기준선을 건드릴 위험이 없다
(DRIFT-INGEST-001 관점에서 안전하다).

## 이 값의 한계 — 반드시 함께 읽어라

- **gross 다.** tape 에 거래비용이 없다. 회전율은 n 에 따라 달라지므로 net 은 절단으로
  구할 수 없다. net 이 필요하면 그 n 으로 실제 재실행해야 한다.
- **구간 지표다.** MDD·Sharpe 의 SSOT 는 일별 NAV 다(SPEC_13 §9-1). 여기 값은 구간
  기준이라 일별 값보다 훨씬 얕게 나온다(같은 설정에서 −34% vs −58%).
- **tape 과 지표 산출물이 다른 실행일 수 있다** (미해결 과제 `TAPE-ASYNC`). 그래서 이
  스크립트는 재실행이 존재하는 n 에 대해 **교차 검증 결과를 함께 기록한다** — 절단값과
  재실행값의 차이가 곧 tape 신선도의 척도다.

실행:  venv/bin/python -m scripts.analysis.n_stocks_curve
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date
from pathlib import Path

import pandas as pd

from backtest.canonical_state import ABL_DIR, _sha256
from backtest.metrics import compute_cagr, compute_mdd, compute_sharpe

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

OUT_PATH = Path('experiments/analysis/n_stocks_curve.json')


def _load_tape(key: str) -> list[dict]:
    path = ABL_DIR / f'{key}_holdings.json'
    if not path.exists():
        raise SystemExit(
            f'holdings tape 이 없다: {path}. 이름이 비슷한 다른 tape 으로 대신 읽지 마라 — '
            f'종목 수가 다른 tape 을 집으면 곡선 전체가 조용히 틀린다.')
    return json.loads(path.read_text(encoding='utf-8'))


def _closed_periods(tape: list[dict], n_periods: int) -> list[dict]:
    """완결 구간만. **개수는 지표 산출물이 정한다 — 여기서 세지 않는다.**

    구간 수는 세 층이 다르다: tape 전체 / 보유가 있는 구간 / 완결 구간. 화면이 직접
    세다가 열린 구간을 섞어 CAGR 이 어긋난 사고가 2026-08-14 에 있었다(1.86%p).
    같은 실수를 여기서 반복하지 않으려고 기준을 산출물에서 가져온다.
    """
    held = [p for p in tape if p['n_portfolio'] > 0]
    if len(held) < n_periods:
        raise SystemExit(f'tape 의 보유 구간이 {len(held)}개인데 지표는 {n_periods}구간을 '
                         f'주장한다 — tape 과 지표가 다른 실행이다 (TAPE-ASYNC).')
    return held[:n_periods]


def _period_returns(periods: list[dict], n: int) -> pd.Series:
    """상위 n 개 동일가중 구간 수익률. 후보가 n 보다 적으면 있는 만큼(계약 CONTRACT-PF-001)."""
    out = []
    for p in periods:
        held = p['holdings'][:n]
        out.append(sum(h['ret'] for h in held) / len(held) if held else 0.0)
    return pd.Series(out, dtype=float)


def build(tag: str, source_n: int | None, max_n: int) -> dict:
    ref_key = tag if source_n is None else f'{tag}_n{source_n}'
    metrics_path = ABL_DIR / f'{ref_key}.json'
    if not metrics_path.exists():
        raise SystemExit(f'기준 지표 산출물이 없다: {metrics_path}')
    ref = json.loads(metrics_path.read_text(encoding='utf-8'))

    tape = _load_tape(ref_key)
    periods = _closed_periods(tape, ref['n_periods'])
    start = date.fromisoformat(min(p['rebalance_date'] for p in periods))
    end = date.fromisoformat(max(p['next_date'] for p in periods))
    cap = max(p['n_portfolio'] for p in periods)

    points = []
    for n in range(1, max_n + 1):
        r = _period_returns(periods, n)
        points.append({
            'n':                 n,
            'method':            'truncation',
            'gross_cagr':        round(compute_cagr(r, start_date=start, end_date=end), 6),
            'mean_period_return': round(float(r.mean()), 6),
            # G5-MDD 과제가 인용하는 값. n 을 늘려도 이게 안 줄면 분산이 안 되는 것이다.
            'period_return_std': round(float(r.std()), 6),
            'mdd_period_basis':  round(compute_mdd(r), 6),
            'sharpe_period_basis': round(compute_sharpe(r), 6),
        })

    # 재실행이 존재하는 n 은 교차 검증한다. 차이가 곧 tape 신선도의 척도다.
    cross = []
    for n in range(1, max_n + 1):
        p = ABL_DIR / f'{tag}_n{n}.json'
        if not p.exists():
            continue
        rerun = json.loads(p.read_text(encoding='utf-8'))
        trunc = points[n - 1]['gross_cagr']
        cross.append({
            'n': n, 'truncation_gross_cagr': trunc,
            'rerun_gross_cagr': round(rerun['cagr'], 6),
            'delta': round(trunc - rerun['cagr'], 6),
            'rerun_run_at': rerun.get('run_at'),
        })

    return {
        'spec': 'docs/설계/SPEC_10_pbr_gate_robustness.md',
        'disclaimer': ('gross · 구간 기준 · tape 절단. net 과 일별 MDD 는 이 방법으로 '
                       '구할 수 없다 (회전율이 n 에 따라 달라지고, 일별 지표는 NAV 가 SSOT). '
                       'MDD·Sharpe 의 SSOT 는 일별 NAV 다 — 여기 값은 구간 기준이라 훨씬 얕다.'),
        'source': {
            'tag': tag, 'artifact_key': ref_key,
            'tape': f'experiments/ablation/{ref_key}_holdings.json',
            'tape_sha256': _sha256(ABL_DIR / f'{ref_key}_holdings.json'),
            'metrics_sha256': _sha256(metrics_path),
            'tape_portfolio_cap': cap,
            'n_periods': ref['n_periods'],
            'first_rebalance': start.isoformat(), 'last_exit': end.isoformat(),
        },
        'points': points,
        'cross_check_vs_rerun': cross,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description='종목 수 곡선 (tape 절단)')
    ap.add_argument('--tag', default='F_pbr_ma200')
    ap.add_argument('--source-n', type=int, default=None,
                    help='기준 tape 의 n. 생략하면 접미사 없는 산출물(=기본 n)을 쓴다.')
    ap.add_argument('--max-n', type=int, default=20)
    args = ap.parse_args()

    d = build(args.tag, args.source_n, args.max_n)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding='utf-8')
    log.info('생성: %s (n=1..%d, 기준 %s)', OUT_PATH, args.max_n, d['source']['artifact_key'])

    for c in d['cross_check_vs_rerun']:
        level = log.info if abs(c['delta']) < 0.01 else log.warning
        level('교차검증 n=%d: 절단 %.4f%% vs 재실행 %.4f%% (차이 %+.4f%%p)',
              c['n'], c['truncation_gross_cagr'] * 100,
              c['rerun_gross_cagr'] * 100, c['delta'] * 100)

    first, last = d['points'][0], d['points'][-1]
    log.info('구간간 표준편차: n=1 %.2f%% → n=%d %.2f%%',
             first['period_return_std'] * 100, last['n'], last['period_return_std'] * 100)


if __name__ == '__main__':
    main()
