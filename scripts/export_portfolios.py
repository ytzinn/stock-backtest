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


def _layers(gate_passed: list[str], univ_result: dict, pipeline) -> dict[str, list[str]]:
    """L0~L3 — 필터를 순서대로 빼면서 만든다 (SPEC_15 §2-1).

    `stats[...]['rejected']` 가 필터별 전수 탈락 목록이므로 차집합으로 계층이 복원된다.
    필터 순서는 `build_ablation_pipeline` 이 조립한 그대로 따른다 — 이름을 하드코딩하지 않는다.
    """
    out: dict[str, list[str]] = {'L0': list(gate_passed)}
    cur = list(gate_passed)
    for i, f in enumerate(pipeline.filters, start=1):
        key = getattr(f, 'stats_key', f.__class__.__name__)
        rejected = set(univ_result['stats'][key]['rejected'])
        cur = [t for t in cur if t not in rejected]
        out[f'L{i}'] = list(cur)
    if out.get(f'L{len(pipeline.filters)}') != list(univ_result['universe']):
        raise RuntimeError('계층 복원이 build_universe 결과와 다르다 — 차집합 전제가 깨졌다')
    return out


def _panel_rows(conn, rp, next_date, layers: dict, candidates: list[dict],
                pit_series: dict, mom_ctx, tag: str) -> list[dict]:
    """한 구간의 패널 행. **원자료만** 담는다 — 표준화·winsorize 는 S-4 의 일이다 (§3-4)."""
    from backtest.data_access import get_avg_turnover, get_close_price, get_market_cap
    from backtest.engine import DELISTING_HAIRCUT, _last_known_price
    from backtest.data_access import is_delisted_at

    deepest = max(k for k in layers if k.startswith('L'))
    ranked  = {c['ticker']: c for c in candidates}
    # 종목마다 set() 을 다시 만들면 O(n²) 이다 — 한 번만 만든다
    layer_sets = {lk: set(v) for lk, v in layers.items()}
    members    = layer_sets[deepest]

    rows = []
    for ticker in layers['L0']:
        pit0   = pit_series.get(ticker, [{}])[0]
        equity = pit0.get('자본총계')
        mktcap = get_market_cap(conn, ticker, rp.date)
        price  = get_close_price(conn, ticker, rp.date)

        # L3ᴿ 4사유 (§2-1). `pbr>0` 은 앞 둘이 양수면 자동 성립이라 사문이지만,
        # 사문임을 산출물로 보이기 위해 그대로 센다.
        reasons = []
        if not equity or equity <= 0:            reasons.append('equity_nonpositive')
        if not mktcap or mktcap <= 0:            reasons.append('mktcap_nonpositive')
        if price is None:                        reasons.append('price_missing')
        if equity and mktcap and equity > 0 and mktcap > 0 and (mktcap / equity) <= 0:
            reasons.append('pbr_nonpositive')

        # 종속변수 — engine._period_stock_data 와 같은 정의. D-1: 상폐를 제외하지 않는다.
        fwd_ret, delisted, exit_ = None, False, None
        if price is not None and price > 0:
            delisted = is_delisted_at(conn, ticker, next_date)
            if delisted:
                last  = _last_known_price(conn, ticker, next_date)
                exit_ = last * DELISTING_HAIRCUT if last else None
            else:
                exit_ = get_close_price(conn, ticker, next_date)
            if exit_ is not None:
                fwd_ret = exit_ / price - 1

        in_l3 = ticker in members
        rows.append({
            'tag': tag, 'rebalance_date': rp.date.isoformat(), 'next_date': next_date.isoformat(),
            'ticker': ticker,
            **{lk: (ticker in v) for lk, v in layer_sets.items()},
            'in_L3R': ticker in ranked,
            'l3r_exclusion': ','.join(reasons) if (in_l3 and reasons) else '',
            'equity': equity, 'market_cap': mktcap, 'price_start': price,
            'price_end': exit_, 'delisted': delisted, 'fwd_ret': fwd_ret,
            'inv_pbr': (equity / mktcap) if (equity and mktcap and mktcap > 0) else None,
            'avg_turnover': get_avg_turnover(conn, ticker, rp.date) if in_l3 else None,
            'mom_126': mom_ctx.get(ticker) if in_l3 else None,
        })

    # **내장 양성 대조** — 4사유로 유도한 L3ᴿ 이 score_and_rank 의 통과분과 정확히 같아야 한다.
    # 어긋나면 사유 유도가 프로덕션과 다른 것이다 (재구현 위험이 여기서 잡힌다).
    derived = {r['ticker'] for r in rows if r[deepest] and not r['l3r_exclusion']}
    if derived != set(ranked):
        raise RuntimeError(
            f'{rp.date} L3R 유도 불일치 — 유도만 {sorted(derived - set(ranked))[:5]} / '
            f'랭킹만 {sorted(set(ranked) - derived)[:5]}')
    return rows


def extract_portfolio_periods(
    tag:              str,
    config:           dict,
    rebalance_points: list[RebalancePoint],
    date_filter:      bool = True,
    n_stocks:         int | None = None,
    panel:            list | None = None,
) -> list[dict]:
    """`n_stocks`가 None이면 `build_ablation_pipeline`의 기본값(20)을 쓴다.

    tape 은 지표(`{tag}.json`)와 **같은 n 으로** 뽑아야 한다. 어긋나면 소비처가
    조용히 다른 전략을 분석한다 (2026-08-12: n=13 운영인데 n=20 tape 만 존재해
    freeze_rebalance 의 turnover 가 종목 수 전이분을 삼켰다).
    """
    kw = {} if n_stocks is None else {'n_stocks': n_stocks}
    pipeline = build_ablation_pipeline(tag, config, seed=None, **kw)
    conn = get_connection()
    if panel is not None:
        # SPEC_15 S-1 은 섀도우 전용이다 (DOTENV-CWD-SILENT-5433).
        # **패널 모드에서만** 건다 — 이 함수는 운영 tape 생성에도 쓰이는 공용 경로라
        # 무조건 걸면 운영 사용이 깨진다.
        from scripts.xsec._guard import assert_shadow_db
        assert_shadow_db(conn)
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

        # SPEC_15 S-1 — **상위 n 절단 전** 전 종목 방출. `panel is None` 이면 아무 일도
        # 일어나지 않으므로 기존 tape 은 비트 단위로 그대로다 (회귀 검사가 이를 강제한다).
        if panel is not None:
            from backtest.filters.momentum_criteria import AbsReturnCriterion
            crit = AbsReturnCriterion(formation_days=126, skip_days=21)   # §3-5 Family A 재사용
            layers = _layers(gate_passed, univ_result, pipeline)
            deepest = max(k for k in layers if k.startswith('L'))
            ctx = crit.prepare(layers[deepest], rebal_date, conn)         # 배치 조회 (1회)
            # CriterionResult.score 가 formation_return (§3-5 mom_126). `.value` 아님.
            mom = {t: crit.evaluate(t, ctx).score for t in layers[deepest]}
            panel.extend(_panel_rows(conn, rp, next_date, layers, candidates,
                                     pit_series, mom, tag))

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


def _write_panel(panel: list[dict], key: str) -> None:
    """계층별 parquet + L3ᴿ 제외율 리포트 (SPEC_15 §3-5).

    **전 구간으로 만들고 슬라이싱은 소비처에 맡긴다** — 여기서 T=19 로 잘라 저장하면
    tape 이 존재하는 나머지 구간에서 L-1a 대조가 불가능해진다 (직전 세션이 tape 을
    전 구간으로 만든 것과 같은 이유).
    """
    import subprocess

    import pandas as pd

    out_dir = Path('experiments/panel')
    out_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(panel)
    layer_cols = sorted([c for c in df.columns if c.startswith('L') and c[1:].isdigit()])

    for lc in layer_cols:
        df[df[lc]].to_parquet(out_dir / f'panel_{key}_{lc}.parquet', index=False)
    df[df['in_L3R']].to_parquet(out_dir / f'panel_{key}_L3R.parquet', index=False)

    deepest = layer_cols[-1]
    l3 = df[df[deepest]]
    reasons: dict = {}
    for _, r in l3[l3['l3r_exclusion'] != ''].iterrows():
        for why in r['l3r_exclusion'].split(','):
            reasons.setdefault(why, {}).setdefault(r['rebalance_date'], 0)
            reasons[why][r['rebalance_date']] += 1

    sha = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True,
                         text=True).stdout.strip()
    report = {
        'key': key,
        'source_commit': sha,                    # 출처 체인 (배포 후 로컬 HEAD 와 대조)
        'rows_total': int(len(df)),
        'periods': sorted(df['rebalance_date'].unique().tolist()),
        'layer_rows': {lc: int(df[lc].sum()) for lc in layer_cols},
        'L3R_rows': int(df['in_L3R'].sum()),
        'per_period_N': {lc: df[df[lc]].groupby('rebalance_date').size().to_dict()
                         for lc in layer_cols + []},
        'L3R_per_period_N': df[df['in_L3R']].groupby('rebalance_date').size().to_dict(),
        'exclusion_by_reason': {k: {'total': int(sum(v.values())), 'per_period': v}
                                for k, v in reasons.items()},
        'delisted_rows_kept': int(df['delisted'].sum()),          # D-1 준수 증거
        'delisted_in_L3R': int(df[df['in_L3R']]['delisted'].sum()),
        'fwd_ret_null_rows': int(df['fwd_ret'].isna().sum()),
        'note': '원자료만. 표준화·winsorize 는 S-4 (SPEC_15 §3-4).',
    }
    (out_dir / f'exclusion_report_{key}.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    log.info(f'  → panel {len(df)}행, L3R {report["L3R_rows"]}행, '
             f'상폐 보존 {report["delisted_rows_kept"]}행 → {out_dir}')


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
    parser.add_argument('--panel', action='store_true',
                        help='SPEC_15 S-1: 상위 n 절단 **전** 전 종목 패널을 함께 방출한다. '
                             '섀도우 전용(assert_shadow_db). 미지정 시 기존 동작과 비트 동일.')
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
        panel: list | None = [] if args.panel else None
        periods = extract_portfolio_periods(
            tag, config, rebalance_points, date_filter=(args.calendar == 'SEMIANNUAL'),
            n_stocks=args.n_stocks, panel=panel,
        )
        out = OUT_DIR / f'{tag}{n_sfx}{suffix}_holdings.json'
        out.write_text(json.dumps(periods, ensure_ascii=False, indent=2), encoding='utf-8')
        log.info(f'  → {out}')
        if panel is not None:
            _write_panel(panel, f'{tag}{n_sfx}{suffix}')

    log.info('전체 완료')


if __name__ == '__main__':
    main()
