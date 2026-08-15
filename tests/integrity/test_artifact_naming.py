"""산출물 이름과 내용의 정합성 (설계메모 v3 §8 무결성 검사 3번).

이름은 약속이다. `F_pbr_ma200_n13.json` 은 "MA200 태그를 종목 수 13 으로 돌린 결과"라고
약속한다. 그 약속이 지켜지는지 **파일을 열어** 확인하는 검사다. 전략 로직은 계산하지 않는다.

지키려는 것 (전부 실제로 데인 것들):

1. **이름의 n 과 내용의 n 일치** — 2026-08-12, n=13 운영이 n=20 산출물을 읽어 라이브
   라이브 매니페스트의 회전율이 92.31% 여야 할 자리에 95.00% 로 기록됐다. 에러는 안 났다.
2. **접미사 없는 레거시 산출물의 처리를 명시** — 산출물 72개 중 `n_stocks` 필드를 가진
   건 4개뿐이다. "필드가 없으면 비교를 건너뛴다"로 짜면 **사고를 낸 파일들만 정확히
   빠져나간다.** 건너뛰지 말고 "기본값으로 간주한다"를 단언하고 목록을 남긴다.
3. **요약 파일은 태그가 아니다** — `summary*.json` 은 여러 태그를 묶은 집계라 태그
   목록에 섞이면 유령 태그가 생긴다. 제외 목록을 손으로 관리하지 않고 **구조로**
   판별한다 (태그 산출물은 `tag` 필드를 갖고, 요약은 안 갖는다).
4. **운영이 가리키는 산출물의 실재** — freeze_rebalance 의 DEFAULT_TAG·N_STOCKS 가
   조립하는 키의 파일이 실제로 있고 내용이 맞아야 한다. 폴백은 사고의 통로였다.

산출물은 git 미추적이라 개발 PC 에서는 skip 될 수 있다. **skip 은 통과가 아니다** —
판정은 산출물이 실재하는 서버에서 돌린 결과로 한다 (tests/integrity/README.md).
"""
from __future__ import annotations

import json
import re
import warnings
from pathlib import Path

import pytest

from backtest.canonical_state import _freeze_constants
from scripts.run_ablation import DEFAULT_N_STOCKS

ABLATION_DIR = Path('experiments/ablation')

#: 성과 JSON 이 아닌 부속 산출물. 이름만으로 배제할 수 있다.
SIDECAR_SUFFIXES = ('_holdings', '_periods', '_dist')

#: `{base}_n{K}` 명명 규약. 기본 종목 수는 접미사가 없다(레거시).
KEY_RE = re.compile(r'^(?P<base>.+)_n(?P<n>\d+)$')


def _artifact_paths() -> list[Path]:
    if not ABLATION_DIR.is_dir():
        return []
    return sorted(p for p in ABLATION_DIR.glob('*.json')
                  if not p.stem.endswith(SIDECAR_SUFFIXES))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def _tag_artifacts() -> list[tuple[Path, dict]]:
    """태그 성과 산출물만. 요약 파일은 `tag` 필드가 없다는 **구조**로 걸러낸다."""
    out = []
    for p in _artifact_paths():
        d = _load(p)
        if 'tag' in d:
            out.append((p, d))
    return out


def _expected_n(stem: str) -> int:
    """파일 이름이 약속하는 종목 수. 접미사가 없으면 기본값(레거시 규약)."""
    m = KEY_RE.match(stem)
    return int(m.group('n')) if m else DEFAULT_N_STOCKS


def _name_content_conflict(stem: str, d: dict) -> str | None:
    """이름이 약속한 종목 수와 내용이 어긋나면 사유, 아니면 None.

    검사 본체를 순수 함수로 떼어 둔 이유: 산출물이 없는 환경에서도 **검사가 실제로
    발화하는지** 증명할 수 있어야 한다. 안 걸리는 검사는 없느니만 못하다.
    """
    if 'n_stocks' not in d:
        return None                       # 레거시 — 검사 2번 소관
    promised = _expected_n(stem)
    if d['n_stocks'] == promised:
        return None
    return f'{stem}: 이름은 n={promised} 인데 내용은 n={d["n_stocks"]}'


requires_artifacts = pytest.mark.skipif(
    not _artifact_paths(),
    reason='experiments/ablation 산출물이 없다 (git 미추적). 서버에서 판정할 것.')


# ── 1. 이름의 n == 내용의 n ──────────────────────────────────────────────────

@requires_artifacts
def test_filename_n_matches_content_n():
    """`_n{K}` 를 달고 있으면 파일 안의 `n_stocks` 가 정확히 K 여야 한다.

    어긋나면 그 파일을 읽는 모든 소비처(대시보드·게이트·라이브 매니페스트)가 조용히
    다른 전략의 숫자를 쓰게 된다. turnover 는 종목 수 전이분까지 삼켜 특히 조용하다.
    """
    mismatched = [c for path, d in _tag_artifacts()
                  if (c := _name_content_conflict(path.stem, d))]
    assert not mismatched, '이름과 내용의 종목 수가 다르다:\n  ' + '\n  '.join(mismatched)


def test_the_check_actually_fires():
    """검사가 사고를 실제로 잡는지 — 2026-08-12 상황을 주입해 확인한다.

    산출물 유무와 무관하게 항상 돈다. 이 테스트가 없으면 위 검사가 조용히 무력해져도
    아무도 모른다 (필드 부재로 전건 skip 되는 경우가 실제로 그랬다).
    """
    # 사고 그 자체: n=13 운영이 접미사 없는 n=20 산출물을 집는 상황
    assert _name_content_conflict('F_pbr_ma200', {'n_stocks': 13}) is not None
    assert _name_content_conflict('F_pbr_ma200_n13', {'n_stocks': 20}) is not None
    # 정상은 통과해야 한다 (늑대소년 방지)
    assert _name_content_conflict('F_pbr_ma200_n13', {'n_stocks': 13}) is None
    assert _name_content_conflict('F_pbr_ma200', {'n_stocks': 20}) is None
    # `_n` 없는 이름은 오탐하지 않는다 (F_pbr_ma5_20 · signcount126 등)
    assert _name_content_conflict('F_pbr_ma5_20', {'n_stocks': 20}) is None
    assert _name_content_conflict('F_pbr_signcount126', {'n_stocks': 20}) is None


# ── 2. 레거시(필드 부재)를 건너뛰지 않는다 ───────────────────────────────────

@requires_artifacts
def test_legacy_artifacts_are_explicitly_assumed_default_n():
    """`n_stocks` 필드가 없는 산출물은 **기본값으로 간주한다**를 명시적으로 단언한다.

    v2 설계안대로 "필드가 없으면 비교 대상이 없으니 통과"로 짜면, 정작 2026-08-12
    사고를 낸 접미사 없는 파일들(`F_pbr_ma200.json` = 실제로는 n20)에서 검사가
    공허하게 통과한다. **잡으려던 대상만 빠져나가는 검사**가 된다.

    그래서 통과시키되 조용히 통과시키지 않는다: 간주 근거를 단언하고 목록을 warning
    으로 남긴다. 근본 해소는 run_ablation 이 `n_stocks` 를 항상 기록하는 것이다.
    """
    legacy = [p.name for p, d in _tag_artifacts() if 'n_stocks' not in d]
    if not legacy:
        return                            # 전부 기록됨 — 근본 해소된 상태

    # 레거시 규약: 접미사가 없는 이름은 기본 종목 수를 뜻한다. 이 전제가 깨지면
    # (예: 기본값이 13 으로 바뀌었는데 옛 파일이 그대로 남아 있으면) 간주 자체가 틀린다.
    assert DEFAULT_N_STOCKS == 20, (
        f'build_ablation_pipeline 기본 n 이 {DEFAULT_N_STOCKS} 로 바뀌었다. '
        f'접미사 없는 레거시 산출물 {len(legacy)}개는 여전히 n=20 산출물이므로 '
        f'"접미사 없음 = 기본값" 간주가 더 이상 성립하지 않는다. '
        f'해당 파일을 `_n20` 으로 개명하거나 재실행하라.')

    for name in legacy:
        assert KEY_RE.match(Path(name).stem) is None, (
            f'{name} 은 `_n` 접미사를 달고 있으면서 내용에 n_stocks 가 없다. '
            f'이름이 약속한 값을 검증할 수단이 없다 — 재실행해 기록하라.')

    warnings.warn(
        f'`n_stocks` 미기록 산출물 {len(legacy)}개를 n={DEFAULT_N_STOCKS} 로 간주한다 '
        f'(내용으로 검증 불가, 이름 규약에만 의존): {", ".join(sorted(legacy)[:5])}'
        + (' …' if len(legacy) > 5 else ''),
        UserWarning, stacklevel=2)


# ── 3. 요약 파일은 태그가 아니다 ─────────────────────────────────────────────

@requires_artifacts
def test_summary_files_are_not_mistaken_for_tags():
    """태그 목록을 파일 이름으로 긁으면 `summary` 가 태그로 잡힌다.

    제외 목록을 손으로 유지하면 새 요약 파일이 생길 때 또 새는다. 구조로 판별한다:
    태그 산출물은 `tag` 필드를 갖고, 묶음 요약은 `scenarios` 를 갖는다.
    """
    tag_stems = {p.stem for p, _ in _tag_artifacts()}
    for path in _artifact_paths():
        if path.stem in tag_stems:
            continue
        d = _load(path)
        assert 'scenarios' in d, (
            f'{path.name} 은 태그 산출물도(`tag` 없음) 묶음 요약도(`scenarios` 없음) '
            f'아니다. 스캐너가 이 파일을 어떻게 다뤄야 할지 정의되지 않았다.')


@requires_artifacts
def test_tag_field_equals_filename():
    """파일 안의 `tag` 는 파일 이름과 같아야 한다 (조회 키 = 파일 이름)."""
    wrong = [f'{p.name}: tag={d["tag"]!r}'
             for p, d in _tag_artifacts() if d['tag'] != p.stem]
    assert not wrong, '파일 이름과 내부 tag 가 다르다:\n  ' + '\n  '.join(wrong)


# ── 4. 운영이 가리키는 산출물 ────────────────────────────────────────────────

@requires_artifacts
def test_operating_artifact_exists_and_matches():
    """freeze_rebalance 가 조립하는 키의 산출물이 실재하고 내용이 맞아야 한다.

    이 검사가 곧 2026-08-12 사고의 직접 재발 방지다. 없으면 **없다고 보고**해야지
    기본 태그로 폴백하면 안 된다.
    """
    tag, n_stocks = _freeze_constants()
    path = ABLATION_DIR / f'{tag}_n{n_stocks}.json'
    assert path.exists(), (
        f'운영 설정({tag}, n={n_stocks})이 가리키는 산출물이 없다: {path}. '
        f'`run_ablation --tags {tag} --n-stocks {n_stocks}` 로 생성하라. '
        f'다른 n 의 산출물로 대신하지 마라.')
    d = _load(path)
    assert d.get('n_stocks') == n_stocks, (
        f'{path.name} 의 내용이 n={d.get("n_stocks")} 다. 운영은 n={n_stocks} 다.')
