r"""
SPEC_14 §14-5 — MA200 이웃 견고성(고원 vs 첨탑) + 장기 계열 비교.

**사전등록. 수치 산출 전 커밋 — 문턱 이후 수정 금지.**
**이 결과로 운영 태그를 교체하지 않는다.** 새 후보를 만들면 20/60 을 낳은
그리드서치를 규모만 키워 반복하는 것이다(§8-4.3). 이 검정은 **§14-4 결정을
뒤집는 방향으로만 작동**한다 — 경고를 띄우거나, 아무 일도 없거나 둘 중 하나다.
(§1 의 비대칭과 같은 구조: 새 후보를 만들 수 없고 기존 신뢰만 확인·하향한다.)

## 무엇을 묻나

§14-4 는 MA200 이 캘린더축·시간축 양쪽에서 안정적이라는 이유로 채택됐다. 그런데
**200 이라는 값 하나만 우연히 잘 나온 것**일 수도 있다. 그렇다면 그 안정성은
잡음이고 결정 근거가 무너진다.

  Q-A **이웃**: 같은 규칙(종가 vs N일선)에서 N 을 100·150·200·250·300 으로 바꿔도
       안정성이 유지되는가? → 고원이면 구조, 첨탑이면 잡음
  Q-B **계열**: 규칙 모양이 다른 장기 이중교차(60/200, 120/200)도 안정적인가?
       → §14-2 사후 가설("관측기간이 길수록 전이가 잘 된다")의 직접 검증

## 사전등록 (2026-08-10, 사용자 확정)

| 항목 | 값 |
|---|---|
| 축 | ① 시간 분할(§14-3 과 동일: 앞 10 / 뒤 10, 경계 2021-04-05) ② 캘린더(4·8 ↔ 5·11) |
| 지표 | 시간축은 반쪽별 net 총복리, 캘린더축은 엔진 net CAGR |
| 이웃 집합 | `ma100 ma150 ma200 ma250 ma300` (5개) |
| 계열 집합 | `ma60_200 ma120_200` + 기존 `ma20_120 ma5_120 ma60_120` |
| 순위 모집단 | 기존 가격 전용 17개 + 신규 6개 = **23개** (배관 복제품 제외) |

**판정 (Q-A, MA200 기준):**

| 조건 | 판정 |
|---|---|
| `ma150`·`ma250` **둘 다** 두 축 모두에서 상위 1/3(23개 중 ≤8위) | `PLATEAU_CONFIRMED` |
| `ma200` 이 어느 축에서든 ≤8위인데 `ma150`·`ma250` **둘 다** >15위 | **`ISOLATED_SPIKE_WARNING`** |
| 그 외 | `INCONCLUSIVE` |

`ISOLATED_SPIKE_WARNING` 이면 **§14-4 채택 결정을 재검토 대상으로 올린다**
(자동 철회가 아니라 사용자 판단 요청).

**Q-B(보조, 판정 비사용)**: 관측기간 ≥120 집단과 <120 집단의 축별 순위 변동 폭 비교.

## 한계

- 신규 6개는 **결과를 본 뒤 추가**된 조합이다(§7-4 탐색 셀). 어떤 수치도 채택 근거가
  되지 않으며, 오직 위 판정표의 경고 발화 여부에만 쓴다.
- 시간 분할의 국면 교락(앞 절반 코로나)은 §14-3 과 동일하게 남는다.
- 23개 중 순위이므로 신규 6개가 들어오면 **기존 17개의 순위도 바뀐다** — §14-2·§14-3
  의 순위와 직접 비교하지 않는다.

실행:
    venv/bin/python -m scripts.calendar_sens.plateau
출력: experiments/calendar_sens/plateau.json
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime

from backtest.ablation import ABLATION_CONFIGS
from scripts.calendar_sens.calsens_lib import OUT_DIR
from scripts.calendar_sens.rank_stability import PLUMBING_PAIR, _cagr, classify
from scripts.calendar_sens.time_split import SPLIT_AT, compound, horizon, load_closed

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

# ── 사전등록 상수 — 수치 산출 후 수정 금지 ───────────────────────────────────
FOCAL      = 'F_pbr_ma200'
NEIGHBORS  = ('F_pbr_ma150', 'F_pbr_ma250')       # 직근 이웃
FAMILY_MA  = ('F_pbr_ma100', 'F_pbr_ma150', 'F_pbr_ma200',
              'F_pbr_ma250', 'F_pbr_ma300')
TOP_THIRD  = 8      # 23개 중 상위 1/3
SPIKE_TAIL = 15     # 이웃이 이 순위보다 아래면 "고립"


def _rank(rows: list[dict], key: str, label: str) -> None:
    for i, r in enumerate(sorted(rows, key=lambda x: -x[key]), 1):
        r[label] = i


def judge(r: dict[str, dict]) -> str:
    focal = r[FOCAL]
    axes = ('front_rank', 'back_rank', 'semi_rank', 'alt_rank')
    nb = [r[n] for n in NEIGHBORS]

    if all(n[a] <= TOP_THIRD for n in nb for a in axes):
        return 'PLATEAU_CONFIRMED'
    if (any(focal[a] <= TOP_THIRD for a in axes)
            and all(min(n[a] for a in axes) > SPIKE_TAIL for n in nb)):
        return 'ISOLATED_SPIKE_WARNING'
    return 'INCONCLUSIVE'


def main() -> None:
    argparse.ArgumentParser(description='SPEC_14 §14-5 이웃 견고성').parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    tags = sorted(t for t in ABLATION_CONFIGS
                  if classify(t) == 'price_only' and t != PLUMBING_PAIR[0])
    rows, missing = [], []
    for t in tags:
        semi, alt = _cagr(t, ''), _cagr(t, '_C')
        if semi is None or alt is None:
            missing.append(t)
            continue
        closed = load_closed(t)
        front = [x for x in closed if date.fromisoformat(x['rebalance_date']) < SPLIT_AT]
        back = [x for x in closed if date.fromisoformat(x['rebalance_date']) >= SPLIT_AT]
        rows.append({'tag': t, 'horizon': horizon(t),
                     'front_mult': compound(front), 'back_mult': compound(back),
                     'semi_net': semi[0], 'alt_net': alt[0]})

    if missing:
        raise SystemExit(f'산출물 없음 {missing} — run_ablation 을 두 캘린더로 먼저 실행할 것')

    for key, label in (('front_mult', 'front_rank'), ('back_mult', 'back_rank'),
                       ('semi_net', 'semi_rank'), ('alt_net', 'alt_rank')):
        _rank(rows, key, label)
    by_tag = {r['tag']: r for r in rows}

    long_g = [r for r in rows if r['horizon'] >= 120]
    short_g = [r for r in rows if r['horizon'] < 120]

    def swing(g: list[dict]) -> float:
        """축별 순위 변동 폭 평균 (|시간축 이동| + |캘린더축 이동|)/2."""
        return sum(abs(r['front_rank'] - r['back_rank'])
                   + abs(r['semi_rank'] - r['alt_rank']) for r in g) / (2 * len(g))

    verdict = judge(by_tag)
    result = {
        'spec': 'SPEC_14 §14-5 이웃 견고성 + 계열 비교',
        'disclaimer': '진단 전용 — **이 결과로 운영 태그를 교체하지 않는다**. '
                      '§14-4 결정을 뒤집는 방향으로만 작동한다.',
        'generated_at': datetime.now().isoformat(),
        'pre_registered': {'focal': FOCAL, 'neighbors': list(NEIGHBORS),
                           'top_third': TOP_THIRD, 'spike_tail': SPIKE_TAIL,
                           'n_population': len(rows),
                           'note': '2026-08-10 사용자 확정 — 수치 산출 전 커밋'},
        'verdict': verdict,
        'verdict_meaning': {
            'PLATEAU_CONFIRMED': 'MA200 의 안정성은 200 이라는 값이 아니라 장기 구간 '
                                 '전체의 성질이다 — §14-4 결정 근거가 강화된다',
            'ISOLATED_SPIKE_WARNING': 'MA200 만 튀고 이웃은 아니다 — 그 안정성이 잡음일 '
                                      '수 있다. **§14-4 채택 결정을 재검토 대상으로 올린다**',
            'INCONCLUSIVE': '판별 불가 — 결정 유지, 추가 근거 없음',
        },
        'ma_family': [by_tag[t] for t in FAMILY_MA if t in by_tag],
        'secondary_horizon': {
            'long_n': len(long_g), 'short_n': len(short_g),
            'long_mean_rank_swing': swing(long_g), 'short_mean_rank_swing': swing(short_g),
            'status': '판정 비사용 — §14-2 사후 가설의 확장 확인',
        },
        'rows': sorted(rows, key=lambda r: -r['horizon']),
    }
    (OUT_DIR / 'plateau.json').write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')

    log.info('모집단 %d개 (기존 17 + 신규 6)', len(rows))
    log.info('%-22s %5s %6s %6s %6s %6s', '태그', '기간', '앞순위', '뒤순위', '4·8', '5·11')
    for r in result['rows']:
        mark = ' ←' if r['tag'] == FOCAL else ('  ~' if r['tag'] in NEIGHBORS else '')
        log.info('%-22s %5d %6d %6d %6d %6d%s', r['tag'], r['horizon'],
                 r['front_rank'], r['back_rank'], r['semi_rank'], r['alt_rank'], mark)
    s = result['secondary_horizon']
    log.info('[보조] 순위 변동 폭 평균 — 장기(≥120) %.2f  단기(<120) %.2f',
             s['long_mean_rank_swing'], s['short_mean_rank_swing'])
    log.info('판정: %s', verdict)
    log.info('  → %s', result['verdict_meaning'][verdict])
    log.info('저장: %s', OUT_DIR / 'plateau.json')


if __name__ == '__main__':
    main()
