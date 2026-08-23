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

REGISTRY_PATH = Path('experiments/BASELINES.json')


class BaselineMismatch(RuntimeError):
    """입력이 선언된 기준선의 것이 아니다. 기본값으로 뭉개지 않는다."""


def _load() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))


def file_digest(path: Path) -> str:
    """sha256 — 텍스트는 CRLF→LF 정규화 후, `.gz` 는 raw.

    개발 PC(CRLF)와 서버(LF)가 같은 파일을 다르게 쓴다 (experiments/README.md).
    정규화하지 않으면 기준선 검사가 플랫폼 차이로 오탐한다.
    """
    data = path.read_bytes()
    if path.suffix != '.gz':
        data = data.replace(b'\r\n', b'\n')
    return hashlib.sha256(data).hexdigest()


def list_baselines() -> list[str]:
    return sorted(_load()['baselines'])


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
    problems: list[str] = []
    files: dict[str, dict] = {}

    for rel in [Path(r).as_posix() for r in required]:
        p = Path(rel)
        expected = bl['inputs'].get(rel)
        if expected is None:
            problems.append(f'{rel}: 기준선 {name} 에 등록되지 않은 입력이다')
            continue
        if not p.exists():
            problems.append(f'{rel}: 파일 없음 (artifact_root={bl["artifact_root"]} 에서 회수하라)')
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
        'verified_at': datetime.now(timezone.utc).isoformat(),
        'inputs': files,
    }
