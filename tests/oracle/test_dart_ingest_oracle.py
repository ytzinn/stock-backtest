"""
ingest/dart_ingest.py 오라클 — "기존 값과 같은가"가 아니라 "옳은가"를 검증한다.
DB 미접속. `conn`/`cursor`는 합성 페이크로 대체한다.

SPEC_13 §5-4(VERIFY-INGEST-002)·§5-7(VERIFY-INGEST-005) 조사 후 작성된 코드의 회귀 방지용.
"""
from __future__ import annotations

from datetime import date

from ingest.dart_ingest import (
    DEFAULT_REPORTS,
    _classify_disclosure,
    _get_valid_collection_targets,
    _years_needing_disclosures,
)


# ── _get_valid_collection_targets ────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *_args, **_kwargs):
        pass

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)


def test_collection_targets_derives_q1_q3_from_fy_h1_years():
    """4월/8월 스냅샷만 있어도(5월/11월 없음) 같은 연도의 Q1/Q3를 유효 대상으로 파생한다.

    [검증된 사실 2026-07-27] krx_listing_snapshots는 4월·8월만 존재 — 이 함수가 유일한
    Q1/Q3 진입 경로다.
    """
    conn = _FakeConn([(date(2024, 4, 3),), (date(2024, 8, 19),)])
    targets = _get_valid_collection_targets('005930', conn)

    assert (2023, 'FY') in targets  # FY = snapshot_year - 1
    assert (2024, 'H1') in targets
    assert (2023, 'Q1') in targets
    assert (2023, 'Q3') in targets
    assert (2024, 'Q1') in targets
    assert (2024, 'Q3') in targets
    # 5월/11월 스냅샷이 실제로 없다는 사실 자체가 §5-4의 위험이므로, FY(2023) 연도쌍만
    # 있고 H1(2024) 스냅샷이 없는 경우에도 그 연도의 Q1/Q3는 파생돼야 한다(상장 구간 가정).


def test_collection_targets_only_fy_year_still_derives_that_years_q1_q3():
    """H1 스냅샷 없이 FY 스냅샷만 있는 연도도 Q1/Q3가 파생된다(신규상장 초년도 등)."""
    conn = _FakeConn([(date(2020, 4, 6),)])
    targets = _get_valid_collection_targets('123456', conn)

    assert targets == sorted([(2019, 'FY'), (2019, 'Q1'), (2019, 'Q3')])


def test_collection_targets_empty_snapshot_returns_empty():
    conn = _FakeConn([])
    assert _get_valid_collection_targets('000000', conn) == []


# ── DEFAULT_REPORTS 안전장치 ──────────────────────────────────────────────────

def test_default_reports_excludes_quarterly():
    """빈 only_reports가 조용히 Q1/Q3까지 수집 대상에 넣지 않는다는 안전장치 (§10 M5)."""
    assert DEFAULT_REPORTS == ('FY', 'H1')
    assert 'Q1' not in DEFAULT_REPORTS
    assert 'Q3' not in DEFAULT_REPORTS


# ── _years_needing_disclosures (VERIFY-INGEST-005) ───────────────────────────

def test_years_needing_disclosures_skips_year_with_full_coverage():
    collect_targets = [(2024, 'FY'), (2024, 'H1')]
    have = {2024: {'FY', 'H1'}}
    assert _years_needing_disclosures(collect_targets, have) == []


def test_years_needing_disclosures_flags_year_missing_new_report_type():
    """FY는 이미 있지만 Q1 disclosures를 한 번도 못 받은 연도 — 스킵하면 안 된다.

    이게 바로 VERIFY-INGEST-005 버그의 재현 조건이다: only_reports=('Q1','Q3')로
    재무만 수집하면서 공시 수집을 통째로 건너뛰면 이 연도의 Q1 공시일이 영원히 fallback.
    """
    collect_targets = [(2024, 'FY'), (2024, 'Q1')]
    have = {2024: {'FY'}}  # Q1 disclosures 없음
    assert _years_needing_disclosures(collect_targets, have) == [2024]


def test_years_needing_disclosures_year_with_no_db_rows_at_all():
    collect_targets = [(2025, 'Q1'), (2025, 'Q3')]
    have: dict[int, set[str]] = {}  # 해당 연도 조회 자체가 없음
    assert _years_needing_disclosures(collect_targets, have) == [2025]


def test_years_needing_disclosures_multi_year_mixed():
    collect_targets = [(2023, 'FY'), (2023, 'H1'), (2024, 'FY'), (2024, 'Q1')]
    have = {2023: {'FY', 'H1'}, 2024: {'FY'}}
    assert _years_needing_disclosures(collect_targets, have) == [2024]


# ── _classify_disclosure (VERIFY-INGEST-001 회귀 방지) ───────────────────────

def test_classify_fy():
    assert _classify_disclosure('사업보고서 (2023.12)', None) == ('FY', None)


def test_classify_h1():
    assert _classify_disclosure('반기보고서 (2023.06)', None) == ('H1', None)


def test_classify_q1_by_period_month_parse():
    """분기보고서 문자열은 Q1/Q3 모두 동일 — (YYYY.MM) 대상월로만 구분된다."""
    assert _classify_disclosure('분기보고서 (2024.03)', None) == ('Q1', 2024)


def test_classify_q3_by_period_month_parse():
    assert _classify_disclosure('분기보고서 (2024.09)', None) == ('Q3', 2024)


def test_classify_amendment_prefix_absorbed():
    """[기재정정] 접두는 substring 매칭이라 자동 흡수된다."""
    assert _classify_disclosure('[기재정정]분기보고서 (2024.09)', None) == ('Q3', 2024)


def test_classify_q1_rcept_fallback_when_parse_fails():
    """(YYYY.MM) 파싱 실패 시 접수일 구간(4~8월)으로 Q1 추정."""
    assert _classify_disclosure('분기보고서', date(2024, 5, 15)) == ('Q1', None)


def test_classify_q3_rcept_fallback_when_parse_fails():
    """접수일 구간(10~익2월)으로 Q3 추정."""
    assert _classify_disclosure('분기보고서', date(2024, 11, 15)) == ('Q3', None)


def test_classify_unrecognized_returns_none():
    assert _classify_disclosure('첨부정정신고서', None) == (None, None)
