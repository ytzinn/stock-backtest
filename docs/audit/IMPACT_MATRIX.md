# IMPACT_MATRIX — Pass 2 재현·영향 행렬 (2026-07-12)

> AUDIT_03 Pass 2 산출물. 프로덕션 코드 미수정 — 모든 수치는 Pass 0B tape(원시 float)와
> 운영 DB 읽기전용 조회에서 산출. 라벨 규칙은 AUDIT_00 §1 원칙 6.
>
> ---
> ## ⚠ 사후 정정 (2026-07-12, Pass 3 배포 검증 중)
>
> **§2의 PIT-AMEND-001 "실편입 26쌍 오염" 산출은 과장이었다.** 근거로 삼은
> `financials_pit.amendment_from`이 정정공시 여부(`is_amendment`)를 보지 않고 부여되고
> 있었다 — 같은 보고서에 공시 행이 2개 이상이기만 하면 정정으로 표시됐다 (PIT-AMEND-002,
> PR #11에서 수정).
>
> | 실측 (운영 DB) | 행수 |
> |---|---|
> | `amendment_from` 보유 | 86,379 |
> | └ **정정공시 없음 = 오탐** | **10,226 (11.8%)** |
> | PIT-AMEND-001 제외 대상(원본 미상) | 18,676 |
> | └ 오탐 | **10,226 (55%)** |
> | └ 진짜 정정 → 백테스트 사용 계정 | 2,091 |
>
> §2가 오염 사례로 든 000880·000150 등은 **정정공시가 없는 그룹**이었다.
> → PIT-AMEND-001은 **P0-B로 환원**, PIT 재빌드 후 재측정에서 실편입 오염이 확인되면 재승격.
> XBRL 백필(사용자 결정 (c))은 실행 결과 **원본 3행만 복구** — 누락분이 XBRL에 존재하지 않는
> 계정(`지배기업소유주지분_1` 등 DART 이름충돌 fallback)에 몰려 있어 원리적으로 복구 불가.
>
> §2·§6의 PIT-AMEND 관련 서술은 이 정정을 반영해 읽어라. 나머지 항목(turnover·HARD·GATE·
> ENGINE 계열)의 산출은 영향받지 않는다.
> ---

## 1. 재현 현황 — P0 11건 전건 실패 테스트 확보

| 항목 | 실패 테스트 | 작성 시점 | 재현 판정 |
|---|---|---|---|
| CORR-METRIC-001 (P0-A) | oracle/test_turnover_oracle.py::test_turnover_expansion_5_to_20_stocks | Pass 0C | ✅ + 실측 오염 |
| CORR-ENGINE-001 | oracle/test_engine_return_oracle.py::test_weighted_return_consumes_portfolio_weights | Pass 0C | ✅ 합성 |
| CORR-ENGINE-002 | oracle/test_engine_return_oracle.py::test_delisting_adjustments_are_order_independent | Pass 0C | ✅ 합성 (실데이터 공존 0건) |
| CORR-METRIC-002 | oracle/test_metrics_oracle.py::test_cagr_uses_actual_calendar_days | Pass 0C | ✅ |
| CORR-ENGINE-003 | oracle/test_pass2_contracts.py::test_engine_run_accepts_injected_valuation_date | **Pass 2** | ✅ (계약 부재) |
| CORR-FRESH-001 | 위 테스트와 결합 + tape #23 실사례(07-11 라벨, 05-22 가격) | Pass 1 | ✅ 실사례 |
| CORR-BENCH-001 | oracle/test_pass2_contracts.py::test_benchmark_fetch_failure_must_not_become_zero_return[×2] | **Pass 2** | ✅ 합성 |
| CORR-HARD-001 | oracle/test_pass2_contracts.py::test_unknown_listed_date_must_not_bypass_seasoning_filter | **Pass 2** | ✅ + **실측 편입 재현** |
| PIT-AMEND-001 | integration/test_pass2_pit_gate.py::test_amended_row_without_original_must_not_leak_amended_value | **Pass 2** | ✅ + **실측 편입 재현** |
| CORR-GATE-001 | integration/test_pass2_pit_gate.py::test_gate_load_accounts_must_prefer_cfs_deterministically | **Pass 2** | ✅ 합성 (실데이터 충돌 0건) |
| CORR-GATE-002 | integration/test_pass2_pit_gate.py::test_gate_verdict_must_reflect_values_known_at_rebalance | **Pass 2** | ✅ 합성 (편입 침투 0건) |
| CORR-DA-001 | integration/test_pass2_pit_gate.py::test_avg_turnover_missing_data_must_not_be_silent_zero | **Pass 2** | ✅ 합성 |

재현 불가 항목: **없음.** 등급 강등 없음. 승격 2건 (아래 §2).

## 2. 등급 승격 2건 — 실데이터 편입 오염 재현

### PIT-AMEND-001 → **P0-A 승격**
[검증된 사실] Pass 0B tape의 실제 편입 817쌍 × 운영 DB 교차: **26개 (ticker, 리밸런싱일)
쌍에서 편입 종목의 재무 계정이 룩어헤드 값**(리밸 시점 시장 미공개 정정값, 원본 미캡처)으로
계산됐다. 000880(2021-08-19)·000150(2023-08-18)·065150(2024-04-03)·038540(2025-04-03)·
003380(2024-08-20)·002020(2020-08-20)·024910(2019-04-03) 등은 RIM 입력 3종(당기순이익·
영업활동현금흐름·자본총계)을 포함한 9개 계정 전부가 오염. 상당수는 RIM equity 2순위 키인
`지배기업소유주지분_1` 단독 오염.
유니버스 레벨로는 매 리밸런싱마다 15~599행(7~216종목)이 노출 창에 있었다.
**반사실 복원 불가**: 원본값이 소실됐으므로 "올바른 실행"의 결과를 재계산할 수 없다 —
AUDIT_03 §3 원칙("선택 자체가 오염됐으면 최종 숫자 변화가 작아도 중대 결함") 그대로 적용.
→ **selection 오염** (산술 아님).

### CORR-HARD-001 → **P0-A 승격**
[검증된 사실] tape 편입 종목 284개 **전원**이 stocks.listed_date NULL = 상장기간 검사를
아무도 안 받았다. 가격 이력 시작일 프록시로 6개월 미만 의심 편입 **6건 재현**:

| ticker | 리밸런싱일 | 첫 가격일 | 상장~편입 |
|---|---|---|---|
| 204270 | 2020-04-03 | 2020-03-04 | **~30일** |
| 237690 | 2016-08-18 | 2016-06-23 | ~56일 |
| 237750 | 2016-08-18 | 2016-07-04 | ~45일 |
| 228340 | 2016-08-18 | 2016-04-06 | ~4.4개월 |
| 377740 | 2023-04-05 | 2022-12-22 | ~3.4개월 |
| 004440 | 2016-08-18 | 2016-03-30 | ~4.7개월 |

[확실하지 않은 사실] 첫 가격일은 상장일의 프록시(수집 시작 아티팩트 가능) — 확인법:
krx_listing_snapshots 최초 등장/외부 IPO 기록 대조. 204270(2020-03 IPO)·237690(2016-06
IPO)은 실제 신규 상장과 부합. → **selection 오염** (스펙이 금지한 종목이 편입됨).

## 3. 침투 없음이 확인된 항목 (유니버스 오염 ≠ 편입 오염)

- **CORR-GATE-002** [검증된 사실]: 자본총계 부호 플립의 노출 창 × 리밸런싱 교차 결과,
  "잘못 포함 가능" 후보가 18개 리밸런싱일에 1~19종목 실재 (2023-08-18 peak 19종목).
  그러나 **실제 편입 침투 0건** — 그런 종목은 R6·모멘텀 등 후속 필터에서 걸러진 것으로
  보임 [Claude 의견]. P0-B 유지. 단 랜덤 시나리오(A/B/C 분포)와 FactorScreener top 20%
  컷은 유니버스 구성에 직접 의존하므로 진단·아카이브 시나리오의 분포는 미세 오염 가능.
- **CORR-ENGINE-002** [검증된 사실]: 상폐+가격결측 공존 실측 0건 (Pass 0C 스캔). P0-B 유지.
- **CORR-GATE-001** [검증된 사실]: 게이트 핵심 계정 CFS/OFS 값 충돌 실측 0건. P0-B 유지.

## 4. 수정 후보 차이표 (tape 기반 shadow 계산, 프로덕션 미수정)

**selection/aggregate 구분** (Pass 0B 2계층 설계 활용):
- 후보 A·B는 **selection 불변** — 순수 산술 수정. 편입 종목 Jaccard = 1.000 (전 구간).
- PIT-AMEND-001·CORR-HARD-001·CORR-GATE-002 수정은 **selection 변경** — 필터/데이터 수정.
  변경 예상 구간: §2 표의 해당 (ticker, 리밸) 쌍이 속한 구간.

### 후보 A — CORR-METRIC-001 수정 (turnover = 0.5Σ|Δw|)

| 시나리오 | net CAGR 현행 | net CAGR 수정 | Δ |
|---|---|---|---|
| F_no_r2r3 (CANONICAL) | 14.091% | 14.009% | **−0.082%p** |
| D_rim_only / D_no_r2 | 10.657% | 10.608% | −0.050%p |
| D_no_r3 | 11.179% | 11.129% | −0.050%p |
| D_no_stability | 9.752% | 9.706% | −0.046%p |

gross/Sharpe(gross)/MDD/robustness/benchmark 불변. 영향 구간: 종목 수 변동 4개 구간만.

### 후보 B — CORR-ENGINE-003+METRIC-002+FRESH-001 수정 (closed-period 공식 기준 + 캘린더 연수)

| 시나리오 | CAGR 현행(공표) | closed+현행연수 | closed+캘린더연수 | Sharpe 현행→closed | MDD |
|---|---|---|---|---|---|
| F_no_r2r3 | 15.272% | 15.651% | **15.663%** | 0.531→0.536 | 불변 −28.68% |
| D_rim_only / D_no_r2 | 11.656% | 12.080% | 12.089% | 0.423→0.432 | 불변 −33.94% |
| D_no_r3 | 12.194% | 12.647% | 12.656% | 0.445→0.455 | 불변 −30.14% |
| D_no_stability | 10.681% | 11.170% | 11.178% | 0.378→0.390 | 불변 −41.38% |

핵심 관찰 [검증된 사실]: 변동의 거의 전부가 **열린 구간 #23 제외**에서 나온다
(+0.38~0.49%p). 캘린더 연수 전환 자체는 closed 구간이 4월→4월 정렬(9.993y vs 10.0y)이라
+0.01%p 수준. → **결합 가설 확정**: #23을 공식 기준에서 제외하면 두 항목이 동시 소거되고,
캘린더 연수 전환의 잔여 영향은 무시 가능 수준.

### 후보 A+B 동시 적용: CANONICAL net CAGR = 14.392% (현행 공표 14.091% 대비 +0.301%p)

### 숫자 불변이 예상되는 수정 (검증된 근거)
- CORR-ENGINE-001 (weight 소비): 현행 전 시나리오 등가중 → 가중평균 == 단순평균. Δ=0.
- CORR-ENGINE-002 (순서 독립): 공존 0건 → 캡처 시나리오 Δ=0.
- CORR-BENCH-001 / CORR-DA-001: 실패 이력·결손 이력 0건 → 과거 결과 Δ=0 (미래 실행의 안전장치).

## 5. 결합 판정

| 결합 | 근거 | 처리 |
|---|---|---|
| ENGINE-003 ↔ METRIC-002 ↔ FRESH-001 | #23 열린 구간 공유. §4-B로 동시 소거 검증 완료 | **1 PR로 묶음 (확정)** |
| ENGINE-001 ↔ ENGINE-002 ↔ SORT-001(P1) | 동일 함수 `_calc_period_return`+정렬 체인. 정렬 tie-break 변경은 ENGINE-002 값에 파급 | 연속 PR (1→2), tie-break는 ENGINE-002 PR에서 함께 고정 |
| PIT-AMEND-001 ↔ CORR-GATE-002 ↔ DOC-PIT-001(P1) | 셋 다 "정정 PIT 데이터 모델"의 산물 — 원본 보존 구조 결정에 공동 종속 | 정책 결정 후 연속 처리 |
| METRIC-001 ↔ SPEC_08 비대칭 비용 설계 | 수정 산식이 Phase B 설계 입력 | PR에서 SPEC_08과 정합 확인 |
| HARD-001 ↔ listed_date 백필(krx_listing_snapshots) | 코드 가드만으론 92% NULL 해소 불가 | 데이터 백필 + 코드 가드 병행 |

## 6. 수정 순서 제안 (Pass 3)

| 순서 | PR | 내용 | 근거 |
|---|---|---|---|
| 1 | audit/CORR-ENGINE-002 | 순서 독립 + tie-break 고정(SORT-001) | AUDIT_03 권장 첫 대상. 캡처 시나리오 Δ=0 → characterization 안 깨져 승인 부담 최소 |
| 2 | audit/CORR-ENGINE-001 | weight 소비 | 같은 함수 연속 작업, Δ=0 |
| 3 | audit/CORR-ENGINE-003 | valuation_date 주입 + closed-period 공식 기준 + 신선도 가드 (METRIC-002·FRESH-001 동시 소거) | **기준 안정화를 먼저** 해야 이후 PR들의 diff가 열린 구간 노이즈 없이 읽힌다. characterization 깨짐(정당) — §4-B 표가 승인 자료 |
| 4 | audit/CORR-METRIC-001 | turnover 산식 (P0-A) | 기준 안정화 후 적용. Δ표 §4-A. SPEC_08 조율 |
| 5 | audit/CORR-BENCH-001 | 예외 전파 (+regime 복제본) | 과거 결과 Δ=0, 미래 안전장치 |
| 6 | audit/CORR-DA-001 | 결손≠무거래 구분 | 〃 |
| 7 | audit/PIT-AMEND-001 | **정책 결정 필요** (P0-A): (a) 해당 계정 제외 (b) available_from을 amendment_from으로 밀기 (c) XBRL 원본 백필 후 정상 경로 | selection 변경 — 26쌍 관련 구간 재실행 diff 필수. GATE-002·DOC-PIT-001과 데이터 모델 결정 공유 |
| 8 | audit/CORR-HARD-001 | listed_date 백필 + NULL 가드 (P0-A) | selection 변경 — 6건 이상 제외 예상 |
| 9 | audit/CORR-GATE-00x | 게이트 결정성(001) + PIT화(002) | 가장 큰 설계 결정. 유니버스 재산정 수반 |

[Claude 의견] 7~9는 사용자 정책 결정이 선행돼야 하므로, 1~6을 먼저 처리하고 7~9는
결정 대기 상태로 두는 것을 권장한다.
