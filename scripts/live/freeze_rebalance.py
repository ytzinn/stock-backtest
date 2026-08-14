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
from backtest.engine import DELISTING_HAIRCUT, _calc_turnover
from backtest.portfolio import build_portfolio
from ingest.connection import get_connection


def _report_type(d: date) -> str:
    """8월 리밸런싱 → H1 반기보고서, 나머지 → FY 연간보고서. #24 라이브 포워드는
    반기 기준 그대로 실행(SPEC_13 불변식 6) — engine.py의 RebalancePoint 전환과
    무관하게 로컬로 유지."""
    return 'H1' if d.month == 8 else 'FY'

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

# `[교체 2026-08-10, 사용자 결정 — SPEC_14 §14-4]` F_pbr_no_r3r4(MA 20/60) → F_pbr_ma200.
# **SPEC_14 §8-4.3·§9 는 이 교체를 금지하고 있었다. 사용자가 그 금지를 명시적으로
# 무효화하고 내린 결정이며, 반대 근거는 §14-4 에 그대로 보존돼 있다.**
#   찬성: 20/60 은 캘린더축(4위→12위)·시간축(2위→13위) 양쪽에서 무너졌고 인접 조합도
#         동반 추락했다. MA200 은 두 축 모두 안정(1→2위, 5→5위)이고 튜닝 파라미터가
#         4개→1개로 줄어 과적합 표면 자체가 작다. SPEC_12 §6-1 사전등록 primary 라
#         그리드서치 산물도 아니다.
#   반대: 일별 net MDD 가 −54.61% → −62.61% (8.00%p 악화). SPEC_10 G5(> −45%)는
#         이미 FAIL 인데 더 크게 위반한다. 낙폭 대책은 별도 과제로 남는다.
# 타이밍: #24 라이브 포워드가 아직 미시작(experiments/live 에 dryrun 뿐)이라
#         포워드 전 구간이 MA200 의 진짜 OOS 관측이 된다.
DEFAULT_TAG = 'F_pbr_ma200'
LIVE_DIR    = Path('experiments/live')

# `[교체 2026-08-11, 사용자 결정 — docs/검토/f_pbr_ma200_median_split.md §5]` 20 → 13.
# **§14-4 와 같은 성격의 결정이다 — 사전등록이 허용해서가 아니라, 사용자가 반대 근거를
# 듣고 내린 판단이다. 반대 근거는 위 문서 §5 에 그대로 보존돼 있다.**
#   찬성: 랭킹 1-13 위가 14-20 위보다 반기당 +6.17%p (15/20 구간, raw p=0.026),
#         앞 10구간(+4.54%, 8/10)·뒤 10구간(+7.79%, 7/10) 양쪽에서 부호 유지.
#         일별 net CAGR 14.45% → 18.76%, 일별 net MDD −62.61% → −57.99% (악화 아님).
#   반대: 분할점 13 을 곡선에서 읽어 골랐다 — 19개 분할점 탐색 보정 후 p=0.269.
#         자리별 초과수익이 메커니즘과 어긋난다(18위 +15.44% 로 2등인데 잘리고,
#         2위 −8.93% 로 최악급인데 남는다). 동일 2016~2026 표본이라 독립 OOS 아님.
#   기각된 논거: "곡선이 spike 가 아니라 plateau 이므로 과적합이 아니다" — 이 축에서는
#         무효다. C_{n+1}−C_n = (r_{n+1}−C_n)/(n+1) 로 이동폭이 강제 감쇠해 n 두 자리
#         구간에서는 spike 자체가 불가능하다. 랭킹 셔플 귀무에서도 최고점이 n>=10 이면
#         spike 비율이 3.8% 뿐이고, 관측 고원 폭 3 은 귀무 중앙값 그대로(p=0.510).
#         고원 검정은 이웃이 독립일 때만 정보를 준다(§14-5 의 MA 이웃과 달리 여기선 중첩).
# 타이밍: #24 라이브 포워드가 아직 미시작(experiments/live 에 dryrun 뿐)이라
#         포워드 전 구간이 n=13 의 진짜 OOS 관측이 된다. §14-4 와 동일한 논거.
# 미해결: SPEC_10 G5(> −45%)는 어느 n 에서도 통과하지 못한다. 구간간 표준편차가
#         n=1 18.78% → n=20 21.06% 로 줄지 않아(전 종목이 같은 저PBR 팩터에 물림)
#         낙폭은 종목 수 축으로 풀리지 않는다 — 별도 과제. 이 수치의 산출물은
#         experiments/analysis/n_stocks_curve.json (scripts/analysis/n_stocks_curve.py).
#         이전에는 이 주석과 검토 문서 산문에만 있어 재현이 불가능했다 (2026-08-14).
N_STOCKS    = 13


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


def _artifact_key(tag: str) -> str:
    """산출물 조회 키. 종목 수가 이름에 들어간다.

    태그 이름만으로는 어느 n 의 산출물인지 알 수 없다 — 그게 2026-08-12 에 n=20 tape
    을 n=13 운영에 쓰게 만든 함정이다. 명명 규약은 `run_ablation --n-stocks` 와 같다.
    """
    return f'{tag}_n{N_STOCKS}'


def _previous_holdings(tag: str, signal_date: date) -> tuple[str | None, dict[str, float]]:
    """직전 리밸런싱 보유 (holdings tape에서 signal_date 이전 최신 구간).

    tape 의 종목 수 상한이 N_STOCKS 와 다르면 **예외를 던진다.** 조용히 다른 n 의
    tape 을 읽으면 turnover·비용이 종목 수 전이분까지 삼켜 조용히 틀린다
    (2026-08-12 발견: n 20→13 교체 후 n=13 tape 이 없어 n=20 tape 으로 폴백,
    manifest 의 expected_turnover 가 0.9231 이어야 할 것이 0.9500 으로 기록됨).
    `_config_hash` 는 n_stocks 를 해시에 넣지만 **산출물 경로는 태그 이름만** 쓰므로
    이 어긋남을 잡지 못한다 — 여기서 잡는다.
    """
    # 산출물 명명 규약: 기본 n 은 접미사 없음, 그 외는 `_n{K}`. N_STOCKS 를 바꾸면
    # 태그 이름은 그대로인데 가리켜야 할 tape 이 달라진다 — 여기서 해소한다.
    abl_dir = Path('experiments/ablation')
    path = abl_dir / f'{_artifact_key(tag)}_holdings.json'
    if not path.exists():
        path = abl_dir / f'{tag}_holdings.json'
    if not path.exists():
        return None, {}
    tape = json.loads(path.read_text(encoding='utf-8'))

    # 구간별 n_portfolio 는 유니버스가 작으면 상한 미만일 수 있다. 상한 자체는
    # 최댓값으로만 확인 가능하므로 max 로 대조한다. 위 폴백이 조용하지 않은 이유가
    # 이 검사다 — 엉뚱한 n 의 tape 을 집으면 여기서 멈춘다.
    tape_cap = max((p['n_portfolio'] for p in tape), default=0)
    if tape_cap != N_STOCKS:
        raise SystemExit(
            f'holdings tape 종목 수 불일치: {path} 의 상한은 {tape_cap} 인데 '
            f'N_STOCKS={N_STOCKS} 이다. 그 n 으로 tape 을 생성하라 '
            f'(run_ablation --tags {tag} --n-stocks {N_STOCKS} → export_portfolios). '
            f'다른 n 의 tape 을 대신 쓰면 turnover 에 종목 수 전이 비용이 섞인다.')

    periods = [p for p in tape
               if p['rebalance_date'] < signal_date.isoformat() and p['n_portfolio'] > 0]
    if not periods:
        return None, {}
    last = max(periods, key=lambda p: p['rebalance_date'])
    tickers = [h['ticker'] for h in last['holdings']]
    return last['rebalance_date'], {t: 1.0 / len(tickers) for t in tickers}


def _gate_status(tag: str) -> str:
    """SPEC_10 게이트 현황을 **산출물에서** 읽는다. 문자열에 박지 않는다.

    2026-08-12 이전에는 'G1·G2 PASS, G5 FAIL' 이 이 파일에 하드코딩돼 있었다.
    그 값은 옛 채택안 F_pbr_no_r3r4(n=20) 판정이라, 태그를 MA200 으로 바꾼 뒤에는
    **다른 전략의 성적표가 라이브 산출물에 복사되는** 상태였다.
    """
    path = Path('experiments/robustness') / f'gate_results_{_artifact_key(tag)}.json'
    if not path.exists():
        return f'SPEC_10 게이트 미산출 ({path.name} 없음)'
    r = json.loads(path.read_text(encoding='utf-8'))
    hg = r.get('hard_gates', {})
    parts = []
    for g in ('G1', 'G2', 'G5'):
        v = hg.get(g, {}).get('pass')
        parts.append(f'{g} {"PASS" if v else "FAIL" if v is not None else "미산출"}')
    return f'SPEC_10 관문: {", ".join(parts)} (산출 {r.get("generated_at", "?")})'


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
            'strategy_version':      f'{tag} v1.0 n={N_STOCKS} ({_gate_status(tag)})',
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
