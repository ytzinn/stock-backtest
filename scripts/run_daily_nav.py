"""
SPEC_09 N-4 — 대상 태그의 holdings tape에서 일별 NAV 생성 + §4 게이트 + reconciliation.

실행 (서버):
  venv/bin/python -m scripts.run_daily_nav
  venv/bin/python -m scripts.run_daily_nav --tags F_pbr_no_r3r4

입력:
  experiments/ablation/{tag}_holdings.json  — 구간별 편입 종목 (07-18 PIT 기준 재생성본,
      SPEC_10 §2 — 07-15/16산 tape로 실행 금지)
  experiments/ablation/{tag}_periods.csv    — engine 기록 구간 수익률·turnover·tc

출력 (experiments/daily_nav/):
  {tag}_daily_nav.csv        — date, nav_gross, nav_net (전 구간 연속)
  {tag}_daily_positions.csv  — date, ticker, value (전역 gross NAV 스케일, long format)
  {tag}_reconciliation.csv   — 구간별 게이트 판정 상세 (G-NAV-1/2/4)
  benchmarks_daily.csv       — KOSPI/KOSDAQ 일별 종가 (FDR 조회분 보존 — 재현성)
  summary.json               — 태그별 §2 지표 + 게이트 결과

게이트 (SPEC_09 §4):
  G-NAV-1: 구간 복리 gross == engine period_return (tol 1e-6, 상폐 포함 구간 1e-3)
  G-NAV-2: 리밸런싱일 차감 비율 == engine transaction_cost (tol 1e-9) —
           tape에서 turnover를 0.5×Σ|Δw|로 재계산해 engine 기록과 대조
  G-NAV-3: 일별 gross MDD ≤ 반기 종점 gross MDD (수학적 필연 — 위반 시 구현 버그)
  G-NAV-4: |구간 복리 net − engine net_return| ≤ |gross×tc| + tol (승법/산술 교차항)

지표 기준 (§2): 일별 MDD·Sharpe·CVaR 등 헤드라인은 **net NAV** 기준.
G-NAV-3 게이트와 tracking error는 gross 기준 (endpoint MDD가 gross 정의라 동일
기준 비교, TE는 리밸런싱일 비용 스파이크 배제) — 리포트에 명기.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from backtest.configs.constants import COST_BUY, COST_SELL
from backtest.configs.rebalance_dates import REBALANCE_DATES
from backtest.daily_nav import daily_nav_for_period, stitch_periods
from backtest.engine import BenchmarkDataUnavailable, _calc_turnover
from backtest.metrics import compute_daily_metrics, compute_mdd
from ingest.connection import get_connection

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

TAPE_DIR = Path('experiments/ablation')
OUT_DIR  = Path('experiments/daily_nav')

DEFAULT_TAGS = ['F_pbr_no_r3r4', 'F_pbr_r6', 'F_momentum_rim', 'D_pbr_only',
                'F_no_stability_clean']

TOL_GROSS_CLEAN   = 1e-6
TOL_GROSS_DELIST  = 1e-3
TOL_TC            = 1e-9
TOL_NET_EXTRA     = 1e-6


def _abort_if_cron_window() -> None:
    """DRIFT-INGEST-001: 크론 시간대(UTC 10:00~10:45 = KST 19:00~19:45) 실행 금지."""
    now = datetime.now(timezone.utc)
    minutes = now.hour * 60 + now.minute
    if 10 * 60 <= minutes < 10 * 60 + 45:
        raise SystemExit(
            'DRIFT-INGEST-001: 크론 시간대(UTC 10:00~10:45) — 백테스트 실행 금지. '
            '크론 종료 후 재실행할 것.'
        )


def _load_tape(tag: str) -> list[dict]:
    path = TAPE_DIR / f'{tag}_holdings.json'
    if not path.exists():
        raise FileNotFoundError(
            f'{path} 없음 — SPEC_10 §2 tape 재생성(export_portfolios) 먼저 실행할 것. '
            f'07-15/16산 tape로 일별 NAV를 만들지 마라 (SPEC_09 N-4).'
        )
    return json.loads(path.read_text(encoding='utf-8'))


def _load_periods_csv(tag: str) -> dict[str, dict]:
    path = TAPE_DIR / f'{tag}_periods.csv'
    if not path.exists():
        raise FileNotFoundError(
            f'{path} 없음 — scripts.run_ablation --tags {tag} --det-only 먼저 실행할 것.'
        )
    out = {}
    with path.open(encoding='utf-8') as f:
        for row in csv.DictReader(f):
            out[row['rebalance_date']] = row
    return out


def _closed_tape_periods(tape: list[dict]) -> list[dict]:
    """완결 구간만 — next_date가 REBALANCE_DATES에 있는 구간 (열린 구간은 today라 없음)."""
    rebal_set = {d.isoformat() for d in REBALANCE_DATES}
    return [p for p in tape if p['next_date'] in rebal_set and p['n_portfolio'] > 0]


def _fetch_benchmarks() -> pd.DataFrame:
    """KS11/KQ11 일별 종가 (2015-01-01~). 실패 시 예외 — 조용한 기본값 금지."""
    import FinanceDataReader as fdr
    out = {}
    for sym, name in [('KS11', 'kospi'), ('KQ11', 'kosdaq')]:
        try:
            s = fdr.DataReader(sym, '2015-01-01')['Close'].dropna()
        except Exception as e:
            raise BenchmarkDataUnavailable(f'{name}({sym}) 일별 시리즈 조회 실패: {e}') from e
        if s.empty:
            raise BenchmarkDataUnavailable(f'{name}({sym}) 일별 시리즈 빈 응답')
        out[name] = s
    return pd.DataFrame(out)


def run_tag(conn, tag: str, benchmarks: pd.DataFrame) -> dict:
    tape       = _load_tape(tag)
    engine_csv = _load_periods_csv(tag)
    closed     = _closed_tape_periods(tape)
    if not closed:
        raise RuntimeError(f'{tag}: 완결 구간이 tape에 없음')

    stitch_inputs: list[dict] = []
    recon_rows:    list[dict] = []
    position_rows: list[dict] = []
    prev_weights:  dict[str, float] = {}
    endpoint_returns: list[float] = []
    all_pass = True

    for p in closed:
        rebal = date.fromisoformat(p['rebalance_date'])
        nxt   = date.fromisoformat(p['next_date'])
        eng   = engine_csv.get(p['rebalance_date'])
        if eng is None:
            raise RuntimeError(f'{tag} {rebal}: periods CSV에 해당 구간 없음 — tape/CSV 세대 불일치')

        tickers = [h['ticker'] for h in p['holdings']]
        weights = {t: 1.0 / len(tickers) for t in tickers}
        has_delisted = any(h.get('delisted') for h in p['holdings'])

        eng_gross = float(eng['period_return'])
        eng_net   = float(eng['net_return'])
        eng_tc    = float(eng['transaction_cost'])
        endpoint_returns.append(eng_gross)

        # G-NAV-2: tape에서 turnover 재계산 → engine tc와 대조
        turnover_re = _calc_turnover(prev_weights, weights)
        tc_re       = turnover_re * (COST_SELL + COST_BUY)
        pass2       = abs(tc_re - eng_tc) < TOL_TC
        prev_weights = weights

        obs, nav, values = daily_nav_for_period(conn, weights, rebal, nxt)

        daily_gross = float(nav.iloc[-1]) - 1.0
        tol1  = TOL_GROSS_DELIST if has_delisted else TOL_GROSS_CLEAN
        diff1 = abs(daily_gross - eng_gross)
        pass1 = diff1 < tol1

        # G-NAV-4: 승법 net의 구간 복리 vs engine 산술 net (교차항 상한)
        daily_net = (1.0 + daily_gross) * (1.0 - eng_tc) - 1.0
        bound4    = abs(eng_gross * eng_tc) + TOL_NET_EXTRA + (tol1 if has_delisted else 0.0)
        diff4     = abs(daily_net - eng_net)
        pass4     = diff4 <= bound4

        all_pass &= pass1 and pass2 and pass4
        recon_rows.append({
            'rebalance_date': rebal.isoformat(), 'next_date': nxt.isoformat(),
            'n_stocks': len(tickers), 'has_delisted': has_delisted,
            'engine_gross': eng_gross, 'daily_gross': daily_gross,
            'diff_gross': daily_gross - eng_gross, 'tol_gross': tol1, 'G_NAV_1': pass1,
            'engine_tc': eng_tc, 'recomputed_tc': tc_re,
            'diff_tc': tc_re - eng_tc, 'G_NAV_2': pass2,
            'engine_net': eng_net, 'daily_net_compound': daily_net,
            'diff_net': daily_net - eng_net, 'bound_net': bound4, 'G_NAV_4': pass4,
        })

        stitch_inputs.append({
            'rebalance_date': rebal, 'obs_dates': obs,
            'nav_path': [float(v) for v in nav], 'transaction_cost': eng_tc,
        })
        for t in values.columns:
            for d, v in values[t].items():
                position_rows.append({'date': d.isoformat(), 'ticker': t,
                                      'period_start': rebal.isoformat(), 'value': float(v)})

        if not (pass1 and pass2 and pass4):
            log.warning('[%s] %s: 게이트 실패 G1=%s G2=%s G4=%s (diff_gross=%.2e diff_tc=%.2e diff_net=%.2e)',
                        tag, rebal, pass1, pass2, pass4, diff1, tc_re - eng_tc, diff4)

    nav_df = stitch_periods(stitch_inputs)

    # 전역 스케일로 종목별 기여 보정 (구간 시작 gross NAV 배수)
    start_scale = {r['rebalance_date'].isoformat(): float(nav_df.loc[r['rebalance_date'], 'nav_gross'])
                   for r in stitch_inputs}
    for row in position_rows:
        row['value_global'] = row['value'] * start_scale[row['period_start']]

    # §2 지표 — 헤드라인은 net NAV 기준
    m_net   = compute_daily_metrics(nav_df['nav_net'])
    m_gross = compute_daily_metrics(nav_df['nav_gross'], benchmark=benchmarks['kospi'])
    te_kosdaq = compute_daily_metrics(nav_df['nav_gross'],
                                      benchmark=benchmarks['kosdaq'])['tracking_error_ann']

    # G-NAV-3: gross 기준 일별 MDD vs 반기 종점 MDD (동일 기준 비교)
    endpoint_mdd = compute_mdd(pd.Series(endpoint_returns))
    pass3 = m_gross['daily_mdd'] <= endpoint_mdd + 1e-12
    all_pass &= pass3
    if not pass3:
        log.error('[%s] G-NAV-3 위반: 일별 gross MDD %.4f > 종점 MDD %.4f — 구현 버그',
                  tag, m_gross['daily_mdd'], endpoint_mdd)

    return {
        'tag': tag, 'nav_df': nav_df, 'recon_rows': recon_rows,
        'position_rows': position_rows, 'all_pass': bool(all_pass),
        'metrics': {
            'n_closed_periods':   len(closed),
            'endpoint_mdd_gross': endpoint_mdd,
            'daily_mdd_gross':    m_gross['daily_mdd'],
            'G_NAV_3':            bool(pass3),
            'net':   {k: (v.isoformat() if isinstance(v, date) else v) for k, v in m_net.items()},
            'gross_mdd_peak':     m_gross['mdd_peak_date'].isoformat(),
            'gross_mdd_trough':   m_gross['mdd_trough_date'].isoformat(),
            'tracking_error_kospi':  m_gross['tracking_error_ann'],
            'tracking_error_kosdaq': te_kosdaq,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description='SPEC_09 일별 NAV 생성 + 게이트')
    parser.add_argument('--tags', nargs='+', default=DEFAULT_TAGS)
    args = parser.parse_args()

    _abort_if_cron_window()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    benchmarks = _fetch_benchmarks()
    benchmarks.to_csv(OUT_DIR / 'benchmarks_daily.csv', encoding='utf-8')
    log.info('벤치마크 일별 시리즈 저장: %s행', len(benchmarks))

    summary: dict = {'generated_at': datetime.now().isoformat(), 'tags': {}}
    any_fail = False
    conn = get_connection()
    try:
        for tag in args.tags:
            log.info('=== %s ===', tag)
            r = run_tag(conn, tag, benchmarks)

            r['nav_df'].to_csv(OUT_DIR / f'{tag}_daily_nav.csv',
                               index_label='date', encoding='utf-8')
            pd.DataFrame(r['recon_rows']).to_csv(
                OUT_DIR / f'{tag}_reconciliation.csv', index=False, encoding='utf-8')
            pd.DataFrame(r['position_rows']).to_csv(
                OUT_DIR / f'{tag}_daily_positions.csv', index=False, encoding='utf-8')

            summary['tags'][tag] = {'all_gates_pass': r['all_pass'], **r['metrics']}
            any_fail |= not r['all_pass']

            m = r['metrics']
            log.info('[%s] 일별 MDD(net)=%.2f%% (종점 %.2f%%) Sharpe(일별)=%.3f '
                     '최악월=%.2f%% CVaR1M=%.2f%% TE(KS)=%.2f%% 게이트=%s',
                     tag, m['net']['daily_mdd'] * 100, m['endpoint_mdd_gross'] * 100,
                     m['net']['daily_sharpe'], m['net']['worst_month_return'] * 100,
                     m['net']['cvar_5pct_1m'] * 100, m['tracking_error_kospi'] * 100,
                     'PASS' if r['all_pass'] else 'FAIL')
    finally:
        conn.close()

    (OUT_DIR / 'summary.json').write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
    log.info('요약 저장: %s', OUT_DIR / 'summary.json')

    if any_fail:
        log.error('게이트 실패 구간 존재 — reconciliation CSV 확인 후 사용자 승인 대기 (SPEC_09 §4)')
        sys.exit(1)


if __name__ == '__main__':
    main()
