"""
RIM (Residual Income Model) 적정가 모델.

adjROE = (0.5×NI + 0.5×CFO) / equity — Dechow(1994) Method C, λ=0.5
FV_per_share = equity * adjROE / r / shares   (영구연금 가정, payout=0)

payout=0 가정: KOSDAQ 소형주 배당 데이터 누락 다수 → 낙관적 편향 허용 (설계서 §3-1).
β=1.0 고정: Phase 2~4. Phase 3 이후 rolling β 도입 검토.

constants: RF, RK — stability_filter.py와 반드시 동일 값 유지.
"""
from __future__ import annotations

RF, RK = 0.0263, 0.0873   # stock-analysis 기존값 유지


class RIMModel:
    """ValuationModel Protocol 구현체."""

    name = 'RIM'

    def __init__(self, beta_adj: float = 0.0):
        # beta_adj: r 오프셋 [-0.02, +0.02]. β=1.0 고정 유지하면서 r 수준만 미세 조정.
        # beta_adj < 0 → r 낙관적(할인율 낮음) → 적정가 상승
        # beta_adj > 0 → r 보수적 → 적정가 하락
        self.beta_adj = beta_adj

    def fair_value(
        self,
        ticker:   str,
        pit_data: dict,
        shares:   float,
        beta:     float = 1.0,
    ) -> float | None:
        """
        주당 적정가(KRW) 반환. 계산 불가 시 None.

        pit_data: pit_series[ticker][0] — 최신 FY flat dict
        shares:   상장주식수 (실제 주식 수)
        beta:     β=1.0 고정 (Phase 2~4). 인자 유지는 Protocol 호환성.
        """
        equity = pit_data.get('자본총계')
        ni     = pit_data.get('당기순이익')
        cfo    = pit_data.get('영업활동현금흐름')

        if equity is None or equity <= 0:
            return None
        if shares is None or shares <= 0:
            return None

        ni  = ni  if ni  is not None else 0.0
        cfo = cfo if cfo is not None else 0.0

        adj_roe = (0.5 * ni + 0.5 * cfo) / equity
        r       = RF + beta * (RK - RF) + self.beta_adj

        if r <= 0:
            return None

        # FV_equity = equity × (adjROE / r)  — 영구연금, payout=0 낙관적 편향
        fv_equity = equity * adj_roe / r
        if fv_equity <= 0:
            return None

        return fv_equity / shares


class _SkeletonModel:
    """Phase 5 멀티모델 skeleton. 구현 전까지 사용 금지."""

    name = 'SKELETON'

    def fair_value(self, ticker, pit_data, shares, beta=1.0):
        raise NotImplementedError('Phase 5 이후 구현 예정')
