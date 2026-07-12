# TECH_DEBT — 코드 정합성 부채 대장

> 포맷: AUDIT_00_MASTER.md §4. 라인 번호는 보조 정보 — **심볼과 commit SHA가 기준**이다.
> 이 파일은 Pass 0C에서 개설됐다. 등급은 Pass 1에서 확정한다 (P0-A/P0-B만 Pass 2·3 대상).
> 증거 테스트 중 "의도적 실패" 상태인 것들은 tests/oracle/README.md 참조 — 통과시키려고
> 오라클을 고치지 마라.

---

## P0-A — 숫자가 틀렸음이 재현됨

### CORR-METRIC-001 — turnover 산식이 비중 변화를 무시 → 거래비용·net 수익률 오염
- **Commit**: 5ea5c48
- **Location**: `backtest/engine.py::_calc_turnover` (Lines 186-195)
- **Expected contract**: turnover = 0.5 × Σ|w_new − w_old| (재조정 규모의 표준 정의)
- **Actual behavior**: `sold / max(len(prev), len(curr), 1)` — 이탈 종목 수 비율만 계산.
  종목 수가 같고 등가중이면 우연히 일치(항등식), **종목 수가 바뀌는 구간에서 어긋남**.
- **Result impact**: **Y — 재현됨.** turnover는 `engine.py:110-112`에서
  `tc = turnover × (COST_SELL + COST_BUY)`로 거래비용에 직접 입력된다 [검증된 사실].
  Pass 0B 실측 tape 스캔(scripts/audit/turnover_impact_scan.py) 결과:
  | 시나리오 | 오염 구간 | 기록 vs 올바른 값 | net 왜곡 |
  |---|---|---|---|
  | F_no_r2r3 (CANONICAL) | 2017-04-05 (5→20) | 0.25 vs 1.00 | +0.510%p |
  | F_no_r2r3 (CANONICAL) | 2020-08-20 (7→20) | 0.35 vs 1.00 | +0.442%p |
  | D_rim_only/D_no_r2/D_no_r3 | 2017-04-05 (5→20) | 0.25 vs 1.00 | +0.510%p |
  | D_no_stability | 2017-04-05 (6→20) | 0.30 vs 1.00 | +0.476%p |
  CANONICAL 누적 거래비용 0.952%p 과소계상 → net CAGR이 실제보다 유리하게 보고됨.
- **Affected scenarios**: 전 시나리오의 `net_return`/`net_cagr`/`net_sharpe`/`avg_turnover`
  (종목 수 변동 구간 보유 시). gross 지표는 무관.
- **Evidence**: `tests/oracle/test_turnover_oracle.py::test_turnover_expansion_5_to_20_stocks`
  (의도적 실패) + `scripts/audit/turnover_impact_scan.py` 출력 (GAPS.md Pass 0C 절)
- **Label**: [검증된 사실]
- **비고**: SPEC_08이 소형/대형 비대칭 거래비용을 설계 중 — 수정 시 그 설계와 결합 조율 필요.

---

## P0-B — 조용한 숫자 오염이 가능한 구조

### CORR-ENGINE-001 — build_portfolio()의 weight를 _calc_period_return()이 소비하지 않음
- **Commit**: 5ea5c48
- **Location**: `backtest/engine.py::_calc_period_return` (Lines 198-249)
- **Expected contract**: `{ticker: weight}` 포트폴리오의 **가중** 수익률
- **Actual behavior**: weight 무시, `sum(stock_returns)/len(stock_returns)` 단순평균
- **Result impact**: Conditional — 현행 등가중(1/N)에서는 두 계산이 일치. 비등가중 도입
  (업종 상한 25% 등 Phase 3 예정 기능) 즉시 조용히 틀린다. v5.2 MAX_STOCK_WEIGHT 유령
  파라미터 사고의 근본 원인이 아직 살아있는 것.
- **Evidence**: `tests/oracle/test_engine_return_oracle.py::test_weighted_return_consumes_portfolio_weights` (의도적 실패)
- **Label**: [검증된 사실]

### CORR-ENGINE-002 — 상폐 opt/cons 조정값이 종목 순회 순서에 의존
- **Commit**: 5ea5c48
- **Location**: `backtest/engine.py::_calc_period_return` (Lines 220-243, `n -= 1` 상호작용)
- **Expected contract**: 편입 종목 집합이 같으면 순서와 무관하게 동일한 (gross, opt, cons)
- **Actual behavior**: 분모 `n`이 순회 중 감소 → 가격결측 종목이 상폐 종목보다 앞/뒤냐에
  따라 상폐 조정 가중치 `w = 1/n`이 달라짐. 순회 순서 = RIM 상승여력 정렬 순서이므로
  **정렬 tie-break를 바꾸면 편입종목이 같아도 opt/cons가 바뀐다.**
- **Result impact**: Conditional — 합성 케이스로 순서 의존성 **재현됨** (opt_adj 0.12 vs 0.08).
  단, Pass 0B 실측 tape 5개 스캔 결과 상폐+가격결측 공존 구간 **미발견** (상폐 구간 2개는
  전부 단독 상폐) → 현재 공표 수치는 영향권 밖. 나머지 24개 시나리오는 미스캔.
- **Evidence**: `tests/oracle/test_engine_return_oracle.py::test_delisting_adjustments_are_order_independent` (의도적 실패) + `scripts/audit/turnover_impact_scan.py` 공존 스캔
- **Label**: [검증된 사실] (재현) / [확실하지 않은 사실] (미캡처 24개 시나리오 영향 — 확인법: 해당 시나리오 tape 캡처 후 동일 스캔)

### CORR-METRIC-002 — CAGR 연수를 캘린더일수가 아니라 구간수÷2로 계산
- **Commit**: 5ea5c48
- **Location**: `backtest/metrics.py::compute_cagr` (Lines 29-36)
- **Expected contract**: CAGR = (Π(1+r))^(365.25/실제경과일수) − 1
- **Actual behavior**: `years = len(returns) / periods_per_year` — 4월→8월(≈4.5개월)과
  8월→4월(≈7.5개월)을 같은 반년으로 취급. 시그니처가 날짜를 받지도 않음.
- **Result impact**: Y(방향 확정)/Conditional(크기) — closed 20구간은 2016-04-05→2026-04-03
  = 9.993년 vs 관례 10.0년으로 우연히 근접(4월→4월 정렬 덕). **왜곡의 주범은 열린 구간
  #23**: n=21 → 10.5년 vs 실제 ≈10.27년 → CANONICAL CAGR 15.27%는 캘린더 기준 ≈15.65%로
  약 0.38%p 과소 보고. CORR-ENGINE-003과 #23을 통해 결합 (AUDIT_00 §5 결합 주의 그대로).
- **Evidence**: `tests/oracle/test_metrics_oracle.py::test_cagr_uses_actual_calendar_days` (의도적 실패)
- **Label**: [검증된 사실]

### CORR-ENGINE-003 — 열린 구간 종료일을 date.today()로 결정 (재현성 결함)
- **Commit**: 5ea5c48
- **Location**: `backtest/engine.py::BacktestEngine.run` (Line 69)
- **Expected contract**: 같은 코드·같은 DB → 같은 결과 (valuation_date 주입)
- **Actual behavior**: 마지막 구간 종료일 = 실행일. 실행 날짜마다 결과 변동.
- **Result impact**: Y (재현성) — Pass 0B baseline은 `is_open_period` 플래그로 #23을
  분리 마킹해 완화. 수정은 CORR-METRIC-002와 함께 (closed-period 기준 채택 시 동시 소거).
- **Evidence**: GAPS.md §1, Pass 0B selection tape의 end_date=캡처일
- **Label**: [검증된 사실]

### CORR-BENCH-001 — 벤치마크 조회 실패 시 0.0 반환
- **Commit**: 5ea5c48
- **Location**: `backtest/engine.py::_calc_kospi_return` / `_calc_kosdaq_return` (Lines 259-298)
- **Expected contract**: 조회 실패는 실패로 전파 (또는 명시적 재시도/중단)
- **Actual behavior**: `except Exception → log.warning + return 0.0` — 네트워크 장애가
  "벤치마크 0% 수익"으로 둔갑해 alpha·robustness를 조용히 오염.
- **Result impact**: Conditional — 발생 시점의 로그로만 탐지 가능. Pass 1B에서 기존 결과의
  벤치마크 값 vs 독립 재조회 대조 필요.
- **Evidence**: 코드 직접 확인. (오라클 테스트는 외부 API 의존이라 미작성 — Pass 1B에서
  fdr monkeypatch 방식 검토)
- **Label**: [검증된 사실] (코드 경로) / [확실하지 않은 사실] (실제 발생 이력 — 확인법: 서버
  로그 grep 'KOSPI 수익률 조회 실패' + baseline 벤치마크 대조)

### PIT-AMEND-001 — 정정 미공개 구간인데 original_amount가 NULL이면 정정값 사용 (룩어헤드)
- **Commit**: 5ea5c48
- **Location**: `backtest/data_access.py::load_pit_series` (Lines 260-267, CASE 분기)
- **Expected contract**: `amendment_from > rebalance_date`면 시장이 그 시점에 알던
  **원본값**을 사용해야 함
- **Actual behavior**: `original_amount IS NULL`이면 ELSE 분기로 떨어져 `f.amount`
  (= 정정 반영값)를 반환 — 원본 미캡처 행에서 조용한 룩어헤드.
- **Result impact**: Unknown — 확인법: 운영 DB에서
  `SELECT COUNT(*) FROM financials_pit WHERE amendment_from IS NOT NULL AND original_amount IS NULL`
  (Pass 1A에서 실행). 0이면 이론상 경로, >0이면 실오염 후보.
- **Evidence**: `tests/integration/test_pit_sql_contracts.py::test_amendment_after_rebalance_with_null_original_uses_amended_value_LOOKAHEAD` (현행 동작 문서화 테스트)
- **Label**: [검증된 사실] (SQL 경로) / [확실하지 않은 사실] (실데이터 발생 건수)

---

## P1 — 계약·정책·재현성 (Pass 1에서 등급 확정, 감사 종료 후 처리)

### CONTRACT-PF-001 — 최소 편입 종목 수 정책 미결 ★ 정책 결정 항목 (임의 수정 금지)
- **Location**: `backtest/portfolio.py::build_portfolio` (docstring vs Lines 33-40)
- docstring: "후보 MIN_PORTFOLIO_STOCKS(5) 미만 → 빈 dict" / 구현: n==0일 때만 빈 dict.
- 실측: 2016-08-18(5종목), 2020-04-03(7종목) 구간 존재 — 5종목 미만은 실측상 드묾.
- 선택지: (a) 5종목 미만 → 현금 100% (b) 그대로 전액 투자(현행) (c) 부족분만 현금 (d) 차선 보완.
- **상호작용 주의**: `pipeline.py:111-118`이 RIM 컷 미달 시 고평가 종목으로 5개까지
  **이미 보완**한다(선택지 (d)의 부분 구현). 정책 결정 시 두 단계를 함께 정할 것.
- **Evidence**: `tests/oracle/test_portfolio_contract.py` (xfail strict=False)
- **Label**: [검증된 사실]

### CORR-METRIC-003 — compute_sharpe의 zero-variance 가드가 잘못된 변수를 검사 (신규)
- **Location**: `backtest/metrics.py::compute_sharpe` (Lines 41-45)
- 가드는 `returns.std() == 0`을 보는데 나눗셈은 `excess.std()`로 한다. 부동소수점 표현
  차이로 `returns.std()`가 ε>0인데 `excess.std()`가 정확히 0이면 가드를 통과해 **inf 반환**.
- Result impact: N(실질) — 실데이터에서 상수 수익률 시계열은 비현실적. 다만 가드 자체가
  결함이므로 P2~P1. 오라클이 우연히 발견.
- **Evidence**: `tests/oracle/test_metrics_oracle.py::test_sharpe_zero_variance_returns_zero` (의도적 실패)
- **Label**: [검증된 사실]

### FALLBACK-MARGIN-001 — fallback available_from과 리밸런싱일의 안전 마진이 0~2일 (신규)
- **Location**: `ingest/pit_loader.py::FALLBACK_OFFSET` + `backtest/configs/rebalance_dates.py`
- FY fallback = 4/5, H1 fallback = 8/19. 리밸런싱일은 법정마감+3영업일(4/3~4/5, 8/18~8/20).
  → 2016·2017·2021·2022·2023년 4월 리밸런싱(전부 4/5)과 2015-08-19에서 fallback 행이
  `available_from <= rebalance_date` **경계값으로 포함**된다. 리밸런싱이 4/3인 해에는 제외.
  fallback 의존 종목이 리밸런싱 날짜 ±2일에 따라 유니버스를 들락거림 [검증된 사실].
- 추가 위험 [Claude 의견]: fallback은 "정시 제출" 가정. 지연 제출 기업은 실제 공시일이
  4/5보다 늦을 수 있어 룩어헤드 가능 — 확인법: disclosures에 늦은 rcept_dt가 있으면서
  fallback_used=TRUE인 (ticker,year) 존재 여부 조회 (Pass 1A).

### 기타 P1 (Pass 0A에서 발견, GAPS.md 참조)
- DOC-ABL-002 (CANONICAL 오라벨: F_momentum_rim → 실제 F_no_r2r3) — SSOT-SCEN-001의 재현 사례
- PROV-ABL-001 (holdings 4건 상폐버그 이전 생성) / PROV-ABL-002 (결과 JSON git_sha 미기록)
- PROV-DB-001 (마이그레이션 이력 테이블 부재) / PROV-PRICE-001 (price_history 7주 지연)
- DOC-SPEC-001 (MASTER.md SPEC 목록 4건 누락) / DOC-ABL-001 (docstring 7개 vs 실제 33개)
- MIX-FSDIV-001 (신규, 통합 테스트로 문서화): load_pit_series의 CFS→OFS fallback이
  **계정 단위**라 한 종목·연도 dict 안에 CFS 매출액과 OFS 당기순이익이 섞일 수 있음.
  docstring "CFS 우선, OFS fallback"은 재무제표 단위인지 계정 단위인지 불명 — 계약 명문화 필요.
