"""
팩터 스크리닝 (v4.3 신규 → 2026-07-05 폐기, 미채택).

4개 팩터 합산 점수 기준 상위 top_pct 종목을 선별한다.
팩터: 매출YoY(rev_yoy) + 영업이익YoY(op_yoy) + GP/A(gpa) + 1/PBR(inv_pbr)

초기 가중치: rev_yoy=1/6, op_yoy=1/6, gpa=1/3, inv_pbr=1/3

2026-07-05: 단일팩터 진단 결과(SPEC_05 §11 STEP 3B) rev_yoy/op_yoy/gpa 프리필터가 RIM 알파를
구조적으로 훼손함을 확인해 채택 파이프라인(`backtest/configs/phase2_rim.py`)에서 제거.
이 클래스는 backtest/ablation.py의 E_*/G_* 시나리오·단일팩터 진단용으로만 남아있다.
"""
from datetime import date

from backtest.data_access import get_market_cap


class FactorScreener:
    """UniverseFilter Protocol 구현체."""

    def __init__(
        self,
        weights:  dict | None = None,   # None이면 동일가중
        top_pct:  float = 0.20,         # 상위 20% (Bayesian 튜닝: 10~40%)
    ):
        self.weights = weights or {
            'rev_yoy': 1 / 6,
            'op_yoy':  1 / 6,
            'gpa':     1 / 3,
            'inv_pbr': 1 / 3,
        }
        self.top_pct = top_pct

    def apply(
        self,
        tickers:        list[str],
        rebalance_date: date,
        pit_series:     dict[str, list[dict]],
        conn,
    ) -> tuple[list[str], dict]:
        selected_set = set(
            _factor_screening(tickers, rebalance_date, pit_series, conn, self.weights, self.top_pct)
        )
        rejected = {t: '팩터 스크리닝 하위' for t in tickers if t not in selected_set}
        passed   = [t for t in tickers if t in selected_set]
        return passed, rejected


def _factor_screening(
    universe:       list[str],
    rebalance_date: date,
    pit_series:     dict[str, list[dict]],
    conn,
    weights:        dict,
    top_pct:        float,
) -> list[str]:
    """
    4개 팩터 percentile rank 가중 합산 → 상위 top_pct 종목 반환.

    팩터 키 (영어, phase2_rim.py에서도 동일):
      rev_yoy — 매출액 YoY   (초기 1/6)
      op_yoy  — 영업이익 YoY (초기 1/6)
      gpa     — GP/A         (초기 1/3, Novy-Marx 2013)
      inv_pbr — 1/PBR        (초기 1/3)
    """
    raw = {
        ticker: _compute_factors(ticker, rebalance_date, pit_series.get(ticker, []), conn)
        for ticker in universe
    }

    scores: dict[str, float] = {t: 0.0 for t in universe}
    for factor in ('rev_yoy', 'op_yoy', 'gpa', 'inv_pbr'):
        vals   = {t: raw[t].get(factor) for t in universe}
        ranked = _percentile_rank(vals)
        for ticker in universe:
            scores[ticker] += weights[factor] * ranked.get(ticker, 0.0)

    n        = max(1, int(len(universe) * top_pct))
    selected = sorted(scores, key=scores.__getitem__, reverse=True)[:n]
    return selected


def _compute_factors(
    ticker:         str,
    rebalance_date: date,
    series:         list[dict],
    conn,
) -> dict:
    """단일 종목 팩터 원시값 계산. series = pit_series[ticker]."""
    pit      = series[0] if len(series) > 0 else {}
    pit_prev = series[1] if len(series) > 1 else {}

    cur_rev  = pit.get('매출액')
    cur_op   = pit.get('영업이익')
    prev_rev = pit_prev.get('매출액')
    prev_op  = pit_prev.get('영업이익')
    assets   = pit.get('자산총계')
    # 매출총이익: 매출총이익 계정 우선, 없으면 매출액 - 매출원가
    gross = pit.get('매출총이익')
    if gross is None and cur_rev is not None:
        cogs  = pit.get('매출원가')
        gross = (cur_rev - cogs) if cogs is not None else None

    # PBR: market_cap / equity — market_cap_history 사용
    mktcap = get_market_cap(conn, ticker, rebalance_date)
    equity = pit.get('자본총계')
    pbr    = (mktcap / equity) if (mktcap and equity and equity > 0) else None

    return {
        'rev_yoy': (cur_rev / prev_rev - 1) if (prev_rev and cur_rev is not None and prev_rev > 0) else None,
        'op_yoy':  (cur_op  / prev_op  - 1) if (prev_op  and cur_op  is not None and prev_op  > 0) else None,
        'gpa':     (gross / assets)          if (assets and gross is not None and assets > 0)       else None,
        'inv_pbr': (1.0 / pbr)               if (pbr and pbr > 0)                                   else None,
    }


def _percentile_rank(vals: dict[str, float | None]) -> dict[str, float]:
    """
    {ticker: value | None} → {ticker: percentile [0~1]}.
    None은 최하위(0) 처리. 동점은 평균 순위.
    """
    valid = [(t, v) for t, v in vals.items() if v is not None]
    valid.sort(key=lambda x: x[1])
    n = len(valid)
    if n == 0:
        return {t: 0.0 for t in vals}

    ranked: dict[str, float] = {t: 0.0 for t in vals}
    i = 0
    while i < n:
        j = i
        # 동점 구간 찾기
        while j < n - 1 and valid[j][1] == valid[j + 1][1]:
            j += 1
        avg_rank = (i + j) / 2
        for k in range(i, j + 1):
            ranked[valid[k][0]] = avg_rank / (n - 1) if n > 1 else 1.0
        i = j + 1

    return ranked
