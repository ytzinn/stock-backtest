"""주장 검증기 — "표가 태그를 설명한다"가 아니라 **"저장소의 주장을 검증한다"**.

## 왜 방향을 뒤집었나

`docs/TAG_MATRIX.md` 는 셀 것을 사람이 정한다 — 미배정·짝없음·설명없음 셋. 그래서
**아무도 생각 못 한 부재는 구조적으로 못 본다.** 2026-08-15 하루에 네 건이 사용자
눈으로 잡혔고(미배정 태그·낡은 CANONICAL 라벨·불리언 모멘텀 매칭·파생 키 누락), 넷 다
"세라고 시키지 않은 것"이었다. 검사를 하나씩 더 붙이는 건 같은 방법의 연장이라 같은
천장에 걸린다.

그런데 이 저장소에는 **이미 기계가 읽을 수 있는 주장**이 흩어져 있다:

- `calendar_sens/stage_b.json` — contrast 마다 `(variant, baseline, n_axes, single_axis)`
- `robustness/gate_results_*.json` — `(tag, u_tag, draws_tag)` = "이게 저것의 귀무분포·벤치다"
- `series.py` 의 `Delta` — "이 둘을 빼면 이만큼이 달라진다"

이것들을 조건표와 대조하면 **커버리지가 저절로 자란다.** 새 산출물이 생기면 새 주장이
따라오고 검사 대상에 자동으로 들어간다 — 사람이 "이것도 세야지" 하고 떠올릴 필요가 없다.

실제로 만들자마자 하나를 잡았다: `gate_results` 가 G2 벤치라고 적은 `U_pbr_path_ew` 의
모멘텀이 채택안과 다르다(레거시 MA 20/60 vs MA 200). 아무도 "G2 벤치의 모멘텀을
확인하라"는 검사를 짜 넣지 않았는데도 나왔다.

## 축을 세는 눈금

**룰은 하나하나가 축이다.** `stage_b` 의 `n_axes` 가 그렇게 센다(`C_STAB` = 4).
매트릭스의 `안정성 룰` 은 열이 하나라 문자열로 비교하면 1이 되는데, 두 눈금을 뭉개면
"단일축"의 뜻이 갈린다 — 이 저장소가 반복해서 밟은 함정이다.

## 사각지대 탐침 (`blind_spots`)

주장 검증기는 **누군가 적어 둔 주장**만 본다. 아무도 모델링하지 않은 차원은 못 본다.
그래서 설정 키를 하나씩 흔들어 표가 반응하는지 재는 탐침을 함께 둔다. 반응하지 않는
키는 표의 사각지대이고, 그 키로 갈리는 두 태그는 표에서 같아 보인다. 처음 돌렸을 때
`screener_weights` 가 걸렸다 — 단일 팩터 변형 넷이 11개 열 전부 같게 보이고 있었다.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path

from backtest.ablation import ABLATION_CONFIGS
from dashboard.artifacts import ScenarioRef
from dashboard.series import SERIES, _split_variant
from dashboard.series_view import pipeline_facts

ROOT = Path(__file__).resolve().parent.parent

#: 유니버스를 만드는 조건. 관문·벤치는 **이것들이 같아야** 비교가 성립한다
#: (랭킹 신호는 당연히 다르다 — 그게 관문이 재려는 차이다).
UNIVERSE_DIMS = ('Hard 필터', '스크리너', '모멘텀')

#: 룰을 뺀 나머지 열 전부. 축을 셀 때 쓴다.
_NON_RULE_DIMS = ('Hard 필터', '스크리너', '모멘텀', '랭킹 신호', '밸류에이션 컷')


@dataclass(frozen=True)
class Violation:
    source: str      # 어느 산출물·코드가 주장했나
    claim: str       # 무엇을 주장했나
    actual: str      # 실제로는 무엇인가
    key: tuple       # 예외 목록 조회용


#: **이미 알고 있고 지금은 안 고치기로 한** 위반. 사유와 날짜를 함께 적는다.
#:
#: 억제 목록이 아니다 — `stale_exemptions()` 가 **더 이상 위반이 아닌 예외**를
#: 찾아내 지우게 만든다. 그게 없으면 예외가 조용히 쌓여, 지금 매트릭스와 똑같이
#: 아무도 안 보는 사각지대가 된다.
KNOWN: dict[tuple, str] = {
    ('gate_undeclared', 'gate_results.json', ''):
        '2026-07-30 산출 — `tag`·`draws_tag`·`u_tag` 필드가 생기기 전(2026-08-12 정정) '
        '산출물이라 대상을 안 밝힌다. 조상 `F_pbr_no_r3r4`(n=20)의 기록이고 '
        '`gate_results_F_pbr_ma200_n13.json` 로 대체됐다. **소급 편집하지 않는다** — '
        '그 실행이 실제로 기록한 것을 왜곡하게 된다 (2026-08-16 판단).',
    ('gate_benchmark', 'F_pbr_ma200', 'U_pbr_path_ew'):
        'G2 벤치의 모멘텀이 레거시 MA 20/60 이라 채택안(MA 200)과 유니버스가 다르다. '
        '사전등록 게이트의 벤치마크 교체는 별도 결정이라 보류 '
        '(2026-08-15, 사용자 판단). 고치려면 채택안에서 파생한 `U_pbr_ma200_ew` 를 '
        '만들어 G2 를 재산출해야 한다.',
}


def _rules(facts: dict) -> set[str]:
    s = facts.get('안정성 룰', '—')
    return set() if s == '—' else set(s.split('·'))


def condition_axes(a: str, b: str) -> list[str]:
    """두 태그가 실제로 다른 축들. **룰은 하나하나가 축이다.**

    `stage_b` 의 `n_axes` 와 같은 눈금이라야 그 주장을 검증할 수 있다.
    """
    fa, fb = pipeline_facts(a), pipeline_facts(b)
    if not fa or not fb:
        return []
    out = [f'룰 {r}' for r in sorted(_rules(fa) ^ _rules(fb))]
    out += [k for k in _NON_RULE_DIMS if fa[k] != fb[k]]
    return out


def universe_axes(a: str, b: str) -> list[str]:
    """유니버스를 가르는 축만. 관문·벤치가 성립하려면 **비어 있어야** 한다."""
    fa, fb = pipeline_facts(a), pipeline_facts(b)
    if not fa or not fb:
        return []
    out = [f'룰 {r}' for r in sorted(_rules(fa) ^ _rules(fb))]
    return out + [k for k in UNIVERSE_DIMS if fa[k] != fb[k]]


def base_tag(key: str) -> str:
    """산출물 키 → 태그. `_n{K}`·`_A`/`_C` 를 되돌린다."""
    return _split_variant(ScenarioRef.from_key(key)).base_tag


# ── 주장 수집기 ──────────────────────────────────────────────────────────────

def _contrast_claims() -> list[Violation]:
    """`stage_b.json` 이 contrast 마다 적어 둔 축 수·단일축 플래그를 검증한다."""
    path = ROOT / 'experiments/calendar_sens/stage_b.json'
    if not path.exists():
        return []
    d = json.loads(path.read_text(encoding='utf-8'))
    cells = (d.get('contrasts_single_axis', []) + d.get('contrasts_multi_axis', [])
             + (d.get('contrasts_rank_cut_2x2') or {}).get('cells', []))
    out = []
    for c in cells:
        v, b = c['variant_tag'], c['baseline_tag']
        if v not in ABLATION_CONFIGS or b not in ABLATION_CONFIGS:
            out.append(Violation(
                'calendar_sens/stage_b.json', f"{c['contrast_id']}: 태그가 설정에 없다",
                f'variant={v} baseline={b}', ('contrast_tag', c['contrast_id'], v)))
            continue
        axes = condition_axes(v, b)
        if len(axes) != c['n_axes']:
            out.append(Violation(
                'calendar_sens/stage_b.json',
                f"{c['contrast_id']}: n_axes={c['n_axes']}",
                f'실제 {len(axes)}개 — {" · ".join(axes) or "(차이 없음)"}',
                ('contrast_axes', c['contrast_id'], v)))
        if c.get('single_axis') != (len(axes) == 1):
            out.append(Violation(
                'calendar_sens/stage_b.json',
                f"{c['contrast_id']}: single_axis={c.get('single_axis')}",
                f'실제 축 {len(axes)}개', ('contrast_single', c['contrast_id'], v)))
    return out


def _recorded_n(key: str) -> int | None:
    """산출물이 **기록한** 종목 수. 이름에서 추론하지 않는다.

    이름과 내용이 어긋나는 사고가 이미 있었으므로(2026-08-12) 여기서는 내용만 읽는다.
    이름↔내용 일치는 `tests/integrity/test_artifact_naming.py` 가 따로 지킨다.
    """
    for path in (ROOT / f'experiments/ablation/{key}.json',
                 ROOT / f'experiments/robustness/random_summary_{key}.json'):
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8')).get('n_stocks')
    # `random_summary` 의 레거시 규약 — 파일명에 태그가 없고 `_n{K}` 만 붙는다.
    sfx = key.rsplit('_', 1)[-1]
    legacy = ROOT / f'experiments/robustness/random_summary{"_" + sfx if sfx.startswith("n") else ""}.json'
    if legacy.exists():
        return json.loads(legacy.read_text(encoding='utf-8')).get('n_stocks')
    return None


def _gate_claims(rob_dir: Path | None = None) -> list[Violation]:
    """`gate_results_*.json` 의 귀무분포·벤치가 판정 대상과 **같은 유니버스·같은 n** 인가.

    이 검사가 2026-08-15 에 G1(모멘텀 불일치)과 G2 를 스스로 짚었다. 산출물이 자기
    입으로 "이게 저것의 귀무분포다"라고 적어 두는데 아무도 대조하지 않고 있었다.

    **n 도 함께 본다.** 종목 수가 다르면 분산이 달라 p95(합격선) 자체가 달라진다 —
    2026-08-12 에 n=13 전략을 n=20 귀무분포로 판정하고 있었고 그 방향은 게이트를
    **관대하게** 만드는 쪽이었다. 조건표는 태그 단위라 n 을 모르므로(n 은 실행
    파라미터다) 산출물이 기록한 값을 직접 사슬로 대조한다.
    """
    # 디렉터리를 인자로 받는 이유: **검사가 추적 중인 산출물을 건드리지 않게** 하려고.
    # 종전에는 실제 파일을 고쳤다 되돌렸는데, 윈도우에서 줄바꿈이 변환돼 원복이
    # 정확하지 않았다 (검사가 데이터를 오염시키는 통로다).
    out = []
    for path in sorted((rob_dir or ROOT / 'experiments/robustness').glob('gate_results*.json')):
        g = json.loads(path.read_text(encoding='utf-8'))
        src = f'robustness/{path.name}'
        key = g.get('tag')
        if not key:
            # **추측하지 않는다.** 종전에는 기본 태그로 채워 넣었는데, 그러면 어느
            # 전략의 성적표인지 모르는 산출물을 아는 척 검증하게 된다.
            out.append(Violation(
                src, '판정 대상을 스스로 밝힌다',
                '`tag` 필드가 없다 — 어느 전략의 게이트인지 산출물만 보고 알 수 없다',
                ('gate_undeclared', path.name, '')))
            continue

        f_tag, f_n = base_tag(key), g.get('n_stocks')
        if f_n is not None and _recorded_n(key) not in (None, f_n):
            out.append(Violation(
                src, f'판정 대상 `{key}` 의 종목 수가 {f_n}',
                f'산출물은 {_recorded_n(key)} 로 기록 — 이름·게이트·내용이 갈렸다',
                ('gate_target_n', key, '')))

        for field, n_field, kind, label in (
                ('draws_tag', 'draws_n_stocks', 'gate_null', 'G1 귀무분포'),
                ('u_tag', None, 'gate_benchmark', 'G2 벤치마크')):
            other_key = g.get(field)
            if not other_key:
                continue
            other = base_tag(other_key)
            if f_tag in ABLATION_CONFIGS and other in ABLATION_CONFIGS:
                axes = universe_axes(f_tag, other)
                if axes:
                    out.append(Violation(
                        src, f'{label} `{other}` 가 `{f_tag}` 의 짝이다',
                        f'유니버스가 다르다 — {" · ".join(axes)}',
                        (kind, f_tag, other)))
            if n_field is None:
                continue        # EW 벤치는 전 종목이라 n 개념이 없다
            other_n = g.get(n_field)
            if other_n != f_n:
                out.append(Violation(
                    src, f'{label} 의 종목 수가 판정 대상과 같다',
                    f'대상 n={f_n} vs 귀무 n={other_n} — 분산이 달라 합격선이 어긋난다',
                    (f'{kind}_n', f_tag, other)))
            if other_n is not None and _recorded_n(other_key) not in (None, other_n):
                out.append(Violation(
                    src, f'{label} `{other_key}` 의 종목 수가 {other_n}',
                    f'산출물은 {_recorded_n(other_key)} 로 기록',
                    (f'{kind}_recorded_n', other_key, '')))
    return out


def _delta_claims() -> list[Violation]:
    """왜-지도의 증분표가 **선언한 축 수**대로 바꾸는가.

    "짝이 맞지 않는 둘을 빼서 X 의 효과라고 부른다" 가 이 저장소의 단골 실수다
    (2026-08-15 하루에 세 번). 산문으로 적힌 주의는 기계가 못 읽으므로 `Delta.axes`
    로 선언하게 하고, 선언과 조건표가 어긋나면 여기서 터진다.
    """
    out = []
    for s in SERIES:
        if not (s.why and s.why.deltas):
            continue
        for d in s.why.deltas:
            if d.base not in ABLATION_CONFIGS or d.variant not in ABLATION_CONFIGS:
                continue
            axes = condition_axes(d.base, d.variant)
            if len(axes) != d.axes:
                out.append(Violation(
                    f'series.py[{s.id}]',
                    f'증분 "{d.label}" 이 축 {d.axes}개',
                    f'실제 {len(axes)}개 — {" · ".join(axes) or "(차이 없음)"}',
                    ('delta_axes', s.id, d.label)))
    return out


def verify() -> list[Violation]:
    """모든 주장을 검증하고 **예외로 등록되지 않은** 위반만 돌려준다."""
    found = _contrast_claims() + _gate_claims() + _delta_claims()
    return [v for v in found if v.key not in KNOWN]


def stale_exemptions() -> list[tuple]:
    """**더 이상 위반이 아닌 예외.** 지우게 만들려고 따로 센다.

    예외가 자기만료되지 않으면 억제 목록이 되고, 억제 목록은 시간이 지나면
    아무도 안 보는 사각지대가 된다 — 지금 매트릭스가 그랬던 것과 같은 실패다.
    """
    live = {v.key for v in _contrast_claims() + _gate_claims() + _delta_claims()}
    return sorted(k for k in KNOWN if k not in live)


# ── 사각지대 탐침 ────────────────────────────────────────────────────────────

#: 타입별 흔들기. 값이 바뀌었는데 표가 그대로면 그 키는 표에 안 보인다.
_MUTATE = {bool: lambda v: not v, int: lambda v: v + 7, float: lambda v: v + 0.11,
           str: lambda v: f'{v}_probe', set: lambda v: (v ^ {'R4'}) or {'R1'},
           dict: lambda v: {**v, '__probe__': 1}}


def blind_spots() -> list[str]:
    """조건표가 **못 보는 설정 키**. 이 키로 갈리는 두 태그는 표에서 같아 보인다.

    주장 검증기가 못 잡는 것을 잡는다 — 그쪽은 누군가 적어 둔 주장만 보지만, 여기는
    **아무도 모델링하지 않은 차원**을 찾는다. 새 설정 키가 생기면 다음 실행에서 걸린다.
    """
    keys = {k for c in ABLATION_CONFIGS.values() for k in c}
    blind = []
    for key in sorted(keys):
        if any(_moves(tag, cfg, key) for tag, cfg in ABLATION_CONFIGS.items()
               if key in cfg):
            continue
        blind.append(key)
    return blind


def _moves(tag: str, cfg: dict, key: str) -> bool:
    """이 키를 흔들면 조건표가 움직이는가."""
    from dashboard.series_view import _facts_of

    mutate = _MUTATE.get(type(cfg[key]))
    if mutate is None:
        return True                      # 흔드는 법을 모르면 사각지대로 몰지 않는다
    mutated = copy.deepcopy(cfg)
    try:
        mutated[key] = mutate(mutated[key])
        return _facts_of(tag, mutated) != _facts_of(tag, cfg)
    except Exception:
        return True                      # 터진다면 최소한 무시되지는 않는다
