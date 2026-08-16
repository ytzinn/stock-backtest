"""태그 조건 매트릭스 생성기 — `docs/TAG_MATRIX.md`.

## 왜 필요한가

`ABLATION_CONFIGS` 는 **플래그**를 담는다. 그런데 그 플래그를 조건으로 바꾸려면 규칙을
알아야 한다: `stability_rules` 가 있으면 그 집합, 없으면 `stability_r6`(기본 True)로
R6 만 빼고, `rank_mode` 가 없으면 `use_rim_filter` 에 따라 RIM 또는 무작위, 밸류에이션
컷은 경로마다 기본이 다르다. 그 해석을 사람이 머릿속으로 하면 **틀린다.**

2026-08-15 에 실제로 틀렸다. `D_rim_only` 와 `D_pbr_only` 를 "랭킹만 다른 짝"으로 보고
증분을 냈는데, 실제로는 **랭킹·R6·밸류에이션 컷 셋**이 함께 달랐다. 이름과 플래그만
보면 안 보이고, 조건으로 펴 놓으면 한눈에 보인다.

## 무엇을 담는가

**코드에서만 결정되는 것**만 담는다 — 조건, 소속 축, 짝이 맞는 랜덤 대조군.
성과 수치는 담지 않는다. 재발행 때마다 낡을 뿐 아니라 `docs/CANONICAL.md` 와 권위가
겹치기 때문이다. 이 문서는 **코드의 순수 함수**이고, 그래서 `--check` 가 의미를 갖는다.

## 짝이 맞는 대조군

`D ≥ C_p95` 같은 관문을 물으려면 **같은 유니버스에서 무작위로 뽑은** 분포가 있어야 한다.
룰 구성이 다르면 "랭킹의 기여"와 "유니버스가 달라서"가 분리되지 않는다 (SPEC_10 §1).
그래서 각 태그마다 조건이 일치하는 랜덤 시나리오를 찾아 적고, 없으면 **없다고 적는다** —
없는 걸 모르면 엉뚱한 분포에 대보고 FAIL 이라 부르게 된다.

실행:
    venv/bin/python -m scripts.make_tag_matrix           # 생성
    venv/bin/python -m scripts.make_tag_matrix --check   # 낡았으면 종료코드 1
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from backtest.ablation import ABLATION_CONFIGS
from dashboard.artifacts import ScenarioRef
from dashboard.series import CALENDAR_VARIANTS, SERIES, _split_variant, claimed_keys
from dashboard.series_view import pipeline_facts
from dashboard.tags import AXIS_EXPLAINS, class_of, note_of

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                    datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'docs/TAG_MATRIX.md'

#: 대조군이 되려면 이 조건들이 전부 같아야 한다. 랭킹 신호는 당연히 다르다
#: (한쪽은 무작위 추첨) — 그게 관문이 재려는 바로 그 차이다.
_MATCH_KEYS = ('Hard 필터', '안정성 룰', '스크리너', '모멘텀')

_RANDOM_SIGNALS = ('무작위 추첨',)

#: 반기(기본) 캘린더 표기. `CALENDAR_VARIANTS` 에 없는 = 접미사 없는 키가 이것이다.
_BASE_CALENDAR = '반기 (기본)'


def all_rows() -> list[str]:
    """표에 실을 키 — 설정 + **실행 파라미터로 파생된 산출물 키**.

    종전에는 `ABLATION_CONFIGS` 만 실어서, 종목 수를 독립변수로 쓸어 본 네 실행
    (`F_pbr_ma200_n10`·`_n12`·`_n13`·`_n20`)과 캘린더 변형 넷이 **행 자체가 없었다.**
    "설명 절에 적어 뒀다"로는 부족했다 — 표를 훑는 사람은 73행을 보고 그게 전부라고
    읽는다 (2026-08-16 사용자가 두 번 물어서 알았다).

    이 함수 때문에 문서가 **카탈로그에 의존한다.** 그래도 `--check` 는 의미를 잃지
    않는다: 이 문서엔 성과 수치가 없어서 같은 태그를 재실행해도 안 바뀌고, **새 n 값이
    생길 때만** 바뀐다. 그때는 바뀌는 게 맞다. (개발 PC·서버 카탈로그가 76개로 동일함을
    2026-08-16 에 확인했다 — 기계마다 달라질 거라던 우려는 사실이 아니었다.)
    """
    from dashboard.artifacts import build_catalog

    derived = [k for k in build_catalog().keys()
               if k not in ABLATION_CONFIGS and _base_tag(k) in ABLATION_CONFIGS]
    return sorted(set(ABLATION_CONFIGS) | set(derived))


def _calendar_of(key: str) -> str:
    """이 키가 쓰는 리밸런싱 캘린더. **열이 없으면 `_A`/`_C` 가 같아 보인다.**

    파생 키를 행으로 올리면서 함께 만들었다 — 안 만들면 `F_pbr_no_r3r4_A` 와 `_C` 가
    11개 열 전부 같게 뜬다. 단일 팩터 스크리너 넷이 그랬던 것과 같은 사고다.
    """
    ref = _split_variant(ScenarioRef.from_key(key))
    return CALENDAR_VARIANTS.get(ref.params.get('calendar'), _BASE_CALENDAR)


#: 행을 가르는 조건 열. 종목 수는 표기가 아니라 **값**으로 따로 붙인다.
_COND_COLS = ('랭킹 신호', '안정성 룰', 'Hard 필터', '스크리너', '모멘텀',
              '밸류에이션 컷', '캘린더')


def twin_groups() -> list[list[str]]:
    """조건이 완전히 같아 **표에서 구별되지 않는** 행 묶음.

    구별이 안 되면 짝 대조군도 같은 답을 받으므로, 둘 중 하나를 다른 하나의 대조군으로
    쓰면 아무것도 안 재는 셈이 된다. 단일 팩터 스크리너 넷이 오래 그 상태였다.

    정상인 경우도 있어서(같은 산식을 다른 배관으로 부르는 쌍) 실패시키지 않고 문서에
    드러낸다. 다만 `tests/integrity` 가 **묶음 안의 성적이 같은지**는 확인한다 —
    조건이 같은데 성적이 다르면 표가 못 보는 차원이 있다는 뜻이다.

    종목 수는 `산출물 키 참조 (기본 20)` 과 `20 (기록)` 처럼 표기가 갈리므로 **값으로**
    묶는다. 표기로 묶으면 같은 설정인 쌍이 다른 행으로 빠져나간다.
    """
    by_cond: dict[tuple, list[str]] = {}
    for key in all_rows():
        f = _row_facts(key)
        if f:
            by_cond.setdefault(tuple(f[c] for c in _COND_COLS) + (_n_value(key),),
                               []).append(key)
    return [v for v in by_cond.values() if len(v) > 1]


def _n_value(key: str) -> object:
    """이 행이 **실제로 쓰는 종목 수**. 표기가 아니라 값이다.

    `산출물 키 참조 (기본 20)` 과 `20 (기록)` 은 글자가 다를 뿐 같은 20 이다. 표기로
    비교하면 같은 설정인 쌍이 서로 다른 행으로 빠져나간다.
    """
    from dashboard.artifacts import build_catalog
    from dashboard.series_view import DEFAULT_N_STOCKS

    art = build_catalog().get(key)
    if art is not None and art.n_stocks is not None:
        return art.n_stocks
    label = pipeline_facts(_base_tag(key)).get('종목 수', '')
    if label.startswith('상한 없음'):
        return 'all'                      # 랭킹이 없어 상한 자체가 없다
    if '고정' in label:
        return int(label.split()[0])
    return ScenarioRef.from_key(key).params.get('n_stocks', DEFAULT_N_STOCKS)


def _row_facts(key: str) -> dict:
    """행 하나의 조건. 파생 키는 부모 조건을 물려받고 **달라지는 축만** 덮어쓴다."""
    facts = dict(pipeline_facts(_base_tag(key)))
    if not facts:
        return {}
    facts['캘린더'] = _calendar_of(key)
    if key in ABLATION_CONFIGS:
        return facts
    # 종목 수는 **산출물이 기록한 값**을 쓴다. 이름에서 추론하지 않는다 (이름과 내용이
    # 어긋난 사고가 이미 있었다 — 2026-08-12). 기록이 없으면 부모 규칙을 그대로 둔다.
    from dashboard.artifacts import build_catalog

    art = build_catalog().get(key)
    if art is not None and art.n_stocks is not None:
        facts['종목 수'] = f'{art.n_stocks} (기록)'
    return facts


def _base_tag(key: str) -> str:
    """산출물 키 → 태그. `_n{K}`(종목 수)·`_A`/`_C`(캘린더) 접미사를 되돌린다.

    분해 규칙을 여기 베끼지 않는다 — `ScenarioRef.from_key` 와 `series._split_variant`
    가 이미 단일 정의이고, 캘린더 접미사는 "떼어낸 나머지가 실제 config 일 때만" 뗀다
    는 판정까지 들어 있다 (`..._A` 로 끝나는 멀쩡한 태그를 망가뜨리지 않으려고).
    """
    return _split_variant(ScenarioRef.from_key(key)).base_tag


def _axes_of(tag: str) -> list[str]:
    """이 태그가 등록된 축들. 다대다라 여럿일 수 있다.

    **명시 배정만 세면 안 된다** — 모멘텀 그리드처럼 패턴으로 붙는 축이 있어서,
    `spec.tags` 만 보면 23개가 통째로 "미배정"으로 잡힌다. 배정 규칙은
    `series.claimed_keys` 가 단일 정의다.

    **키 변형도 되돌려 센다.** 등록 대장은 산출물 키로 배정하는데(`F_pbr_ma200_n13`,
    `F_pbr_no_r3r4_A`) 이 문서는 **태그** 단위다. 문자열로만 맞추면 현행 채택안
    `F_pbr_ma200` 의 소속 축이 `momentum_grid` 하나로 뜬다 — 정작 관문(`benchmarks`)과
    종목 수 축에서 쓰이는데 그 사실이 채택안 행에서 사라진다 (2026-08-15 사용자 발견).
    """
    # 후보에 **등록 대장이 명시한 산출물 키도** 넣는다. 종목 수 축은
    # `F_pbr_ma200_n*` 패턴으로 붙는데, 설정 이름만 넘기면 `_n13` 이 없어 아무것도
    # 안 걸린다 — 채택안 행에서 그 축이 통째로 빠진다.
    available = sorted(set(ABLATION_CONFIGS) | {k for s in SERIES for k in s.tags})
    return [s.id for s in SERIES
            if any(_base_tag(k) == tag for k in claimed_keys(s, available))]


def _random_pool() -> dict[str, dict]:
    return {t: f for t in sorted(ABLATION_CONFIGS)
            if (f := pipeline_facts(t)) and f['랭킹 신호'] in _RANDOM_SIGNALS}


def matched_benchmark(tag: str, randoms: dict[str, dict]) -> str:
    """조건이 일치하는 무작위 대조군. 없으면 `—`.

    **없다고 적는 것이 이 열의 요점이다.** `D_pbr_no_r3r4`(룰 {R1,R2,R5,R6})에 맞는
    랜덤 분포는 없는데, 그걸 모르고 `C_stability_random`(전 6룰) p95 에 대보면 유니버스가
    다른 비교를 관문 판정처럼 읽게 된다.
    """
    facts = pipeline_facts(tag)
    if not facts or facts['랭킹 신호'] in _RANDOM_SIGNALS:
        return '—'
    hits = [t for t, f in randoms.items()
            if all(f[k] == facts[k] for k in _MATCH_KEYS)]
    return ' · '.join(f'`{t}`' for t in hits) if hits else '**없음**'


def render() -> str:
    randoms = _random_pool()
    tags = all_rows()
    # 아래 요약 절들은 **태그 단위** 개념이다 (미배정·짝없음·설명없음). 파생 키는
    # 부모의 배정·대조군을 그대로 물려받으므로 여기서 다시 세면 같은 사실이 부풀려진다.
    configs = sorted(ABLATION_CONFIGS)

    lines = [
        '# 태그 조건 매트릭스',
        '',
        '> **생성물이다. 손으로 고치지 마라.** `scripts/make_tag_matrix.py` 가 만든다.',
        '> 값은 플래그를 다시 해석한 것이 아니라 `build_ablation_pipeline` 으로 **실제',
        '> 조립한 파이프라인**에서 읽는다 — 해석 규칙의 단일 정의가 그 함수이기 때문이다.',
        '',
        '성과 수치는 없다. 이 문서는 **코드의 순수 함수**이고, 수치의 권위는',
        '`docs/CANONICAL.md` 와 산출물에 있다.',
        '',
        '## 읽는 법',
        '',
        '- **안정성 룰** — 실제로 적용되는 룰 집합. `stability_r6`·`stability_rules` 를',
        '  해석한 결과다.',
        '- **밸류에이션 컷** — RIM 적정가 대비 고평가 종목을 빼는 단계. 랭킹을 1/PBR 로',
        '  바꾸면 **함께 사라진다** — 두 조건이 한 몸이라 "랭킹만의 효과"를 잴 수 없다.',
        '- **모멘텀** — `✓` 가 아니라 **판정 기준**을 적는다. 기준이 다르면 통과하는',
        '  종목이 달라 **유니버스가 다르기** 때문이다. `✓` 로 뭉갰을 때 짝 대조군 열이',
        '  레거시 `MA 20/60` 풀을 MA200·52주·절대수익 태그의 짝이라고 불렀다.',
        '  파라미터가 같으면 클래스가 달라도 같은 이름이다 — `F_pbr_ma_double_adapter` 는',
        '  레거시와 같은 산식을 부르고 gross·net 이 소수점 6자리까지 같다.',
        '- **종목 수** — n 은 태그가 아니라 **실행이 정하는 값**이라 표기가 두 갈래다.',
        '  - `{K} (기록)` — 산출물이 자기 안에 적어 둔 값. 종목 수를 독립변수로 쓸어 본',
        '    행들(`F_pbr_ma200_n10`·`_n12`·`_n13`·`_n20`)이 이것이다. **이름이 아니라',
        '    내용을 읽는다** — 이름과 내용이 어긋난 사고가 이미 있었다.',
        '  - `산출물 키 참조` — 태그로는 안 정해진다. `run_ablation --n-stocks` 가 정해',
        '    산출물 키의 `_n{K}` 접미사와 `n_stocks` 필드에 남는다. 접미사가 없으면',
        '    기본값이다. 현행 채택안이 태그 `F_pbr_ma200` · 산출물 **`F_pbr_ma200_n13`',
        '    (n=13)** 인 것이 그 예이고, 이 구별이 사라져서 2026-08-12 에 n=13 운영이',
        '    n=20 산출물을 읽었다.',
        '  - `고정` — 태그가 값을 박아 둔 것 (무작위 추첨의 `random_n`).',
        '  - `상한 없음` — 필터 통과 **전 종목**을 담는다 (랭킹이 없으므로 상한도 없다).',
        '- **캘린더** — 리밸런싱 앵커. 파생 키를 행으로 올리면서 함께 만든 열이다 —',
        '  없으면 `F_pbr_no_r3r4_A`(분기)와 `_C`(위상 이동)가 **11개 열 전부 같게** 뜬다.',
        '- **짝 대조군** — 조건이 같은 무작위 추첨 시나리오. `D ≥ C_p95` 같은 관문은',
        '  **이 열에 값이 있을 때만** 물을 수 있다. `없음` 이면 유니버스가 다른 분포에',
        '  대보게 되므로 관문 판정을 내리면 안 된다 (SPEC_10 §1).',
        '  **이 열은 종목 수를 맞춰 주지 않는다.** 태그 단위에서는 알 수 없기 때문이다 —',
        '  같은 `C_pbr_path_random` 이 n=20 벌(p95 14.15%)과 n=13 벌(p95 15.61%)로 두 벌',
        '  있다. 관문을 물 때는 `experiments/robustness/gate_results_*.json` 의',
        '  `draws_n_stocks` 가 대상의 `n_stocks` 와 같은지 반드시 확인하라.',
        '',
        '  > **`[해소 2026-08-15]`** 2026-08-15 이전에는 채택안(MA200)의 관문이 레거시',
        '  > `MA 20/60` 풀(`C_pbr_path_random`)에 걸려 있었다. `pools.json`(07-29, n=20)과',
        '  > `pools_n13.json`(08-12, n=13)이 **md5 동일**인 것이 증거다 — 08-12 재추첨은',
        '  > `--n-pick` 만 바꿨고 유니버스는 다시 짓지 않았다(`run_random_pool.py` 의 대상',
        '  > 태그가 하드코딩이라 모멘텀을 바꿀 수단이 없었다). 지금은 채택안 설정에서',
        '  > 파생된 `C_pbr_ma200_random` 으로 다시 뽑았다 — 풀이 8,229 → **6,445 종목**으로',
        '  > 좁아졌고 p95 는 15.61% → **15.47%** 다. **판정은 그대로 G1 PASS**',
        '  > (20.33% ≥ 15.47%, 귀무분포 백분위 99.4%).',
        '',
        '  > ⚠️ **G2 는 아직 같은 불일치가 남아 있다.** 벤치마크 `U_pbr_path_ew` 의 모멘텀이',
        '  > `MA 20/60` 이라, 채택안(MA 200)을 **다른 유니버스의 동일가중**과 견준다.',
        '  > 사전등록 게이트의 벤치마크 교체는 별도 결정 사항이라 그대로 뒀다.',
        '- **소속 축** — 대시보드 등록 대장(`dashboard/series.py`)에서 이 태그를 쓰는 축.',
        '  비어 있으면 화면 어디에도 안 뜬다. 등록 대장은 **산출물 키**로 배정하므로',
        '  (`F_pbr_ma200_n13`, `F_pbr_no_r3r4_A`) 접미사를 되돌려 센다 — 안 그러면 현행',
        '  채택안의 소속 축에서 관문·종목 수 축이 빠진다.',
        '- **왜 만들었나** — 축과 조건만으로는 알 수 없는 것만 적는다. 모멘텀 그리드나',
        '  룰 조합처럼 **축이 곧 이유인 태그는 비워 둔다** (열이 이미 답하는 것을 다시',
        '  적으면 중복이고, 중복한 설명은 갈라진다). 내용은 `dashboard/tags.py` 소유.',
        '',
        f'총 **{len(tags)}개** 행 — 설정 {len(ABLATION_CONFIGS)}개 + '
        f'실행 파라미터로 파생된 산출물 키 {len(tags) - len(ABLATION_CONFIGS)}개.',
        '',
        '## 파생 키도 행으로 싣는다',
        '',
        '`n_stocks`·캘린더는 설정이 아니라 **실행 때 정해진다**(`run_ablation --n-stocks K`,',
        '`--calendar A`). 그래서 `ABLATION_CONFIGS` 에는 부모 태그만 있는데, 설정만 실었더니',
        '종목 수를 독립변수로 쓸어 본 네 실행과 캘린더 변형 넷이 **행 자체가 없었다.**',
        '설명 절만 두는 것으로는 부족했다 — 표를 훑는 사람은 보이는 행이 전부라고 읽는다.',
        '',
        '파생 행은 부모의 조건을 그대로 물려받고 **달라지는 축만** 다르다. `종목 수` 는',
        '산출물이 기록한 값(`{K} (기록)`), `캘린더` 는 앵커다. 조건이 궁금하면 부모 행과',
        '나란히 놓고 보면 된다.',
        '',
        '> 이 절 때문에 문서가 **카탈로그에 의존한다.** 그래도 `--check` 는 의미를 잃지',
        '> 않는다: 여기엔 성과 수치가 없어서 같은 태그를 재실행해도 안 바뀌고, **새 n 값이',
        '> 생길 때만** 바뀐다 — 그때는 바뀌는 게 맞다.',
        '',
        '| 태그 | 분류 | 랭킹 신호 | 안정성 룰 | Hard | 스크리너 | 모멘텀 | 밸류에이션 컷 | 종목 수 | 캘린더 | 짝 대조군 | 소속 축 | 왜 만들었나 |',
        '|---|---|---|---|---|---|---|---|---|---|---|---|---|',
    ]

    for tag in tags:
        f = _row_facts(tag)
        if not f:
            continue
        base = _base_tag(tag)
        axes = _axes_of(base)
        lines.append(
            f'| `{tag}` | {class_of(tag) or "—"} | {f["랭킹 신호"]} | {f["안정성 룰"]} | '
            f'{f["Hard 필터"]} | {f["스크리너"]} | {f["모멘텀"]} | {f["밸류에이션 컷"]} | '
            f'{f["종목 수"]} | {f["캘린더"]} | {matched_benchmark(base, randoms)} | '
            f'{", ".join(axes) if axes else "**미배정**"} | '
            f'{note_of(tag) or ("축 설명 참조" if set(axes) & set(AXIS_EXPLAINS) else "**없음**")} |')

    orphans = [t for t in configs if not _axes_of(t)]
    unexplained = [t for t in configs if not note_of(t)
                   and not set(_axes_of(t)) & set(AXIS_EXPLAINS)]
    unbenched = [t for t in configs
                 if matched_benchmark(t, randoms) == '**없음**']

    # **조건이 완전히 같은 행 묶음.** 표에서 구별되지 않는다는 뜻이고, 그러면 짝
    # 대조군도 같은 답을 받는다. 단일 팩터 스크리너 넷이 그 상태였는데(2026-08-15)
    # 아무도 못 봤다 — 열을 하나 더 만들 때까지. 정상인 경우도 있다
    # (`F_pbr_ma_double_adapter` 는 레거시와 같은 산식이라 조건이 같은 게 맞다).
    # 그래서 실패시키지 않고 **표에 드러낸다.**
    twins = twin_groups()

    lines += [
        '',
        '## 관문을 물을 수 없는 태그',
        '',
        f'짝이 맞는 무작위 대조군이 없는 태그가 **{len(unbenched)}개** 있다. 이들에게 '
        '`D ≥ C_p95` 형태의 관문을 물으면, 룰 구성이 다른 유니버스에서 뽑은 분포와 '
        '견주는 것이라 판정이 성립하지 않는다.',
        '',
    ]
    lines += [f'- `{t}`' for t in unbenched] or ['- (없음)']
    lines += [
        '',
        '## 등록 대장에 없는 태그',
        '',
        f'축 어디에도 안 들어간 태그가 **{len(orphans)}개**. 화면에 안 뜨므로 '
        '만들어 두고 잊기 쉽다.',
        '',
    ]
    lines += [f'- `{t}`' for t in orphans] or ['- (없음)']
    lines += [
        '',
        '## 왜 만들었는지 안 적힌 태그',
        '',
        f'축이 설명해 주지도 않고 개별 설명도 없는 태그가 **{len(unexplained)}개**. '
        '조건표만으로는 "왜 이 조합을 굳이 만들었나"를 알 수 없는 자리다. '
        '설명은 `dashboard/tags.py` 에 추가한다.',
        '',
    ]
    lines += [f'- `{t}`' for t in unexplained] or ['- (없음)']
    lines += [
        '',
        '## 조건이 완전히 같은 행',
        '',
        f'모든 조건 열이 같아 **이 표에서 구별되지 않는** 묶음이 {len(twins)}개 있다. '
        '구별이 안 되면 짝 대조군도 같은 답을 받으므로, 둘 중 하나를 다른 하나의 '
        '대조군으로 쓰면 아무것도 안 재는 셈이 된다.',
        '',
        '정상인 경우도 있다 — 같은 산식을 다른 배관으로 부르는 쌍이 그렇다. 그래서 '
        '실패로 다루지 않고 여기 드러내기만 한다. **모르는 묶음이 보이면 열이 하나 '
        '모자란 것이다** (단일 팩터 스크리너 넷이 그 상태로 오래 있었다).',
        '',
    ]
    lines += [f'- {" · ".join(f"`{t}`" for t in g)}' for g in twins] or ['- (없음)']
    lines.append('')
    return '\n'.join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description='태그 조건 매트릭스 생성')
    ap.add_argument('--check', action='store_true',
                    help='낡았으면 종료코드 1 (파일을 쓰지 않는다)')
    args = ap.parse_args()

    body = render()
    if args.check:
        # 줄바꿈을 정규화해 비교한다. git 이 CRLF/LF 로 다르게 체크아웃해도 같은
        # 내용이면 같다고 봐야 한다 (make_canonical 의 _sha256 과 같은 이유).
        current = OUT.read_text(encoding='utf-8') if OUT.exists() else ''
        if current.replace('\r\n', '\n') != body.replace('\r\n', '\n'):
            log.error('%s 가 낡았다 — `python -m scripts.make_tag_matrix` 로 재생성하라', OUT)
            sys.exit(1)
        log.info('%s 최신 상태', OUT)
        return

    OUT.write_text(body, encoding='utf-8')
    log.info('%s 생성 (%d개 태그)', OUT, len(ABLATION_CONFIGS))


if __name__ == '__main__':
    main()
