"""
안 A(분기 46개)·안 C({Q1,Q3} 파생 23개) 스케줄 오라클 — SPEC_13 §7-1·§7-4 (Q-E).

REBALANCE_SCHEDULE_A는 scripts/generate_rebalance_dates.py --freq quarterly의
출력을 리터럴로 박은 것(§7-4 "생성기 출력 하드코딩"). 여기서는 그 리터럴이
§7-1 상위집합/파생 불변식을 지키는지, §7-3 fiscal_year 규칙과 일치하는지만
검증한다 — DB 접근 없음, fast suite 대상.
"""
from __future__ import annotations

from datetime import date

from backtest.configs.rebalance_dates import REBALANCE_DATES
from backtest.configs.schedule import (
    REBALANCE_DATES_C,
    REBALANCE_DATES_Q,
    REBALANCE_SCHEDULE_A,
    REBALANCE_SCHEDULE_C,
)


def test_schedule_a_total_count_is_46():
    assert len(REBALANCE_SCHEDULE_A) == 46


def test_schedule_a_report_type_breakdown():
    counts: dict[str, int] = {}
    for rp in REBALANCE_SCHEDULE_A:
        counts[rp.report_type] = counts.get(rp.report_type, 0) + 1
    # §7-1: 기존 반기 23(FY 12 + H1 11) + Q1 12 + Q3 11 = 46
    assert counts == {'FY': 12, 'H1': 11, 'Q1': 12, 'Q3': 11}


def test_schedule_a_is_strictly_sorted_by_date():
    dates = [rp.date for rp in REBALANCE_SCHEDULE_A]
    assert dates == sorted(dates)
    assert len(set(dates)) == len(dates), '중복 날짜 없음'


def test_schedule_a_calendar_id_is_a():
    assert all(rp.calendar_id == 'A' for rp in REBALANCE_SCHEDULE_A)


def test_schedule_a_is_superset_of_existing_semiannual_dates():
    """§7-1: 기존 반기 23개가 안 A에 전부 포함(상위집합 검증)."""
    a_dates = {rp.date for rp in REBALANCE_SCHEDULE_A}
    assert set(REBALANCE_DATES) <= a_dates


def test_schedule_a_fy_h1_subset_matches_semiannual_report_types():
    """안 A의 FY/H1 앵커는 기존 반기 REBALANCE_DATES와 날짜·report_type이 완전히 같아야 한다."""
    fy_h1 = {rp.date: rp.report_type for rp in REBALANCE_SCHEDULE_A
             if rp.report_type in ('FY', 'H1')}
    assert set(fy_h1) == set(REBALANCE_DATES)


def test_schedule_c_is_q1_q3_subset_of_schedule_a():
    assert len(REBALANCE_SCHEDULE_C) == 23
    assert all(rp.report_type in ('Q1', 'Q3') for rp in REBALANCE_SCHEDULE_C)
    a_q1q3_dates = {rp.date for rp in REBALANCE_SCHEDULE_A if rp.report_type in ('Q1', 'Q3')}
    c_dates = {rp.date for rp in REBALANCE_SCHEDULE_C}
    assert c_dates == a_q1q3_dates


def test_schedule_c_calendar_id_is_c():
    assert all(rp.calendar_id == 'C' for rp in REBALANCE_SCHEDULE_C)


def test_derived_date_views_match_schedules():
    assert REBALANCE_DATES_Q == tuple(rp.date for rp in REBALANCE_SCHEDULE_A)
    assert REBALANCE_DATES_C == tuple(rp.date for rp in REBALANCE_SCHEDULE_C)


def test_spot_check_q1_q3_dates_against_spec_13_section_7_1_table():
    """§7-1 독립 구현 산출 테이블(2015~2026) 대조 — 임의 표본."""
    by_date = {(rp.date, rp.report_type): rp for rp in REBALANCE_SCHEDULE_A}
    expect = {
        (date(2016, 5, 18), 'Q1'): 2016,
        (date(2016, 11, 17), 'Q3'): 2016,
        (date(2020, 5, 20), 'Q1'): 2020,
        (date(2020, 11, 18), 'Q3'): 2020,
        (date(2025, 5, 20), 'Q1'): 2025,
        (date(2025, 11, 19), 'Q3'): 2025,
        (date(2026, 5, 20), 'Q1'): 2026,
    }
    for key, fiscal_year in expect.items():
        assert key in by_date, f'{key} 누락'
        assert by_date[key].fiscal_year == fiscal_year


def test_schedule_a_2026_has_no_q3_yet():
    """§7-1: 2026 Q3는 아직 범위 밖(as_of=2026-07-28 기준 미도래)."""
    assert (date(2026, 11, 17), 'Q3') not in {(rp.date, rp.report_type) for rp in REBALANCE_SCHEDULE_A}
    assert not any(rp.report_type == 'Q3' and rp.fiscal_year == 2026 for rp in REBALANCE_SCHEDULE_A)


def test_fiscal_year_rule_q1_h1_q3_use_current_year_fy_uses_prior_year():
    for rp in REBALANCE_SCHEDULE_A:
        if rp.report_type == 'FY':
            assert rp.fiscal_year == rp.date.year - 1
        else:
            assert rp.fiscal_year == rp.date.year
