"""
Pass 2 — P0 항목 재현용 최소 실패 테스트 (DB 미접속분).

⚠ 이 파일의 테스트는 **의도적으로 실패 상태**다 (tests/oracle/README.md 표 참조).
  각 실패가 TECH_DEBT.md 해당 항목의 재현 증거이며, Pass 3에서 프로덕션을 고치면
  저절로 통과한다. 통과시키려고 테스트를 고치지 마라.
"""
from __future__ import annotations

import inspect
import sys
import types
from datetime import date

import pytest

import backtest.engine as engine
import backtest.filters.hard_filter as hard_filter_mod
from backtest.engine import BacktestEngine
from backtest.filters.hard_filter import _hard_filter


# ── CORR-ENGINE-003: valuation_date 주입 계약 ───────────────────────────────────

def test_engine_run_accepts_injected_valuation_date():
    """
    [CORR-ENGINE-003 + CORR-FRESH-001 재현/계약]
    같은 코드·같은 DB면 실행 날짜와 무관하게 같은 결과가 나와야 한다.
    현재 engine.run()은 마지막(열린) 구간 종료일을 내부에서 date.today()로 정하므로
    (engine.py:69) 호출자가 평가 기준일을 고정할 방법이 없다.

    기대 계약 (AUDIT_02 B-2 해법 제안): run(rebalance_dates, valuation_date=...) 주입.
    수정 시 valuation_date는 price_history 최신일과의 신선도 검증(CORR-FRESH-001)의
    기준점도 된다 — Pass 0B tape #23이 end=2026-07-11 라벨로 2026-05-22 stale 가격을
    사용한 실사례 참조 (AUDIT_MANIFEST.json).

    ⚠ 의도적 실패 — 현재 시그니처: (rebalance_dates, run_name, ablation_tag).
    """
    params = inspect.signature(BacktestEngine.run).parameters
    assert 'valuation_date' in params, (
        'engine.run()에 valuation_date 주입 파라미터가 없다 — 열린 구간 종료일이 '
        'date.today()로 결정돼 실행 날짜마다 결과가 달라진다 (CORR-ENGINE-003)'
    )


# ── CORR-BENCH-001: 벤치마크 조회 실패는 실패로 전파돼야 한다 ─────────────────────

def _install_broken_fdr(monkeypatch):
    broken = types.ModuleType('FinanceDataReader')

    def _raise(*args, **kwargs):
        raise ConnectionError('네트워크 장애 (합성)')

    broken.DataReader = _raise
    monkeypatch.setitem(sys.modules, 'FinanceDataReader', broken)


@pytest.mark.parametrize('fn_name', ['_calc_kospi_return', '_calc_kosdaq_return'])
def test_benchmark_fetch_failure_must_not_become_zero_return(monkeypatch, fn_name):
    """
    [CORR-BENCH-001 재현]
    네트워크 장애가 "벤치마크 0% 수익"으로 둔갑하면 alpha·robustness가 조용히 오염되고
    백테스트는 성공 상태로 끝난다. 기대 계약: 예외 전파 (또는 호출자가 명시적으로
    allow_missing을 넘긴 경우에만 결측 허용 — AUDIT_03 재발 방지 규칙).

    ⚠ 의도적 실패 — 현재 구현은 except Exception → log.warning + return 0.0.
    """
    _install_broken_fdr(monkeypatch)
    fn = getattr(engine, fn_name)
    with pytest.raises(Exception):
        fn(date(2024, 4, 3), date(2024, 8, 20))


# ── CORR-HARD-001: listed_date NULL이면 상장기간 검사가 통과되는 문제 ─────────────

def _patch_hard_filter_env(monkeypatch, listed_date=None, first_price_date=None):
    monkeypatch.setattr(hard_filter_mod, 'has_recent_trade', lambda *a, **k: True)
    monkeypatch.setattr(hard_filter_mod, 'get_avg_turnover', lambda *a, **k: 1e12)
    monkeypatch.setattr(hard_filter_mod, 'is_delisted_at', lambda *a, **k: False)
    monkeypatch.setattr(hard_filter_mod, 'get_listed_date', lambda *a, **k: listed_date)
    monkeypatch.setattr(hard_filter_mod, 'get_first_price_date',
                        lambda *a, **k: first_price_date)


def test_unknown_listed_date_must_not_bypass_seasoning_filter(monkeypatch):
    """
    [CORR-HARD-001 확정 계약 — Pass 3 수정으로 통과 전환]
    MASTER §3-3·SPEC_03 계약: 상장 6개월 미만 종목은 Hard Filter에서 제외.
    종전 구현은 listed_date NULL이면 검사를 생략했다 (운영 DB 92%가 NULL → 요건 사망,
    실편입 6건 재현 — IMPACT_MATRIX §2). 수정: NULL이면 가격 이력 최초일 프록시로 판정
    (+ 배포 후 listed_date 백필, 사용자 결정 2026-07-12).

    합성: listed_date 불명 + 첫 가격이 리밸 30일 전 (신규 상장) → 제외돼야 한다.
    """
    rebal = date(2024, 4, 3)
    _patch_hard_filter_env(monkeypatch, listed_date=None,
                           first_price_date=date(2024, 3, 4))   # 30일 전 상장 신호

    ok, reason = _hard_filter(
        'NEWLY', rebal, pit_series_for_ticker=[{'자본총계': 1.0}], conn=None,
        min_turnover=100_000_000, min_listed_months=6,
    )
    assert ok is False, (
        'listed_date를 알 수 없고 가격 이력이 30일뿐인 종목이 상장기간 검사를 통과했다 '
        '(CORR-HARD-001)'
    )


def test_unknown_listed_date_with_long_price_history_passes(monkeypatch):
    """프록시 반대 방향: 가격 이력이 충분히 오래된 종목은 listed_date NULL이어도 통과."""
    rebal = date(2024, 4, 3)
    _patch_hard_filter_env(monkeypatch, listed_date=None,
                           first_price_date=date(2014, 1, 2))   # 수집 시작일 = 구주

    ok, _ = _hard_filter(
        'OLDIE', rebal, pit_series_for_ticker=[{'자본총계': 1.0}], conn=None,
        min_turnover=100_000_000, min_listed_months=6,
    )
    assert ok is True
