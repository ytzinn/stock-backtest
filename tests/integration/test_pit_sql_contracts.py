"""
[I-1][I-2][I-4][I-5] financials_pit SQL 경계 계약 — load_pit_series / load_gate_passed_tickers.

의도된 계약의 출처:
  - CLAUDE.md 데이터 정합성: "모든 데이터 조회는 available_from <= rebalance_date 조건 필수"
    → 경계 포함(<=)이 명문화된 계약이다.
  - data_access.load_pit_series docstring: "CFS(연결) 우선, OFS(별도) fallback",
    "amendment_from: 이 날짜부터 amended값 사용" (v8_xbrl_original.sql 주석).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backtest.data_access import load_gate_passed_tickers, load_pit_series

pytestmark = pytest.mark.integration

REBAL = date(2024, 4, 3)


def _insert_pit(conn, ticker: str, year: int, account: str, amount: float,
                available_from: date, fs_div: str = 'CFS', report_type: str = 'FY',
                original_amount: float | None = None, amendment_from: date | None = None):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO financials_pit
                (ticker, year, report_type, fs_div, account_nm, amount,
                 available_from, original_amount, amendment_from)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (ticker, year, report_type, fs_div, account, amount,
             available_from, original_amount, amendment_from),
        )


# ── I-1: available_from 경계 ────────────────────────────────────────────────────

def test_available_from_equal_to_rebalance_date_is_included(conn, make_stock):
    """available_from == rebalance_date 행은 포함된다 (<= 계약, CLAUDE.md 명문화)."""
    make_stock('AAA001')
    _insert_pit(conn, 'AAA001', 2023, '자본총계', 1000.0, available_from=REBAL)

    result = load_pit_series(conn, REBAL, n_years=3, report_type='FY')
    assert result['AAA001'][0]['자본총계'] == 1000.0


def test_available_from_after_rebalance_date_is_excluded(conn, make_stock):
    """available_from == rebalance_date + 1일 → 그 종목은 보이지 않아야 한다 (룩어헤드 방지)."""
    make_stock('AAA002')
    _insert_pit(conn, 'AAA002', 2023, '자본총계', 1000.0,
                available_from=REBAL + timedelta(days=1))

    result = load_pit_series(conn, REBAL, n_years=3, report_type='FY')
    assert 'AAA002' not in result


# ── I-2: amendment_from 경계 (룩어헤드 최우선) ──────────────────────────────────

def test_amendment_after_rebalance_uses_original_amount(conn, make_stock):
    """정정공시가 리밸런싱일 이후 → 원본값(original_amount) 사용. 틀리면 룩어헤드다."""
    make_stock('BBB001')
    _insert_pit(conn, 'BBB001', 2023, '당기순이익', amount=200.0,
                available_from=date(2024, 3, 20),
                original_amount=100.0, amendment_from=REBAL + timedelta(days=30))

    result = load_pit_series(conn, REBAL, n_years=3, report_type='FY')
    assert result['BBB001'][0]['당기순이익'] == 100.0


def test_amendment_on_rebalance_date_uses_amended_value(conn, make_stock):
    """경계 규약: amendment_from == rebalance_date 당일부터 정정값 사용 (<= 포함)."""
    make_stock('BBB002')
    _insert_pit(conn, 'BBB002', 2023, '당기순이익', amount=200.0,
                available_from=date(2024, 3, 20),
                original_amount=100.0, amendment_from=REBAL)

    result = load_pit_series(conn, REBAL, n_years=3, report_type='FY')
    assert result['BBB002'][0]['당기순이익'] == 200.0


def test_amendment_before_rebalance_uses_amended_value(conn, make_stock):
    make_stock('BBB003')
    _insert_pit(conn, 'BBB003', 2023, '당기순이익', amount=200.0,
                available_from=date(2024, 3, 20),
                original_amount=100.0, amendment_from=REBAL - timedelta(days=10))

    result = load_pit_series(conn, REBAL, n_years=3, report_type='FY')
    assert result['BBB003'][0]['당기순이익'] == 200.0


def test_amendment_after_rebalance_with_null_original_uses_amended_value_LOOKAHEAD(conn, make_stock):
    """
    ⚠ 현행 동작 문서화 (오라클 아님) — TECH_DEBT.md PIT-AMEND-001.

    amendment_from > rebalance_date 인데 original_amount 가 NULL(원본 미캡처)이면
    CASE의 ELSE 분기로 떨어져 **정정 반영값(amount)이 그대로 쓰인다** = 조용한 룩어헤드.
    올바른 계약이 무엇이어야 하는지(제외? 원본 필수?)는 Pass 1 정책 결정 사항이므로,
    여기서는 현행 동작을 고정해 '동작이 조용히 바뀌는 것'만 막는다.
    이 테스트가 깨지면: PIT-AMEND-001이 수정된 것 — TECH_DEBT.md를 갱신하고 이 테스트를
    새 계약으로 교체하라.
    """
    make_stock('BBB004')
    _insert_pit(conn, 'BBB004', 2023, '당기순이익', amount=200.0,
                available_from=date(2024, 3, 20),
                original_amount=None, amendment_from=REBAL + timedelta(days=30))

    result = load_pit_series(conn, REBAL, n_years=3, report_type='FY')
    assert result['BBB004'][0]['당기순이익'] == 200.0   # ← 룩어헤드 값 (현행 동작)


# ── I-4: CFS ↔ OFS fallback ────────────────────────────────────────────────────

def test_cfs_preferred_when_both_exist(conn, make_stock):
    make_stock('CCC001')
    _insert_pit(conn, 'CCC001', 2023, '자본총계', 1000.0, date(2024, 3, 20), fs_div='CFS')
    _insert_pit(conn, 'CCC001', 2023, '자본총계', 900.0,  date(2024, 3, 20), fs_div='OFS')

    result = load_pit_series(conn, REBAL, n_years=3, report_type='FY')
    assert result['CCC001'][0]['자본총계'] == 1000.0


def test_ofs_used_when_cfs_absent(conn, make_stock):
    make_stock('CCC002')
    _insert_pit(conn, 'CCC002', 2023, '자본총계', 900.0, date(2024, 3, 20), fs_div='OFS')

    result = load_pit_series(conn, REBAL, n_years=3, report_type='FY')
    assert result['CCC002'][0]['자본총계'] == 900.0


def test_cfs_ofs_fallback_is_per_account_MIXING_DOCUMENTED(conn, make_stock):
    """
    ⚠ 현행 동작 문서화 — TECH_DEBT.md MIX-FSDIV-001.

    fallback이 **계정 단위**로 동작한다: 같은 종목·연도 dict 안에 CFS 자본총계와
    OFS 당기순이익이 섞인다. docstring "CFS 우선, OFS fallback"은 재무제표 단위인지
    계정 단위인지 불명 — 연결/별도 혼합 기준의 재무비율이 계산될 수 있다.
    계약 명문화는 Pass 1 판단 사항. 이 테스트는 현행 동작 고정용.
    """
    make_stock('CCC003')
    _insert_pit(conn, 'CCC003', 2023, '자본총계',   1000.0, date(2024, 3, 20), fs_div='CFS')
    _insert_pit(conn, 'CCC003', 2023, '당기순이익',   50.0, date(2024, 3, 20), fs_div='OFS')

    result = load_pit_series(conn, REBAL, n_years=3, report_type='FY')
    pit0 = result['CCC003'][0]
    assert pit0['자본총계'] == 1000.0    # CFS
    assert pit0['당기순이익'] == 50.0    # OFS — 혼합 발생


# ── I-5: load_gate_passed_tickers — DISTINCT ON 최신 연도 + 상폐/제외 경계 ──────

def _setup_gate_stock(conn, make_stock, ticker: str, gate_by_year: dict[int, str],
                      pit_available: dict[int, date]):
    make_stock(ticker)
    for year, avail in pit_available.items():
        _insert_pit(conn, ticker, year, '자본총계', 1000.0, available_from=avail)
    with conn.cursor() as cur:
        for year, status in gate_by_year.items():
            cur.execute(
                "INSERT INTO universe_gate_pit (ticker, year, report_type, status) "
                "VALUES (%s,%s,'FY',%s)",
                (ticker, year, status),
            )


def test_gate_uses_latest_available_year_not_oldest(conn, make_stock):
    """
    최신 연도(2023)가 REJECT면 과거 연도(2022)가 PASS여도 탈락해야 한다.
    DISTINCT ON 정렬(year DESC)이 역전되면 이 테스트가 잡는다.
    """
    _setup_gate_stock(conn, make_stock, 'DDD001',
                      gate_by_year={2022: 'PASS', 2023: 'REJECT'},
                      pit_available={2022: date(2023, 3, 20), 2023: date(2024, 3, 20)})

    assert 'DDD001' not in load_gate_passed_tickers(conn, REBAL, report_type='FY')


def test_gate_latest_year_pass_included(conn, make_stock):
    _setup_gate_stock(conn, make_stock, 'DDD002',
                      gate_by_year={2022: 'REJECT', 2023: 'PASS'},
                      pit_available={2022: date(2023, 3, 20), 2023: date(2024, 3, 20)})

    assert 'DDD002' in load_gate_passed_tickers(conn, REBAL, report_type='FY')


def test_gate_ignores_year_not_yet_available_pit_awareness(conn, make_stock):
    """
    2023년 재무가 아직 미공개(available_from > rebal)면 게이트 판정도 2022 기준이어야
    한다 — 게이트가 미래 연도 판정을 미리 쓰면 그 자체가 룩어헤드다.
    """
    _setup_gate_stock(conn, make_stock, 'DDD003',
                      gate_by_year={2022: 'PASS', 2023: 'REJECT'},
                      pit_available={2022: date(2023, 3, 20),
                                     2023: REBAL + timedelta(days=10)})   # 미공개

    assert 'DDD003' in load_gate_passed_tickers(conn, REBAL, report_type='FY')


def test_gate_excludes_delisted_on_or_before_rebalance(conn, make_stock):
    _setup_gate_stock(conn, make_stock, 'DDD004',
                      gate_by_year={2023: 'PASS'},
                      pit_available={2023: date(2024, 3, 20)})
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO stock_listing_events (ticker, delisted_date, event_type) "
            "VALUES ('DDD004', %s, 'delisted')", (REBAL,),
        )
    assert 'DDD004' not in load_gate_passed_tickers(conn, REBAL, report_type='FY')


def test_gate_keeps_stock_delisted_after_rebalance(conn, make_stock):
    _setup_gate_stock(conn, make_stock, 'DDD005',
                      gate_by_year={2023: 'PASS'},
                      pit_available={2023: date(2024, 3, 20)})
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO stock_listing_events (ticker, delisted_date, event_type) "
            "VALUES ('DDD005', %s, 'delisted')", (REBAL + timedelta(days=1),),
        )
    assert 'DDD005' in load_gate_passed_tickers(conn, REBAL, report_type='FY')


def test_gate_excludes_is_excluded_stock(conn, make_stock):
    make_stock('DDD006', is_excluded=True)
    _insert_pit(conn, 'DDD006', 2023, '자본총계', 1000.0, date(2024, 3, 20))
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO universe_gate_pit (ticker, year, report_type, status) "
            "VALUES ('DDD006', 2023, 'FY', 'PASS')",
        )
    assert 'DDD006' not in load_gate_passed_tickers(conn, REBAL, report_type='FY')
