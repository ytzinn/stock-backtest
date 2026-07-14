"""
[I-6] pit_loader.resolve_dates() — available_from/amendment_from/fallback 결정 규칙.

계약 (CLAUDE.md + pit_loader docstring):
  - available_from = MIN(rcept_dt): 데이터 최초 공개일 (룩어헤드 기준점)
  - amendment_from = MAX(rcept_dt) if MAX > MIN else None
  - disclosures가 없으면 fallback: **법정 제출마감 + 5일** — 정시 제출이라면 항상
    실제 공시일보다 늦으므로 룩어헤드 오염이 없다.
    법정 마감(진실 기준): FY=익년 3/31(사업보고서 90일), H1=8/14(반기 45일),
    Q1=5/15, Q3=11/14.
"""
from __future__ import annotations

from datetime import date

import pytest

from ingest.pit_loader import FALLBACK_OFFSET, resolve_dates

pytestmark = pytest.mark.integration

# 법정 제출 마감일 (자본시장법 기준 — 테스트의 독립 진실값, 코드에서 import하지 않음)
STATUTORY_DEADLINE = {
    'FY': lambda year: date(year + 1, 3, 31),
    'H1': lambda year: date(year, 8, 14),
    'Q1': lambda year: date(year, 5, 15),
    'Q3': lambda year: date(year, 11, 14),
}


def _insert_disclosure(conn, ticker: str, year: int, report_type: str,
                       rcept_dt: date, rcept_no: str, is_amendment: bool = False):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO disclosures (rcept_no, ticker, rcept_dt, report_type, year, is_amendment) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (rcept_no, ticker, rcept_dt, report_type, year, is_amendment),
        )


def test_single_disclosure_no_amendment(conn, make_stock):
    make_stock('FFF001')
    _insert_disclosure(conn, 'FFF001', 2023, 'FY', date(2024, 3, 20), 'R001')

    with conn.cursor() as cur:
        avail, amend, fallback = resolve_dates(cur, 'FFF001', 2023, 'FY')

    assert avail == date(2024, 3, 20)
    assert amend is None
    assert fallback is False


def test_amendment_uses_latest_amendment_disclosure_date(conn, make_stock):
    """원본 3/20 + 정정공시 6/10 → available_from=3/20(최초 공개), amendment_from=6/10."""
    make_stock('FFF002')
    _insert_disclosure(conn, 'FFF002', 2023, 'FY', date(2024, 3, 20), 'R002')
    _insert_disclosure(conn, 'FFF002', 2023, 'FY', date(2024, 6, 10), 'R003',
                       is_amendment=True)

    with conn.cursor() as cur:
        avail, amend, fallback = resolve_dates(cur, 'FFF002', 2023, 'FY')

    assert avail == date(2024, 3, 20)
    assert amend == date(2024, 6, 10)
    assert fallback is False


def test_multiple_amendments_use_the_last_one(conn, make_stock):
    """
    정정이 여러 번이면 **마지막** 정정일. financials.amount는 DART 최신 버전(마지막 정정
    반영본)이므로, 그 값이 완전히 공개된 시점은 마지막 정정일이다 (MIN을 쓰면 룩어헤드 잔존).
    """
    make_stock('FFF006')
    _insert_disclosure(conn, 'FFF006', 2023, 'FY', date(2024, 3, 20), 'R010')
    _insert_disclosure(conn, 'FFF006', 2023, 'FY', date(2024, 5, 2), 'R011', is_amendment=True)
    _insert_disclosure(conn, 'FFF006', 2023, 'FY', date(2024, 9, 8), 'R012', is_amendment=True)

    with conn.cursor() as cur:
        _, amend, _ = resolve_dates(cur, 'FFF006', 2023, 'FY')

    assert amend == date(2024, 9, 8)


def test_non_amendment_republication_must_not_set_amendment_from(conn, make_stock):
    """
    ⚠ PIT-AMEND-002 회귀 방지 (핵심).
    같은 보고서에 공시 행이 2개 이상이지만 **정정공시가 아닌** 경우(재공시·중복 접수 등)
    amendment_from이 붙으면 안 된다. 종전 구현은 is_amendment를 보지 않고
    `MAX(rcept_dt) if MAX > MIN`으로 계산해 이 케이스를 정정으로 오탐했다 —
    운영 DB 실측 10,226행이 이 오탐이었고, load_pit_series의 "정정 미공개" 분기를
    근거 없이 발동시켜 유니버스를 축소시켰다.
    """
    make_stock('FFF007')
    _insert_disclosure(conn, 'FFF007', 2023, 'FY', date(2024, 3, 20), 'R020')
    _insert_disclosure(conn, 'FFF007', 2023, 'FY', date(2024, 4, 15), 'R021')  # 정정 아님

    with conn.cursor() as cur:
        avail, amend, _ = resolve_dates(cur, 'FFF007', 2023, 'FY')

    assert avail == date(2024, 3, 20)
    assert amend is None


def test_amendment_only_disclosure_does_not_set_amendment_from(conn, make_stock):
    """
    공시가 정정본 하나뿐이면(원본 미수집) available_from = 그 정정일이고, amount도 그
    버전이므로 룩어헤드가 없다 → amendment_from은 None (max_amend > min 조건).
    """
    make_stock('FFF008')
    _insert_disclosure(conn, 'FFF008', 2023, 'FY', date(2024, 6, 10), 'R030',
                       is_amendment=True)

    with conn.cursor() as cur:
        avail, amend, _ = resolve_dates(cur, 'FFF008', 2023, 'FY')

    assert avail == date(2024, 6, 10)
    assert amend is None


@pytest.mark.parametrize('report_type', ['FY', 'H1', 'Q1', 'Q3'])
def test_fallback_is_statutory_deadline_plus_5_days(conn, make_stock, report_type):
    """
    disclosures 없음 → fallback available_from == 법정마감 + 5일.
    '항상 실제 공시일보다 늦음'(CLAUDE.md)은 정시 제출 가정 하에 법정마감보다 뒤인
    것으로 보장된다 — 지연 제출 리스크는 FALLBACK-MARGIN-001(TECH_DEBT) 참조.
    """
    make_stock('FFF003')
    year = 2023

    with conn.cursor() as cur:
        avail, amend, fallback = resolve_dates(cur, 'FFF003', year, report_type)

    deadline = STATUTORY_DEADLINE[report_type](year)
    yr_off, mo, day = FALLBACK_OFFSET[report_type]

    assert fallback is True
    assert amend is None
    assert avail == date(year + yr_off, mo, day)          # 코드 정의와 일치
    assert (avail - deadline).days == 5                    # 정확히 법정마감 +5일
    assert avail > deadline                                # 법정마감보다 항상 뒤


def test_fallback_not_used_when_disclosure_exists(conn, make_stock):
    """공시가 있으면 fallback 경로로 절대 빠지지 않는다."""
    make_stock('FFF004')
    _insert_disclosure(conn, 'FFF004', 2023, 'H1', date(2023, 8, 10), 'R004')

    with conn.cursor() as cur:
        avail, _, fallback = resolve_dates(cur, 'FFF004', 2023, 'H1')

    assert fallback is False
    assert avail == date(2023, 8, 10)


def test_other_report_type_disclosure_does_not_leak(conn, make_stock):
    """FY 공시만 있는데 H1을 조회하면 H1은 fallback이어야 한다 (report_type 격리)."""
    make_stock('FFF005')
    _insert_disclosure(conn, 'FFF005', 2023, 'FY', date(2024, 3, 20), 'R005')

    with conn.cursor() as cur:
        _, _, fallback = resolve_dates(cur, 'FFF005', 2023, 'H1')

    assert fallback is True
