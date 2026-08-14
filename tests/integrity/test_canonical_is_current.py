"""`docs/CANONICAL.md` 가 지금 산출물과 일치하는가 — 지도와 땅의 대조.

`make_canonical --check` 와 같은 판정을 테스트로 고정한 것이다. 손으로 실행해야만
도는 검사는 결국 안 돌게 된다.

**왜 이게 무결성 검사인가**: CANONICAL.md 는 산출물의 파생물이다. 산출물이 바뀌었는데
문서를 재생성하지 않으면, 문서는 **틀린 게 아니라 낡은** 상태가 된다 — 그리고 낡음은
읽는 사람에게 보이지 않는다. 2026-08-12 에 정확히 그렇게 데였다(이미 바뀐 순위를 인용해
논리를 세움). 산출 일자를 문서에 박아둔 것도, 이 검사도 같은 사고를 겨냥한다.

대시보드 배너 역시 같은 `collect()` 를 읽으므로, 이 검사가 통과하면 **문서·화면·산출물
셋이 같은 사실을 말한다.** 셋 중 하나만 갱신되는 상황이 바로 공용화로 없애려던 것이다.
"""
from __future__ import annotations

import pytest

from backtest.canonical_state import ABL_DIR, collect
from scripts.make_canonical import OUT_PATH, build


@pytest.mark.skipif(not ABL_DIR.is_dir() or not OUT_PATH.exists(),
                    reason='산출물 또는 docs/CANONICAL.md 가 없다.')
def test_canonical_matches_current_artifacts():
    text, _ = build()
    current = OUT_PATH.read_text(encoding='utf-8')
    assert current == text, (
        f'{OUT_PATH.name} 이 산출물과 어긋난다. 산출물이 바뀌었는데 문서를 재생성하지 '
        f'않았거나, 문서를 손으로 고쳤다. `python -m scripts.make_canonical` 을 실행하라.')


@pytest.mark.skipif(not ABL_DIR.is_dir(), reason='산출물이 없다.')
def test_banner_and_document_share_one_collector():
    """배너와 문서가 **같은 수집 결과**를 소비하는지 — 값이 아니라 경로를 확인한다.

    값을 비교하면 "우연히 같은 값"과 "같은 출처"를 구분하지 못한다. 여기서는 배너가
    import 하는 collect 가 문서 생성기가 쓰는 그 함수인지(동일 객체) 확인한다.
    누군가 대시보드에 사본을 만들면 여기서 걸린다.
    """
    from dashboard import canonical_banner
    from scripts import make_canonical

    assert canonical_banner.collect is collect
    assert make_canonical.collect is collect
