"""
[I-3] 상장폐지일 경계 + "가격 끊김 ≠ 상폐" 구분 (v5.3 haircut 버그 회귀 방지).

계약:
  - is_delisted_at(): delisted_date <= as_of → True. 상폐 당일부터 상폐로 판정.
  - get_close_price(): date <= as_of 최신값 반환 — 가격이 끊겨도 **절대 None이 되지 않는다.**
    따라서 상폐 판정을 get_close_price() is None으로 하면 안 된다 (v5.3 버그의 근본 원인,
    d2d619e/48a9adc에서 수정). 이 성질 자체를 계약으로 고정해 회귀를 막는다.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backtest.data_access import get_close_price, is_delisted_at

pytestmark = pytest.mark.integration

DELIST = date(2023, 6, 15)


@pytest.fixture()
def delisted_stock(conn, make_stock):
    make_stock('EEE001')
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO stock_listing_events (ticker, delisted_date, event_type) "
            "VALUES ('EEE001', %s, 'delisted')", (DELIST,),
        )
    return 'EEE001'


def test_day_before_delisting_not_delisted(conn, delisted_stock):
    assert is_delisted_at(conn, delisted_stock, DELIST - timedelta(days=1)) is False


def test_on_delisting_date_is_delisted(conn, delisted_stock):
    assert is_delisted_at(conn, delisted_stock, DELIST) is True


def test_day_after_delisting_is_delisted(conn, delisted_stock):
    assert is_delisted_at(conn, delisted_stock, DELIST + timedelta(days=1)) is True


def test_no_event_row_means_not_delisted(conn, make_stock):
    make_stock('EEE002')
    assert is_delisted_at(conn, 'EEE002', DELIST) is False


def test_null_delisted_date_means_not_delisted(conn, make_stock):
    """상장 이벤트만 있고 delisted_date NULL → 상폐 아님 (IS NOT NULL 조건 검증)."""
    make_stock('EEE003')
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO stock_listing_events (ticker, listed_date, event_type) "
            "VALUES ('EEE003', '2020-01-06', 'listed')",
        )
    assert is_delisted_at(conn, 'EEE003', DELIST) is False


def test_price_gap_does_not_mean_delisted_and_price_is_stale_not_none(conn, make_stock):
    """
    ★ v5.3 회귀 방지 핵심: 가격이 30일 전에 끊긴 종목(상폐 이벤트 없음)에 대해
      (a) is_delisted_at()은 False — 가격 끊김과 상폐가 구분된다.
      (b) get_close_price()는 None이 아니라 **오래된 가격을 그대로 반환**한다.
    (b)가 이 함수의 문서화된 계약이다 — 소비자는 이 성질을 알고 써야 하며,
    'None이면 상폐'라는 가정은 영원히 성립하지 않는다.
    """
    make_stock('EEE004')
    stale_day = DELIST - timedelta(days=30)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO price_history (ticker, date, adj_close) VALUES ('EEE004', %s, 12345.0)",
            (stale_day,),
        )

    assert is_delisted_at(conn, 'EEE004', DELIST) is False
    assert get_close_price(conn, 'EEE004', DELIST) == 12345.0   # None이 아님 — 계약


def test_delisted_stock_price_also_stale_not_none(conn, delisted_stock):
    """상폐 종목도 마찬가지 — 상폐 후 as_of로 조회하면 마지막 가격이 나온다 (None 아님)."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO price_history (ticker, date, adj_close) VALUES ('EEE001', %s, 500.0)",
            (DELIST - timedelta(days=3),),
        )
    assert get_close_price(conn, 'EEE001', DELIST + timedelta(days=60)) == 500.0
