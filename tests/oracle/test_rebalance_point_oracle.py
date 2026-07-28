"""
RebalancePoint 오라클 — SPEC_13 §7-3 (DEBT-3 대응, engine.run() 시그니처 변경).

옛 engine.py::_report_type(d) = 'H1' if d.month==8 else 'FY' 는 삭제됐다.
REBALANCE_POINTS(기존 반기 23개를 감싼 것)가 그 옛 로직과 정확히 동일한
report_type·fiscal_year를 내는지가 "반기 결과 비트 불변" 검증의 첫 단추다.
DB 접근 없음 — fast suite 대상.
"""
from __future__ import annotations

from datetime import date

from backtest.configs.rebalance_dates import REBALANCE_DATES
from backtest.configs.schedule import REBALANCE_POINTS, RebalancePoint, _semiannual_point


def _old_report_type(d: date) -> str:
    """삭제된 engine.py::_report_type()의 리터럴 재현 — 대조용(회귀 방지)."""
    return 'H1' if d.month == 8 else 'FY'


def test_rebalance_points_count_matches_rebalance_dates():
    assert len(REBALANCE_POINTS) == len(REBALANCE_DATES) == 23


def test_rebalance_points_dates_match_rebalance_dates_order():
    assert [rp.date for rp in REBALANCE_POINTS] == REBALANCE_DATES


def test_rebalance_points_report_type_matches_old_report_type_for_all_23():
    """옛 _report_type()과 report_type이 전부 일치 — 비트 불변의 전제."""
    for rp in REBALANCE_POINTS:
        assert rp.report_type == _old_report_type(rp.date), rp


def test_rebalance_points_calendar_id_is_semiannual():
    assert all(rp.calendar_id == 'SEMIANNUAL' for rp in REBALANCE_POINTS)


def test_semiannual_point_fy_fiscal_year_is_prior_calendar_year():
    """4월 리밸런싱 → 전년도 FY (예: 2025년 4월 → fiscal_year=2024)."""
    rp = _semiannual_point(date(2025, 4, 3))
    assert rp.report_type == 'FY'
    assert rp.fiscal_year == 2024
    assert rp.nominal_period_end == date(2024, 12, 31)


def test_semiannual_point_h1_fiscal_year_is_same_calendar_year():
    """8월 리밸런싱 → 당해 H1 (예: 2025년 8월 → fiscal_year=2025)."""
    rp = _semiannual_point(date(2025, 8, 20))
    assert rp.report_type == 'H1'
    assert rp.fiscal_year == 2025
    assert rp.nominal_period_end == date(2025, 6, 30)


def test_rebalance_point_is_frozen():
    """dataclass(frozen=True) — 불변이어야 스케줄 SSOT로 안전하게 공유 가능."""
    rp = REBALANCE_POINTS[0]
    try:
        rp.report_type = 'H1'  # type: ignore[misc]
        assert False, '수정 가능하면 안 됨'
    except AttributeError:
        pass


def test_spot_check_known_dates():
    """§7-3 예시(2025년 4월→FY 2024, 2025년 8월→H1 2025)와 실제 REBALANCE_POINTS 대조."""
    by_date = {rp.date: rp for rp in REBALANCE_POINTS}
    assert by_date[date(2025, 4, 3)].report_type == 'FY'
    assert by_date[date(2025, 4, 3)].fiscal_year == 2024
    assert by_date[date(2025, 8, 20)].report_type == 'H1'
    assert by_date[date(2025, 8, 20)].fiscal_year == 2025
