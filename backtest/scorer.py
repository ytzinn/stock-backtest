"""
밸류에이션 필터 + 저평가 랭킹. pipeline.score_and_rank() 와 동일 로직을 독립 함수로 제공.
단독 분석 스크립트나 Ablation 실험에서 pipeline 없이 직접 호출 가능.
"""
from __future__ import annotations

from datetime import date

from backtest.data_access import get_close_price, get_shares_outstanding
from backtest.interfaces import ValuationModel


def score_universe(
    universe:       list[str],
    rebalance_date: date,
    pit_series:     dict[str, list[dict]],
    conn,
    model:          ValuationModel,
    rim_threshold:  float = 0.05,
) -> list[dict]:
    """
    유니버스 종목에 대해 적정가 계산 → 밸류에이션 필터 → upside% 내림차순 반환.

    rim_threshold:
      price > fv × (1 + rim_threshold) 이면 고평가 제외.
      초기값 0.05 (+5% 여유). Bayesian 튜닝 범위 [-0.10, +0.20].
    """
    result = []
    for ticker in universe:
        pit0   = pit_series.get(ticker, [{}])[0]
        shares = get_shares_outstanding(conn, ticker, rebalance_date)
        fv     = model.fair_value(ticker, pit0, shares or 0, beta=1.0)
        price  = get_close_price(conn, ticker, rebalance_date)

        if fv is None or price is None or price <= 0:
            continue

        upside = (fv / price - 1) * 100

        if price > fv * (1 + rim_threshold):
            continue

        result.append({
            'ticker':     ticker,
            'upside_pct': upside,
            'model':      model.name,
            'fair_value': fv,
            'price':      price,
        })

    return sorted(result, key=lambda x: x['upside_pct'], reverse=True)
