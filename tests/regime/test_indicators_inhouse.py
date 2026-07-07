"""
SPEC_07 §11 — indicators_inhouse.py 테스트.

분위수/로그비율/동결 임계값 등 순수 함수는 DB 없이 직접 검증한다.
PBR/book equity의 `available_from<=t` look-ahead 방지 자체는 SQL WHERE절에 있어
DB 없이는 값으로 검증할 수 없으므로, 가드 문구가 소스에서 사라지지 않았는지
회귀 테스트(문자열 검사)로 지킨다.
"""
from __future__ import annotations

import inspect
import logging
from datetime import date

import pytest

from backtest.regime import data_access_regime as dar
from backtest.regime import indicators_inhouse as ind


def test_value_spread_positive_when_growth_pricier_than_value():
    pbr = {f'lo{i}': 0.5 + i * 0.05 for i in range(5)}
    pbr.update({f'mid{i}': 1.0 + i * 0.1 for i in range(5)})
    pbr.update({f'hi{i}': 3.0 + i * 0.5 for i in range(5)})
    vs = ind.value_spread(pbr)
    assert vs is not None
    assert vs > 0


def test_value_spread_none_when_sample_too_small():
    assert ind.value_spread({'a': 1.0, 'b': 2.0}) is None


def test_size_val_gap_negative_when_largecap_cheaper():
    # SIZE_DECILES=10 → _quantile_labels 최소표본(n_quantiles*3=30) 충족 필요
    tickers = [f't{i}' for i in range(30)]
    caps = {t: (i + 1) * 100.0 for i, t in enumerate(tickers)}   # t29 최대(대형) ... t0 최소(소형)
    pbr = {t: 2.0 for t in tickers}
    for t in tickers[:3]:       # 소형(최하위 decile) = 고PBR(비쌈)
        pbr[t] = 3.0
    for t in tickers[27:]:      # 대형(최상위 decile) = 저PBR(쌈)
        pbr[t] = 1.0
    gap = ind.size_val_gap(pbr, caps)
    assert gap is not None
    assert gap < 0   # log(대형/소형) < 0 (대형이 더 쌈)


def test_mega_cap_concentration_basic():
    caps = {f't{i}': 10.0 for i in range(19)}
    caps['giant'] = 900.0
    conc = ind.mega_cap_concentration(caps, top_n=1)
    assert conc == pytest.approx(900.0 / (900.0 + 19 * 10.0))


def test_check_dropped_pct_warns_above_30pct(caplog):
    with caplog.at_level(logging.WARNING):
        triggered = ind.check_dropped_pct_threshold(date(2020, 1, 31), 0.35)
    assert triggered is True
    assert any('dropped_pct' in r.message for r in caplog.records)


def test_check_dropped_pct_silent_below_30pct(caplog):
    with caplog.at_level(logging.WARNING):
        triggered = ind.check_dropped_pct_threshold(date(2020, 1, 31), 0.10)
    assert triggered is False
    assert not caplog.records


def test_size_mom_6m_only_queries_formation_and_current_dates_never_future(monkeypatch):
    """t-6M formation이 t 이후 날짜를 조회하지 않는지 확인(룩어헤드 방지)."""
    requested_dates = []

    def fake_latest_close_batch(conn, tickers, d):
        requested_dates.append(d)
        return {t: 100.0 + i for i, t in enumerate(tickers)}

    monkeypatch.setattr(ind, 'latest_close_batch', fake_latest_close_batch)
    monkeypatch.setattr(ind, 'is_delisted_at', lambda conn, ticker, d: False)

    bucket_caps = {f't{i}': float(i) for i in range(30)}   # SIZE_DECILES(10)*3 최소표본 충족
    return_start, current = date(2020, 1, 31), date(2020, 7, 31)
    result = ind.size_mom_6m(conn=None, bucket_universe=list(bucket_caps), bucket_caps=bucket_caps,
                              return_start_date=return_start, current_date=current)

    assert result is not None
    assert set(requested_dates) == {return_start, current}
    assert all(d <= current for d in requested_dates)


def test_size_mom_6m_applies_delisting_haircut_not_flat_carry_forward(monkeypatch):
    """상폐된 소형주는 latest_close_batch의 carry-forward 가격이 아니라 haircut된 값을 써야 한다."""
    tickers = [f't{i}' for i in range(30)]
    bucket_caps = {t: float(i) for i, t in enumerate(tickers)}   # t0..t29, 오름차순 시총

    def fake_latest_close_batch(conn, tks, d):
        return {t: 100.0 for t in tks}   # 형성일/현재 모두 100 (상폐 전 마지막 가격)

    monkeypatch.setattr(ind, 'latest_close_batch', fake_latest_close_batch)
    monkeypatch.setattr(ind, 'is_delisted_at', lambda conn, ticker, d: ticker == 't0')
    monkeypatch.setattr(ind, '_last_known_price', lambda conn, ticker, d: 100.0)

    result = ind.size_mom_6m(conn=None, bucket_universe=tickers, bucket_caps=bucket_caps,
                              return_start_date=date(2020, 1, 31), current_date=date(2020, 7, 31))

    # t0(소형 decile 최하위, 상폐)는 100*DELISTING_HAIRCUT로 청산돼야 하며, 나머지는 100 그대로(0%).
    # 상폐 haircut이 없다면 소형 버킷 평균수익도 0%가 되어 이 테스트가 실패한다.
    assert result is not None
    assert result < 0   # 소형 버킷에 상폐 손실이 반영돼 대형 대비 음수여야 함


def test_book_equity_batch_sql_keeps_available_from_guard():
    """§4-2 PIT 준수 — available_from<=as_of 가드가 두 CTE(latest, prioritized) 모두에 있어야 한다."""
    src = inspect.getsource(dar.book_equity_batch)
    assert src.count('available_from <= %s') == 2


def test_list_universe_tickers_sql_excludes_delisted_by_as_of():
    src = inspect.getsource(dar.list_universe_tickers)
    assert 'delisted_date <= %s' in src
