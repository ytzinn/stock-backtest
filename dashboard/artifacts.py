"""ArtifactCatalog — "무슨 산출물이 존재하는가?"만 답하는 층.

이 층은 **비교하지 않는다.** 어떤 태그가 어느 축에 속하는지는 시리즈 등록 대장
(`dashboard/series.py`)가 소유한다. 여기는 존재 여부·조회 키·부속 파일·신선도만 안다.

## 왜 태그가 아니라 artifact_key 인가

`tag` 하나를 유일키로 쓰면 깨진다. `F_pbr_ma200` 은 종목 수 20짜리 결과와 13짜리 결과를
둘 다 가리킬 수 있고, 실제로 2026-08-12 에 n=13 운영이 n=20 산출물을 읽어 회전율이
92.31% 여야 할 자리에 95.00% 로 기록됐다. **파일 조회는 무조건 `artifact_key` 로 한다.**

## 존재 방식이 두 가지다

- `file` — `experiments/ablation/{key}.json` 이 실재한다 (72개).
- `summary` — 파일은 없고 `summary.json` 의 `scenarios` 에만 있다 (A/B/C 랜덤 4개).
  500회 반복의 분포 집계라 단일 실행 산출물이 없다. 화면에서 CAGR 대신 `median_cagr` 을
  쓰는 이유가 이것이다.

둘을 뭉개면 "파일이 없다"와 "원래 파일로 존재하지 않는다"를 구분할 수 없다.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

#: `{base}_n{K}` 명명 규약. `F_pbr_ma5_20`·`signcount126` 처럼 `_n` 없는 숫자는 안 걸린다.
_KEY_RE = re.compile(r'^(?P<base>.+)_n(?P<n>\d+)$')

ROOT = Path(__file__).resolve().parent.parent
ABL_DIR = ROOT / 'experiments/ablation'

#: 성과 JSON 이 아닌 부속 산출물.
SIDECARS = {'periods': '_periods.csv', 'holdings': '_holdings.json', 'dist': '_dist.csv'}
#: 부속 파일의 stem 접미사 (`F_pbr_ma200_holdings` 처럼 확장자를 뗀 모양).
SIDECAR_STEMS = tuple(s.rsplit('.', 1)[0] for s in SIDECARS.values())


@dataclass(frozen=True)
class ScenarioRef:
    """시리즈가 가리키는 하나의 시나리오. **조회는 artifact_key 로만 한다.**"""

    base_tag: str
    artifact_key: str
    # `hash=False` 가 필수다. frozen dataclass 는 __hash__ 를 자동 생성하는데 dict 필드가
    # 끼면 해싱 시점에 TypeError 로 터진다. Streamlit 은 위젯 옵션을 해싱하므로 이걸
    # 빼먹으면 ScenarioRef 를 셀렉트 옵션으로 쓰는 순간 **화면이 죽는다** (2026-08-14 발생).
    # 동등성 비교에는 남겨둔다 — eq 가 같으면 hash 대상 필드도 같으므로 계약은 지켜진다.
    params: dict = field(default_factory=dict, hash=False)
    label: str | None = None

    @classmethod
    def of(cls, base_tag: str, *, n_stocks: int | None = None,
           label: str | None = None) -> ScenarioRef:
        """명명 규약대로 키를 조립한다. 기본 종목 수는 접미사가 없다(레거시)."""
        key = f'{base_tag}_n{n_stocks}' if n_stocks is not None else base_tag
        params = {'n_stocks': n_stocks} if n_stocks is not None else {}
        return cls(base_tag=base_tag, artifact_key=key, params=params, label=label)

    @classmethod
    def from_key(cls, key: str, label: str | None = None) -> ScenarioRef:
        """이미 조립된 키를 되읽는다. `_n{K}` 가 있으면 base_tag 와 n 을 분리한다."""
        m = _KEY_RE.match(key)
        if not m:
            return cls(base_tag=key, artifact_key=key, params={}, label=label)
        return cls(base_tag=m.group('base'), artifact_key=key,
                   params={'n_stocks': int(m.group('n'))}, label=label)

    @property
    def display(self) -> str:
        return self.label or self.artifact_key


@dataclass(frozen=True)
class Artifact:
    """카탈로그 한 항목."""

    key: str
    source: str                     # 'file' | 'summary'
    path: Path | None               # source='summary' 면 None
    n_stocks: int | None            # 내용에서 읽은 값. 없으면 None (레거시 → 기본값 간주)
    generated_at: str | None
    n_periods: int | None
    sidecars: dict[str, Path]       # 실재하는 것만
    git_tracked: bool | None        # None = 판정 불가(git 없음). False 와 다르다.
    metrics: dict = field(default_factory=dict)   # 산출물이 기록한 지표 원본 (재계산 금지)

    @property
    def exists_local(self) -> bool:
        return self.source == 'summary' or (self.path is not None and self.path.exists())


@lru_cache(maxsize=1)
def _tracked_paths() -> frozenset[str] | None:
    """`git ls-files experiments` 한 번. 실패하면 **False 가 아니라 None** 을 뜻한다.

    산출물 상당수가 git 미추적이라(대용량 6개 파일군) "개발 PC 엔 없는데 서버엔 있다"가
    정상이다. 그 사실을 화면에서 설명하려면 추적 여부를 알아야 한다. 다만 git 이 없는
    환경에서 "미추적"으로 단정하면 거짓말이 된다.
    """
    try:
        out = subprocess.run(['git', 'ls-files', 'experiments'], cwd=ROOT,
                             capture_output=True, text=True, timeout=20, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return frozenset(out.stdout.splitlines())


def _tracked(path: Path) -> bool | None:
    tracked = _tracked_paths()
    if tracked is None:
        return None
    return path.relative_to(ROOT).as_posix() in tracked


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


class ArtifactCatalog:
    """존재하는 산출물의 목록. 조회는 artifact_key 로만."""

    def __init__(self, items: dict[str, Artifact]):
        self._items = items

    def __contains__(self, key: str) -> bool:
        return key in self._items

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self._items.values())

    def keys(self) -> list[str]:
        return sorted(self._items)

    def get(self, key: str) -> Artifact | None:
        return self._items.get(key)

    def require(self, key: str) -> Artifact:
        """없으면 **없다고 말한다.** 기본 태그로 폴백하지 않는다 — 그게 사고의 통로였다."""
        a = self._items.get(key)
        if a is None:
            raise KeyError(
                f'산출물 `{key}` 이 카탈로그에 없다. 이름이 비슷한 다른 키로 대신 읽지 '
                f'마라 — 종목 수가 다른 산출물을 집으면 조용히 틀린다.')
        return a


def build_catalog(abl_dir: Path | None = None) -> ArtifactCatalog:
    """`experiments/ablation/` 을 훑어 카탈로그를 만든다.

    요약 파일(`summary*.json`)은 태그가 아니다 — 제외 목록을 손으로 유지하면 새 요약이
    생길 때 또 샌다. **구조로 판별한다**: 태그 산출물은 `tag` 필드를 갖고 묶음 요약은
    `scenarios` 를 갖는다.
    """
    abl_dir = abl_dir or ABL_DIR
    items: dict[str, Artifact] = {}
    summary: dict = {}

    if not abl_dir.is_dir():
        return ArtifactCatalog(items)

    for path in sorted(abl_dir.glob('*.json')):
        if path.stem.endswith(SIDECAR_STEMS):
            continue
        d = _read_json(path)
        if 'tag' not in d:                       # 묶음 요약
            if 'scenarios' in d and path.stem == 'summary':
                summary = d
            continue
        sidecars = {name: p for name, suffix in SIDECARS.items()
                    if (p := abl_dir / f'{path.stem}{suffix}').exists()}
        items[path.stem] = Artifact(
            key=path.stem, source='file', path=path,
            n_stocks=d.get('n_stocks'),
            generated_at=d.get('run_at') or d.get('generated_at'),
            n_periods=d.get('n_periods'),
            sidecars=sidecars, git_tracked=_tracked(path), metrics=d)

    # 파일 없이 summary.json 에만 있는 시나리오 (A/B/C 랜덤 = 500회 분포 집계).
    for key, s in (summary.get('scenarios') or {}).items():
        if key in items:
            continue
        items[key] = Artifact(
            key=key, source='summary', path=None,
            n_stocks=s.get('n_stocks'),
            generated_at=s.get('run_at'),
            n_periods=s.get('n_periods'),
            sidecars={name: p for name, suffix in SIDECARS.items()
                      if (p := abl_dir / f'{key}{suffix}').exists()},
            git_tracked=None, metrics=s)

    return ArtifactCatalog(items)
