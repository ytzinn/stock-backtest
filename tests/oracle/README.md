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
| `test_metrics_oracle.py::test_sharpe_zero_variance_returns_zero` | zero-variance 가드가 returns.std()를 검사하고 나눗셈은 excess.std()로 → inf 가능 (오라클 작성 중 신규 발견) | CORR-METRIC-003 |

### Pass 2 추가분 (2026-07-12)

| 실패 테스트 | 재현하는 결함 | TECH_DEBT ID |
|---|---|---|
| `test_pass2_contracts.py::test_unknown_listed_date_must_not_bypass_seasoning_filter` | listed_date NULL → 상장 6개월 검사 생략 | CORR-HARD-001 (P0-A) |
| `tests/integration/test_pass2_pit_gate.py` 2건 | 게이트 비결정 병합 / 게이트 룩어헤드 | CORR-GATE-001/002 |

### 해소됨 (Pass 3)

| 테스트 | 항목 | 해소 PR |
|---|---|---|
| `test_delisting_adjustments_are_order_independent` | CORR-ENGINE-002 | audit/CORR-ENGINE-002 (2-pass 재작성 + tie-break 고정) |
| `test_weighted_return_consumes_portfolio_weights` | CORR-ENGINE-001 | audit/CORR-ENGINE-001 (weight 소비 + 재정규화) |
| `test_engine_run_accepts_injected_valuation_date` | CORR-ENGINE-003 (+METRIC-002·FRESH-001) | audit/CORR-ENGINE-003 (valuation_date 주입 + closed-period 공식 기준 + 캘린더 CAGR + 신선도 경고) |
| `test_cagr_uses_actual_calendar_days` | CORR-METRIC-002 | audit/CORR-ENGINE-003 (compute_cagr에 캘린더 경계 파라미터) |
| `test_turnover_expansion_5_to_20_stocks` | CORR-METRIC-001 (P0-A) | audit/CORR-METRIC-001 (0.5Σ|Δw| 표준 정의) — **characterization 5건 정당 깨짐, baseline 갱신 승인 대기** |
| `test_benchmark_fetch_failure_must_not_become_zero_return` (×2) | CORR-BENCH-001 | audit/CORR-BENCH-001 (BenchmarkDataUnavailable 예외, regime 복제본 포함) |
| `test_avg_turnover_missing_data_must_not_be_silent_zero` | CORR-DA-001 | audit/CORR-DA-001 (PriceDataUnavailable + hard_filter 명시 사유) |
| `test_amended_row_without_original_must_not_leak_amended_value` | PIT-AMEND-001 (P0-A) | audit/PIT-AMEND-001 (원본 미상 계정 노출창 제외 + XBRL 백필 런북) |

**정상 상태 요약**: fast suite = 통과 다수 + **의도적 실패 2개(HARD-001·sharpe P2)** + xfail 1개 + **characterization 정당 깨짐 5개(METRIC-001, baseline 승인 대기)**.
integration suite = 통과 32개 + **의도적 실패 2개(GATE×2)**.
이 실패들을 통과시키려고 테스트를 고치지 마라 — Pass 3의 프로덕션 수정이 통과시킨다.
