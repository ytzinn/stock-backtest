"""
포트폴리오 구성 — 동일가중(1/N).

Phase 2 확정값:
  - 동일가중 (1/N), 종목당 비중 상한 없음 — 2026-07-05 MAX_STOCK_WEIGHT 폐지
    (build_portfolio()가 캡을 적용해도 engine._calc_period_return()이 실제로는
     weight 값을 쓰지 않고 보유 종목 수로 단순평균했기 때문에 캡이 실질적으로
     무의미했음. n_stocks=20 목표 충족 시엔 1/20=5%로 어차피 캡과 동일했고,
     드물게 종목 수가 적은 구간에서만 "표시상 5%, 실제 계산은 1/n"로 괴리가
     있었음 — 코드·실제 동작 불일치 해소를 위해 캡 자체를 제거.)
  - 목표 종목 수: n_stocks (기본 20)
  - 후보 미달 시: **충족 종목 수만큼 전액 투자 (동일가중 1/n)** — 2026-07-12 정책 확정
    (CONTRACT-PF-001). MIN_PORTFOLIO_STOCKS는 구성 차단 기준이 아니라
    ① pipeline.score_and_rank의 고평가 보완 목표치 ② engine의 경고 기준선이다.
  - 업종 최대 25%, KOSDAQ 최대 60% — 업종/거래소 데이터 미수집으로 Phase 2 미구현
"""
from __future__ import annotations

MIN_PORTFOLIO_STOCKS = 5   # 보완 목표치·경고 기준선 (구성 차단 아님 — 모듈 docstring 참조)


def build_portfolio(
    candidates: list[dict],
    n_stocks:   int | None = 20,
) -> dict[str, float]:
    """
    score_and_rank() 반환값(upside_pct 내림차순 정렬)을 받아 포트폴리오 비중 반환.

    반환: {ticker: weight}  (동일가중 1/n, 합계 1.0)
    n_stocks=None이면 상한 없음 — 후보 전 종목 편입 (SPEC_10 §3-2 U_pbr_path_ew,
    적격 유니버스 동일가중. candidates[:None] == 전체 슬라이스라 로직 동일).
    계약 (2026-07-12 정책 확정, CONTRACT-PF-001): 후보가 1개라도 있으면 그 수만큼
    전액 투자한다. 빈 dict는 후보 0개일 때만 — engine이 period_return=0으로 처리.
    (검토된 대안: 현금 100% / 부족분 현금 / 차선 보완 — TECH_DEBT.md 참조.
     5종목 미만 실측 0건, 5~7종목 구간 2건뿐이라 결과 영향 미미.)

    Phase 2 미구현: 업종 상한 25%, KOSDAQ 상한 60%.
    (sector 데이터 미수집. Phase 3 이후 classification_history 연동 시 추가.)
    """
    selected = candidates[:n_stocks]
    n = len(selected)
    if n == 0:
        return {}

    weight = 1.0 / n

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
