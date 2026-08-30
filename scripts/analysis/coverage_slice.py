"""
구간 절단 사전등록 — 커버리지 기반 구간 선택 (2026-08-24).

**이 모듈은 수익률을 읽지 않는다.** 입력은 계정 커버리지 매트릭스와 태그의 규칙
조합뿐이다. 선택 규칙을 결과와 분리해 두기 위한 것이므로, 여기에 수익률·CAGR·
게이트 판정을 끌어들이지 마라.

사전등록 기준 (docs/검토/2026.08.24._PREREG_PERIOD_TRUNCATION.md §0-1):
    각 태그는, 그 태그가 실제로 사용하는 안정성 규칙 **전부**의 판정가능 비율이
    임계값 이상인 **선택 앵커**의 구간만 사용한다.

구간 p = (anchor_i → anchor_{i+1}) 의 판정 근거는 **선택 시점** anchor_i 의
커버리지다. anchor_{i+1} 은 청산일일 뿐 종목 선택에 관여하지 않는다.
"""
from __future__ import annotations

import csv
from pathlib import Path

COVERAGE_CSV = Path(
    'experiments/analysis/2026.08.19._trade_halt_impact/account_coverage_matrix.csv'
)
PREREG_THRESHOLD = 0.50   # 사전등록 (2026-08-24) — 실행 후 수정 금지


def load_rule_coverage(path: Path = COVERAGE_CSV) -> dict[str, dict[str, float]]:
    """{앵커: {규칙: 판정가능_비율}} — `구분 == '규칙'` 행만."""
    out: dict[str, dict[str, float]] = {}
    with path.open(encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            if row['구분'] != '규칙':
                continue
            out.setdefault(row['구간'], {})[row['항목']] = float(row['판정가능_%']) / 100.0
    return out


def selected_anchors(rules: set[str], threshold: float = PREREG_THRESHOLD,
                     coverage: dict[str, dict[str, float]] | None = None) -> list[str]:
    """임계값을 통과한 선택 앵커 (오름차순).

    태그가 쓰는 규칙 중 **하나라도** 커버리지 행이 없으면 판정할 수 없으므로
    그 앵커는 제외한다 (조용히 통과시키지 않는다 — fail-closed).
    """
    cov = coverage if coverage is not None else load_rule_coverage()
    keep = []
    for anchor, per_rule in cov.items():
        if not rules <= set(per_rule):
            continue
        if min(per_rule[r] for r in rules) >= threshold:
            keep.append(anchor)
    return sorted(keep)


def contiguous_tail(selected: list[str], all_anchors: list[str]) -> list[str]:
    """복리 연쇄용 **연속** 앵커 구간 — 마지막 탈락 앵커 이후 전부.

    CAGR·MDD·Sharpe 는 NAV 경로를 잇는 지표라 구멍이 있으면 정의되지 않는다.
    구멍 앞을 버리고 뒤를 취한다 (뒤가 최신이고 데이터 지평 문제에서 자유롭다).
    """
    sel = set(selected)
    drop_idx = [i for i, a in enumerate(all_anchors) if a not in sel]
    start = (max(drop_idx) + 1) if drop_idx else 0
    return all_anchors[start:]


def threshold_sweep(rules: set[str], lo: int = 1, hi: int = 78,
                    coverage: dict[str, dict[str, float]] | None = None,
                    ) -> list[tuple[int, tuple[str, ...]]]:
    """임계값 1%~hi% 스윕 → [(임계값%, 선택 앵커 튜플)]."""
    cov = coverage if coverage is not None else load_rule_coverage()
    return [(t, tuple(selected_anchors(rules, t / 100.0, cov)))
            for t in range(lo, hi + 1)]
