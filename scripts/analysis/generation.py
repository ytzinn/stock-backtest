"""산출물의 **세대**를 내용으로 판정한다 — 경로로 판정하지 않는다.

## 왜 있는가

`[검증된 사실]` `_poisoned_summaries` 는 **경로 집합**이었다. 그런데 2026-08-29 전환이
같은 경로에 조립분을 앉혔고, `run_ablation` 을 한 번 돌리면 같은 경로에 폐기 세대가
다시 앉는다. **경로는 정체성이 아니다** — 그것이 이 결함의 전부다.

경로 기반 가드는 **두 방향 모두** 틀린다:

- 거짓 음성: 조립분 경로에 `run_ablation` 산출이 다시 앉아도 못 잡는다
- 거짓 양성: 등재 섀도우본(`_baselines/shadow_20260819/experiments/daily_nav/summary.json`)은
  폐기본과 **상대경로 문자열이 같다**. 경로로 보면 둘이 구분되지 않는다

`[Claude 의견]` 이 저장소는 표면 지문으로 종류를 판정했다가 **세 번** 틀렸다
(H1 결측 서명 오독 · `amount` 변동을 정정으로 오독 · 파일명 접미사로 계열 오독).
세 번 다 내용 기반 판별자로 고쳐졌다. 네 번째를 만들지 않는다.

## 판별 규약

**단일 판별자에 의존하지 않는다.** 순서대로 보고, 먼저 확정되는 것을 쓴다:

| id | 판별자 | 확정하는 것 |
|---|---|---|
| `D1` | 등재부에 있는 sha256 과 일치 | `registered` — 형식 무관(csv·gz 포함) |
| `D2` | 폐기 세대로 **기록된** sha256 과 일치 | `poisoned` — 양성 식별 |
| `D3` | 생성기 지문 (`run_at` / `composed_from`+`method`) | `poisoned` / `composed` |
| `D4` | `provenance` 가 등재 기준선을 가리키고 입력 sha 가 전부 일치 | `composed` |

컨테이너(`tags`·`scenarios`)는 **항목별로** 본다. 전부 깨끗해야 파일이 깨끗하다.
일부만 깨끗하면 `mixed` 이고, `mixed` 는 **깨끗하지 않다.**

## fail-closed

    판별 불가 · 필드 없음 · 형식 불명  →  `unknown`  →  **깨끗하지 않다**

`[Claude 의견]` 반대로 하면(모르면 깨끗함) 이 저장소가 반복해 잡은 fail-open 의 다음
사례가 된다. **미분류가 불편하다고 판별자를 느슨하게 하지 마라 — 파일에 필드를 채워라.**

## 이 모듈이 지키는 자기 규칙

**원칙을 강제하는 도구는 그 원칙의 첫 번째 적용 대상이다.** 이 모듈은 경로 문자열로
어떤 판정도 하지 않으며, `tests/oracle/test_generation_oracle.py` 가 AST 로 강제한다
(`endswith`·`startswith`·`fnmatch`·`glob` 호출 0건). 경로는 **파일을 여는 데만** 쓴다.

등록부·해시 함수를 import 하지 않고 **주입받는다.** 순환 import 를 피하려는 것이기도
하지만, 더 큰 이유는 이 판정 논리를 등록부 없이 단위 시험할 수 있게 하려는 것이다.
"""
from __future__ import annotations

import json
from pathlib import Path

#: 깨끗하다고 인정하는 판정. 나머지는 전부 막는다.
CLEAN = ('registered', 'composed')

#: `run_ablation`·`run_daily_nav` 계열 산출물의 지문. 조립기는 이 필드를 쓰지 않는다.
_GENERATOR_STAMP = 'run_at'
#: 조립기가 남기는 지문. 둘 다 있어야 조립분으로 인정한다.
_COMPOSED_STAMPS = ('composed_from', 'method')
#: 항목 컨테이너. 안에 세대가 섞일 수 있어 항목별로 판정한다.
_CONTAINERS = ('tags', 'scenarios')


class Verdict:
    """판정 하나. `verdict` 와 **무엇이 그렇게 판정했는지**(`by`)를 함께 들고 다닌다.

    근거 없는 판정은 나중에 리팩터링할 때 커버리지가 조용히 사라진다 — 어느 판별자가
    잡고 있었는지 모르면 그 판별자를 지워도 아무도 모른다.
    """

    __slots__ = ('verdict', 'why', 'by')

    def __init__(self, verdict: str, why: str, by: list[str]):
        self.verdict, self.why, self.by = verdict, why, list(by)

    @property
    def clean(self) -> bool:
        return self.verdict in CLEAN

    def __repr__(self) -> str:
        return f'Verdict({self.verdict!r}, by={self.by}, why={self.why!r})'


def _load_json(path: Path):
    """JSON 이면 파싱값, 아니면 `None`. **확장자를 보지 않는다** — 열어 본다."""
    try:
        return json.loads(path.read_bytes().decode('utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _has_generator_stamp(obj) -> bool:
    """`run_ablation` 계열 지문이 어디든 있는가. 컨테이너 항목 안까지 본다."""
    if not isinstance(obj, dict):
        return False
    if _GENERATOR_STAMP in obj:
        return True
    for c in _CONTAINERS:
        for entry in (obj.get(c) or {}).values():
            if isinstance(entry, dict) and _GENERATOR_STAMP in entry:
                return True
    return False


def _is_composed(obj) -> bool:
    return isinstance(obj, dict) and all(s in obj for s in _COMPOSED_STAMPS)


def _provenance_is_registered(obj, registry: dict) -> bool:
    """`provenance` 가 등재 기준선을 가리키고, **기록된 입력 sha 가 전부 등재값과 같은가.**

    출처 선언만 보는 것이 아니라 그 선언이 등록부와 맞는지까지 본다 — 선언은 베낄 수
    있어도 sha 는 못 베낀다.
    """
    prov = (obj or {}).get('provenance') if isinstance(obj, dict) else None
    if not isinstance(prov, dict):
        return False
    bl = (registry.get('baselines') or {}).get(prov.get('baseline'))
    if bl is None:
        return False
    shared = registry.get('shared_inputs') or {}
    inputs = prov.get('inputs') or {}
    if not inputs:
        return False
    for rel, meta in inputs.items():
        got = (meta or {}).get('sha256')
        want = (bl.get('inputs') or {}).get(rel)
        if want is None:
            want = (shared.get(rel) or {}).get('sha256')
        if want is None or got != want:
            return False
    return True


def classify(path: Path, *, registry: dict, digest_fn,
             _depth: int = 0) -> Verdict:
    """파일 하나의 세대 판정. **경로 문자열은 쓰지 않는다** — 연 내용만 본다.

    `registry`  — `BASELINES.json` 을 읽은 dict
    `digest_fn` — 경로 → sha256 (등록부와 같은 정규화 규약을 쓰는 함수)
    """
    if not path.exists():
        return Verdict('unknown', '파일이 없다', [])

    by: list[str] = []

    # ── D1: 등재된 sha256 인가 ───────────────────────────────────────────────
    sha = digest_fn(path)
    where = _registered_where(sha, registry)
    if where is not None:
        return Verdict('registered', f'등재부의 {where} 와 sha256 일치', ['D1'])
    by.append('D1(불일치)')

    # ── D2: 폐기 세대로 **기록된** sha256 인가 (양성 식별) ────────────────────
    rec = (registry.get('_poisoned_summaries') or {}).get('digests') or {}
    if sha in rec:
        meta = rec[sha]
        return Verdict(
            'poisoned',
            f'폐기 세대로 기록된 sha256 ({meta.get("generation")} 세대, '
            f'{meta.get("recorded_as")} 로 기록)', by + ['D2'])
    by.append('D2(불일치)')

    obj = _load_json(path)
    if obj is None:
        return Verdict('unknown', 'JSON 이 아니거나 읽을 수 없다 — 내용으로 세대를 '
                                  '말할 수 없다', by)

    # ── D3: 생성기 지문 ─────────────────────────────────────────────────────
    container = _container_entries(obj)
    if container is not None:
        return _classify_container(container, registry, digest_fn, by)

    if _has_generator_stamp(obj):
        # **`poisoned` 라고 부르지 않는다.** 값이 폐기 세대라는 뜻이 아니라, 등재되지도
        # 조립되지도 않은 생산 산출물이라는 뜻이다 — 값이 맞아도 출처가 고정돼 있지
        # 않으면 공식 수치의 근거가 될 수 없다. 이름을 뭉개면 다음 사람이 오해한다.
        return Verdict('unregistered',
                       f'{_GENERATOR_STAMP!r} 지문 — run_ablation 계열 산출물이고 '
                       f'등재본도 기록된 폐기본도 아니다 (값의 옳고 그름과 무관하다)',
                       by + ['D3'])
    if _is_composed(obj):
        return Verdict('composed',
                       f'{list(_COMPOSED_STAMPS)} 지문 — 조립 산출물', by + ['D3'])
    by.append('D3(지문 없음)')

    # ── D4: provenance 가 등재 기준선을 가리키는가 ──────────────────────────
    if _provenance_is_registered(obj, registry):
        return Verdict('composed',
                       'provenance 가 등재 기준선을 가리키고 입력 sha 가 전부 일치',
                       by + ['D4'])

    return Verdict('unknown',
                   '어느 판별자도 세대를 확정하지 못했다 — fail-closed 로 막는다',
                   by + ['D4(불일치)'])


def _registered_where(sha: str, registry: dict) -> str | None:
    """이 sha256 이 등록부 어디에 있는가. 없으면 `None`."""
    for rel, spec in (registry.get('shared_inputs') or {}).items():
        if isinstance(spec, dict) and spec.get('sha256') == sha:
            return f'shared_inputs[{rel}]'
    for name, bl in (registry.get('baselines') or {}).items():
        for rel, want in (bl.get('inputs') or {}).items():
            if want == sha:
                return f'baselines[{name}].inputs[{rel}]'
    return None


def _container_entries(obj) -> dict | None:
    """항목 컨테이너면 {키: 항목}, 아니면 `None`."""
    for c in _CONTAINERS:
        entries = obj.get(c)
        if isinstance(entries, dict) and entries:
            return entries
    return None


def _classify_container(entries: dict, registry: dict, digest_fn,
                        by: list[str]) -> Verdict:
    """컨테이너는 **항목별로** 본다. 전부 깨끗해야 파일이 깨끗하다.

    한 파일 안에 세대가 섞일 수 있다는 것이 `daily_nav/summary.json` 에서 실제로
    일어났다 — 현행 채택안 태그만 조립분이고 나머지는 폐기 세대다.
    """
    clean, dirty = [], []
    for key, entry in entries.items():
        if isinstance(entry, dict) and _is_composed(entry):
            clean.append(key)
        else:
            dirty.append(key)
    if not dirty:
        return Verdict('composed', f'컨테이너 항목 {len(clean)}개 전부 조립분',
                       by + ['D3(컨테이너)'])
    if clean:
        return Verdict(
            'mixed',
            f'컨테이너에 세대가 섞였다 — 조립분 {clean}, 나머지 {len(dirty)}개는 '
            f'조립 지문이 없다', by + ['D3(컨테이너)'])
    return Verdict('poisoned',
                   f'컨테이너 항목 {len(dirty)}개 전부 조립 지문이 없다',
                   by + ['D3(컨테이너)'])


def entry_classify(path: Path, key: str, *, registry: dict, digest_fn) -> Verdict:
    """컨테이너 **항목 하나**의 세대. 한 키만 읽는 소비처를 위한 것이다.

    파일 전체가 `mixed` 여도 소비처가 읽는 항목이 조립분이면 그 소비는 안전하다.
    파일 단위 판정으로 뭉개면 그 사실이 사라진다.
    """
    obj = _load_json(path)
    entries = _container_entries(obj) if isinstance(obj, dict) else None
    if entries is None or key not in entries:
        return Verdict('unknown', f'{key!r} 항목이 없다', [])
    entry = entries[key]
    if _is_composed(entry):
        return Verdict('composed', f'{key!r} 에 조립 지문이 있다', ['D3(항목)'])
    if isinstance(entry, dict) and _GENERATOR_STAMP in entry:
        return Verdict('poisoned', f'{key!r} 에 {_GENERATOR_STAMP!r} 지문이 있다',
                       ['D3(항목)'])
    return Verdict('unknown', f'{key!r} 의 세대를 확정할 수 없다', ['D3(항목)'])
