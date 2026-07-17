"""
Phase 2 백테스트 인터페이스 정의.

UniverseFilter, ValuationModel Protocol을 정의한다.
이 Protocol을 만족하는 클래스는 BacktestPipeline에 주입 가능하다.
"""
from typing import Protocol
from datetime import date


class UniverseFilter(Protocol):
    """
    유니버스 필터 인터페이스.
    구현체: HardFilter, StabilityFilter, FactorScreener, MomentumFilter
    """
    def apply(
        self,
        tickers:        list[str],
        rebalance_date: date,
        pit_series:     dict[str, list[dict]],
        conn,
        # pit_series[ticker] = [현재FY, t-1FY, t-2FY] (내림차순, 없으면 리스트 짧아짐)
        # [0] = rebalance_date 기준 최신 FY (available_from <= rebalance_date)
        # [1] = 전년도 FY, [2] = 2년 전 FY
    ) -> tuple[list[str], dict]:
        """반환: (통과 종목 리스트, 탈락 상세 dict {ticker: reason_str 또는 reason_list})"""
        ...


class ValuationModel(Protocol):
    """
    적정가 모델 인터페이스.
    구현체: RIMModel (Phase 2~4), Phase 5 이후 멀티모델 확장
    """
    name: str  # 'RIM' | 'EV_SALES' | 'FCFF' | 'ENSEMBLE' 등

    def fair_value_total(
        self,
        ticker:   str,
        pit_data: dict,   # 단일 연도 PIT dict (pit_series[ticker][0])
        beta:     float,
    ) -> float | None:
        """기업 전체 적정가치 FV_total(KRW) 반환. 계산 불가 시 None.
        BacktestPipeline은 이 값을 시가총액과 비교한다 (BASIS-RIM-001 — 주당
        환산 금지: 수정주가 리베이스와 PIT 주식수의 기저가 어긋난다)."""
        ...

    def fair_value(
        self,
        ticker:   str,
        pit_data: dict,
        shares:   float,
        beta:     float,
    ) -> float | None:
        """주당 적정가(KRW) = fair_value_total / shares. 단건 조회·리포트용 —
        백테스트 랭킹에 사용 금지. 계산 불가 시 None."""
        ...
