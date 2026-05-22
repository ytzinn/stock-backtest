"""
RIM (Residual Income Model) 적정가 모델.

adjROE = (0.5×NI + 0.5×CFO) / equity — Dechow(1994) Method C, λ=0.5
FV_per_share = equity * adjROE / r / shares   (영구연금 가정, payout=0)

equity 우선순위: 지배기업소유주지분 > 자본총계
  CFS(연결)에서 자본총계는 비지배지분을 포함하므로 지배주주 기준 적정가 산출 시
  지배기업소유주지분을 먼저 사용한다. OFS나 비지배지분=0인 기업은 동일값.

payout=0 가정: KOSDAQ 소형주 배당 데이터 누락 다수 → 낙관적 편향 허용 (설계서 §3-1).
β=1.0 고정: Phase 2~4. Phase 3 이후 rolling β 도입 검토.

"""
from __future__ import annotations

from backtest.configs.constants import RF, RK  # noqa: F401


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

        산식 (SPEC_04 §7-1, stock-analysis fair_value.py 동일):
          adjROE = (0.5×NI + 0.5×CFO) / equity   Dechow(1994) Method C
          g      = adjROE × (1 - payout), clamp [0, r×0.9]
          FV     = equity + equity × (adjROE - r) × g / (1+r-g)
          FV_per_share = FV / shares

        배당금지급 missing → payout=0 (낙관적 편향 허용, KOSDAQ 소형주 누락 다수).
        beta_adj: r 오프셋 [-0.02, +0.02]. β=1.0 고정.
        """
        import logging

        ni     = pit_data.get('당기순이익')
        cfo    = pit_data.get('영업활동현금흐름')
        equity = (pit_data.get('지배기업소유주지분')
                  or pit_data.get('지배기업소유주지분_1')
                  or pit_data.get('자본총계'))

        if None in (ni, cfo, equity) or equity <= 0 or (shares or 0) <= 0:
            return None

        r = RF + beta * (RK - RF) + self.beta_adj
        if r <= 0:
            return None

        adj_roe = (0.5 * ni + 0.5 * cfo) / equity

        # 배당금지급 3분류 처리 (v4.7)
        div_raw = pit_data.get('배당금지급')
        if div_raw is None:
            logging.debug(f'[RIM] {ticker} 배당금지급 missing — payout=0 적용')
            div = 0.0
        else:
            div = float(div_raw)

        payout = abs(div) / max(abs(ni), 1) if ni != 0 else 0.0
        g      = max(0.0, min(adj_roe * (1 - payout), r * 0.9))

        denom = 1 + r - g
        if abs(denom) < 1e-6:
            return None

        fv_total = equity + equity * (adj_roe - r) * g / denom
        if fv_total <= 0:
            return None

        return fv_total / shares


class _SkeletonModel:
    """Phase 5 멀티모델 skeleton. 구현 전까지 사용 금지."""

    name = 'SKELETON'

    def fair_value(self, ticker, pit_data, shares, beta=1.0):
        raise NotImplementedError('Phase 5 이후 구현 예정')
