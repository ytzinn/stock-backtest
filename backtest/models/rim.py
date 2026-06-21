"""
RIM (Residual Income Model) 적정가 모델 — Ohlson(1995) 지속성 단일단계형.

산식:
  adjROE = (0.5×NI + 0.5×CFO) / equity   Dechow(1994) Method C, λ=0.5
  V/B    = 1 + (adjROE - r) / (1 + r - ω),  ω = 초과이익 지속성 [0,1)
  FV     = equity × clamp(V/B, 0, VB_CAP)
  FV_per_share = FV / shares

이전 산식(g·payout 기반)과의 차이:
  구 산식 FV = B + B×(adjROE-r)×g/(1+r-g) 는 분자에 ×g가 추가돼
  ROE 민감도가 PBR 대비 ~20배 낮은 병리가 있었음 (2026-06-21 설계검토 문서 참조).
  ω=0.6 기준 ROE 2배 시 업사이드: 구 산식 +1.1%p → 신 산식 +29.3%p.

equity 우선순위: 지배기업소유주지분 > 지배기업소유주지분_1 > 자본총계
β=1.0 고정: Phase 2~4. beta_adj로 r 오프셋 미세 조정.
ω: scripts/estimate_omega.py로 한국 PIT 패널에서 직접 추정 후 초기값 0.62와 비교.
"""
from __future__ import annotations

from backtest.configs.constants import RF, RK, OMEGA, VB_CAP  # noqa: F401


class RIMModel:
    """ValuationModel Protocol 구현체."""

    name = 'RIM'

    def __init__(self, beta_adj: float = 0.0, omega: float = OMEGA, vb_cap: float = VB_CAP):
        self.beta_adj = beta_adj
        self.omega    = omega
        self.vb_cap   = vb_cap

    def fair_value(
        self,
        ticker:   str,
        pit_data: dict,
        shares:   float,
        beta:     float = 1.0,
    ) -> float | None:
        """
        주당 적정가(KRW) 반환. 계산 불가 시 None.

        V = B × [1 + (adjROE - r) / (1 + r - ω)],  V/B clamped to [0, vb_cap].
        """
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

        premium  = (adj_roe - r) / (1.0 + r - self.omega)
        vb       = max(0.0, min(1.0 + premium, self.vb_cap))
        fv_total = equity * vb

        if fv_total <= 0:
            return None

        return fv_total / shares


class _SkeletonModel:
    """Phase 5 멀티모델 skeleton. 구현 전까지 사용 금지."""

    name = 'SKELETON'

    def fair_value(self, ticker, pit_data, shares, beta=1.0):
        raise NotImplementedError('Phase 5 이후 구현 예정')
