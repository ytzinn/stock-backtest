"""
RebalancePoint — 리밸런싱 앵커를 (날짜, report_type, fiscal_year) 묶음으로 명시.

SPEC_13 §7-3(DEBT-3 대응). 기존 `engine.py::_report_type(d)`는 월(月)만 보고
report_type을 추론했다 — 8월이면 H1, 나머지는 전부 FY. 분기 캘린더(5월=Q1,
11월=Q3)를 얹으면 이 월 추론이 깨진다(5월/11월도 전부 FY로 잘못 분류). 이
모듈은 그 추론을 없애고 앵커 생성 시점에 report_type·fiscal_year를 명시 부여한다.

REBALANCE_POINTS는 기존 REBALANCE_DATES(반기 23개, SSOT)를 감싼 것 — 새 하드코딩
리스트가 아니라 옛 `_report_type()`과 동일한 규칙(월==8 → H1)으로 파생한다. 그래야
"반기 결과 비트 불변" 검증(오라클)이 옛 구현과의 1:1 대응으로 가능하다.

안 A(분기 46개)·안 C(위상축 파생 23개) 스케줄 생성은 Q-E 몫 — 여기서는 다루지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from backtest.configs.rebalance_dates import REBALANCE_DATES

ReportType = Literal["FY", "Q1", "H1", "Q3"]
CalendarId = Literal["SEMIANNUAL", "A", "C"]


@dataclass(frozen=True)
class RebalancePoint:
    date:               date
    report_type:        ReportType
    fiscal_year:        int    # 이 앵커가 원하는 사업연도 (DEBT-3 — stale 방지)
    nominal_period_end: date  # fiscal_year·report_type이 커버하는 기간 종료일
    calendar_id:        CalendarId


def _semiannual_point(d: date) -> RebalancePoint:
    """기존 `_report_type()`과 동일한 월 기반 규칙 — 반기 캘린더 전용.

    4월 리밸런싱 → 전년도 FY(12/31 마감). 8월 리밸런싱 → 당해 H1(6/30 마감).
    """
    if d.month == 8:
        fiscal_year = d.year
        return RebalancePoint(
            date=d, report_type='H1', fiscal_year=fiscal_year,
            nominal_period_end=date(fiscal_year, 6, 30), calendar_id='SEMIANNUAL',
        )
    fiscal_year = d.year - 1
    return RebalancePoint(
        date=d, report_type='FY', fiscal_year=fiscal_year,
        nominal_period_end=date(fiscal_year, 12, 31), calendar_id='SEMIANNUAL',
    )


REBALANCE_POINTS: list[RebalancePoint] = [_semiannual_point(d) for d in REBALANCE_DATES]
