"""
SPEC_10 §5-2 — 강건성 진단 공유 함수 (G3′ LOO · G4′ top-k 제거 · G6′ 부호검정).

전략(F_pbr_no_r3r4)과 랜덤 추첨(C_pbr_path_random) **양쪽에 동일 코드로 적용**한다
— 두 벌 구현 금지 (SPEC_10 R-3). 순수 함수 — oracle 테스트 대상.

용어:
  margin        = Π(1+r_A) − Π(1+r_B)  (동일 구간 집합이면 CAGR 대소와 동치 —
                  연수 항이 공통이라 총복리 비교로 충분, 문서화된 정의)
  period_stock  = {period_key: [(ticker, weight_eff, ret), ...]}  (유효비중 합 1.0)
"""
from __future__ import annotations

from math import comb


def compound(returns: list[float]) -> float:
    """총복리 배수 Π(1+r)."""
    total = 1.0
    for r in returns:
        total *= 1.0 + r
    return total


def margin(returns_a: list[float], returns_b: list[float]) -> float:
    """총복리 마진 (A − B). 동일 구간 집합 전제 — 길이 불일치는 정렬 결함이므로 예외."""
    if len(returns_a) != len(returns_b):
        raise ValueError(f'구간 수 불일치: A={len(returns_a)} B={len(returns_b)}')
    return compound(returns_a) - compound(returns_b)


def loo_reversal_count(returns_a: list[float], returns_b: list[float]) -> tuple[int, list[int]]:
    """
    G3′ 구간 의존도: Leave-one-period-out 시 전체 마진의 부호가 뒤집히는 구간 수.
    반환: (반전 구간 수, 반전 유발 구간 인덱스 목록). 전체 마진이 0이면 반전 정의
    불가 — (len, 전체) 반환 (최악 취급).
    """
    base = margin(returns_a, returns_b)
    if base == 0.0:
        return len(returns_a), list(range(len(returns_a)))
    flipped = []
    for i in range(len(returns_a)):
        a = returns_a[:i] + returns_a[i + 1:]
        b = returns_b[:i] + returns_b[i + 1:]
        if (margin(a, b) > 0) != (base > 0):
            flipped.append(i)
    return len(flipped), flipped


def total_contributions(period_stock: dict) -> dict[str, float]:
    """종목별 총 기여 = Σ_구간 (유효비중 × 구간수익률)."""
    out: dict[str, float] = {}
    for rows in period_stock.values():
        for ticker, w_eff, ret in rows:
            out[ticker] = out.get(ticker, 0.0) + w_eff * ret
    return out


def top_contributors(period_stock: dict, k: int) -> list[str]:
    """총 기여 상위 k 종목 (동률 시 ticker 오름차순 tie-break 고정)."""
    contrib = total_contributions(period_stock)
    return [t for t, _ in sorted(contrib.items(), key=lambda kv: (-kv[1], kv[0]))[:k]]


def remove_stocks_period_returns(period_stock: dict, removed: set[str]) -> list[float]:
    """
    지정 종목 제거 후 구간 수익률 재계산 — 잔여 종목 유효비중 재정규화
    (engine._aggregate_period_return의 재정규화 관례와 동일).
    한 구간의 전 종목이 제거되면 그 구간 0.0 (현금 가정).
    period_key 정렬 순서로 반환.
    """
    out = []
    for key in sorted(period_stock):
        rows = [(t, w, r) for t, w, r in period_stock[key] if t not in removed]
        total_w = sum(w for _, w, _ in rows)
        if total_w <= 0:
            out.append(0.0)
            continue
        out.append(sum((w / total_w) * r for _, w, r in rows))
    return out


def topk_removal_margin(period_stock_a: dict, period_stock_b: dict, k: int) -> float:
    """
    G4′ 종목 의존도: A·B **각자의** top-k 기여종목을 제거(양쪽 동일 처리)한 뒤의
    총복리 마진. 낮을수록 A의 우위가 소수 종목에 의존적.
    """
    a_rets = remove_stocks_period_returns(period_stock_a, set(top_contributors(period_stock_a, k)))
    b_rets = remove_stocks_period_returns(period_stock_b, set(top_contributors(period_stock_b, k)))
    return margin(a_rets, b_rets)


def sign_test(diffs: list[float]) -> tuple[int, int, float]:
    """
    G6′ 부호검정 (양측 이항, p=0.5). 반환: (양수 수, 유효 n(0 제외), p-값).
    scipy 미사용 — math.comb 직접 계산. n=20 검정력 낮음은 보고서에 명기.
    """
    nonzero = [d for d in diffs if d != 0.0]
    n = len(nonzero)
    if n == 0:
        return 0, 0, 1.0
    pos = sum(1 for d in nonzero if d > 0)
    tail = min(pos, n - pos)
    p = sum(comb(n, i) for i in range(tail + 1)) / 2 ** n * 2
    return pos, n, min(p, 1.0)


def percentile_below(value: float, null_values: list[float]) -> float:
    """value보다 **작은** 귀무표본 비율 (0~1). 동률은 절반 가중."""
    if not null_values:
        raise ValueError('귀무분포 비어 있음')
    below = sum(1 for v in null_values if v < value)
    ties  = sum(1 for v in null_values if v == value)
    return (below + 0.5 * ties) / len(null_values)
