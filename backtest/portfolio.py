"""
포트폴리오 구성 — 동일가중(1/N) + 비중 제약.

Phase 2 확정값:
  - 동일가중 (1/N), 종목당 최대 5%
  - 목표 종목 수: n_stocks (기본 20)
  - 최소 종목 수: MIN_PORTFOLIO_STOCKS (기본 5) — 미달 시 해당 기간 건너뜀 (return=0)
  - 업종 최대 25%, KOSDAQ 최대 60% — 업종/거래소 데이터 미수집으로 Phase 2 미구현
"""
from __future__ import annotations

MAX_STOCK_WEIGHT   = 0.05  # 종목당 최대 비중 5%
MIN_PORTFOLIO_STOCKS = 5   # 이 미만이면 포트폴리오 구성 불가로 빈 dict 반환


def build_portfolio(
    candidates: list[dict],
    n_stocks:   int = 20,
) -> dict[str, float]:
    """
    score_and_rank() 반환값(upside_pct 내림차순 정렬)을 받아 포트폴리오 비중 반환.

    반환: {ticker: weight}  (합이 1.0에 가깝지만 cap 적용 시 미달 가능)
    후보가 MIN_PORTFOLIO_STOCKS 미만이면 빈 dict → engine이 period_return=0으로 처리.

    Phase 2 미구현: 업종 상한 25%, KOSDAQ 상한 60%.
    (sector 데이터 미수집. Phase 3 이후 classification_history 연동 시 추가.)
    """
    selected = candidates[:n_stocks]
    n = len(selected)
    if n == 0:
        return {}

    raw_weight = 1.0 / n
    weight = min(raw_weight, MAX_STOCK_WEIGHT)

    return {item['ticker']: weight for item in selected}


def apply_portfolio_constraints(
    weights: dict[str, float],
    sector_map:   dict[str, str] | None = None,
    exchange_map: dict[str, str] | None = None,
    max_sector_pct:   float = 0.25,
    max_kosdaq_pct:   float = 0.60,
) -> dict[str, float]:
    """
    Phase 3 이후 활성화. sector_map / exchange_map이 제공될 때 업종·거래소 제약 적용.
    현재는 입력 그대로 반환.
    """
    if sector_map is None and exchange_map is None:
        return weights

    # TODO Phase 3: 업종별 집중 제한, KOSDAQ 비중 상한 적용
    return weights
