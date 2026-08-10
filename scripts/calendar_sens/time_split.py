"""
SPEC_14 §14-3 — 시간 분할 검정: 모멘텀 설정이 **시간 축**으로도 과적합인가.

**사전등록. 이 파일은 수치 산출 전에 커밋된다 — 분할점·문턱 이후 수정 금지.**
**진단 전용.** 어떤 태그도 채택 후보가 되지 않는다 (§9). 대체 룰을 고르지 않는다 (§8-4.3).

## 왜 필요한가

지금까지의 증거는 전부 **같은 10년·같은 종목·앵커만 다름**이라 서로 독립이 아니다.
§14-2 에서 4·8월 모멘텀 순위가 5·11월을 거의 예측하지 못한다는 점추정(ρ=+0.157)이
나왔고, 현행안이 쓰는 MA 20/60 이 4위→12위로, 인접 조합 3개(confirm 3·7, slope 30)도
−8~−9계단 함께 떨어졌다. 태그 하나의 잡음이 아니라 **20/60 근방 전체가 흔들린다.**

시간 분할은 **캘린더 축을 건드리지 않고**(SPEC_13 §1-3 b′ 유효) 독립된 세 번째 각도를
준다: 4·8월 캘린더 그대로, 앞 10구간에서 고르고 뒤 10구간에서 검증한다.

## 사전등록 (2026-08-10, 사용자 확정)

| 항목 | 값 |
|---|---|
| 대상 | 가격 전용 17개 (재무 스택 고정, 모멘텀만 다름). 배관 복제품 제외 |
| 데이터 | `{tag}_periods.csv` 반기 캘린더 — **재실행 없음** |
| 구간 | 완결 20구간 (gate=0 인 2015년 2개, 열린 구간 1개 제외) |
| 분할 | **앞 10 / 뒤 10**. 앞 2016-04-05\~2020-08-20, 뒤 2021-04-05\~2025-08-20 |
| 지표 | 반쪽별 net 총복리 배수 Π(1+net) — 같은 구간 집합이라 순위는 CAGR 대소와 동치 |
| 통계량 | 앞·뒤 순위의 Spearman ρ + 태그쌍 bootstrap CI (2,000회, seed `SPEC14:TIMESPLIT`) |
| **초점** | **MA 20/60(현행안)의 앞·뒤 순위** |

**판정 (초점 태그 기준):**

| 조건 | 판정 |
|---|---|
| 앞 절반 **≤3위** AND 뒤 절반 **>10위** | `TIME_OVERFIT_CONFIRMED` |
| 앞·뒤 **둘 다 ≤5위** | `TIME_ROBUST` |
| 그 외 | `INCONCLUSIVE` |

**보조(판정 비사용)**: 반쪽별 관측기간↔성적 상관 — §14-2 사후 가설("장기일수록 전이가
잘 된다")이 시간 축에서도 보이는지. 사후 가설이므로 확인용일 뿐 근거가 되지 않는다.

## 한계 (실행 전 명시)

- **반쪽당 10구간뿐이라 잡음이 크다.** n=17 의 ρ 도 CI 가 넓을 것이다.
- 두 반쪽은 **시장 국면이 다르다**(앞: 2016\~2021 상승 + 코로나 급락·급반등, 뒤:
  2021\~2026). "과적합"과 "국면 차이"가 교락된다 — 이 검정은 결정적 증거가 아니라
  **세 번째 각도**다.
- 구간 제외 시 잔여 구간의 거래비용은 재계산하지 않는다(기록값 사용 — 진단 목적,
  `gate_analysis` 의 LOO 관례와 동일).

실행:
    venv/bin/python -m scripts.calendar_sens.time_split
출력: experiments/calendar_sens/time_split.json
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import random
from datetime import date, datetime

import numpy as np

from backtest.ablation import ABLATION_CONFIGS
from scripts.calendar_sens.calsens_lib import ABL_DIR, OUT_DIR
from scripts.calendar_sens.rank_stability import PLUMBING_PAIR, classify, spearman

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

# ── 사전등록 상수 — 수치 산출 후 수정 금지 ───────────────────────────────────
FOCAL_TAG   = 'F_pbr_no_r3r4'      # MA 20/60 = 현행안이 실제로 쓰는 설정
SPLIT_AT    = date(2021, 4, 5)     # 이 앵커부터 뒤 절반
FRONT_TOP   = 3                    # 앞 절반 이 순위 이내면 "앞에서 뽑혔을 것"
BACK_FLOOR  = 10                   # 뒤 절반 이 순위 밖이면 "뒤에서 무너짐"
ROBUST_TOP  = 5                    # 앞·뒤 둘 다 이 순위 이내면 시간축 견고
N_BOOT      = 2000
BOOT_SEED   = 'SPEC14:TIMESPLIT'
CI_BOUNDS   = (2.5, 97.5)


def load_closed(tag: str) -> list[dict]:
    """완결 구간만 — gate=0(2015년 TTM 미충족) 과 열린 구간 제외."""
    path = ABL_DIR / f'{tag}_periods.csv'
    rows = [r for r in csv.DictReader(path.open(encoding='utf-8'))
            if r.get('n_gate') not in (None, '', '0')]
    return rows[:-1]                       # 마지막은 열린 구간


def compound(rows: list[dict]) -> float:
    total = 1.0
    for r in rows:
        total *= 1.0 + float(r['net_return'])
    return total


def horizon(tag: str) -> int:
    """§14-2 와 같은 정의 — 그 판정기준이 보는 가장 긴 과거 구간(거래일)."""
    mc = ABLATION_CONFIGS[tag].get('momentum_criterion')
    if mc is None:
        return 60
    t = mc['type']
    if t == 'ma_double_adapter':
        return mc.get('ma_long', 60)
    if t == 'ma200':
        return mc['ma_window']
    if t == '52w_high':
        return mc['window']
    return mc.get('formation_days', 0)


def boot_rho(x: list[float], y: list[float]) -> dict:
    rng = random.Random(BOOT_SEED)
    n, vals = len(x), []
    for _ in range(N_BOOT):
        idx = [rng.randrange(n) for _ in range(n)]
        xs, ys = [x[i] for i in idx], [y[i] for i in idx]
        if len(set(xs)) < 3 or len(set(ys)) < 3:
            continue
        vals.append(spearman(xs, ys))
    arr = np.asarray(vals)
    lo, hi = np.percentile(arr, CI_BOUNDS)
    return {'n_valid': len(vals), 'ci_low': float(lo), 'ci_high': float(hi),
            'mean': float(arr.mean())}


def judge(front_rank: int, back_rank: int) -> str:
    if front_rank <= FRONT_TOP and back_rank > BACK_FLOOR:
        return 'TIME_OVERFIT_CONFIRMED'
    if front_rank <= ROBUST_TOP and back_rank <= ROBUST_TOP:
        return 'TIME_ROBUST'
    return 'INCONCLUSIVE'


def main() -> None:
    ap = argparse.ArgumentParser(description='SPEC_14 §14-3 시간 분할 검정')
    ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    tags = sorted(t for t in ABLATION_CONFIGS
                  if classify(t) == 'price_only' and t != PLUMBING_PAIR[0])

    rows, meta = [], None
    for t in tags:
        closed = load_closed(t)
        front = [r for r in closed if date.fromisoformat(r['rebalance_date']) < SPLIT_AT]
        back = [r for r in closed if date.fromisoformat(r['rebalance_date']) >= SPLIT_AT]
        if meta is None:
            meta = {'n_closed': len(closed), 'n_front': len(front), 'n_back': len(back),
                    'front_span': [front[0]['rebalance_date'], front[-1]['next_date']],
                    'back_span': [back[0]['rebalance_date'], back[-1]['next_date']]}
        elif (len(front), len(back)) != (meta['n_front'], meta['n_back']):
            raise SystemExit(f'{t}: 구간 수 불일치 — 세대가 다른 CSV 가 섞였다')
        rows.append({'tag': t, 'horizon': horizon(t),
                     'front_mult': compound(front), 'back_mult': compound(back),
                     'full_mult': compound(closed)})

    for key, label in (('front_mult', 'front_rank'), ('back_mult', 'back_rank'),
                       ('full_mult', 'full_rank')):
        for i, r in enumerate(sorted(rows, key=lambda x: -x[key]), 1):
            r[label] = i

    focal = next(r for r in rows if r['tag'] == FOCAL_TAG)
    fx = [r['front_mult'] for r in rows]
    bx = [r['back_mult'] for r in rows]
    rho = spearman(fx, bx)

    hs = [r['horizon'] for r in rows]
    result = {
        'spec': 'SPEC_14 §14-3 시간 분할',
        'disclaimer': '진단 전용 — 채택 후보 없음(§9), 대체 룰 선정 금지(§8-4.3).',
        'generated_at': datetime.now().isoformat(),
        'pre_registered': {
            'focal_tag': FOCAL_TAG, 'split_at': SPLIT_AT.isoformat(),
            'front_top': FRONT_TOP, 'back_floor': BACK_FLOOR, 'robust_top': ROBUST_TOP,
            'n_boot': N_BOOT, 'seed': BOOT_SEED,
            'note': '2026-08-10 사용자 확정 — 수치 산출 전 커밋',
        },
        'split': meta,
        'spearman_front_back': rho,
        'bootstrap': boot_rho(fx, bx),
        'focal': {'tag': FOCAL_TAG, 'front_rank': focal['front_rank'],
                  'back_rank': focal['back_rank'], 'full_rank': focal['full_rank']},
        'verdict': judge(focal['front_rank'], focal['back_rank']),
        'secondary_horizon_correlation': {
            'front': spearman(hs, fx), 'back': spearman(hs, bx),
            'status': '판정 비사용 — §14-2 사후 가설의 시간축 확인용',
        },
        'rows': sorted(rows, key=lambda r: r['front_rank']),
    }
    (OUT_DIR / 'time_split.json').write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')

    log.info('분할: 앞 %d구간 %s / 뒤 %d구간 %s',
             meta['n_front'], meta['front_span'], meta['n_back'], meta['back_span'])
    log.info('%-24s %8s %8s %8s', '태그', '앞순위', '뒤순위', '이동')
    for r in result['rows']:
        mark = '  ← 현행안' if r['tag'] == FOCAL_TAG else ''
        log.info('%-24s %8d %8d %+8d%s', r['tag'], r['front_rank'], r['back_rank'],
                 r['front_rank'] - r['back_rank'], mark)
    b = result['bootstrap']
    log.info('앞↔뒤 순위상관 ρ=%+.3f  CI95=[%+.3f, %+.3f]', rho, b['ci_low'], b['ci_high'])
    log.info('현행안(MA 20/60): 앞 %d위 → 뒤 %d위 (전체 %d위)',
             focal['front_rank'], focal['back_rank'], focal['full_rank'])
    log.info('판정: %s', result['verdict'])
    sh = result['secondary_horizon_correlation']
    log.info('[보조] 관측기간↔성적  앞 ρ=%+.3f  뒤 ρ=%+.3f', sh['front'], sh['back'])
    log.info('저장: %s', OUT_DIR / 'time_split.json')


if __name__ == '__main__':
    main()
