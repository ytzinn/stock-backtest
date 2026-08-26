"""SPEC_15 — rank IC 의 순수 함수 (오라클 대상).

**Spearman = 순위에 대한 Pearson.** 치환 귀무분포는 구간 *내* 순위를 섞은 것이므로,
구간마다 y 순위를 한 번 표준화해 두면 IC 하나가 내적 한 번이 된다. B=1,000 × T=19 를
행렬 곱으로 처리하려고 이렇게 쓴다 — `scipy.stats.spearmanr` 를 19,000번 부르지 않는다.

`[Claude 의견]` 속도가 아니라 **검증 가능성** 때문에 분리한다. 이 파일의 함수들은 DB 도
패널도 모르므로, 오라클이 합성 입력으로 옳음을 고정할 수 있다.
"""
from __future__ import annotations

import numpy as np


def rankdata_average(x: np.ndarray) -> np.ndarray:
    """동점 평균 순위 (`scipy.stats.rankdata` 의 'average' 와 같은 규약).

    동점을 평균으로 처리하지 않으면 입력 순서가 IC 에 새어 들어간다 — 이 저장소가
    `_rank_key` 에서 한 번 겪은 유형이다 (CORR-SORT-001).
    """
    x = np.asarray(x, dtype=float)
    order = np.argsort(x, kind='mergesort')          # 안정 정렬
    ranks = np.empty(len(x), dtype=float)
    ranks[order] = np.arange(1, len(x) + 1, dtype=float)
    # 동점 구간을 평균으로 덮는다
    xs = x[order]
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j + 2) / 2.0
        i = j + 1
    return ranks


def _zrank(x: np.ndarray) -> np.ndarray:
    """순위를 중심화·정규화. 내적이 곧 상관이 되도록."""
    r = rankdata_average(x)
    r = r - r.mean()
    n = np.linalg.norm(r)
    return r / n if n > 0 else r


def rank_ic(signal: np.ndarray, fwd: np.ndarray) -> float:
    """한 구간의 Spearman rank IC."""
    if len(signal) < 2:
        return float('nan')
    return float(np.dot(_zrank(signal), _zrank(fwd)))


def ic_series(periods: list[tuple[np.ndarray, np.ndarray]]) -> np.ndarray:
    """구간별 IC 계열. `periods` = [(signal, fwd), ...] 시간순."""
    return np.array([rank_ic(s, f) for s, f in periods], dtype=float)


def permutation_mean_ic(periods: list[tuple[np.ndarray, np.ndarray]],
                        b: int, seed: int) -> np.ndarray:
    """구간 **내** 신호 치환의 mean(IC) 귀무분포. 길이 `b`.

    구간 간을 섞지 않는다 — 횡단면 구조(구간별 종목 수·수익률 분포)를 보존해야
    귀무가 의미를 갖는다. 구간마다 신호 순위를 독립으로 섞는다.
    """
    rng = np.random.default_rng(seed)
    acc = np.zeros(b, dtype=float)
    for sig, fwd in periods:
        n = len(sig)
        if n < 2:
            continue
        zy = _zrank(fwd)
        zs = _zrank(sig)                       # 치환은 순위의 재배열이므로 zs 를 섞으면 된다
        idx = np.argsort(rng.random((b, n)), axis=1, kind='stable')
        acc += (zs[idx] * zy).sum(axis=1)
    return acc / len(periods)


def synthetic_signal(fwd: np.ndarray, rho: float, rng: np.random.Generator) -> np.ndarray:
    """설계 rank IC ≈ `rho` 인 합성 신호.

    `fwd` 의 정규점수에 `rho` 만큼 실어 잡음을 섞는다. 정규점수 위에서 만든 선형 결합의
    Pearson 상관이 `rho` 이고, 단조변환에 불변인 Spearman 은 그 값에 가깝다
    — **가깝다는 것을 P-2 가 실측으로 확인한다** (설계값을 그대로 믿지 않는다).
    """
    n = len(fwd)
    y = _zrank(fwd) * np.sqrt(n)                       # 대략 표준화된 정규점수 대용
    e = rng.standard_normal(n)
    e = (e - e.mean()) / (e.std() or 1.0)
    return rho * y + np.sqrt(max(0.0, 1.0 - rho ** 2)) * e
