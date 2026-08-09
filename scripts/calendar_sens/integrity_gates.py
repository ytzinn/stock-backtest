"""
SPEC_14 §6-4 무결성 게이트 — **성과 산출 전 필수**. 불통과 시 수치 미발행·즉시 중단.

게이트 5종:
  G-CAL-1 스냅샷 동등성   현행안 반기 gross/net **비트 재현** (§0-4)
  G-CAL-2 배관 양성 대조군 `F_pbr_ma_double_adapter` == `F_pbr_no_r3r4` (반기)
  G-CAL-3 신규 태그 검증   `F_pbr_no_r3r4r5` 가 현행안과 **stability_rules 만** 다른지
                          (config 대조) + 반기 실행 산출물 존재
  G-CAL-4 안 C 스케줄     `RebalancePoint.fiscal_year` 정확 조회 + `late_or_missing_
                          current_report` 비율 기록 (DB 접속 — `--no-db` 로 생략 가능)
  G-CAL-5 관측일 동일     판정에 쓰는 전 태그 × 전 캘린더가 공통 기간에서 **같은
                          관측일 인덱스**를 갖는지 (개수만이 아니라 날짜 자체)

실행 (동결 스냅샷 워크트리에서):
    venv/bin/python -m scripts.calendar_sens.integrity_gates
    venv/bin/python -m scripts.calendar_sens.integrity_gates --no-db   # G-CAL-4 생략

출력: experiments/calendar_sens/integrity_gates.json
`stage_b.py` 는 이 파일의 `all_pass=true` 없이는 실행을 거부한다.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime

import pandas as pd

from backtest.ablation import ABLATION_CONFIGS
from backtest.configs.schedule import REBALANCE_SCHEDULE_C
from backtest.metrics import slice_common_period
from scripts.calendar_sens.calsens_lib import (
    ABL_DIR,
    CALENDARS,
    COMMON_E,
    COMMON_S,
    INCUMBENT_ENGINE_GROSS_CAGR,
    INCUMBENT_FULL_GROSS_CAGR,
    INCUMBENT_FULL_NET_CAGR,
    INCUMBENT_TAG,
    NAV_DIR,
    OUT_DIR,
    PLUMBING_TAG,
    REQUIRED_TAGS,
    calendar_tag,
    load_nav,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

NEW_TAG = 'F_pbr_no_r3r4r5'


def _ablation_gross(tag: str) -> float:
    """engine metrics['cagr'] — gross CAGR (전체기간·완결 구간 기준)."""
    path = ABL_DIR / f'{tag}.json'
    if not path.exists():
        raise FileNotFoundError(f'{path} 없음 — run_ablation 먼저 실행할 것')
    return float(json.loads(path.read_text(encoding='utf-8'))['cagr'])


def _daily_nav_metric(tag: str, key: str, calendar: str = 'SEMIANNUAL') -> float:
    """일별 NAV 승법 CAGR (SPEC_13 §9-1 metric SSOT). key = net_cagr | gross_cagr."""
    from backtest.configs.schedule import tag_suffix
    path = NAV_DIR / f'summary{tag_suffix(calendar)}.json'
    if not path.exists():
        raise FileNotFoundError(f'{path} 없음 — run_daily_nav 먼저 실행할 것')
    tags = json.loads(path.read_text(encoding='utf-8')).get('tags', {})
    name = calendar_tag(tag, calendar)
    if name not in tags:
        raise KeyError(f'{path}: {name} 항목 없음 — 해당 태그 daily NAV 미실행')
    return float(tags[name][key])


def _daily_nav_net(tag: str, calendar: str = 'SEMIANNUAL') -> float:
    return _daily_nav_metric(tag, 'net_cagr', calendar)


def _daily_nav_gross(tag: str, calendar: str = 'SEMIANNUAL') -> float:
    return _daily_nav_metric(tag, 'gross_cagr', calendar)


# ── G-CAL-1 ──────────────────────────────────────────────────────────────────

def gate_snapshot_equivalence() -> dict:
    """현행안 반기 gross·net 비트 재현 (§0-4). 부동소수 tolerance 없음.

    **gross 는 두 가지가 있고 서로 다른 값이다** — 둘 다 대조한다:
      - 엔진 구간복리  `compute_cagr(Π(1+r_i))`     = 0.15819563103474055
      - 일별 NAV 승법  `compute_nav_cagr(NAV_E/1.0)` = 0.15819563103474077
    수학적으로 같지만 누적 순서가 달라 끝 2자리가 갈린다(상대차 1.4e-15). SPEC_14 §0-4
    가 인용한 값은 **일별 NAV 쪽**이고, 2026.07.30 재발행 §3 이 인용한 값은 **엔진 쪽**이다.
    한쪽만 보고 다른 쪽 출처와 비교하면 멀쩡한 재현이 FAIL 로 뜬다 (실제로 그랬다).
    """
    nav_gross    = _daily_nav_gross(INCUMBENT_TAG)
    engine_gross = _ablation_gross(INCUMBENT_TAG)
    net          = _daily_nav_net(INCUMBENT_TAG)

    ok_nav_g = nav_gross    == INCUMBENT_FULL_GROSS_CAGR
    ok_eng_g = engine_gross == INCUMBENT_ENGINE_GROSS_CAGR
    ok_n     = net          == INCUMBENT_FULL_NET_CAGR
    return {
        'gate': 'G-CAL-1 스냅샷 동등성',
        'pass': bool(ok_nav_g and ok_eng_g and ok_n),
        'daily_nav_gross_expected': INCUMBENT_FULL_GROSS_CAGR,
        'daily_nav_gross_actual':   nav_gross,
        'daily_nav_gross_bit_identical': bool(ok_nav_g),
        'engine_gross_expected': INCUMBENT_ENGINE_GROSS_CAGR,
        'engine_gross_actual':   engine_gross,
        'engine_gross_bit_identical': bool(ok_eng_g),
        'net_expected': INCUMBENT_FULL_NET_CAGR, 'net_actual': net,
        'net_bit_identical': bool(ok_n),
        'note': '전체기간 20구간 기준값 — 공통 기간 값(§3)과 혼동 금지',
    }


# ── G-CAL-2 ──────────────────────────────────────────────────────────────────

def gate_plumbing_control() -> dict:
    """SPEC_12 §4-5 배관 양성 대조군 — 신규 모멘텀 배관이 레거시와 완전 동일해야 한다."""
    a, b = _ablation_gross(PLUMBING_TAG), _ablation_gross(INCUMBENT_TAG)
    return {
        'gate': 'G-CAL-2 배관 양성 대조군',
        'pass': bool(a == b),
        'plumbing_tag': PLUMBING_TAG, 'plumbing_gross': a,
        'incumbent_gross': b, 'bit_identical': bool(a == b),
    }


# ── G-CAL-3 ──────────────────────────────────────────────────────────────────

def gate_new_tag() -> dict:
    """신규 태그가 현행안과 **stability_rules 한 축만** 다른지 config 로 검증."""
    inc = dict(ABLATION_CONFIGS[INCUMBENT_TAG])
    new = dict(ABLATION_CONFIGS.get(NEW_TAG, {}))
    if not new:
        return {'gate': 'G-CAL-3 신규 태그 검증', 'pass': False,
                'reason': f'{NEW_TAG} 가 ABLATION_CONFIGS 에 없다'}

    diff_keys = sorted(
        k for k in set(inc) | set(new) if inc.get(k) != new.get(k)
    )
    rules_ok = new.get('stability_rules') == {'R1', 'R2', 'R6'}
    axis_ok  = diff_keys == ['stability_rules']

    artifact_ok, gross = True, None
    try:
        gross = _ablation_gross(NEW_TAG)
    except FileNotFoundError:
        artifact_ok = False

    return {
        'gate': 'G-CAL-3 신규 태그 검증',
        'pass': bool(rules_ok and axis_ok and artifact_ok),
        'tag': NEW_TAG,
        'stability_rules': sorted(new.get('stability_rules', [])),
        'expected_rules': ['R1', 'R2', 'R6'],
        'config_diff_vs_incumbent': diff_keys,
        'single_axis_config': bool(axis_ok),
        'semiannual_artifact_exists': artifact_ok,
        'semiannual_gross_cagr': gross,
        'note': '반기 캘린더로 먼저 실행해 구성 정합을 확인한다 (§6-4)',
    }


# ── G-CAL-4 ──────────────────────────────────────────────────────────────────

def gate_schedule_c(with_db: bool = True) -> dict:
    """안 C 스케줄 정합 + `late_or_missing_current_report` 비율.

    `late_or_missing` 정의: 같은 앵커에서 `fiscal_year` 를 **지정하지 않은**(가용 최신
    연도) 게이트 통과 집합 대비, **정확 지정**했을 때 빠지는 종목 비율. `data_access.
    load_gate_passed_tickers` docstring 이 "호출부가 신청 유니버스와의 차집합으로
    계산한다"고 명시한 그 집계다 (SPEC_13 §0-A DEBT-3).
    """
    rows = []
    schedule_ok = True
    for rp in REBALANCE_SCHEDULE_C:
        expect_end = {
            'Q1': (rp.fiscal_year, 3, 31), 'H1': (rp.fiscal_year, 6, 30),
            'Q3': (rp.fiscal_year, 9, 30), 'FY': (rp.fiscal_year, 12, 31),
        }[rp.report_type]
        ok = (rp.calendar_id == 'C'
              and rp.report_type in ('Q1', 'Q3')
              and (rp.nominal_period_end.year, rp.nominal_period_end.month,
                   rp.nominal_period_end.day) == expect_end)
        schedule_ok &= ok
        rows.append({'date': rp.date.isoformat(), 'report_type': rp.report_type,
                     'fiscal_year': rp.fiscal_year,
                     'nominal_period_end': rp.nominal_period_end.isoformat(),
                     'consistent': bool(ok)})

    result = {
        'gate': 'G-CAL-4 안 C 스케줄',
        'n_anchors': len(REBALANCE_SCHEDULE_C),
        'schedule_consistent': bool(schedule_ok),
        'anchors': rows,
    }

    if not with_db:
        result.update({'pass': bool(schedule_ok), 'late_or_missing': 'SKIPPED (--no-db)'})
        return result

    from backtest.data_access import load_gate_passed_tickers
    from ingest.connection import get_connection

    conn = get_connection()
    lom = []
    try:
        for rp in REBALANCE_SCHEDULE_C:
            latest = set(load_gate_passed_tickers(conn, rp.date, report_type=rp.report_type))
            exact  = set(load_gate_passed_tickers(conn, rp.date, report_type=rp.report_type,
                                                  fiscal_year=rp.fiscal_year))
            missing = latest - exact
            lom.append({
                'date': rp.date.isoformat(), 'report_type': rp.report_type,
                'fiscal_year': rp.fiscal_year,
                'n_latest_year': len(latest), 'n_exact_year': len(exact),
                'n_late_or_missing': len(missing),
                'late_or_missing_ratio': (len(missing) / len(latest)) if latest else None,
            })
            if exact - latest:
                # 정확 조회 결과가 최신 조회의 부분집합이 아니면 로더 계약 위반
                schedule_ok = False
                lom[-1]['contract_violation'] = sorted(exact - latest)[:5]
    finally:
        conn.close()

    ratios = [r['late_or_missing_ratio'] for r in lom if r['late_or_missing_ratio'] is not None]
    result.update({
        'pass': bool(schedule_ok),
        'late_or_missing': lom,
        'late_or_missing_ratio_mean': (sum(ratios) / len(ratios)) if ratios else None,
        'late_or_missing_ratio_max':  max(ratios) if ratios else None,
        'note': '분기 전략에 "신속 공시 기업 선호" 효과가 섞인다 — 보고서에 명시 (SPEC_13 §0-A)',
    })
    return result


# ── G-CAL-5 ──────────────────────────────────────────────────────────────────

def gate_observation_dates() -> dict:
    """전 태그 × 전 캘린더의 공통 기간 관측일이 **날짜까지** 동일한지.

    개수만 세면 안 된다 — 같은 2,424개라도 날짜가 어긋나면 §10 의 "동일 달력일
    pairing" 이 깨져 δ(j) 가 다른 날을 빼는 값이 된다.
    """
    ref_index: pd.DatetimeIndex | None = None
    ref_name = None
    rows, all_ok, missing = [], True, []

    for calendar in CALENDARS:
        for tag in REQUIRED_TAGS:
            name = f'{calendar_tag(tag, calendar)}'
            try:
                nav = load_nav(tag, calendar)['nav_net']
            except FileNotFoundError as e:
                missing.append(name)
                all_ok = False
                rows.append({'series': name, 'ok': False, 'reason': str(e).split('—')[0].strip()})
                continue
            try:
                sliced = slice_common_period(nav, COMMON_S, COMMON_E)
            except ValueError as e:
                # 공통 시작일이 관측일에 없다 = 그 전략이 S 시점에 아직 존재하지 않는다.
                # 예외로 게이트 전체를 날리면 어느 시리즈가 문제인지 안 보인다 — 행으로 남긴다.
                all_ok = False
                rows.append({'series': name, 'ok': False,
                             'nav_first': nav.index[0].date().isoformat(),
                             'nav_last':  nav.index[-1].date().isoformat(),
                             'reason': str(e).split('—')[0].strip()})
                continue
            if ref_index is None:
                ref_index, ref_name = sliced.index, name
                ok = True
            else:
                ok = sliced.index.equals(ref_index)
            all_ok &= ok
            rows.append({'series': name, 'n_obs': len(sliced),
                         'first': sliced.index[0].date().isoformat(),
                         'last': sliced.index[-1].date().isoformat(), 'ok': bool(ok)})

    return {
        'gate': 'G-CAL-5 관측일 동일',
        'pass': bool(all_ok),
        'reference_series': ref_name,
        'n_obs_reference': len(ref_index) if ref_index is not None else None,
        'missing_series': missing,
        'series': rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description='SPEC_14 §6-4 무결성 게이트')
    ap.add_argument('--no-db', action='store_true',
                    help='G-CAL-4 의 late_or_missing 집계(DB 접속) 생략')
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log.info('=== SPEC_14 §6-4 무결성 게이트 ===')

    gates = []
    for fn in (gate_snapshot_equivalence, gate_plumbing_control, gate_new_tag,
               gate_observation_dates):
        try:
            gates.append(fn())
        except Exception as e:                       # noqa: BLE001 — 게이트는 실패도 기록
            gates.append({'gate': fn.__name__, 'pass': False, 'error': f'{type(e).__name__}: {e}'})
    try:
        gates.append(gate_schedule_c(with_db=not args.no_db))
    except Exception as e:                            # noqa: BLE001
        gates.append({'gate': 'G-CAL-4 안 C 스케줄', 'pass': False,
                      'error': f'{type(e).__name__}: {e}'})

    all_pass = all(g.get('pass') for g in gates)
    out = {
        'spec': 'SPEC_14 §6-4', 'generated_at': datetime.now().isoformat(),
        'db_checks': not args.no_db,
        'all_pass': bool(all_pass), 'gates': gates,
    }
    path = OUT_DIR / 'integrity_gates.json'
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding='utf-8')

    for g in gates:
        log.info('%s %s%s', 'PASS' if g.get('pass') else 'FAIL', g['gate'],
                 f"  ({g['error']})" if 'error' in g else '')
    log.info('저장: %s', path)
    if not all_pass:
        raise SystemExit('무결성 게이트 불통과 — 성과 수치 미발행, 즉시 중단 (§6-4)')


if __name__ == '__main__':
    main()
