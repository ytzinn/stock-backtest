"""
TRADE-HALT 계약 — `has_recent_trade` 가 **시장 거래일** 기준으로 판정하는가.

이 결함의 본질은 docstring("영업일 기준")과 구현(그 종목 자신의 최근 5 '행')의 불일치였다.
그래서 여기서 재는 것은 "행 vs 거래일"이고, **역할의 좁음도 함께 테스트로 못박는다** —
정지 기간에 is_suspended=TRUE 행이 쌓이는 종목은 종전 구현도 올바로 탈락시켰으므로,
이 함수가 담당하는 것은 "상폐는 아닌데 행 생성이 완전히 끊긴" 좁은 틈뿐이다.
그 사실을 주석이 아니라 여기에 둔다 (사실 주장을 주석에 두면 낡는다 — GATE-FINANCIAL 교훈).
"""
from __future__ import annotations

from datetime import date

import pytest

import backtest.data_access as da
from backtest.data_access import PriceDataUnavailable, has_recent_trade

# 합성 시장 거래일 — 연속 10 거래일 (주말·공휴일 없는 것으로 가정)
CAL = [date(2026, 8, d) for d in (3, 4, 5, 6, 7, 10, 11, 12, 13, 14)]
AS_OF = CAL[-1]           # 2026-08-14
WINDOW5 = CAL[-5:]        # 08-10 ~ 08-14


class _FakeCursor:
    """price_history 를 (date, is_suspended) 목록으로 흉내낸다."""

    def __init__(self, rows: list[tuple[date, bool]]):
        self._rows = rows
        self._result = None

    def execute(self, sql, params):
        if 'date = ANY' in sql:                     # 최근 거래일 실거래 조회
            _, days = params
            hit = any(d in days and not susp for d, susp in self._rows)
            self._result = (1,) if hit else None
        else:                                        # _has_any_price_row
            self._result = (1,) if self._rows else None

    def fetchone(self):
        return self._result

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)


@pytest.fixture(autouse=True)
def _patch_calendar(monkeypatch):
    """거래일 캘린더를 합성 값으로 고정 (daily_nav.trading_dates 재사용 경로)."""
    import backtest.daily_nav as dn
    monkeypatch.setattr(dn, 'trading_dates', lambda conn, s, e: CAL)
    return CAL


def test_rows_stopped_is_rejected():
    """**이 결함의 핵심.** 행 생성이 완전히 끊긴 종목은 탈락해야 한다.

    종전 구현은 그 종목의 마지막 5 '행'(6월 정상거래)을 보고 통과시켰다.
    """
    rows = [(date(2026, 6, d), False) for d in (23, 24, 25, 26, 29)]
    assert has_recent_trade(_FakeConn(rows), '008500', AS_OF, window=5) is False


def test_suspension_rows_were_already_rejected():
    """**역할의 좁음.** 정지 기간에 is_suspended=TRUE 행이 쌓이면 종전 구현도 탈락시켰다.

    2014~2025 매년 300~470종목이 이 형태라, 이 결함이 실제로 발동하는 범위는 좁다.
    """
    rows = [(d, True) for d in CAL]
    assert has_recent_trade(_FakeConn(rows), 'SUSPENDED', AS_OF, window=5) is False


def test_normal_trading_passes():
    rows = [(d, False) for d in CAL]
    assert has_recent_trade(_FakeConn(rows), 'NORMAL', AS_OF, window=5) is True


@pytest.mark.parametrize('gap,expected', [(0, True), (4, True), (5, False), (6, False)])
def test_window_convention_is_T_minus_4(gap, expected):
    """창 규약 `[T-4, T]` 유지. 바꾸면 규칙 정의 변경이라 재실행이 필요해진다."""
    last_trade = CAL[-1 - gap]
    assert has_recent_trade(_FakeConn([(last_trade, False)]), 'X', AS_OF, window=5) is expected


def test_no_rows_at_all_raises():
    """CORR-DA-001: '데이터 미수집'과 '거래정지'는 다른 상태다."""
    with pytest.raises(PriceDataUnavailable):
        has_recent_trade(_FakeConn([]), 'MISSING', AS_OF, window=5)


def test_uses_shared_trading_calendar():
    """캘린더를 새로 만들지 않고 daily_nav.trading_dates 를 재사용하는가 (CLAUDE.md 관례).

    docstring 은 옛 구현을 인용하므로 **본문만** 본다 — 설명과 코드를 섞어 검사하면
    이 함수가 고치려던 것과 같은 종류의 오탐이 난다.
    """
    import ast
    import inspect
    src = inspect.getsource(da.has_recent_trade)
    fn = ast.parse(src.lstrip()).body[0]
    body = fn.body[1:] if ast.get_docstring(fn) else fn.body   # docstring 제외
    code = chr(10).join(ast.unparse(n) for n in body)
    assert 'trading_dates' in code, '공용 거래일 캘린더를 쓰지 않는다'
    assert 'ORDER BY date DESC' not in code, '종목 자신의 행을 자르는 옛 구현이 남아 있다'
