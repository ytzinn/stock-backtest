"""
SPEC_14 A단계 — 메커니즘 진단. **투표 규칙 없음, 자체 판정 없음** (§5).

A단계는 B단계 결과의 **원인을 해석하는 재료**다. A-2·A-3·A-4가 하나의 원인(데이터
가용성)에서 동시 발화할 수 있어 같은 현상을 여러 표로 세게 되므로, 서로 다른 축의
증거를 합산하지 않는다.

구현 범위 (`[확정 2026-08-06, 사용자]`):
  A-1  캘린더·유니버스 수준효과(Δ_EW) + 캘린더별 선별층 기여 Active_c  ✔
  A-3  게이트 단계별 탈락 · 앵커별 풀 크기                              ✔
  A-5  구간별 gap 집중도 (달력 반기 고정 구간)                          ✔
  §4   보유기간 구조표 (달력일) + 연간 회전율                            ✔
  A-2  데이터 품질·가용성 (발행사-연도 매칭 정정 분석)                   미구현 — 별도 세션
  A-4  편입·순위 안정성 counterfactual 2×2                              미구현 — 별도 세션
       (엔진·필터에 as_of_price / as_of_fin 이원화가 필요해 백테스트 결과에
        영향을 주는 코드 변경이 된다. CLAUDE.md 규칙상 전체 테스트 재통과 필수)

실행:
    venv/bin/python -m scripts.calendar_sens.stage_a
출력: experiments/calendar_sens/stage_a.json
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import statistics
from datetime import datetime

import numpy as np
import pandas as pd

from backtest.configs.schedule import get_schedule
from backtest.metrics import compute_daily_metrics
from scripts.calendar_sens.calsens_lib import (
    ABL_DIR,
    CALENDARS,
    COMMON_E,
    COMMON_S,
    EW_TAG,
    INCUMBENT_TAG,
    OUT_DIR,
    annualized_log_return,
    cagr_from_g,
    calendar_tag,
    common_period_years,
    load_nav,
    log_returns,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

SEMI, ALT = 'SEMIANNUAL', 'C'


# ── §4 보유기간 구조 (달력일) ────────────────────────────────────────────────

def holding_period_structure() -> dict:
    """`[VERIFY-CAL-001]` 안 C는 "위상만 다른" 캘린더가 아니다 — 보유기간 배열 비교.

    **단위는 달력일**이다 (§10 bootstrap 의 거래일 단위와 혼동 금지).
    """
    out = {}
    for calendar in CALENDARS:
        pts = get_schedule(calendar)
        gaps = [(pts[i + 1].date - pts[i].date).days for i in range(len(pts) - 1)]
        by_month: dict[str, list[int]] = {}
        for i, g in enumerate(gaps):
            by_month.setdefault(f'{pts[i].date.month:02d}월→{pts[i+1].date.month:02d}월',
                                []).append(g)
        out[calendar] = {
            'n_anchors': len(pts), 'n_intervals': len(gaps),
            'mean_days': statistics.mean(gaps), 'max_days': max(gaps), 'min_days': min(gaps),
            'stdev_days': statistics.pstdev(gaps),
            'by_transition': {k: {'n': len(v), 'mean': statistics.mean(v)}
                              for k, v in sorted(by_month.items())},
            'unit': '달력일 (calendar days)',
        }
    out['note'] = (
        'v0.1 §6-1 의 "거래비용·회전율 구조가 동일하고 위상만 다르다"는 철회됐다. '
        '평균·최대 보유기간, 신호 노출 기간, 회전율 발생 시점, 계절·실적발표 구간 '
        '포함 비중, 데이터 노후화가 함께 교락한다 — B단계 결과는 "순수 위상 과적합"이 '
        '아니라 **캘린더 민감성**으로 서술한다 (§4).'
    )
    return out


def annual_turnover() -> dict:
    """캘린더별 연간 회전율 = 평균 구간 turnover × 연간 리밸런싱 횟수 (현행안 기준)."""
    out = {}
    for calendar in CALENDARS:
        path = ABL_DIR / f'{calendar_tag(INCUMBENT_TAG, calendar)}_periods.csv'
        if not path.exists():
            out[calendar] = {'error': f'{path} 없음'}
            continue
        with path.open(encoding='utf-8') as f:
            rows = [r for r in csv.DictReader(f) if r.get('turnover')]
        vals = [float(r['turnover']) for r in rows]
        # 첫 구간 turnover=1.0 은 전액 신규 매수 관례 — 정상 상태 회전율에서 분리
        steady = vals[1:] if vals else []
        out[calendar] = {
            'n_periods': len(vals), 'mean_turnover_all': statistics.mean(vals) if vals else None,
            'mean_turnover_ex_first': statistics.mean(steady) if steady else None,
            'rebalances_per_year': 2,
            'annual_turnover_ex_first': (statistics.mean(steady) * 2) if steady else None,
        }
    return out


# ── A-1 캘린더·유니버스 수준효과 + 선별층 기여 ───────────────────────────────

def _g(nav: pd.Series, years: float) -> float:
    return annualized_log_return(log_returns(nav, COMMON_S, COMMON_E).to_numpy(), years)


def a1_level_effect_and_active() -> dict:
    """(1) Q1 판정값 `Δ_EW` (2) 캘린더별 선별층 기여 `Active_c` — **두 지표를 분리**.

    `[v0.3 정정]` v0.2 의 "EW active return" 은 정의 오류였다 — active return 은
    전략 − EW 이므로 EW 자체에는 active return 이 없다.

    `Δ_EW` 를 "순수 캘린더 효과"라 부르지 않는다. EW 도 그 시점의 유니버스·가용
    종목·거래가능성에 영향받으므로 정확한 명칭은 **"룰 선별을 제거한 캘린더·
    유니버스 수준효과"** 다 (§5 A-1).
    """
    years = common_period_years()
    out: dict = {'years': years, 'basis': 'net 1차, gross 병기 — 연율 로그수익률'}

    g_ew, g_strat, active = {}, {}, {}
    for calendar in CALENDARS:
        ew   = load_nav(EW_TAG, calendar)
        strat = load_nav(INCUMBENT_TAG, calendar)
        g_ew[calendar]    = {k: _g(ew[f'nav_{k}'], years)    for k in ('net', 'gross')}
        g_strat[calendar] = {k: _g(strat[f'nav_{k}'], years) for k in ('net', 'gross')}

        # a_{c,t} = log(1+r_전략) − log(1+r_EW) (일별)
        detail = {}
        for k in ('net', 'gross'):
            a = (log_returns(strat[f'nav_{k}'], COMMON_S, COMMON_E)
                 - log_returns(ew[f'nav_{k}'], COMMON_S, COMMON_E))
            active_nav = np.exp(a.cumsum())
            m = compute_daily_metrics(active_nav)
            detail[k] = {
                'active_g': g_strat[calendar][k] - g_ew[calendar][k],
                'active_cagr_equivalent': cagr_from_g(g_strat[calendar][k] - g_ew[calendar][k]),
                'active_mdd': m['daily_mdd'],
                'active_mdd_peak': m['mdd_peak_date'].isoformat(),
                'active_mdd_trough': m['mdd_trough_date'].isoformat(),
                'active_worst_month': m['worst_month_return'],
            }
        detail['by_period'] = _active_by_period(strat['nav_net'], ew['nav_net'], calendar)
        active[calendar] = detail

    out['delta_ew'] = {
        'net':   g_ew[ALT]['net']   - g_ew[SEMI]['net'],
        'gross': g_ew[ALT]['gross'] - g_ew[SEMI]['gross'],
        'note': 'Q1 판정값 — CI 와 최종 판정은 stage_b.py 가 동일 block index 로 산출한다',
    }
    out['g_ew'] = g_ew
    out['g_strategy'] = g_strat
    out['cagr_equivalent'] = {
        'ew':       {c: {k: cagr_from_g(v) for k, v in g_ew[c].items()} for c in CALENDARS},
        'strategy': {c: {k: cagr_from_g(v) for k, v in g_strat[c].items()} for c in CALENDARS},
        'note': 'CAGR 단순 차이는 복리 경로가 달라 왜곡되므로 이해용 병기일 뿐, 1차는 g (§5 A-1)',
    }
    out['active_by_calendar'] = active
    out['verify_note'] = (
        '현행안 EW 의 **전체기간** 값(6.8812%)을 공통기간 값으로 대용하지 말 것 — '
        '§9-1c 참고값과 §9-7 공통기간 값이 이미 다르다 (§5 A-1 [VERIFY]).'
    )
    return out


def _active_by_period(strat: pd.Series, ew: pd.Series, calendar: str) -> list[dict]:
    """리밸런싱 구간별 active log return 합 (해당 캘린더의 앵커 기준)."""
    a = (log_returns(strat, COMMON_S, COMMON_E) - log_returns(ew, COMMON_S, COMMON_E))
    anchors = [p.date for p in get_schedule(calendar)]
    edges = [d for d in anchors if COMMON_S <= d <= COMMON_E]
    edges = sorted(set([COMMON_S] + edges + [COMMON_E]))
    out = []
    for i in range(len(edges) - 1):
        lo, hi = pd.Timestamp(edges[i]), pd.Timestamp(edges[i + 1])
        seg = a[(a.index > lo) & (a.index <= hi)]
        if len(seg) == 0:
            continue
        out.append({'start': edges[i].isoformat(), 'end': edges[i + 1].isoformat(),
                    'n_days': len(seg), 'active_log_sum': float(seg.sum())})
    return out


# ── A-3 게이트 단계별 탈락 ───────────────────────────────────────────────────

def a3_gate_stages() -> dict:
    """앵커별 풀 크기 + 단계별 탈락 분해 (현행안 기준, 캘린더별).

    `[VERIFY]` 현행안 반기 앵커 풀 크기 미상 — 여기서 산출한다 (§5 A-3).

    한계: `{tag}_periods.csv` 는 필터 단계별 **통과 수**만 기록한다. 모멘텀 통과 후
    PBR 랭킹 단계에서 자본총계·시가총액 결측으로 빠지는 종목은 별도 컬럼이 없어
    `momentum_passed → n_stocks` 안에 top-20 절단과 함께 섞여 있다 — 분리하려면
    `universe_stats.rejected` 원본이 필요하다.
    """
    out = {}
    for calendar in CALENDARS:
        path = ABL_DIR / f'{calendar_tag(INCUMBENT_TAG, calendar)}_periods.csv'
        if not path.exists():
            out[calendar] = {'error': f'{path} 없음 — run_ablation 먼저'}
            continue
        rows = []
        with path.open(encoding='utf-8') as f:
            for r in csv.DictReader(f):
                def _i(k):
                    v = r.get(k)
                    return int(v) if v not in (None, '') else None
                gate, hard = _i('n_gate'), _i('hard_passed')
                stab, mom  = _i('stability_passed'), _i('momentum_passed')
                sel        = _i('n_stocks')
                rows.append({
                    'rebalance_date': r['rebalance_date'],
                    'n_gate': gate, 'hard_passed': hard,
                    'stability_passed': stab, 'momentum_passed': mom, 'n_selected': sel,
                    'drop_hard':      (gate - hard) if None not in (gate, hard) else None,
                    'drop_stability': (hard - stab) if None not in (hard, stab) else None,
                    'drop_momentum':  (stab - mom)  if None not in (stab, mom) else None,
                    'drop_rank_and_top20': (mom - sel) if None not in (mom, sel) else None,
                })
        pools = [r['momentum_passed'] for r in rows if r['momentum_passed'] not in (None, 0)]
        gates = [r['n_gate'] for r in rows if r['n_gate'] not in (None, 0)]
        out[calendar] = {
            'n_anchors': len(rows),
            'n_active_anchors': len(gates),
            'gate_pool_min': min(gates) if gates else None,
            'gate_pool_max': max(gates) if gates else None,
            'final_pool_min': min(pools) if pools else None,
            'final_pool_max': max(pools) if pools else None,
            'per_anchor': rows,
        }
    out['reference'] = {
        'alt_C_known': '안 C 102~927종목 (유효 20앵커)',
        'alt_A_known': '안 A 36~927 (유효 41앵커)',
        'source': 'SPEC_14 §5 A-3 [검증된 사실]',
    }
    out['limitation'] = (
        'PBR 랭킹 단계의 자본총계·시가총액 결측 탈락은 drop_rank_and_top20 안에 '
        'top-20 절단과 함께 섞여 있다 (periods.csv 에 별도 컬럼 없음).'
    )
    return out


# ── A-5 구간별 gap 집중도 (달력 반기 고정 구간) ──────────────────────────────

def a5_gap_concentration() -> dict:
    """`(현행안 − 안 C)` 일별 active log gap 을 **달력 반기**로 집계 (§5 A-5).

    `[v0.3 정정]` 두 캘린더는 리밸런싱일과 보유기간 배열이 다르므로 "각 구간"이
    어느 캘린더 기준인지 불명확했다 → **캘린더와 독립적인 고정 구간**(1/1~6/30,
    7/1~12/31)을 쓴다. **판정 비사용, 배경 정보.**

    구간 수는 §5 A-5 가 예상한 20이 아니라 **21**이다 — 공통 기간 양끝(2016-05-18,
    2026-04-03)이 둘 다 상반기 중간이라 2016H1·2026H1 이 부분 구간으로 잡힌다.
    """
    semi = load_nav(INCUMBENT_TAG, SEMI)['nav_net']
    alt  = load_nav(INCUMBENT_TAG, ALT)['nav_net']
    gap  = log_returns(semi, COMMON_S, COMMON_E) - log_returns(alt, COMMON_S, COMMON_E)

    label = gap.index.map(lambda d: f'{d.year}H{1 if d.month <= 6 else 2}')
    buckets = gap.groupby(label).sum().sort_index()
    vals = buckets.to_numpy(dtype=float)
    order = np.argsort(-np.abs(vals))[:3]
    total_abs = float(np.abs(vals).sum())

    return {
        'definition': '달력 반기 (1/1~6/30, 7/1~12/31) — 캘린더 독립 고정 구간',
        'status': '판정 비사용, 배경 정보 (§5 A-5)',
        'n_buckets': len(vals),
        'by_half': [{'half': k, 'gap_log': float(v)} for k, v in buckets.items()],
        'median': float(np.median(vals)),
        'q1': float(np.percentile(vals, 25)), 'q3': float(np.percentile(vals, 75)),
        'positive_share': float(np.mean(vals > 0)),
        'total_gap_log': float(vals.sum()),
        'top3_halves': [buckets.index[i] for i in order],
        'top3_abs_share_of_total_abs': (float(np.abs(vals[order]).sum() / total_abs)
                                        if total_abs > 0 else None),
        'top3_signed_share_of_total': (float(vals[order].sum() / vals.sum())
                                       if vals.sum() != 0 else None),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description='SPEC_14 A단계 메커니즘 진단')
    ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    result = {
        'spec': 'SPEC_14 A단계',
        'disclaimer': '진단 전용 — 본 실행만으로 채택 후보 없음 (§9). '
                      'A단계는 투표 규칙이 없고 자체 판정을 내리지 않는다 (§5).',
        'generated_at': datetime.now().isoformat(),
        'common_period': {'S': COMMON_S.isoformat(), 'E': COMMON_E.isoformat()},
        'holding_period_structure': holding_period_structure(),
        'annual_turnover': annual_turnover(),
        'A1_level_effect_and_active': a1_level_effect_and_active(),
        'A3_gate_stages': a3_gate_stages(),
        'A5_gap_concentration': a5_gap_concentration(),
        'not_implemented': {
            'A2': '데이터 품질·가용성 (발행사-연도 매칭 정정 분석) — 별도 세션',
            'A4': '편입·순위 안정성 counterfactual 2×2 — 엔진 as_of_price/as_of_fin '
                  '이원화 필요, 별도 세션',
        },
    }
    path = OUT_DIR / 'stage_a.json'
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')

    hp = result['holding_period_structure']
    for c in CALENDARS:
        log.info('[%s] 보유기간 평균 %.1f일 최대 %d일 표준편차 %.1f일 (달력일)',
                 c, hp[c]['mean_days'], hp[c]['max_days'], hp[c]['stdev_days'])
    a1 = result['A1_level_effect_and_active']
    log.info('Δ_EW(net) = %+.4f%%p  (EW 반기 %.4f%% / 안C %.4f%% CAGR 환산)',
             a1['delta_ew']['net'] * 100,
             a1['cagr_equivalent']['ew'][SEMI]['net'] * 100,
             a1['cagr_equivalent']['ew'][ALT]['net'] * 100)
    for c in CALENDARS:
        log.info('[%s] Active(net) g=%+.4f%%p  active MDD=%.2f%%', c,
                 a1['active_by_calendar'][c]['net']['active_g'] * 100,
                 a1['active_by_calendar'][c]['net']['active_mdd'] * 100)
    a5 = result['A5_gap_concentration']
    log.info('A-5 달력 반기 %d구간: 중앙값 %+.4f 양수비율 %.1f%% 상위3 절대비중 %.1f%%',
             a5['n_buckets'], a5['median'], a5['positive_share'] * 100,
             (a5['top3_abs_share_of_total_abs'] or 0) * 100)
    log.info('저장: %s', path)


if __name__ == '__main__':
    main()
