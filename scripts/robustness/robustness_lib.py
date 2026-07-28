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


# ── paired moving block bootstrap (SPEC_13 §9-3a 사전등록) ────────────────────

def paired_block_bootstrap_cagr_diff(
    monthly_a:    list[float],
    monthly_b:    list[float],
    *,
    block_months: int = 12,
    n_resamples:  int = 10_000,
    seed:         int = 13,
    months_per_year: float = 12.0,
) -> dict:
    """후보(A) − 인컴번트(B) ΔCAGR 의 paired moving block bootstrap (SPEC_13 §9-3a).

    안 A/안 C와 인컴번트는 **앵커 교집합이 0** 이라 구간 단위 페어링이 정의 불가하므로,
    공통 시간축인 **월간 수익률**에서 정의한다. 두 시리즈를 **같은 블록 인덱스로 동시
    추출**해 paired 구조(동일 기간 대응)를 보존한다 — 각자 독립 리샘플하면 짝이 깨져
    차이의 분산이 과대추정된다.

    겹침 허용 moving block: 시작점을 0..n-block 에서 균등 추출해 길이 block 블록을
    이어붙이고 **원 길이 n 으로 절단**한다(마지막 블록 초과분 버림 — 표본 길이 불변).

    반환 dict: delta_cagr_point(원표본 ΔCAGR), p_gt_0, p_gt_delta(δ=0.1%p 초과 확률),
      ci_low/ci_high(2.5/97.5 백분위), n_months, n_resamples, block_months, seed.
    """
    import numpy as np

    if len(monthly_a) != len(monthly_b):
        raise ValueError(f'월 수 불일치: A={len(monthly_a)} B={len(monthly_b)} — paired 전제 위반')
    n = len(monthly_a)
    if n < block_months:
        raise ValueError(f'표본 월 수({n})가 블록 길이({block_months})보다 짧다')

    a = np.asarray(monthly_a, dtype=float)
    b = np.asarray(monthly_b, dtype=float)

    def _cagr(x: np.ndarray) -> float:
        return float(np.prod(1.0 + x) ** (months_per_year / len(x)) - 1.0)

    point = _cagr(a) - _cagr(b)

    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block_months))
    max_start = n - block_months            # 겹침 허용 시작점 상한 (inclusive)

    diffs = np.empty(n_resamples, dtype=float)
    for i in range(n_resamples):
        starts = rng.integers(0, max_start + 1, size=n_blocks)
        idx = np.concatenate([np.arange(s, s + block_months) for s in starts])[:n]
        diffs[i] = _cagr(a[idx]) - _cagr(b[idx])

    return {
        'delta_cagr_point': point,
        'p_gt_0':           float(np.mean(diffs > 0.0)),
        'p_gt_delta':       float(np.mean(diffs > 0.001)),   # δ = +0.1%p
        'ci_low':           float(np.percentile(diffs, 2.5)),
        'ci_high':          float(np.percentile(diffs, 97.5)),
        'n_months':         n,
        'n_resamples':      n_resamples,
        'block_months':     block_months,
        'seed':             seed,
    }
