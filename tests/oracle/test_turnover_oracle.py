"""
[O-4] turnover 오라클 — 올바른 정의: turnover = 0.5 × Σ_{t∈prev∪curr} |w_new[t] − w_old[t]|

현금 비중 규약: 포트폴리오는 항상 100% 투자(Σw=1.0)로 가정한다. 빈 포트폴리오(현금 100%)
와의 전환은 첫 구간 관례(전액 신규 매수 = 1.0)를 따른다. 이 정의에서 turnover 1.0 =
포트폴리오 전체를 팔고 전체를 새로 사는 완전 교체다 (단방향 매도분 + 단방향 매수분의 평균).

[검증된 사실 — 소비처] engine.py:110-112 에서 turnover가 거래비용에 직접 입력된다:
    tc = turnover * (COST_SELL + COST_BUY);  net_ret = gross_ret - tc
따라서 turnover 오류는 리포트 지표에 그치지 않고 **모든 시나리오의 net 수익률을 오염**시킨다
→ AUDIT_00 §5 분기 기준에 따라 CORR-METRIC-001은 P0-A다.

⚠ test_turnover_expansion_5_to_20_stocks 는 의도적 실패 상태 (CORR-METRIC-001 증거).
  현행 산식 sold/max(len(prev),len(curr),1) 은 종목 수가 같고 등가중일 때만 올바른 정의와
  일치한다 (합집합 항등식으로 증명 가능) — 종목 수가 바뀌는 구간에서 어긋난다.
"""
from __future__ import annotations

import pytest

from backtest.engine import _calc_turnover


def turnover_true(prev: dict[str, float], curr: dict[str, float]) -> float:
    """오라클 정의: 0.5 × Σ |w_new − w_old| (합집합 순회)."""
    tickers = set(prev) | set(curr)
    return 0.5 * sum(abs(curr.get(t, 0.0) - prev.get(t, 0.0)) for t in tickers)


def _equal_weight(tickers: list[str]) -> dict[str, float]:
    w = 1.0 / len(tickers)
    return {t: w for t in tickers}


def test_turnover_expansion_5_to_20_stocks():
    """
    AUDIT_01 지정 케이스: 이전 5종목(각 20%) → 신규 20종목(기존 5종목 전부 잔류, 각 5%).
    올바른 값: 0.5 × (5×|0.05−0.20| + 15×|0.05−0|) = 0.5 × (0.75 + 0.75) = 0.75.
    실제로는 기존 종목을 20%→5%로 줄이고 15종목을 신규 매수하는 대규모 재조정이다.

    ⚠ 현행 산식은 sold=0 → turnover 0.0 을 반환한다. 이 실패가 CORR-METRIC-001 증거다.
      Pass 0B 실측 baseline에서도 종목 수 변동 구간이 실재한다: F_no_r2r3 기준
      2016-08-18(20→5), 2017-04-05(5→20), 2020-04-03(20→7), 2020-08-20(7→20).
      해당 구간의 기록된 turnover(→net 수익률)는 전부 이 산식으로 계산된 값이다.
      실측 tape 기반 정량 대조는 scripts/audit/turnover_impact_scan.py 결과
      (GAPS.md Pass 0C 절) 참조.
    """
    prev = _equal_weight([f'P{i}' for i in range(5)])                      # 각 0.20
    curr = _equal_weight([f'P{i}' for i in range(5)] +
                         [f'Q{i}' for i in range(15)])                     # 각 0.05

    expected = turnover_true(prev, curr)
    assert expected == pytest.approx(0.75, abs=1e-12)   # 오라클 자기 검증
    assert _calc_turnover(prev, curr) == pytest.approx(expected, abs=1e-12)


def test_turnover_agrees_when_n_equal_and_equal_weight():
    """
    종목 수가 같고 등가중이면 현행 산식 sold/n == 0.5×Σ|Δw| (항등).
    잔류 종목 Δw=0, 이탈 s개 × 1/n 매도 + 신규 s개 × 1/n 매수 → 0.5×2s/n = s/n.
    현행 구현이 이 부분집합에서는 옳다는 것을 고정한다 (통과 유지).
    """
    prev = _equal_weight([f'A{i}' for i in range(20)])
    curr = _equal_weight([f'A{i}' for i in range(10)] + [f'B{i}' for i in range(10)])

    expected = turnover_true(prev, curr)
    assert expected == pytest.approx(0.5, abs=1e-12)
    assert _calc_turnover(prev, curr) == pytest.approx(expected, abs=1e-12)


def test_turnover_full_replacement_is_one():
    """완전 교체(동일 종목 수) → 1.0. 현행 구현도 일치 (통과 유지)."""
    prev = _equal_weight([f'A{i}' for i in range(20)])
    curr = _equal_weight([f'B{i}' for i in range(20)])
    assert turnover_true(prev, curr) == pytest.approx(1.0, abs=1e-12)
    assert _calc_turnover(prev, curr) == pytest.approx(1.0, abs=1e-12)


def test_turnover_first_period_convention_full_buy():
    """첫 구간(prev 없음) = 전액 신규 매수 1.0 — 문서화된 관례 (통과 유지)."""
    curr = _equal_weight([f'A{i}' for i in range(20)])
    assert _calc_turnover({}, curr) == 1.0


def test_turnover_no_change_is_zero():
    prev = _equal_weight([f'A{i}' for i in range(20)])
    assert turnover_true(prev, dict(prev)) == 0.0
    assert _calc_turnover(prev, dict(prev)) == 0.0
