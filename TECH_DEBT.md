# TECH_DEBT — 코드 정합성 부채 대장

> 포맷: AUDIT_00_MASTER.md §4. 라인 번호는 보조 정보 — **심볼과 commit SHA가 기준**이다.
> 증거 테스트 중 "의도적 실패" 상태인 것들은 tests/oracle/README.md 참조 — 통과시키려고
> 오라클을 고치지 마라.
>
> **기준 commit: de93559** (개별 `Commit:` 표기가 없는 항목의 공통 기준. Pass 0C 등재분은 5ea5c48).
>
> **상태: Pass 2 재현·영향 행렬 완료 (2026-07-12, Fable 5).**
> P0 11건 전건에 실패 테스트 커밋 (재현 불가 0건). 실데이터 편입 오염이 재현된
> **PIT-AMEND-001·CORR-HARD-001을 P0-A로 승격** (P0-A 3건 · P0-B 8건).
> 영향 행렬·수정 후보 차이표·결합 판정·수정 순서는 **IMPACT_MATRIX.md** 참조.
> 다음 단계: Pass 3 — IMPACT_MATRIX §6 순서로 항목당 1 PR (7~9번은 정책 결정 선행).

---

## P0-A — 숫자가 틀렸음이 재현됨 (3건)

### CORR-METRIC-001 — turnover 산식이 비중 변화를 무시 → 거래비용·net 수익률 오염
- **Commit**: 5ea5c48
- **Location**: `backtest/engine.py::_calc_turnover` (Lines 186-195)
- **Expected contract**: turnover = 0.5 × Σ|w_new − w_old| (재조정 규모의 표준 정의)
- **Actual behavior**: `sold / max(len(prev), len(curr), 1)` — 이탈 종목 수 비율만 계산.
  종목 수가 같고 등가중이면 항등식으로 우연히 일치, **종목 수가 바뀌는 구간에서 어긋남**.
- **Result impact**: **Y — 재현됨.** `engine.py:110-112`에서 `tc = turnover × (COST_SELL+COST_BUY)`로
  거래비용에 직접 입력 [검증된 사실]. Pass 0B tape 스캔 실측:
  CANONICAL(F_no_r2r3) 2017-04-05 기록 0.25 vs 참값 1.00 / 2020-08-20 0.35 vs 1.00
  → 누적 거래비용 0.952%p 과소계상, net CAGR 유리하게 보고. D 계열 4개도 동일 패턴.
- **Affected scenarios**: 전 시나리오의 `net_*`/`avg_turnover` (종목 수 변동 구간 보유 시). gross 무관.
- **Evidence**: `tests/oracle/test_turnover_oracle.py::test_turnover_expansion_5_to_20_stocks`(의도적 실패)
  + `scripts/audit/turnover_impact_scan.py`
- **Pass 1 판정**: **P0-A 유지 확정.** 추가 확인 — SPEC_05 §13 Fitness Function이
  `0.05 × metrics['turnover']`를 소비하도록 설계돼 있음(코드 미구현, Phase 3 예정) [검증된 사실].
  수정 없이 Phase 3 진입 시 튜닝 목적함수까지 오염된다.
- **Pass 2**: 수정 후보 차이표 산출 — net CAGR Δ −0.046~−0.082%p, selection 불변(순수 산술).
  IMPACT_MATRIX §4-A. 수정 순서 4번.
- **Label**: [검증된 사실]
- **비고**: SPEC_08 소형/대형 비대칭 거래비용 설계와 결합 조율 필요.

### PIT-AMEND-001 — 정정 미공개 + original_amount NULL → 정정값 사용 (룩어헤드) ★ Pass 2 승격
- **Commit**: 5ea5c48 / **Location**: `backtest/data_access.py::load_pit_series` (260-267 CASE)
  + `backtest/regime/data_access_regime.py::book_equity_batch` (159-171, 동일 CASE 복제)
- **Expected contract**: SPEC_02 §3-1-2 "정정 미공개 시점 → 원본값 사용 (PIT 보존)"
- **Actual behavior**: `original_amount IS NULL`이면 ELSE로 정정값 반환
- **Result impact**: **Y — 편입 오염 재현 (Pass 2).** Pass 0B 실편입 817쌍 × 운영 DB 교차:
  **26개 (ticker, 리밸) 쌍**의 재무 계정이 시장 미공개 정정값으로 계산됨 (000880·000150·
  065150 등은 RIM 입력 포함 9계정 전부). 유니버스 레벨 노출: 매 리밸 15~599행.
  **원본 소실로 반사실 복원 불가** — "선택 자체가 오염" 유형. 상세: IMPACT_MATRIX §2.
- **Evidence**: `tests/integration/test_pass2_pit_gate.py::test_amended_row_without_original_must_not_leak_amended_value`(의도적 실패) + 서버 교차 조회
- **Pass 2 판정**: **P0-A 승격.** 수정은 정책 결정 필요((a)계정 제외 (b)available_from 이동
  (c)XBRL 원본 백필) — CORR-GATE-002·DOC-PIT-001과 데이터 모델 결정 공유. 수정 순서 7번.
- **Label**: [검증된 사실]

### CORR-HARD-001 — listed_date NULL이면 상장기간 검사 통과 ★ Pass 2 승격
- **Commit**: 5ea5c48 / **Location**: `backtest/filters/hard_filter.py::_hard_filter` (66-68)
- **Expected contract**: MASTER §3-3·SPEC_03 — 상장 6개월 미만 제외
- **Actual behavior**: NULL이면 검사 생략 (stocks의 92.1%가 NULL)
- **Result impact**: **Y — 편입 재현 (Pass 2).** tape 편입 종목 284개 전원 listed_date NULL.
  가격 이력 프록시 기준 6개월 미만 의심 편입 **6건**: 204270(상장 ~30일 만에 2020-04-03
  편입), 237690(~56일), 237750(~45일), 228340·377740·004440(3~5개월). 상세: IMPACT_MATRIX §2.
- **Evidence**: `tests/oracle/test_pass2_contracts.py::test_unknown_listed_date_must_not_bypass_seasoning_filter`(의도적 실패) + 서버 교차 조회
- **Pass 2 판정**: **P0-A 승격.** 수정 = listed_date 백필(krx_listing_snapshots) + NULL 가드
  병행. selection 변경 확실. 수정 순서 8번.
- **Label**: [검증된 사실](프록시 기준) / [확실하지 않은 사실](정확한 상장일 — krx_listing_snapshots/외부 IPO 기록 대조)

---

## P0-B — 조용한 숫자 오염이 가능한 구조 (8건)

### CORR-ENGINE-001 — build_portfolio()의 weight를 _calc_period_return()이 소비하지 않음
- **Commit**: 5ea5c48 / **Location**: `backtest/engine.py::_calc_period_return` (198-249)
- **Expected contract**: `{ticker: weight}` 가중 수익률 / **Actual**: weight 무시 단순평균
- **Result impact**: Conditional — 등가중에선 일치. SPEC_04 §9-1이 예정한 비등가중 요소
  (업종 상한·현금 보유·주문규모 제한) 도입 즉시 조용히 틀림. v5.2 MAX_STOCK_WEIGHT 사고의
  근본 원인이 인터페이스 계약 부채로 잔존.
- **Evidence**: `tests/oracle/test_engine_return_oracle.py::test_weighted_return_consumes_portfolio_weights`(의도적 실패)
- **Pass 1 판정**: P0-B 유지 확정. **Label**: [검증된 사실]

### CORR-ENGINE-002 — 상폐 opt/cons 조정값이 종목 순회 순서에 의존
- **Commit**: 5ea5c48 / **Location**: `backtest/engine.py::_calc_period_return` (220-243, `n -= 1`)
- **Expected contract**: 편입 집합이 같으면 순서 무관 동일 결과
- **Actual behavior**: 분모 n이 순회 중 감소 → 가격결측 종목의 위치에 따라 상폐 조정 가중치 변동
- **Result impact**: Conditional — 합성 재현 성공(opt_adj 0.12 vs 0.08). 실측 tape 5개에선
  상폐+가격결측 공존 미발견(상폐 2건 전부 단독) → 현재 공표 수치 영향권 밖. 미캡처 24개 미확인.
- **Evidence**: `tests/oracle/test_engine_return_oracle.py::test_delisting_adjustments_are_order_independent`(의도적 실패)
- **Pass 1 판정**: P0-B 유지 확정. 관련 신규 항목 CORR-SORT-001(정렬 안정성) 분리 등재.
- **Label**: [검증된 사실](재현) / [확실하지 않은 사실](미캡처 시나리오 — 확인법: tape 캡처 후 동일 스캔)

### CORR-METRIC-002 — CAGR 연수를 캘린더일수가 아니라 구간수÷2로 계산
- **Commit**: 5ea5c48 / **Location**: `backtest/metrics.py::compute_cagr` (29-36)
- **Result impact**: Y(방향)/Conditional(크기) — closed 20구간은 4월→4월 정렬로 우연히 근접.
  왜곡 주범은 열린 구간 #23 포함 시(10.5년 vs 실제 ≈10.27년, CANONICAL 기준 ≈0.38%p 과소).
- **Evidence**: `tests/oracle/test_metrics_oracle.py::test_cagr_uses_actual_calendar_days`(의도적 실패)
- **Pass 1 판정**: P0-B 유지 확정. metrics.py 단일 정의 확인(복제본 없음). SPEC_05 §12는 연수
  산정 규약을 아예 정의하지 않음 — 문서가 현행 관례를 뒷받침하지 않음 [검증된 사실].
  CORR-ENGINE-003과 #23으로 결합(동시 수정 필수). **Label**: [검증된 사실]

### CORR-ENGINE-003 — 열린 구간 종료일을 date.today()로 결정 (재현성 결함)
- **Commit**: 5ea5c48 / **Location**: `backtest/engine.py::BacktestEngine.run` (69)
- **Evidence(Pass 2)**: `tests/oracle/test_pass2_contracts.py::test_engine_run_accepts_injected_valuation_date`(의도적 실패)
- **Pass 1 판정**: P0-B 유지 확정. 해법 제안(AUDIT_02 B-2 지시 반영):
  `engine.run(rebalance_dates, valuation_date=date(...))` 주입 + closed-period 공식 기준 채택
  (CORR-METRIC-002와 동시 소거). 신규 발견 CORR-FRESH-001과도 결합 — 아래 참조.
- **Label**: [검증된 사실]

### CORR-FRESH-001 — 데이터 신선도 가드 부재: 열린 구간이 stale 가격을 조용히 사용 (Pass 1 신규)
- **Commit**: 5ea5c48
- **Location**: `backtest/engine.py::BacktestEngine.run` (69) × `backtest/data_access.py::get_close_price` (98-110)
- **Expected contract**: 구간 종료일 라벨과 실제 사용 가격의 기준일이 일치하거나, 불일치 시 경고
- **Actual behavior**: `date.today()`가 종료일이 되고 `get_close_price(date<=as_of 최신값)`가
  결합되면, 엔진은 MAX(price_history.date) 이후의 어떤 날짜로 실행돼도 **같은 stale 가격으로
  "오늘까지의 수익률"을 라벨링**한다. 어디에서도 신선도를 검사하지 않는다.
- **Result impact**: **Y — 실사례 확인.** Pass 0B tape #23 구간이 end_date=2026-07-11로
  기록됐으나 price_history MAX(date)=2026-05-22 [검증된 사실, 서버 조회] — 7주 stale 가격이
  "7월 11일까지의 수익률"로 표시됨. closed_period 기준 채택 시 자동 소거되나, live_snapshot
  용도가 남는 한 가드 필요.
- **Affected scenarios**: 전 시나리오의 열린 구간(#23) + 향후 모든 live 실행
- **Evidence**: tests/baselines/selection/F_no_r2r3.json #23 vs AUDIT_MANIFEST.json price_max_date
- **Label**: [검증된 사실]

### CORR-BENCH-001 — 벤치마크 조회 실패 시 0.0 반환
- **Commit**: 5ea5c48 / **Location**: `backtest/engine.py::_calc_kospi_return`/`_calc_kosdaq_return`
  (259-298) + `backtest/regime/data_access_regime.py::kospi_return` (238-253, 동일 관례 복제)
- **Pass 1 판정**: P0-B 유지 확정. 서버 보존 로그 전수 grep 결과 '수익률 조회 실패' 발생 이력
  0건 [검증된 사실 — 단, 과거 ablation 실행 로그가 전부 보존됐는지는 확인 불가]. 구조는 그대로
  이므로 등급 유지. regime 쪽 복제 구현도 동일 수정 필요 지점으로 추가.
- **Evidence(Pass 2)**: `tests/oracle/test_pass2_contracts.py::test_benchmark_fetch_failure_must_not_become_zero_return`(×2, 의도적 실패)
- **Label**: [검증된 사실](코드 경로·로그 부재) / [확실하지 않은 사실](로그 보존 완전성)

> PIT-AMEND-001·CORR-HARD-001은 Pass 2에서 실데이터 편입 오염이 재현돼 **P0-A로 승격** — 위 참조.

### CORR-GATE-001 — dq_gate가 fs_div를 구분하지 않고 CFS/OFS를 비결정적으로 병합 (Pass 1 신규)
- **Commit**: 5ea5c48
- **Location**: `ingest/dq_gate.py::_load_accounts` (130-140)
- **Expected contract**: 게이트 판정 입력은 결정적이어야 하고, CFS/OFS 기준이 명시돼야 함
- **Actual behavior**: `SELECT account_nm, amount FROM financials WHERE ...` (fs_div 필터·ORDER BY
  없음) → dict comprehension이 동일 account_nm의 CFS/OFS 행을 **스캔 순서대로 덮어씀** —
  어느 쪽이 이기는지 비결정적.
- **Result impact**: Conditional — 실측: 게이트 핵심 계정(자본총계 등 5종)에서 CFS/OFS 값이
  서로 다른 (ticker,year,rt,acct) **0건** [검증된 사실, 서버 조회] → 현재 데이터에선 무해.
  단 값이 갈리는 데이터가 들어오는 순간 게이트 판정(=유니버스)이 실행마다 흔들릴 수 있는 구조.
- **Evidence(Pass 2)**: `tests/integration/test_pass2_pit_gate.py::test_gate_load_accounts_must_prefer_cfs_deterministically`(의도적 실패 — OFS가 CFS를 덮어씀을 실증)
- **Label**: [검증된 사실](구조) / 현재 무해(실측)

### CORR-GATE-002 — 게이트가 정정 반영값으로 판정 → 게이트 경유 룩어헤드 (Pass 1 신규)
- **Commit**: 5ea5c48
- **Location**: `ingest/dq_gate.py::_load_accounts` (financials.amount 사용) ×
  `ingest/dart_ingest.py::_upsert_financials` (317-321, 정정 시 amount 덮어씀)
- **Expected contract**: universe_gate_pit는 이름대로 PIT — 리밸런싱 시점에 시장이 알던 값으로 판정
- **Actual behavior**: 게이트는 `financials`(최신 정정 반영값)로 일괄 재판정된다. 정정으로
  R02(자본잠식) 등 판정이 뒤집히면, 정정 **이전** 리밸런싱일에도 뒤집힌 판정이 적용됨.
- **Result impact**: Conditional — 실측: 정정으로 자본총계 **부호가 뒤집힌 행 145건**
  [검증된 사실, 서버 조회] = R02 플립 후보.
- **Pass 2**: 노출 창 × 리밸런싱 교차 — "잘못 포함 가능" 후보가 18개 리밸런싱일에 1~19종목
  실재하나 **실제 편입 침투 0건** (IMPACT_MATRIX §3) → P0-B 유지. 단 랜덤 분포·스크리너
  percentile 컷은 유니버스에 직접 의존하므로 진단 시나리오 미세 오염 가능 [Claude 의견].
- **Evidence(Pass 2)**: `tests/integration/test_pass2_pit_gate.py::test_gate_verdict_must_reflect_values_known_at_rebalance`(의도적 실패)
- **Label**: [검증된 사실](경로·후보 규모·비침투) 

### CORR-DA-001 — get_avg_turnover/has_recent_trade가 "데이터 없음"과 "거래 없음"을 구분 못 함 (Pass 1 신규)
- **Commit**: 5ea5c48
- **Location**: `backtest/data_access.py::get_avg_turnover` (26-51, COALESCE 0) /
  `::has_recent_trade` (54-75, 행 없으면 False)
- **Expected contract**: 데이터 미수집(수집 실패)과 실제 무거래는 다른 상태
- **Actual behavior**: 둘 다 "0 / False" — Hard Filter가 해당 종목을 조용히 제외(fail-closed).
- **Result impact**: Conditional — 방향은 보수적(잘못 편입이 아니라 잘못 제외)이지만,
  price_ingest 부분 실패 시 **유니버스가 조용히 왜곡**되고 아무 경고도 없다. AUDIT_02 판정
  규칙("조회 실패 시 0 반환 구조는 P0-B") 적용.
- **Evidence(Pass 2)**: `tests/integration/test_pass2_pit_gate.py::test_avg_turnover_missing_data_must_not_be_silent_zero`(의도적 실패)
- **Label**: [검증된 사실](구조) / [확실하지 않은 사실](과거 수집 실패로 인한 실제 제외 사례)

---

## P1 — 계약·정책·재현성 (감사 종료 후 처리, Pass 2·3 비대상)

### CONTRACT-PF-001 — 최소 편입 종목 수 정책 미결 ★ 정책 결정 항목 (임의 수정 금지)
- **Location**: `backtest/portfolio.py::build_portfolio` (docstring vs 33-40)
- 계약이 이제 **3중으로 어긋남** [검증된 사실]: ① docstring "5개 미만 → 빈 dict"
  ② 구현 "n==0만 빈 dict, 1개라도 있으면 전액 투자" ③ SPEC_04 §9-1 "충족 종목 수만큼 편입,
  **현금 보유 허용**" — 셋 다 다르다. 선택지 (a)현금100% (b)전액투자(현행) (c)부족분 현금
  (d)차선 보완. `pipeline.py:111-118`의 고평가 보완(선택지 d의 부분 구현)과 함께 결정할 것.
- **Evidence**: `tests/oracle/test_portfolio_contract.py` (xfail strict=False)
- **Pass 1 판정**: P1 유지 (정책 결정 항목). **Label**: [검증된 사실]

### CONTRACT-PF-002 — 업종 25%·KOSDAQ 60% 상한: 문서는 "확정값", 코드는 미구현 스텁 (Pass 1 신규)
- **Location**: `backtest/portfolio.py::apply_portfolio_constraints` (43-58, 입력 그대로 반환)
  vs MASTER §3-3 표("확정값")·§3-7("Phase 2에서는 하드 룰로 유지")·SPEC_04 §9-1
- 문서만 읽으면 업종 분산이 적용된 결과로 오독한다. portfolio.py docstring은 "Phase 2 미구현"을
  명시하나 MASTER §3-7 "하드 룰로 유지"와 정면 충돌 [검증된 사실]. 어느 쪽이 맞는지 판정하지
  않고 둘 다 기록 (AUDIT_02 B-5 지시).

### CONTRACT-COST-001 — 거래세: SPEC은 시장별 차등, 코드는 전 종목 KOSPI 세율 (Pass 1 신규)
- **Location**: `backtest/configs/constants.py` (TAX=0.0033 단일) vs SPEC_04 §9-2
  (TAX_KOSPI 0.33% / TAX_KOSDAQ 0.18% + `total_cost(market, side)`)
- KOSDAQ 종목 매도에 0.15%p 과대 비용 → net 수익률 **보수 편향** (소형주 전략이라 KOSDAQ
  비중 상당 — 방향은 안전하나 문서 계약과 다름) [검증된 사실]. SPEC_08 비대칭 비용 설계 시 함께 정리.

### CORR-SORT-001 — 랭킹 tie-break 계약 미문서화 (Pass 1 신규, AUDIT_02 B-2 ★ 지시)
- **Location**: `backtest/pipeline.py::score_and_rank` (120, `sorted(key=upside_pct, reverse=True)`)
- 동률 시 순서 = 파이썬 안정 정렬 → 필터 통과 순서 → `load_gate_passed_tickers`의
  `ORDER BY s.ticker` [검증된 사실 — 체인 추적]. 즉 현재 tie-break는 "티커 오름차순"이지만
  어디에도 계약으로 명시돼 있지 않다. 정렬 방식이 바뀌면 n_stocks 경계의 동률 종목 편입이
  바뀌고, CORR-ENGINE-002와 결합해 편입이 같아도 opt/cons가 바뀐다.
- **Pass 1 판정**: P1 (upside_pct 완전 동률은 실측상 희귀 — [확실하지 않은 사실], 확인법:
  tape에서 동률 쌍 검색). 수정 시 tie-break를 ticker로 명시 고정 권고.

### SSOT-CONST-001 — RF가 metrics.py에 재선언 (Pass 1 신규)
- **Location**: `backtest/metrics.py:16` `RF_ANNUAL = 0.0263` (import 아닌 재선언) vs
  `backtest/configs/constants.py::RF`
- constants.RF 변경 시 Sharpe만 옛 값 사용 — 조용한 drift 구조 [검증된 사실]. STEP 10
  RF/ERP 민감도 작업 예정이라 그 전에 정리 필요.

### SSOT-EQUITY-001 — equity 우선순위 규칙 2곳 정의 (Pass 1 신규)
- **Location**: `backtest/models/rim.py::fair_value` (48-50 인라인 or-체인) vs
  `backtest/regime/data_access_regime.py::_EQUITY_KEYS` (21, "rim.py와 동일" 주석만 의존)
- 의도적 분리(배치 성능)는 정당 [AUDIT_00 원칙 4 적용] — 그러나 (a) 공통 상수 미공유,
  (b) drift 방지 테스트 부재. 우선순위 변경 시 두 곳이 조용히 갈라진다 [검증된 사실].

### ISOL-STAB-001 — StabilityFilter 기본 생성자가 폐기된 R2/R3를 부활시킴 (Pass 1 신규, B-4)
- **Location**: `backtest/filters/stability_filter.py::__init__` (33-34, 기본값 `_ALL_RULES`)
- 폐기 코드 격리 판정: FactorScreener는 CANONICAL 경로에서 완전 격리 ✅ (ablation의
  use_screener 플래그 경로만). R2/R3는 프로덕션이 explicit `active_rules`로 우회 ✅.
  **그러나 `StabilityFilter()` 무인자 생성 시 R2/R3 포함 전체 룰로 조립된다** — v5.2 재조립
  사고(phase2가 G_full로 조립)와 같은 유형의 재발 경로 [검증된 사실]. 격리 제안: 기본값을
  채택 룰셋으로 바꾸거나 active_rules를 필수 인자로.

### FALLBACK-MARGIN-001 — fallback available_from과 리밸런싱일 마진 0~2일
- **Pass 1 판정**: P1 유지, 위험 축소 확정 — 실측: fallback_used 그룹 **17개뿐**, 지연 제출
  룩어헤드(공시일 > fallback일) **0건** [검증된 사실, 서버 조회]. 경계 일치 5개 리밸런싱
  날짜의 구조적 관찰은 유효하나 실오염 없음.

### DOC-PIT-001 — SPEC_02 스키마는 append-only PIT, 실제는 단일행 upsert (Pass 1 신규)
- **Location**: SPEC_02 §스키마 `UNIQUE (..., account_nm, available_from)` vs
  `ingest/schema.sql:71` `UNIQUE (..., account_nm)` + `ingest/pit_loader.py` ON CONFLICT DO UPDATE
- 문서는 "과거 값을 소급 수정하지 않"는 버전 이력 구조를 약속하나, 실제는 제자리 덮어쓰기
  + original_amount 1칸 보존(정정 1회 깊이) [검증된 사실]. 연쇄 정정 시 중간 상태 소실 —
  PIT-AMEND-001의 구조적 배경. 아키텍처 결정 필요 항목.

### MIX-FSDIV-001 — load_pit_series의 CFS→OFS fallback이 계정 단위 (혼합 가능)
- **Pass 1 판정**: P1 유지 — 실측 핵심 계정 CFS/OFS 값 충돌 0건으로 현재 무해 [검증된 사실].
  계약 명문화(재무제표 단위 vs 계정 단위)만 필요.

### CORR-METRIC-003 — compute_sharpe zero-variance 가드가 잘못된 변수 검사
- **Pass 1 판정**: **P2로 확정** (Pass 0C 잠정 P2~P1 → P2). 상수 수익률 시계열은 실데이터에서
  발생 불가, inf는 조용하지 않고 요란함. 가드 결함 자체는 사실 [검증된 사실].
- **Evidence**: `tests/oracle/test_metrics_oracle.py::test_sharpe_zero_variance_returns_zero`(의도적 실패)

### 기타 P1 (Pass 0A 발견, 재검토 유지)
- DOC-ABL-002: CANONICAL 오라벨 (phase2_rim.py:55 주석 "F_momentum_rim" → 실제 F_no_r2r3).
  Pass 1 추가 확인: 시나리오 개수 서술이 3중 불일치 — ablation.py docstring "7개" vs
  MASTER "13개 시나리오" vs 실제 33개 [검증된 사실].
- PROV-ABL-001(holdings 4건 상폐버그 이전 생성) / PROV-ABL-002(결과 JSON git_sha 미기록)
- PROV-DB-001(마이그레이션 이력 테이블 부재)
- DOC-SPEC-001(MASTER SPEC 목록 4건 누락) / DOC-ABL-001(개수 불일치, 위와 통합)

---

## P2 — 성능·운영 (2건 신규)

### OPS-CRON-001 — 가격·시총 수집 크론 부재 → price_history 7주 지연의 원인 (Pass 1 신규)
- **[검증된 사실]** 서버 `crontab -l`: dashboard.health 30분 주기 1건뿐. price_ingest 크론 없음.
  마지막 가격 수집 = 2026-05-23 수동 실행 (logs/price*.log). PROV-PRICE-001 원인 확정.
  CORR-FRESH-001(신선도 가드 부재)과 결합 시 조용한 stale 실행이 가능했던 배경.

### OPS-DELIST-001 — delisting_ingest의 stock_listing_events INSERT 멱등성 없음 (Pass 1 신규)
- **Location**: `ingest/delisting_ingest.py::_upsert_delisted_stock` (52-61, ON CONFLICT 없는 순수 INSERT)
- 실측: 상폐 이벤트 4,124행 중 중복 6행 [검증된 사실]. `is_delisted_at`은 LIMIT 1이라 판정
  무영향. 재실행마다 중복 누적되는 구조만 정리 필요.

---

## Pass 1에서 해소·종결된 항목

- **A-3 ★ "10년+ 소형주 홀딩 상폐 플래그 0건이 구조적으로 가능한가" (MASTER 미결)**:
  **불가능 — 그리고 이미 해소됨.** 백테스트 구간 상폐 1,569종목(distinct) 실재 [검증된 사실,
  서버 조회]. "상폐 0건" 관찰은 v5.3 haircut 버그(d2d619e 수정)의 증상이었고, 수정 후
  Pass 0B tape에는 상폐 보유 2건(066110@2022-04-05, 001880@2023-08-18)이 정상 기록됨.
- **티커 재사용 오판정 경로 (A-3)**: 구조상 가능(`is_delisted_at`이 재상장 이벤트를 무시)하나
  실측 재사용 후보 **0건** [검증된 사실] → 항목 미등재, 관찰 기록만.
- **stock_listing_history 잔존 참조 (A-3)**: 전체 grep 0건 — 금지 규칙 준수 확인 [검증된 사실].
- **DELISTING_HAIRCUT·거래비용·OMEGA/VB_CAP SSOT (B-3)**: 단일 정의 + import 소비 확인,
  regime(mtm_monthly, indicators_inhouse)·scripts 전부 engine에서 import [검증된 사실]. 정상.
- **rebalance_dates SSOT (B-3)**: configs/rebalance_dates.py 단일 하드코딩, 복제 없음 [검증된 사실].

---

## P3 — 문서 stale (Pass 1 신규, 일괄)

### DOC-MASTER-001 — MASTER.md 서술 노후 모음
[검증된 사실] ① §3-1 "RF, RK 선언 위치: rim.py·stability_filter.py 두 파일에서 동일 값 유지"
— 실제는 constants.py SSOT를 두 파일이 import (코드가 문서보다 개선된 상태).
② 디렉토리 트리 주석 "price_ingest.py # FDR DataReader" — 실제 pykrx (§2 표와도 자체 모순).
③ 트리 주석 "rim.py # Gordon growth" — 실제 Ohlson 지속성형 (§3-1과 자체 모순).
④ §2 흐름도·트리의 `universe_loader.py` — 저장소에 존재하지 않는 파일.
⑤ §1-1 "개발: VS Code Remote SSH" vs CLAUDE.md "Windows 로컬 개발".
