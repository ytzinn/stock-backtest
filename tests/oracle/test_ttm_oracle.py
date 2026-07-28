"""[O-TTM] TTM 연도 정렬 오라클 — DEBT-2 (SPEC_13 §4-2, §8 QG0 오라클 #1).

load_pit_series_ttm 은 위치가 아니라 명시적 사업연도로 TTM을 조립해야 한다.
중간 연도가 결측되면 종전 구현은 리스트 위치가 당겨져 FY_2022 − H1_2021(엉뚱)
+ H1_2023 같은 조합을 조용히 만들었다. 이 오라클은:
  1. _make_ttm 이 TTM = FY_{y-1} − interim_{y-1} + interim_y 를 정확히 계산하는지
  2. 연율화 fallback(H1×2 등)이 완전히 제거됐는지
  3. 중간 연도 결측 시 오조합 대신 계정 부재로 처리되는지
  4. 완전 데이터 종목은 종전과 동일한 [current, prev, prev2] 를 내는지
  5. Q1/Q3 확장(§6-2, Q-D)이 H1과 동일 산식으로 동작하는지
를 고정한다. DB 접근 없음 — fast suite 대상 (load_pit_by_year 몽키패치).
"""
from __future__ import annotations

from datetime import date

import pytest

import backtest.data_access as da
from backtest.data_access import _make_ttm, load_pit_series_ttm

REBAL = date(2024, 8, 20)


# ── _make_ttm 순수 계약 ──────────────────────────────────────────────────────

def test_make_ttm_formula():
    """TTM = FY_{y-1} − H1_{y-1} + H1_y (IS/CF)."""
    fy_prev = {'매출액': 2000.0, '당기순이익': 300.0}
    h1_curr = {'매출액': 1000.0, '당기순이익': 180.0}
    h1_prev = {'매출액': 900.0,  '당기순이익': 150.0}
    ttm = _make_ttm(fy_prev, h1_curr, h1_prev, 'T')
    assert ttm['매출액']     == 2000.0 - 900.0 + 1000.0   # 2100
    assert ttm['당기순이익'] == 300.0 - 150.0 + 180.0     # 330


def test_make_ttm_no_annualization_when_fy_missing():
    """DEBT-2: FY_{y-1} 결측 → 연율화(H1×2) 금지, 계정 부재."""
    ttm = _make_ttm({}, {'매출액': 1000.0}, {'매출액': 900.0}, 'T')
    assert '매출액' not in ttm   # 종전엔 1000×2=2000 이었음


def test_make_ttm_requires_all_three():
    """세 값 중 하나라도 없으면 그 계정 없음."""
    assert '매출액' not in _make_ttm({'매출액': 2000.0}, {'매출액': 1000.0}, {}, 'T')
    assert '매출액' not in _make_ttm({'매출액': 2000.0}, {}, {'매출액': 900.0}, 'T')


def test_make_ttm_bs_snapshot_passthrough():
    """BS 계정(자본총계 등)은 H1_y 스냅샷 그대로 — TTM 미적용."""
    ttm = _make_ttm({'매출액': 2000.0}, {'자본총계': 5000.0, '매출액': 1000.0},
                    {'매출액': 900.0}, 'T')
    assert ttm['자본총계'] == 5000.0
    assert ttm['매출액']   == 2100.0


# ── 연도 정렬 (load_pit_series_ttm, load_pit_by_year 몽키패치) ────────────────

def _patch(monkeypatch, fy_years, h1_years):
    """fy_years·h1_years 는 단일 종목 'X'의 {year: accounts}. load_pit_by_year 는
    {ticker: {year: accounts}} 계약이므로 'X'로 감싼다."""
    def fake(conn, rebalance_date, n_years=3, report_type='FY'):
        return {'X': dict(fy_years)} if report_type == 'FY' else {'X': dict(h1_years)}
    monkeypatch.setattr(da, 'load_pit_by_year', fake)


def test_ttm_complete_data_anchors_by_year(monkeypatch):
    """완전 데이터: [TTM_{y_c}, TTM_{y_c-1}, TTM_{y_c-2}] 정확 조립."""
    h1 = {2024: {'매출액': 1000.0}, 2023: {'매출액': 900.0},
          2022: {'매출액': 800.0},  2021: {'매출액': 700.0}}
    fy = {2023: {'매출액': 2000.0}, 2022: {'매출액': 1800.0},
          2021: {'매출액': 1600.0}, 2020: {'매출액': 1400.0}}
    _patch(monkeypatch, fy, h1)
    out = load_pit_series_ttm(None, REBAL, report_type='H1')['X']
    assert out[0]['매출액'] == 2000.0 - 900.0 + 1000.0  # TTM_2024 = 2100
    assert out[1]['매출액'] == 1800.0 - 800.0 + 900.0   # TTM_2023 = 1900
    assert out[2]['매출액'] == 1600.0 - 700.0 + 800.0   # TTM_2022 = 1700


def test_ttm_missing_middle_year_no_silent_miscombination(monkeypatch):
    """H1_2023 결측: TTM_2024 는 H1_{y-1} 부재로 계정 없음 — 위치 당김 오조합 금지."""
    h1 = {2024: {'매출액': 1000.0}, 2022: {'매출액': 800.0}, 2021: {'매출액': 700.0}}
    fy = {2023: {'매출액': 2000.0}, 2022: {'매출액': 1800.0},
          2021: {'매출액': 1600.0}, 2020: {'매출액': 1400.0}}
    _patch(monkeypatch, fy, h1)
    out = load_pit_series_ttm(None, REBAL, report_type='H1')['X']
    # 종전 위치 구현: FY_2023 − H1_2022 + H1_2024 = 2000−800+1000 = 2200 (오조합)
    assert '매출액' not in out[0]                       # TTM_2024: H1_2023 결측 → 부재
    assert '매출액' not in out[1]                       # TTM_2023: H1_2023 결측 → 부재
    assert out[2]['매출액'] == 1600.0 - 700.0 + 800.0   # TTM_2022 = 1700 (전부 존재)


def test_ttm_fy_anchor_by_year(monkeypatch):
    """FY 리밸런싱: 최신 FY 연도 앵커로 [y_f, y_f-1, y_f-2] 연속 연도."""
    fy = {2023: {'매출액': 2000.0}, 2022: {'매출액': 1800.0}, 2021: {'매출액': 1600.0}}
    _patch(monkeypatch, fy, {})
    out = load_pit_series_ttm(None, date(2024, 4, 3), report_type='FY')['X']
    assert out[0]['매출액'] == 2000.0
    assert out[1]['매출액'] == 1800.0
    assert out[2]['매출액'] == 1600.0


def test_ttm_fy_anchor_missing_middle_year_pads_empty(monkeypatch):
    """FY 갭(2022 결측): y_f-1 자리는 빈 dict — 비연속 연도를 당겨오지 않는다."""
    fy = {2023: {'매출액': 2000.0}, 2021: {'매출액': 1600.0}}
    _patch(monkeypatch, fy, {})
    out = load_pit_series_ttm(None, date(2024, 4, 3), report_type='FY')['X']
    assert out[0]['매출액'] == 2000.0
    assert out[1] == {}                 # 2022 결측 → 빈 dict (종전: 2021을 당겨옴)
    assert out[2]['매출액'] == 1600.0   # 2021 은 정확히 y_f-2 자리


# ── Q1/Q3 확장 (§6-2, Q-D) — H1과 동일 산식, interim report_type만 다르다 ────────

def test_ttm_q1_anchor_same_formula_as_h1(monkeypatch):
    """Q1 리밸런싱: TTM_y = FY_{y-1} − Q1_{y-1} + Q1_y — H1과 동일 산식."""
    q1 = {2024: {'매출액': 1000.0}, 2023: {'매출액': 900.0},
          2022: {'매출액': 800.0},  2021: {'매출액': 700.0}}
    fy = {2023: {'매출액': 2000.0}, 2022: {'매출액': 1800.0},
          2021: {'매출액': 1600.0}, 2020: {'매출액': 1400.0}}
    _patch(monkeypatch, fy, q1)
    out = load_pit_series_ttm(None, date(2024, 5, 20), report_type='Q1')['X']
    assert out[0]['매출액'] == 2000.0 - 900.0 + 1000.0  # TTM_2024 = 2100
    assert out[1]['매출액'] == 1800.0 - 800.0 + 900.0   # TTM_2023 = 1900
    assert out[2]['매출액'] == 1600.0 - 700.0 + 800.0   # TTM_2022 = 1700


def test_ttm_q3_anchor_same_formula_as_h1(monkeypatch):
    """Q3 리밸런싱: TTM_y = FY_{y-1} − Q3_{y-1} + Q3_y — H1과 동일 산식."""
    q3 = {2024: {'매출액': 1000.0}, 2023: {'매출액': 900.0},
          2022: {'매출액': 800.0},  2021: {'매출액': 700.0}}
    fy = {2023: {'매출액': 2000.0}, 2022: {'매출액': 1800.0},
          2021: {'매출액': 1600.0}, 2020: {'매출액': 1400.0}}
    _patch(monkeypatch, fy, q3)
    out = load_pit_series_ttm(None, date(2024, 11, 19), report_type='Q3')['X']
    assert out[0]['매출액'] == 2000.0 - 900.0 + 1000.0  # TTM_2024 = 2100
    assert out[1]['매출액'] == 1800.0 - 800.0 + 900.0   # TTM_2023 = 1900
    assert out[2]['매출액'] == 1600.0 - 700.0 + 800.0   # TTM_2022 = 1700


def test_ttm_q3_missing_middle_year_no_silent_miscombination(monkeypatch):
    """Q3_2023 결측: TTM_2024는 interim_{y-1} 부재로 계정 없음 — 위치 당김 오조합 금지."""
    q3 = {2024: {'매출액': 1000.0}, 2022: {'매출액': 800.0}, 2021: {'매출액': 700.0}}
    fy = {2023: {'매출액': 2000.0}, 2022: {'매출액': 1800.0},
          2021: {'매출액': 1600.0}, 2020: {'매출액': 1400.0}}
    _patch(monkeypatch, fy, q3)
    out = load_pit_series_ttm(None, date(2024, 11, 19), report_type='Q3')['X']
    assert '매출액' not in out[0]                       # TTM_2024: Q3_2023 결측 → 부재
    assert '매출액' not in out[1]                       # TTM_2023: Q3_2023 결측 → 부재
    assert out[2]['매출액'] == 1600.0 - 700.0 + 800.0   # TTM_2022 = 1700 (전부 존재)


def test_ttm_invalid_report_type_raises():
    """미지원 report_type은 조용히 넘어가지 않고 예외를 던진다."""
    with pytest.raises(ValueError):
        load_pit_series_ttm(None, date(2024, 1, 1), report_type='H2')
