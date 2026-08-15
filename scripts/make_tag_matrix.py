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
from dashboard.series import SERIES, claimed_keys
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


def _axes_of(tag: str) -> list[str]:
    """이 태그가 등록된 축들. 다대다라 여럿일 수 있다.

    **명시 배정만 세면 안 된다** — 모멘텀 그리드처럼 패턴으로 붙는 축이 있어서,
    `spec.tags` 만 보면 23개가 통째로 "미배정"으로 잡힌다. 배정 규칙은
    `series.claimed_keys` 가 단일 정의다.
    """
    tags = sorted(ABLATION_CONFIGS)
    return [s.id for s in SERIES if tag in claimed_keys(s, tags)]


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
    tags = sorted(ABLATION_CONFIGS)

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
        '- **종목 수** — **태그가 n 을 박아 두는가**를 적는다. 숫자가 아닌 이유는',
        '  n 을 안 건드려서가 **아니라** 그 반대다: n 은 독립변수로 쓸어 본 축이고',
        '  (`F_pbr_ma200` → `_n10`·`_n12`·`_n13`·`_n20`, 그리고 tape 절단 곡선 n=1\\~20 —',
        '  `n_stocks` 축), 그 값이 **태그가 아니라 산출물 쪽**에 붙는다. 한 태그에',
        '  산출물이 다섯이고 n 이 셋인데 여기 숫자를 하나 적을 방법이 없다.',
        '  - `산출물 키 참조` — 태그로는 안 정해진다. `run_ablation --n-stocks` 가 정해',
        '    산출물 키의 `_n{K}` 접미사와 `n_stocks` 필드에 남는다. 접미사가 없으면',
        '    기본값이다. 현행 채택안이 태그 `F_pbr_ma200` · 산출물 **`F_pbr_ma200_n13`',
        '    (n=13)** 인 것이 그 예이고, 이 구별이 사라져서 2026-08-12 에 n=13 운영이',
        '    n=20 산출물을 읽었다.',
        '  - `고정` — 태그가 값을 박아 둔 것 (무작위 추첨의 `random_n`).',
        '  - `상한 없음` — 필터 통과 **전 종목**을 담는다 (랭킹이 없으므로 상한도 없다).',
        '- **짝 대조군** — 조건이 같은 무작위 추첨 시나리오. `D ≥ C_p95` 같은 관문은',
        '  **이 열에 값이 있을 때만** 물을 수 있다. `없음` 이면 유니버스가 다른 분포에',
        '  대보게 되므로 관문 판정을 내리면 안 된다 (SPEC_10 §1).',
        '  **이 열은 종목 수를 맞춰 주지 않는다.** 태그 단위에서는 알 수 없기 때문이다 —',
        '  같은 `C_pbr_path_random` 이 n=20 벌(p95 14.15%)과 n=13 벌(p95 15.61%)로 두 벌',
        '  있다. 관문을 물 때는 `experiments/robustness/gate_results_*.json` 의',
        '  `draws_n_stocks` 가 대상의 `n_stocks` 와 같은지 반드시 확인하라.',
        '',
        '  > ⚠️ **현행 채택안 `F_pbr_ma200` 의 짝 대조군은 `없음` 이다.** 유일한 모멘텀',
        '  > 경로 대조군 `C_pbr_path_random` 이 레거시 `MA 20/60` 풀이기 때문이다.',
        '  > `experiments/robustness/pools.json`(07-29, n=20)과 `pools_n13.json`',
        '  > (08-12, n=13)이 **md5 동일**이다 — 08-12 재추첨은 `--n-pick` 만 바꿨고',
        '  > 유니버스는 다시 짓지 않았다(`run_random_pool.py` 의 `TAG` 가 하드코딩이라',
        '  > 모멘텀 기준을 바꿀 수단이 없다). 즉 **SPEC_10 G1 은 MA200 전략을 MA 20/60',
        '  > 풀의 귀무분포에 대고 있다.** 판정이 뒤집힌다는 뜻은 아니다 — 더 좁은 풀의',
        '  > p95 가 오를지 내릴지는 재본 적이 없다. `docs/CANONICAL.md` 의 G1 PASS 를',
        '  > 인용할 때 이 사실을 함께 적어라.',
        '- **소속 축** — 대시보드 등록 대장(`dashboard/series.py`)에서 이 태그를 쓰는 축.',
        '  비어 있으면 화면 어디에도 안 뜬다.',
        '- **왜 만들었나** — 축과 조건만으로는 알 수 없는 것만 적는다. 모멘텀 그리드나',
        '  룰 조합처럼 **축이 곧 이유인 태그는 비워 둔다** (열이 이미 답하는 것을 다시',
        '  적으면 중복이고, 중복한 설명은 갈라진다). 내용은 `dashboard/tags.py` 소유.',
        '',
        f'총 **{len(tags)}개** 태그.',
        '',
        '| 태그 | 분류 | 랭킹 신호 | 안정성 룰 | Hard | 스크리너 | 모멘텀 | 밸류에이션 컷 | 종목 수 | 짝 대조군 | 소속 축 | 왜 만들었나 |',
        '|---|---|---|---|---|---|---|---|---|---|---|---|',
    ]

    for tag in tags:
        f = pipeline_facts(tag)
        if not f:
            continue
        axes = _axes_of(tag)
        lines.append(
            f'| `{tag}` | {class_of(tag) or "—"} | {f["랭킹 신호"]} | {f["안정성 룰"]} | '
            f'{f["Hard 필터"]} | {f["스크리너"]} | {f["모멘텀"]} | {f["밸류에이션 컷"]} | '
            f'{f["종목 수"]} | {matched_benchmark(tag, randoms)} | '
            f'{", ".join(axes) if axes else "**미배정**"} | '
            f'{note_of(tag) or ("축 설명 참조" if set(axes) & set(AXIS_EXPLAINS) else "**없음**")} |')

    orphans = [t for t in tags if not _axes_of(t)]
    unexplained = [t for t in tags if not note_of(t)
                   and not set(_axes_of(t)) & set(AXIS_EXPLAINS)]
    unbenched = [t for t in tags
                 if matched_benchmark(t, randoms) == '**없음**']
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
