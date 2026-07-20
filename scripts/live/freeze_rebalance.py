"""
SPEC_11 §5 — #24 라이브 포워드 동결 manifest (사전 기록, 리밸런싱 실행 **전** git 커밋).

운영 규칙 (§5-3):
  - manifest 커밋 후 해당 리밸런싱 신호는 어떤 이유로도 소급 수정하지 않는다.
    이후 재계산이 달라지면 recomputed_signal.yaml로 병기, 원본 불변.
  - SPEC_10 관문 FAIL이어도 shadow portfolio로 동일 기록 (자금 집행 여부만 분리).
  - 이 기록이 프로젝트 유일의 진짜 OOS 관측 축적 수단이다.

실행 (서버):
  dry-run 스키마 검증: venv/bin/python -m scripts.live.freeze_rebalance --dry-run
  실제 동결(#24):      venv/bin/python -m scripts.live.freeze_rebalance \
                         --signal-date 2026-08-XX --execution-date 2026-08-XX \
                         --test-status "fast:151pass integration:pass"

출력: experiments/live/{execution_date|dryrun}/manifest.yaml
      (기존 파일 존재 시 dry-run 외에는 중단 — 원본 불변 규칙)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from backtest.ablation import ABLATION_CONFIGS, build_ablation_pipeline
from backtest.configs.constants import (COST_BUY, COST_SELL, OMEGA, RF, RK, VB_CAP)
from backtest.data_access import (get_max_price_date, load_gate_passed_tickers,
                                  load_pit_series_ttm)
from backtest.engine import DELISTING_HAIRCUT, _calc_turnover, _report_type
from backtest.portfolio import build_portfolio
from ingest.connection import get_connection

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

DEFAULT_TAG = 'F_pbr_no_r3r4'
LIVE_DIR    = Path('experiments/live')
N_STOCKS    = 20


def _abort_if_cron_window() -> None:
    now = datetime.now(timezone.utc)
    minutes = now.hour * 60 + now.minute
    if 10 * 60 <= minutes < 10 * 60 + 45:
        raise SystemExit('DRIFT-INGEST-001: 크론 시간대(UTC 10:00~10:45) — 신호 생성 금지.')


def _config_hash(tag: str) -> str:
    """constants + 활성 룰 + n_stocks 직렬화 해시 (set은 정렬 list로 정규화)."""
    cfg = {k: (sorted(v) if isinstance(v, (set, frozenset)) else v)
           for k, v in ABLATION_CONFIGS[tag].items()}
    payload = json.dumps({
        'tag': tag, 'config': cfg, 'n_stocks': N_STOCKS,
        'constants': {'RF': RF, 'RK': RK, 'OMEGA': OMEGA, 'VB_CAP': VB_CAP,
                      'DELISTING_HAIRCUT': DELISTING_HAIRCUT,
                      'COST_SELL': COST_SELL, 'COST_BUY': COST_BUY},
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]


def _git_sha() -> str:
    return subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip()


def _db_snapshot(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute('SELECT MAX(date) FROM market_cap_history')
        mc_max = cur.fetchone()[0]
        cur.execute('SELECT MAX(available_from), COUNT(*) FROM financials_pit')
        af_max, af_cnt = cur.fetchone()
    price_max = get_max_price_date(conn)
    return {
        'price_max_date': price_max.isoformat() if price_max else None,
        'market_cap_max_date': mc_max.isoformat() if mc_max else None,
        # financials_pit에 빌드 id 컬럼 부재 → MAX(available_from)+행수 대체 식별자
        # (SPEC_11 §5-1 구현 노트, 2026-07-19)
        'financial_pit_build_id': f'{af_max.isoformat() if af_max else "none"}_{af_cnt}rows',
    }


def _rejection_summary(stats: dict) -> dict:
    out = {}
    for fname, s in stats.items():
        reasons: Counter = Counter()
        for v in s['rejected'].values():
            if isinstance(v, list):
                reasons.update(v)
            else:
                reasons[str(v)] += 1
        out[fname] = {'passed': s['passed'], 'rejected': len(s['rejected']),
                      'reasons': dict(reasons.most_common())}
    return out


def _previous_holdings(tag: str, signal_date: date) -> tuple[str | None, dict[str, float]]:
    """직전 리밸런싱 보유 (holdings tape에서 signal_date 이전 최신 구간)."""
    path = Path('experiments/ablation') / f'{tag}_holdings.json'
    if not path.exists():
        return None, {}
    periods = [p for p in json.loads(path.read_text(encoding='utf-8'))
               if p['rebalance_date'] < signal_date.isoformat() and p['n_portfolio'] > 0]
    if not periods:
        return None, {}
    last = max(periods, key=lambda p: p['rebalance_date'])
    tickers = [h['ticker'] for h in last['holdings']]
    return last['rebalance_date'], {t: 1.0 / len(tickers) for t in tickers}


def main() -> None:
    parser = argparse.ArgumentParser(description='SPEC_11 §5 동결 manifest')
    parser.add_argument('--tag', default=DEFAULT_TAG)
    parser.add_argument('--signal-date', default=None, help='기본: price_history 최신 거래일')
    parser.add_argument('--execution-date', default=None, help='기본: signal-date와 동일')
    parser.add_argument('--dry-run', action='store_true',
                        help='스키마 검증용 — experiments/live/dryrun/에 생성 (덮어쓰기 허용)')
    parser.add_argument('--test-status', default='not_run',
                        help='실행 시점 fast/integration 상태 문자열')
    args = parser.parse_args()

    try:
        import yaml
    except ImportError as e:
        raise SystemExit('pyyaml 필요: venv/bin/pip install pyyaml') from e

    _abort_if_cron_window()

    conn = get_connection()
    try:
        signal_date = (date.fromisoformat(args.signal_date) if args.signal_date
                       else get_max_price_date(conn))
        execution_date = (date.fromisoformat(args.execution_date) if args.execution_date
                          else signal_date)

        out_dir = LIVE_DIR / ('dryrun' if args.dry_run else execution_date.isoformat())
        out_path = out_dir / 'manifest.yaml'
        if out_path.exists() and not args.dry_run:
            raise SystemExit(
                f'{out_path} 이미 존재 — manifest는 소급 수정 금지 (§5-3). '
                f'재계산 신호는 recomputed_signal.yaml로 병기할 것.'
            )

        tag = args.tag
        rtype = _report_type(signal_date)
        pipeline = build_ablation_pipeline(tag, ABLATION_CONFIGS[tag], seed=None)
        gate = load_gate_passed_tickers(conn, signal_date, report_type=rtype)
        pit  = load_pit_series_ttm(conn, signal_date, report_type=rtype)
        univ = pipeline.build_universe(gate, signal_date, pit, conn)
        candidates = pipeline.score_and_rank(univ['universe'], signal_date, pit, conn)
        portfolio  = build_portfolio(candidates, n_stocks=N_STOCKS)

        prev_date, prev_w = _previous_holdings(tag, signal_date)
        turnover = _calc_turnover(prev_w, portfolio)

        manifest = {
            'strategy_version':      f'{tag} v1.0 (SPEC_10 관문: G1·G2 PASS, G5 FAIL — '
                                     f'채택 보류, shadow 기록)',
            'git_commit_sha':        _git_sha(),
            'config_hash':           _config_hash(tag),
            'database_snapshot_date': date.today().isoformat(),
            **_db_snapshot(conn),
            'signal_date':           signal_date.isoformat(),
            'execution_date':        execution_date.isoformat(),
            'execution_rule':        '종가 체결 가정 (CONTRACT-NAV-005), 실제는 당일 분할 주문',
            'report_type':           rtype,
            'n_gate_passed':         len(gate),
            'selected_tickers':      sorted(portfolio),
            'target_weights':        {t: round(w, 6) for t, w in sorted(portfolio.items())},
            'pbr_scores':            [{'rank': i + 1, 'ticker': c['ticker'],
                                       'inv_pbr': round(c['upside_pct'], 6),
                                       'pbr': round(1.0 / c['upside_pct'], 4)
                                              if c['upside_pct'] else None}
                                      for i, c in enumerate(candidates)],
            'filter_rejection_reasons': _rejection_summary(univ['stats']),
            'previous_rebalance_date': prev_date,
            'expected_turnover':     round(turnover, 6),
            'expected_cost':         round(turnover * (COST_SELL + COST_BUY), 6),
            'random_seed':           None,
            'test_suite_status':     args.test_status,
            'dry_run':               args.dry_run,
            'generated_at':          datetime.now().isoformat(),
        }
    finally:
        conn.close()

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False, width=100),
        encoding='utf-8')
    log.info('manifest 생성: %s (%d종목 편입, 통과 풀 %d, expected_turnover=%.2f)',
             out_path, len(portfolio), len(candidates), turnover)
    if args.dry_run:
        log.info('dry-run — 스키마 검증용. 실제 #24 동결은 8월 신호일에 --dry-run 없이 실행.')


if __name__ == '__main__':
    main()
