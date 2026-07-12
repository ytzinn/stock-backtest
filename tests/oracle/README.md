# tests/oracle/

**독립 오라클 — "기존 값과 같은가"가 아니라 "수학적·경제적으로 옳은가"를 검증한다.**

- DB 미접속. 손계산 가능한 합성 케이스만 사용한다.
- `tests/characterization/`(기존 동작 기록)과 정반대 계약이다:
  **버그 수정 시 characterization은 깨질 수 있지만, oracle이 깨지면 수정이 틀린 것이다.**
  (AUDIT_00_MASTER.md §1 원칙 1)
- 상수(RF, RK, OMEGA, VB_CAP, DELISTING_HAIRCUT, 거래비용)는 전부 프로덕션 SSOT에서
  import한다. 테스트 안에 숫자를 다시 하드코딩하지 않는다.

## ⚠ 의도적으로 실패 상태로 남긴 테스트 (P0 증거)

AUDIT_01 Pass 0C 지시에 따라, 아래 테스트는 **현재 구현의 결함을 재현하는 오라클**이며
xfail 처리하지 않고 실패 상태 그대로 둔다. **이 테스트를 통과시키려고 오라클을 고치지 마라 —
Pass 3에서 프로덕션 코드를 고치면 저절로 통과한다.**

| 실패 테스트 | 재현하는 결함 | TECH_DEBT ID |
|---|---|---|
| `test_engine_return_oracle.py::test_weighted_return_consumes_portfolio_weights` | `_calc_period_return()`이 weight를 소비하지 않고 단순평균 | CORR-ENGINE-001 |
| `test_engine_return_oracle.py::test_delisting_adjustments_are_order_independent` | 상폐 opt/cons 조정값이 종목 순회 순서에 의존 | CORR-ENGINE-002 |
| `test_turnover_oracle.py::test_turnover_expansion_5_to_20_stocks` | turnover 산식이 비중 변화를 무시 (거래비용 입력값 오염) | CORR-METRIC-001 |
| `test_metrics_oracle.py::test_cagr_uses_actual_calendar_days` | CAGR 연수를 캘린더일수가 아니라 구간수÷2로 계산 | CORR-METRIC-002 |
| `test_metrics_oracle.py::test_sharpe_zero_variance_returns_zero` | zero-variance 가드가 returns.std()를 검사하고 나눗셈은 excess.std()로 → inf 가능 (오라클 작성 중 신규 발견) | CORR-METRIC-003 |

fast suite를 "전부 통과" 기준으로 쓰려면 위 5개를 제외하고 본다:
`pytest -m "not integration" --deselect <...>` 또는 TECH_DEBT.md의 해당 항목이 닫혔는지 확인.
