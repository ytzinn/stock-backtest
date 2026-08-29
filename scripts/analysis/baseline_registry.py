"""
대조 기준선 등록부 소비기 (2026-08-24 신설).

**왜 있는가.** 2026-08-24 재슬라이스가 "기존 공표 수치"라는 모호한 표현 하나로
수정 전(운영) tape 를 읽고 수정 후(섀도우) 커버리지 매트릭스를 섞어 썼다.
양성 대조는 통과했다 — 같은 tape 안에서는 산식이 맞았기 때문이다.

    양성 대조는 **내부 정합성**을 검사한다. **출처**는 검사하지 않는다.
    두 축은 별개이고, 별개로 검사해야 한다.

그래서 이 모듈은 산식을 보지 않는다. **입력 파일이 선언된 기준선의 것인지**만
본다. 하나라도 어긋나면 예외를 던진다 (fail-closed — 조용한 통과 금지).

등록부: `experiments/BASELINES.json`.
기준선은 네 가지를 반드시 명시한다 — **branch / db_port / artifact_root / commit**.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.analysis import generation

REGISTRY_PATH = Path('experiments/BASELINES.json')


class BaselineMismatch(RuntimeError):
    """입력이 선언된 기준선의 것이 아니다. 기본값으로 뭉개지 않는다."""


def _load() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))


def _decodes_as_utf8(data: bytes) -> bool:
    try:
        data.decode('utf-8')
    except UnicodeDecodeError:
        return False
    return True


def _looks_like_text(data: bytes) -> bool:
    """이 바이트열이 텍스트인가. **확장자를 보지 않는다 — 내용을 본다.**

    `[검증된 사실]` 종전에는 `path.suffix != '.gz'` 로 갈랐다. 그래서 이진인
    `.parquet` 5종이 CRLF 정규화를 맞았고, 그 파일들은 실제로 `\r\n` 바이트쌍을
    16~40개씩 갖고 있다. 등록부에는 **raw sha256** 으로 등재돼 있어(`_added_20260826_panel`
    이 그렇게 선언했다) `file_digest` 가 그 등재값을 **재현하지 못했다**
    (2026-08-29 세대 판별자 도입 중 발견).

    확장자 목록으로 하는 판별은 확장자가 늘 때마다 뒤처진다 — 이 저장소가 표면
    지문으로 종류를 판정했다가 틀린 네 번째 사례였다.
    """
    return b'\x00' not in data and _decodes_as_utf8(data)


def file_digest(path: Path) -> str:
    """sha256 — **텍스트만** CRLF→LF 정규화 후, 이진은 raw.

    개발 PC(CRLF)와 서버(LF)가 같은 파일을 다르게 쓴다 (experiments/README.md).
    정규화하지 않으면 기준선 검사가 플랫폼 차이로 오탐한다. 반대로 이진 파일을
    정규화하면 내용을 훼손한 해시가 나온다 — 텍스트·이진 판정은 `_looks_like_text`
    가 **내용으로** 한다.
    """
    data = path.read_bytes()
    if _looks_like_text(data):
        data = data.replace(b'\r\n', b'\n')
    return hashlib.sha256(data).hexdigest()


def list_baselines() -> list[str]:
    return sorted(_load()['baselines'])


def poisoned_summaries() -> set[str]:
    """폐기 세대로 **기록된 경로** — 참고용이다. **판정에 쓰지 마라.**

    `[검증된 사실]` 이 집합은 2026-08-29 까지 곧 판정이었고, 그것이 틀렸다.
    같은 경로에 조립분이 앉을 수 있고(거짓 음성), 등재 섀도우본은 폐기본과 상대경로
    문자열이 같다(거짓 양성). 판정은 `generation_of()` 가 **내용으로** 한다.
    """
    return {Path(p).as_posix() for p in _load()['_poisoned_summaries']['paths']}


def poisoned_digests() -> dict[str, dict]:
    """폐기 세대의 **양성 식별자** — sha256 → 기록 메타. 판별자 D2 의 근거."""
    return dict((_load()['_poisoned_summaries'].get('digests') or {}))


def generation_of(path: str | Path) -> generation.Verdict:
    """파일 하나의 세대 판정. **경로가 아니라 내용으로 본다.**

    판정 논리는 `scripts/analysis/generation` 소유다 — 등록부와 해시 함수를 주입한다.
    """
    return generation.classify(Path(path), registry=_load(), digest_fn=file_digest)


def generation_of_entry(path: str | Path, key: str) -> generation.Verdict:
    """컨테이너 **항목 하나**의 세대. 파일이 `mixed` 여도 그 항목은 깨끗할 수 있다."""
    return generation.entry_classify(Path(path), key, registry=_load(),
                                     digest_fn=file_digest)


def assert_not_poisoned(*paths: str | Path, root: str | Path = '.') -> None:
    """폐기 세대(또는 **세대를 확정할 수 없는 것**)를 근거로 쓰려 하면 거부한다.

    `[검증된 사실]` 종전에는 경로 문자열 비교였다. 2026-08-29 조립 전환이 폐기본
    경로에 조립분을 앉히면서, 경로로는 두 세대가 구분되지 않게 됐다 —
    `run_ablation` 을 한 번 돌리면 같은 경로가 조용히 다시 폐기 세대가 된다.

    이제 `generation_of()` 로 **내용을 보고** 판정하며, `registered`·`composed` 만
    통과시킨다. **`unknown` 은 막는다** (fail-closed) — "모르면 깨끗함"은 이 저장소가
    반복해 잡은 fail-open 의 다음 사례가 된다.

    `root` — 상대경로를 어디에 붙여 열 것인가. 기준선 입력은 `local_root` 아래에
    있으므로 등재 키와 디스크 경로가 다르다. 이것을 안 넘겨서 등재 섀도우본이
    폐기본으로 오판되는 것이 종전 구현의 거짓 양성이었다.

    예외를 경고로 낮추지 마라 — 그러면 봉쇄가 아니다.
    """
    bad = []
    for p in paths:
        full = Path(root) / Path(p)
        v = generation_of(full)
        if not v.clean:
            bad.append(f'{Path(p).as_posix()} [{v.verdict}] {v.why}')
    if bad:
        meta = _load()['_poisoned_summaries']
        raise BaselineMismatch(
            f'{meta["issue"]}: 세대가 확인되지 않은 산출물을 근거로 쓸 수 없다 '
            f'(registered·composed 만 통과):\n  ' + '\n  '.join(bad) +
            '\n지표는 tape 에서 재산출하거나, 조립 산출물의 지문을 채워라.')


def local_root(name: str) -> Path:
    """개발 PC 에서 그 기준선의 파일이 놓인 루트."""
    return Path(resolve(name).get('local_root', '.'))


# 공표 형식 -> 반올림 반폭. **지표마다 다르다.**
# 백분율로 공표된 값과 비율로 공표된 값에 같은 허용오차를 쓰면 후자가 오탐한다
# (2026-08-24 net_sharpe 사고). 일괄값으로 되돌리지 마라.
_ROUNDING_HALF_WIDTH = {
    'percent_4dp': 5e-7,    # '15.5640%'  -> 0.00005%p = 5e-7 (분수)
    'percent_2dp': 5e-5,    # '15.56%'
    'percent_1dp': 5e-4,    # '93.4%'
    'ratio_4dp':   5e-5,    # '0.5025'
    'ratio_3dp':   5e-4,    # '0.502'
    'exact':       0.0,     # 정수 (풀 크기 등)
}


def expected_metrics(name: str) -> dict:
    """공표 기준값 + 그 정밀도에 맞는 허용오차."""
    return resolve(name)['expected_metrics']


def metric_tolerance(exp: dict, key: str) -> float:
    """지표 하나의 허용오차 — 참조값이 어떤 형식으로 공표됐는지에서 유도한다.

    `_tolerance_mode == 'full'` 이면 전정밀 참조값이므로 1e-12.
    `'published'` 면 `_published_as[key]` 의 반올림 반폭.
    등록되지 않은 지표는 **예외를 던진다** — 조용히 관대한 기본값을 주면
    검사기가 아니게 된다.
    """
    if exp.get('_tolerance_mode') == 'full':
        return 1e-12
    form = exp.get('_published_as', {}).get(key)
    if form is None:
        raise BaselineMismatch(
            f'지표 {key!r} 의 공표 형식이 등록부에 없다 — 허용오차를 정할 수 없다. '
            f'experiments/BASELINES.json 의 `_published_as` 에 추가하라.')
    if form not in _ROUNDING_HALF_WIDTH:
        raise BaselineMismatch(
            f'알 수 없는 공표 형식 {form!r} (지표 {key}). '
            f'등록된 형식: {sorted(_ROUNDING_HALF_WIDTH)}')
    return _ROUNDING_HALF_WIDTH[form]


def required_inputs(name: str) -> list[str]:
    return list(resolve(name)['required_inputs'])


def resolve(name: str) -> dict:
    reg = _load()
    if name not in reg['baselines']:
        raise BaselineMismatch(
            f'기준선 {name!r} 이 등록부에 없다. 등록된 것: {sorted(reg["baselines"])}. '
            f'새 재실행이면 experiments/BASELINES.json 에 **추가**하라 '
            f'(기존 항목의 해시를 덮어쓰지 마라).')
    return reg['baselines'][name]


def verify(name: str, required: list[str], extra: list[str] | None = None) -> dict:
    """선언된 기준선에 대해 입력 파일을 전수 대조한다.

    `required` — 이 기준선에 등록돼 있어야 하는 상대경로.
    `extra`    — 기준선 밖 공용 입력. 등록부의 `compatible_baselines` 로 판정한다.

    반환: 결과에 그대로 실을 provenance 블록.
    실패하면 `BaselineMismatch`. **경고로 낮추지 마라** — 이 검사기가 느슨해지면
    2026-08-24 사고가 그대로 재발한다.
    """
    reg = _load()
    bl = resolve(name)
    root = Path(bl.get('local_root', '.'))
    problems: list[str] = []
    files: dict[str, dict] = {}

    # 경로를 **해석한 뒤** 세대를 본다. 종전에는 상대경로 문자열만 넘겨서, 등재
    # 섀도우본(local_root 아래)이 루트의 폐기본과 같은 문자열이라는 이유로 막힐 수
    # 있었다 — 경로가 정체성이 아니라는 이 절의 요지가 그대로 적용된다.
    assert_not_poisoned(*required, root=root)
    assert_not_poisoned(*(extra or []))

    for rel in [Path(r).as_posix() for r in required]:
        p = root / rel
        expected = bl['inputs'].get(rel)
        if expected is None:
            problems.append(f'{rel}: 기준선 {name} 에 등록되지 않은 입력이다')
            continue
        if not p.exists():
            problems.append(
                f'{rel}: 파일 없음 — {p} 에 없다. '
                f'{bl["artifact_root"]}/… 에서 {root}/experiments/… 로 회수하라')
            continue
        actual = file_digest(p)
        files[rel] = {'sha256': actual, 'size': p.stat().st_size,
                      'mtime': datetime.fromtimestamp(p.stat().st_mtime,
                                                      timezone.utc).isoformat()}
        if actual != expected:
            problems.append(
                f'{rel}: sha256 불일치 — 기대 {expected[:16]}… 실제 {actual[:16]}… '
                f'(다른 기준선의 파일일 수 있다)')

    for rel in [Path(r).as_posix() for r in (extra or [])]:
        p = Path(rel)
        spec = reg.get('shared_inputs', {}).get(rel)
        if spec is None:
            problems.append(f'{rel}: 공용 입력으로 등록되지 않았다')
            continue
        if name not in spec['compatible_baselines']:
            problems.append(
                f'{rel}: 기준선 {name} 과 호환되지 않는다 '
                f'(호환: {spec["compatible_baselines"]}) — **기준선 혼입**')
        if not p.exists():
            problems.append(f'{rel}: 파일 없음')
            continue
        actual = file_digest(p)
        files[rel] = {'sha256': actual, 'size': p.stat().st_size,
                      'mtime': datetime.fromtimestamp(p.stat().st_mtime,
                                                      timezone.utc).isoformat(),
                      'shared_input': True}
        if actual != spec['sha256']:
            problems.append(f'{rel}: sha256 불일치 — 기대 {spec["sha256"][:16]}… '
                            f'실제 {actual[:16]}…')

    if problems:
        raise BaselineMismatch(
            '기준선 검사 실패 — 결과를 산출하지 않는다:\n  ' + '\n  '.join(problems))

    return {
        'baseline': name,
        'label': bl['label'],
        'branch': bl['branch'],
        'db_port': bl['db_port'],
        'artifact_root': bl['artifact_root'],
        'commit': bl['commit'],
        'valid_for_official_numbers': bl['valid_for_official_numbers'],
        'local_root': root.as_posix(),
        'verified_at': datetime.now(timezone.utc).isoformat(),
        'inputs': files,
    }
