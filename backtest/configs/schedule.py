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


# ── 안 A(분기, 46개) — 생성기 출력 하드코딩 ──────────────────────────────────
# 생성: scripts/generate_rebalance_dates.py --as-of 2026-07-28 --freq quarterly
# (§7-1 독립 구현 산출 Q1/Q3 날짜와 23/23 일치 확인됨, SPEC_13 §7-1)
REBALANCE_SCHEDULE_A: tuple[RebalancePoint, ...] = (
    RebalancePoint(date(2015, 4, 3),   "FY", 2014, date(2014, 12, 31), "A"),
    RebalancePoint(date(2015, 5, 20),  "Q1", 2015, date(2015, 3, 31),  "A"),
    RebalancePoint(date(2015, 8, 19),  "H1", 2015, date(2015, 6, 30),  "A"),
    RebalancePoint(date(2015, 11, 18), "Q3", 2015, date(2015, 9, 30),  "A"),
    RebalancePoint(date(2016, 4, 5),   "FY", 2015, date(2015, 12, 31), "A"),
    RebalancePoint(date(2016, 5, 18),  "Q1", 2016, date(2016, 3, 31),  "A"),
    RebalancePoint(date(2016, 8, 18),  "H1", 2016, date(2016, 6, 30),  "A"),
    RebalancePoint(date(2016, 11, 17), "Q3", 2016, date(2016, 9, 30),  "A"),
    RebalancePoint(date(2017, 4, 5),   "FY", 2016, date(2016, 12, 31), "A"),
    RebalancePoint(date(2017, 5, 18),  "Q1", 2017, date(2017, 3, 31),  "A"),
    RebalancePoint(date(2017, 8, 18),  "H1", 2017, date(2017, 6, 30),  "A"),
    RebalancePoint(date(2017, 11, 17), "Q3", 2017, date(2017, 9, 30),  "A"),
    RebalancePoint(date(2018, 4, 4),   "FY", 2017, date(2017, 12, 31), "A"),
    RebalancePoint(date(2018, 5, 18),  "Q1", 2018, date(2018, 3, 31),  "A"),
    RebalancePoint(date(2018, 8, 20),  "H1", 2018, date(2018, 6, 30),  "A"),
    RebalancePoint(date(2018, 11, 19), "Q3", 2018, date(2018, 9, 30),  "A"),
    RebalancePoint(date(2019, 4, 3),   "FY", 2018, date(2018, 12, 31), "A"),
    RebalancePoint(date(2019, 5, 20),  "Q1", 2019, date(2019, 3, 31),  "A"),
    RebalancePoint(date(2019, 8, 20),  "H1", 2019, date(2019, 6, 30),  "A"),
    RebalancePoint(date(2019, 11, 19), "Q3", 2019, date(2019, 9, 30),  "A"),
    RebalancePoint(date(2020, 4, 3),   "FY", 2019, date(2019, 12, 31), "A"),
    RebalancePoint(date(2020, 5, 20),  "Q1", 2020, date(2020, 3, 31),  "A"),
    RebalancePoint(date(2020, 8, 20),  "H1", 2020, date(2020, 6, 30),  "A"),
    RebalancePoint(date(2020, 11, 18), "Q3", 2020, date(2020, 9, 30),  "A"),
    RebalancePoint(date(2021, 4, 5),   "FY", 2020, date(2020, 12, 31), "A"),
    RebalancePoint(date(2021, 5, 20),  "Q1", 2021, date(2021, 3, 31),  "A"),
    RebalancePoint(date(2021, 8, 19),  "H1", 2021, date(2021, 6, 30),  "A"),
    RebalancePoint(date(2021, 11, 17), "Q3", 2021, date(2021, 9, 30),  "A"),
    RebalancePoint(date(2022, 4, 5),   "FY", 2021, date(2021, 12, 31), "A"),
    RebalancePoint(date(2022, 5, 18),  "Q1", 2022, date(2022, 3, 31),  "A"),
    RebalancePoint(date(2022, 8, 18),  "H1", 2022, date(2022, 6, 30),  "A"),
    RebalancePoint(date(2022, 11, 17), "Q3", 2022, date(2022, 9, 30),  "A"),
    RebalancePoint(date(2023, 4, 5),   "FY", 2022, date(2022, 12, 31), "A"),
    RebalancePoint(date(2023, 5, 18),  "Q1", 2023, date(2023, 3, 31),  "A"),
    RebalancePoint(date(2023, 8, 18),  "H1", 2023, date(2023, 6, 30),  "A"),
    RebalancePoint(date(2023, 11, 17), "Q3", 2023, date(2023, 9, 30),  "A"),
    RebalancePoint(date(2024, 4, 3),   "FY", 2023, date(2023, 12, 31), "A"),
    RebalancePoint(date(2024, 5, 20),  "Q1", 2024, date(2024, 3, 31),  "A"),
    RebalancePoint(date(2024, 8, 20),  "H1", 2024, date(2024, 6, 30),  "A"),
    RebalancePoint(date(2024, 11, 19), "Q3", 2024, date(2024, 9, 30),  "A"),
    RebalancePoint(date(2025, 4, 3),   "FY", 2024, date(2024, 12, 31), "A"),
    RebalancePoint(date(2025, 5, 20),  "Q1", 2025, date(2025, 3, 31),  "A"),
    RebalancePoint(date(2025, 8, 20),  "H1", 2025, date(2025, 6, 30),  "A"),
    RebalancePoint(date(2025, 11, 19), "Q3", 2025, date(2025, 9, 30),  "A"),
    RebalancePoint(date(2026, 4, 3),   "FY", 2025, date(2025, 12, 31), "A"),
    RebalancePoint(date(2026, 5, 20),  "Q1", 2026, date(2026, 3, 31),  "A"),
)

# 안 C = 안 A의 {Q1,Q3} 부분집합 (23개) — 새 하드코딩 아님, §7-C-6 파생 뷰
REBALANCE_SCHEDULE_C: tuple[RebalancePoint, ...] = tuple(
    RebalancePoint(p.date, p.report_type, p.fiscal_year, p.nominal_period_end, "C")
    for p in REBALANCE_SCHEDULE_A if p.report_type in ("Q1", "Q3")
)

# 파생 뷰 (날짜만 필요한 소비처용 — 하드코딩 아님)
REBALANCE_DATES_Q: tuple[date, ...] = tuple(p.date for p in REBALANCE_SCHEDULE_A)
REBALANCE_DATES_C: tuple[date, ...] = tuple(p.date for p in REBALANCE_SCHEDULE_C)

# 불변식 assert (import 시 — §7-4)
assert set(REBALANCE_DATES) <= set(REBALANCE_DATES_Q), '안 A 상위집합 위반'
assert set(REBALANCE_DATES_C) <= set(REBALANCE_DATES_Q), '안 C ⊂ 안 A 위반'


# ── CLI 캘린더 선택 (Q-G — 소비 스크립트 공용, 복제 금지) ──────────────────────

CALENDAR_CHOICES: tuple[CalendarId, ...] = ('SEMIANNUAL', 'A', 'C')

_SCHEDULES: dict[str, tuple[RebalancePoint, ...]] = {
    'SEMIANNUAL': tuple(REBALANCE_POINTS),
    'A':          REBALANCE_SCHEDULE_A,
    'C':          REBALANCE_SCHEDULE_C,
}


def get_schedule(calendar_id: str) -> tuple[RebalancePoint, ...]:
    """`--calendar` 값 → 해당 RebalancePoint 스케줄. 알 수 없는 값이면 예외."""
    try:
        return _SCHEDULES[calendar_id]
    except KeyError:
        raise ValueError(
            f'알 수 없는 캘린더: {calendar_id!r} (가능: {", ".join(CALENDAR_CHOICES)})'
        ) from None


def tag_suffix(calendar_id: str) -> str:
    """산출물 태그 접미사. 반기(기존 공식 산출물)는 무접미사로 유지해 덮어쓰기를 막는다."""
    get_schedule(calendar_id)          # 유효성 검증 (오타로 조용히 무접미사가 되는 것 방지)
    return '' if calendar_id == 'SEMIANNUAL' else f'_{calendar_id}'
