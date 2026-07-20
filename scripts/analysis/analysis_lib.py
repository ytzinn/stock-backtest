"""SPEC_11 분석 공용 순수 함수 — 스피어만 순위상관·Jaccard. oracle 테스트 대상."""
from __future__ import annotations

import math


def _average_ranks(values: list[float]) -> list[float]:
    """동률 평균 순위 (1-based)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float:
    """스피어만 순위상관 (동률 평균 순위 → 피어슨). scipy 미사용. n<2 또는 상수열이면 0.0."""
    if len(xs) != len(ys):
        raise ValueError(f'길이 불일치: {len(xs)} vs {len(ys)}')
    n = len(xs)
    if n < 2:
        return 0.0
    rx, ry = _average_ranks(xs), _average_ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sx  = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy  = math.sqrt(sum((b - my) ** 2 for b in ry))
    if sx == 0 or sy == 0:
        return 0.0
    return cov / (sx * sy)


def jaccard(a: set, b: set) -> float:
    """|A∩B| / |A∪B|. 둘 다 빈 집합이면 1.0 (동일 취급)."""
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)
