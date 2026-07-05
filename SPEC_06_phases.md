# SPEC_06 — Phase 로드맵 & 체크리스트 & 산출물 & 향후 확장

> **용도**: Claude Code 실행 시 현재 Phase 체크리스트를 확인하는 참조 파일.
>   각 Phase 시작 전 해당 섹션의 체크리스트를 먼저 읽어라.
> **Claude Code 지시**:
>   Phase 0A부터 순서대로 진행하라. 이전 Phase 체크리스트가 모두 통과되기 전에
>   다음 Phase 구현을 시작하지 말 것. 게이팅 실패 시 즉시 중단하고 보고하라.

---

# 18. 진행 순서 (Phase별 로드맵)

## Phase 0A — 샘플 검증 ← **현재 시작점 권장**

**목표**: 핵심 파이프라인 검증. 소규모로 먼저.

**대상 종목**: 삼성전자, 현대차, POSCO홀딩스, NAVER, 셀트리온 등 10~30개

**검증 항목**:

**[DATA-1] adj_close 정확성 교차검증** ← v4.4 추가
- 삼성전자(005930) 2020년 50:1 액면분할 전후 adj_close 수익률 연속성 수동 확인
  - 분할 전날 종가 × (1/50) ≈ 분할 당일 adj_close 여야 함
- pykrx adj_close와 FinanceDataReader adj_close를 동일 기간 비교, 괴리율 1% 초과 종목 기록
- 검증 실패 시: FDR을 기본 소스로 전환하거나 수작업 보정 방안 결정 후 Phase 0B 진행

> ✅ **2026-05 완료**: 삼성전자 2020 액면분할 전후 수익률 연속성 확인. FDR DataReader 기준 adj_close 채택.

**[DATA-2] DART 계정명 매핑 게이팅** ← v4.4 추가
- 샘플 30개 종목 기준 핵심 3개 계정(매출액, 영업이익, 자본총계) 매핑 실패율 측정
- **게이팅 기준**: 실패율 20% 초과 시 → `account_mapping` 테이블 완성 후 Phase 0B 진행
- 실패율 20% 이하: Phase 0B에서 300종목 기준으로 재측정 후 전종목 수집 결정

> ✅ **2026-05 완료**: 핵심 3계정 매핑 실패율 **0.0%** (게이팅 기준 20% 이하 통과).

**[DATA-3] β=1.0 고정 편향 정량화** ← v4.4 추가
- 샘플 종목의 실제 β 범위 측정 (pykrx 또는 수동 회귀)
- 대형주(삼성전자, 현대차)와 KOSDAQ 소형주 β 차이 수치화
- 결과를 β=1.0 고정의 r 오차로 환산해 메모에 기록 (예: β=0.7 → r 실제 4.8% vs 가정 6.73%, 오차 1.9%p)
- 이 정보는 Phase 3 rolling β 도입 여부 결정 시 참고

**[기존 항목]**
- PIT `available_from` 로직 정확성 (수동 10건 확인)
- adj_close 수정주가 연속성 확인 (액면분할 구간 전후) ← DATA-1과 통합
- `market_cap_history` 기반 PBR 계산값 확인
- RIM 적정가 계산값 검증 (`stock-analysis` 기존 계산값과 비교)
- 팩터 스크리닝 percentile rank 계산 정확성

**[INFRA-1] DB 인덱스 생성 확인** ← v4.4 추가
```sql
-- Phase 0A 완료 전 반드시 확인
CREATE INDEX IF NOT EXISTS idx_price_history_ticker_date
    ON price_history (ticker, date);
CREATE INDEX IF NOT EXISTS idx_price_history_date
    ON price_history (date);
CREATE INDEX IF NOT EXISTS idx_financials_pit_avail_ticker
    ON financials_pit (available_from, ticker);
CREATE INDEX IF NOT EXISTS idx_financials_pit_ticker_year
    ON financials_pit (ticker, year, report_type);
CREATE INDEX IF NOT EXISTS idx_universe_gate_pit_ticker_year
    ON universe_gate_pit (ticker, year, report_type);
```

**[DATA-4] `fallback_used` 비율 체크** ← v4.7 추가
- 샘플 30종목 기준 `financials_pit.fallback_used=TRUE` 비율 측정
- **게이팅 기준**: 20% 초과 시 → DART 공시일 수집 로직 재검토 후 Phase 0B 진행
  - fallback 비율이 높으면 공시일 수집 실패 가능성 (수집 정상이면 10% 이하 예상)
- 비율과 함께 fallback 종목 목록 출력 → 수동 확인으로 룩어헤드 위험 평가

> ✅ **2026-05 완료**: fallback_used 비율 **1.3%** (게이팅 기준 20% 이하 통과).

---

---

## Phase 0A 완료 체크 (2026-05 기준)

| 항목 | 결과 | 판정 |
|------|------|------|
| DATA-1 adj_close 연속성 | 삼성전자 2020 액면분할 정상 확인 | ✅ PASS |
| DATA-2 계정 매핑 실패율 | 0.0% | ✅ PASS (기준 20% 이하) |
| DATA-4 fallback_used 비율 | 1.3% | ✅ PASS (기준 20% 이하) |
| supplement_cf 수집 완료 | **완료** (2026-05-25 기준) | ✅ PASS |
| pit_loader 재실행 | CF 수집 완료 → **즉시 실행 필요** | ⬜ 다음 단계 |

---

## Phase 0B — 중규모 수집

**목표**: 파이프라인 안정성 확인

**대상**: KOSPI 200 + KOSDAQ 100 (약 300종목)

---

## Phase 0C — 전종목 수집

**목표**: 전체 운영

**대상**: KOSPI/KOSDAQ 전체 + 상장폐지 종목 (FDR KRX-DELISTING)

> **검증 (2026-07-04, 서버 DB 직접 조회)**: 오랫동안 미체크였으나 실제로는 실행된 것으로 추정돼
> 뒤늦게 확인. 상세 근거는 MASTER 버전이력 v5.2 참조.

**검증 체크리스트**:
- [x] `stocks` 종목 수 2,000개 이상 — 3,264개 ✅ PASS
- [x] `stock_listing_events` 상장폐지 종목 포함 확인 — delisted 4,124건 ✅ PASS
- [x] `price_history` `adj_close` 컬럼 정상 수집 확인 — 2014-01-02~2026-05-22 ✅ PASS
- [x] `market_cap_history` 2014년부터 수집 확인 — 2014-01-02~2026-05-22 ✅ PASS
- [ ] `financials` 2014년 데이터 포함 확인 — **❌ FAIL**: 실제 범위는 2015~2025 (2014년 없음).
      MASTER §3-2의 TTM 제약("2015-04·08 유니버스 0개")의 실제 원인. 우회 중이라 조치 불필요.
- [x] `ingest_status` 수집 완료율 90% 이상 — 표면상 87.5%지만 재분석 결과 실질 95.0%
      (pending 409 = 스팩·리츠 259개 라벨링 누락 + KOSPI 우선주 150개 corp_code 매핑 실패,
      영향 제한적). 상세: MASTER 버전이력 v5.2.
- [ ] **FDR KRX-DELISTING 완결성 검증** ← v4.4 추가 — **미검증**
  - 2020~2022년 상장폐지 종목 수를 KRX 공식 수치(KIND 공시)와 비교
  - FDR 수집 종목 수 / KRX 공식 수치 < 80%이면 수집 방식 재검토

---

## Phase 1 — PIT + DQ Gate

**목표**: 룩어헤드 없는 PIT 구조 + 자동 유니버스 필터

> **현재 상태 (2026-05-25)**: supplement_cf 수집 완료. pit_loader 재실행 → dq_gate 재실행 → 체크리스트 검증 순서로 진행.

**선행 작업 (Phase 0A → Phase 1 연결)**:
- [x] `pit_loader` 재실행 — CF 데이터 반영된 `financials_pit` 재구성 — **2026-05-25 완료** (343,524 → 643,482행, CF 계정 반영)
- [x] `dq_gate` 재실행 — **2026-05-25 완료** (PASS 41,500건 / REJECT 754건. R04 핵심계정누락 601건, R02 자본잠식 150건, R09 대차대조표오차 2건)

**검증 체크리스트**:
- [x] `available_from`이 실제 공시일보다 이전 없음 (10건 수동 확인) — **2026-05 완료**
- [x] R06~R08이 Hard Filter로 올바르게 이동됐는지 확인 (DQ Gate에서 제거) — **2026-05-25 확인**: dq_gate.py에 R06~R08 없음. R07(상장폐지) hard_filter.py 구현 완료. R06(감사의견)·R08(관리종목)은 DB 미수집으로 미구현, Phase 3 예정
- [x] `universe_gate_pit` PASS 종목 수 전체의 60% 이상 — **2026-05-25 재확인: 2,783 / 2,808개 = 99.1%**
- [x] 자본잠식(R02), V01 오류(R09) 해당 연도 REJECT 확인 — **2026-05-25 확인: R02 154건, R09 2건**
- [x] 동일 종목이 과거 REJECT 연도 이후 PASS로 복귀 가능한지 확인 (시점별 판정 동작 검증) — **2026-05-25 확인: 193개 종목에서 복귀 동작 확인**

---

## Phase 2 — RIM 단일 모델 백테스트 ✅ **완료 (2026-06-21) → 가격보정 재실행 완료 (2026-07-02)**

**목표**: 4단계 필터 + RIM 단일 모델 + Ablation Test

> 13개 시나리오 Ablation Test 완료, RIM 산식 교체·가격 소급보정 재실행 완료. 변경 이력·근거는
> MASTER 버전이력 v5.0·v5.1·v5.2 참조. 결과: `experiments/ablation/summary.json`,
> `experiments/runs/2026.07.02. BACKTEST_RESULTS.md`.
> **Phase 3 진입 조건 충족**: F_momentum_rim > C_p95 ✅, 모멘텀 효과 ✅, D_rim_only ≥ C_p95 ✅
> (근소 우위, 경계값 근방). 팩터 스크리닝(E)은 원인 규명 후 전체 폐기 결정 — 아래 Phase 3 절 참조.

**완료된 작업** (v4.8 모듈화 구조):
1. ✅ `backtest/interfaces.py` — UniverseFilter, ValuationModel Protocol 정의
2. ✅ `backtest/filters/hard_filter.py` — `has_recent_trade(window=5)` + `max_lookback_days=90` 추가
3. ✅ `backtest/filters/stability_filter.py` — R1~R6 하드 룰 (`use_r6` 플래그)
4. ✅ `backtest/filters/factor_screener.py` — 4팩터, 동일가중 (**폐기** — 채택 파이프라인에서 제거, ablation 기록용으로만 보존)
5. ✅ `backtest/filters/momentum_filter.py` — MA AND 이중 조건
6. ✅ `backtest/models/rim.py` — RIMModel (Dechow λ=0.5)
7. ✅ `backtest/pipeline.py`, `engine.py`, `metrics.py`, `portfolio.py`
8. ✅ `backtest/ablation.py` — 13개 시나리오 (no_r6 변형 포함)
9. ✅ `backtest/configs/rebalance_dates.py` — 23개 날짜 하드코딩 (2015-04·08 TTM 미충족 빈 구간 포함, 유효 21개)
10. ✅ XBRL 파이프라인: `xbrl_historical_ingest.py`, `xbrl_mapper.py`, `amendment_checker.py`
11. ✅ `load_pit_series_ttm()` — H1 TTM 계산 (FY−H1_prev+H1_curr)
12. ✅ Ablation Test 13개 시나리오 → `experiments/ablation/`

**Phase 2 검증 결과 (현재)**:
- ✅ Ablation Test 13개 시나리오 완료
- ✅ `available_from <= rebalance_date` 코드 전체 적용 확인
- ✅ D(11.99%) ≥ C_stability_random p95(11.94%) → RIM 통계적 유효성 확인 (근소 우위, 과신 금지)
- ✅ 재무안정성 필터(R6 포함): H_no_stability MDD -37.7% vs F -32.6% → 안정성 기여 확인
- ✅ 팩터 스크리닝(E=6.29%): 원인 규명 완료 → 전체 폐기 결정 (SPEC_05 §11 참조)
- ✅ no_r6 이상 수치 해소 (가격 소급보정 완료, R6 실질적 방어 효과 확인)

**Ablation 결과 요약 (2026-07-02 최신):**

| 시나리오 | CAGR | Alpha(KS) | Sharpe | MDD |
|---------|------|-----------|--------|-----|
| B_hard_random | 4.68% (중앙) | — | — | — |
| C_stability_random | 6.80% (중앙) / p95=11.94% | — | — | — |
| D_rim_only | 11.99% | -1.84% | 0.434 | -33.9% |
| E_screener_rim | 6.29% | -7.54% | 0.251 | -35.2% |
| **F_momentum_rim** | **14.63%** | +0.80% | **0.508** | **-32.6%** |
| G_full | 9.23% | -4.60% | 0.347 | -25.3% |
| H_no_stability | 11.81% | -2.02% | 0.405 | -37.7% |
| KOSPI | 13.83% | — | — | — |
| KOSDAQ | 2.12% | — | — | — |

> 상세: `experiments/runs/2026.07.02. BACKTEST_RESULTS.md`

---

## Phase 3 — 기업 분류기 + 파라미터 튜닝 ← **현재 시작점**

> Phase 2 완료 → RIM 산식 교체 → 가격 소급보정 재실행 → STEP 3 신호분리 ablation →
> STEP 3B FactorScreener 폐기 결정, 순서로 Phase 3 진입 가능 상태. 각 단계 근거·수치는
> MASTER 버전이력 v5.0~v5.2, SPEC_05 §11(STEP 3/3B) 참조.
> **다음 과제**: classifier.py 활성화 + Bayesian 튜닝 (팩터 가중치 튜닝은 대상 소멸로 제외).
> 참고 자료: `2026.06.21. 백테스트_검토_및_모델개선_워크플로우.md` (STEP 1~11 체크리스트),
> `2026.06.21. 백테스트_설계검토_및_RIM산식_교체.md` (RIM 산식 교체 근거).

**미결 항목 (Phase 3 진행 중 확정 필요, 임의로 정하지 않음)**:
- **최소 편입 종목 수 규칙**: `backtest/portfolio.py`의 `MIN_PORTFOLIO_STOCKS=5`가 여전히
  `build_portfolio()`에 실제로 적용되지 않음(코드는 `n==0`일 때만 빈 dict 반환, docstring과 실제
  동작 불일치). 실측 확인 결과 189개 조합 중 5종목 미만은 1건뿐이라 시급성은 낮음(상세: MASTER
  버전이력 v5.2). STEP 7 임계값 확정은 여전히 미결.
- **벤치마크 우선순위 재배치**: KOSPI 대신 "Hard+Stability 통과 동일가중"을 1차 KPI로 삼자는
  제안(워크플로우 문서 STEP 5, New-1)이 검토만 된 상태 — 아직 확정 아님.

**전제 조건**: Phase 2 Ablation Test 완료 ✅ (RIM 유효성 확인, F_momentum_rim 우수)

**목표**: classifier.py 활성화 (팩터 가중치 Bayesian 튜닝은 FactorScreener 폐기로 제외 — SPEC_05 §14)

**작업**:
1. `backtest/classifier.py` 활성화 (v4.2 §7 구현)
2. `backtest/engine.py` Phase 3 버전 — 분류기 + 분류 이력 연동
3. ~~팩터 가중치 4개 Bayesian 튜닝 추가~~ — **제외 (2026-07-05)**: FactorScreener 자체 폐기로
   튜닝 대상 소멸. Phase 2 튜닝 파라미터는 3개(`beta_adj`, `rim_threshold`, `n_stocks`)로 유지.
4. `experiments/runs/run_002_classified.csv`

**검증 체크리스트**:
- [ ] 타입 분포 확인 (특정 타입 70% 이상이면 분류 로직 오류)
- [ ] STABLE/GROWTH 동시 고점수 종목 없음 확인
- [ ] classifier 도입 후 F_momentum_rim 대비 성과 개선 여부 확인 (팩터 가중치 튜닝 항목은 삭제됨)

---

## Phase 4 — Bayesian 튜닝 + Walk-forward + Fitness Sensitivity

**전제 조건**: Phase 3 체크리스트 통과

**목표**: 최종 파라미터 확정 + OOS 검증

**작업**:
1. Rolling Walk-forward W1~W5 (5개 튜닝 윈도우) 실행
2. Fitness Sensitivity Analysis
3. W1~W5 OOS Alpha 양수 비율 4/5 이상 후보 전략 선정
4. 인간 검토 → 파라미터 확정
5. **Final Holdout W6/W7 평가 (확정 후 1회만 실행)**

**검증 체크리스트**:
- [ ] 파라미터 총수 Phase별 상한 이하
- [ ] W1~W5 OOS Alpha 양수 비율 4/5 이상
- [ ] Sensitivity Analysis `params_stable=True` > 80%
- [ ] 최악 OOS MDD > -40%
- [ ] Reasoning Log 내용 인간이 이해 가능한 수준
- [ ] **Final Holdout W6/W7 OOS Alpha 모두 > 0%** ← v4.4 추가

---

## Phase 5 — 멀티모델 확장 (조건부 실행)

**전제 조건**: Phase 4에서 OOS Alpha 확인 완료. Phase 2 Ablation Test에서 RIM 단일 모델 Alpha가 유의미하게 존재함을 검증.

**결정 기준**: Phase 4 OOS Alpha가 연 5% 미만이거나 Robustness < 60%이면 멀티모델 추가 전에 전략 구조 재검토 우선.

**확장 모델 후보 (산식 확정은 Phase 2 결과 후 결정):**

| 모델 | 대상 타입 | 미결 항목 |
|------|----------|----------|
| Peer PER | STABLE 보조 | 피어 적자 기업 처리 방식 |
| EV/Sales | GROWTH, TURNAROUND | 적정 배수 결정 방식 |
| Mid-cycle EV/EBITDA | CYCLICAL | 사이클 길이(3년 vs 5년) |
| NAV | ASSET | 자산 항목별 할인율 |
| FCFF | LEVERAGED | terminal growth rate, WACC |

**공통 미결 항목**:
- 극단값 winsorization 기준 (percentile 5~95%?)
- sanity check 기준 (적정가/현재가 비율 상한)

---

# 19. 향후 확장 메모

```
# [모듈화 시점별 작업 계획 — v4.8]
#
# ■ Phase 2 전 (지금 구현) — filters/ + BacktestPipeline 조립
#   - backtest/interfaces.py 작성 (UniverseFilter, ValuationModel Protocol)
#   - backtest/filters/ 4개 파일 작성 (hard_filter, stability_filter,
#     factor_screener, momentum_filter)
#   - backtest/models/rim.py 작성 (RIMModel 클래스)
#   - backtest/pipeline.py 작성 (BacktestPipeline)
#   - backtest/configs/phase2_rim.py 조립
#   이 단계의 인터페이스가 확정되면 이후 모델 추가는 파일만 추가하면 됨.
#
# ■ Phase 3 전 — ClassifiedScreener 추가
#   - classifier.py 활성화 후 ClassifiedScreener 구현
#   - BacktestPipeline filters에 ClassifiedScreener 교체 또는 추가
#   - 인터페이스(UniverseFilter) 변경 없음
#
# ■ Phase 5 전 — 멀티모델 구현체 + EnsembleModel
#   - backtest/models/ev_sales.py, fcff.py, peer_per.py, nav.py 작성
#   - backtest/models/ensemble.py 작성 (weights 파라미터 Bayesian 튜닝 대상)
#   - backtest/configs/phase5_multimodel.py 조립
#   - 인터페이스(ValuationModel) 변경 없음
#   Phase 2 Ablation 결과 전까지 멀티모델 산식 확정하지 않음.
```

```
# [향후 확장] 산업 트렌드 가점
# 자동화 불가(룩어헤드 편향 위험)로 현재 스코프 제외.
# 향후 추가 시 고려할 대안:
#   1. 섹터 6개월 상대 모멘텀 (해당 섹터 수익률 > KOSPI 수익률이면 가점)
#      → 2026-07-05 FactorScreener 레이어 자체가 폐기됐으므로, 추가한다면 독립 필터 클래스로
#        신설(MomentumFilter 확장 또는 신규 SectorMomentumFilter)해야 함 — 옛 스크리너 레이어에
#        얹는 방식은 불가
#   2. LLM 기반 공시 텍스트 분석 (stock-analysis dart_watcher 연동, 읽기 전용)
#      → Year 2 계획(§20)과 연계
# 두 방안 모두 RIM + 모멘텀 baseline 검증 후 추가.

# [향후 검토] β 수집 방안 (Phase 3 이후)
# Phase 0A에서 β=1.0 고정 편향을 정량화한 결과를 보고 도입 여부 결정.
#   옵션 A — price_history adj_close + KOSPI 지수 rolling 36개월 회귀 β (PIT 준수)
#   옵션 B — pykrx get_market_fundamental_by_date() β 수집 (로컬 전용)
# 두 옵션 도입 시 β=1.0 고정 대비 성과 차이를 별도 실험으로 비교 후 채택 결정.

# [향후 검토] 대주주 지분율 Hard Filter (Phase 3 이후)
# 한국 KOSDAQ 소형주는 대주주 지분율 > 70%인 경우 유동주식이 극히 적어
# 주가가 대주주 의사결정에 좌우되는 구조. 팩터 모델 신호가 왜곡될 수 있음.
# 구현 조건: DART 대주주 지분율 시계열을 PIT 기준으로 수집하는 파이프라인 추가 필요.
# 현재 스코프 제외. Phase 2 결과에서 해당 패턴 종목이 성과를 저해하면 우선 검토.

# [미채택] PBR×ROE (= ROE/PBR = NI/시가총액 = E/P = PER의 역수)
# 사실상 PER 역수와 동일. (과거) 팩터 스크리닝에 PER 역수를 추가하는 것과 같아
# 4팩터(매출YoY, 영업이익YoY, GP/A, 1/PBR) 구성에서 중복이라 판단했었음.
# 2026-07-05 FactorScreener 자체가 폐기되어 이 논의는 더 이상 유효하지 않으나,
# R6(adjROE < r 제외)가 수익성 하한을 이미 처리하고 있다는 점은 여전히 유효.

# [향후 검토] 슬리피지 비율 함수 모델링 (Phase 3 이후)
# 주문금액/일평균거래대금 비율의 함수로 모델링:
#   비율 5% 이하 → 0.1%, 10% 이하 → 0.2%, 초과 → 0.5%
# AUM 확대 시 소형주 슬리피지 과소평가 문제 해소.
```

---

# 20. 장기 발전 방향

## Year 1 (현재)
- Phase 0~4: RIM + 모멘텀 Baseline 검증 및 확정 (팩터 스크리닝은 시도 후 2026-07-05 폐기 — SPEC_05 §11 STEP 3B)
- Phase 5: 멀티모델 확장 (Phase 4 결과에 따라 실행 여부 결정)

## Year 2
- ML Classifier (PIT 데이터 1,000종목 × 5년 이상 확보 후)
- 섹터 모멘텀 팩터 추가 (독립 필터 클래스로 신설 — 옛 FactorScreener 레이어는 폐기되어 재사용 불가)
- NLP 공시 반영 (`stock-analysis` dart_watcher 연동 — 읽기 전용)

## Year 3
- 이벤트 드리븐 전략 (공시 알림 → 자동 리밸런싱 시그널)
- 한국형 알파 플랫폼화

---

# 21. 최종 산출물 포맷

```json
{
    "rebalance_date": "2026-04-04",
    "strategy": "RIM Value + Factor Screening + Momentum",
    "phase": "phase2_rim_only",
    "run_id": 42,
    "ablation_tag": "F_full",
    "fitness": 0.312,
    "metrics": {
        "cagr": 0.187, "sharpe": 1.24, "alpha": 0.091,
        "mdd": -0.213, "turnover": 0.38, "robustness": 0.76
    },
    "delisting_cagr_range": {"optimistic": 0.192, "base": 0.187, "conservative": 0.171},
    "late_disclosure_stats": {
        "excluded_count": 12,
        "excluded_next_period_return_avg": 0.043,
        "alpha_contribution_est": 0.008
    },
    "universe_stats": {
        "after_hard_filter": 1450,
        "after_stability_filter": 890,
        "after_screening": 178,
        "after_momentum_filter": 134,
        "after_valuation_filter": 34
    },
    "performance_note": "배당 미반영, adj_close 기준 수익률. KOSPI 벤치마크도 동일 조건. 상장폐지 청산: 기준 시나리오(종가×70%) 메인.",
    "top_picks": [
        {
            "ticker": "005930",
            "corp_name": "삼성전자",
            "screener_score": 0.83,
            "rim_fair_value": 95000,
            "current_price": 72000,
            "upside_pct": 31.9,
            "weight": 0.05
        }
    ]
}
```

---

# 22. Claude Code 실행 가이드

각 Phase는 하나의 `.md` 설계 문서로 Claude Code에 전달한다.

```
Phase N 설계서.md
├─ 개요 (목표, 전제 조건)
├─ 파일별 작업 (신규/수정, 코드 블록)
├─ 검증 절차 (Step별 실행 명령 + 기대 출력)
└─ 주의사항
```

기존 `stock-analysis/`와의 관계: 완전히 독립. 코드·DB·환경변수 공유 없음.
로직 참조 가능하나 import 금지. PostgreSQL 포트: 기존 5432, 백테스트 5433.

---

# 23. 한 줄 결론

이 시스템의 목적은 복잡한 적정가 계산기가 아니라,
**한국 상장 전종목 대상으로 재무안정성 검증 → RIM 가치평가 → 모멘텀 확인의
3단계 필터로 가치 함정을 걸러내며, 시장 대비 더 유리한 종목을 지속 선별하는 설명가능한 투자 의사결정 엔진**이다.
(팩터 스크리닝은 v4.3에서 4단계째로 시도됐으나 RIM 알파를 구조적으로 훼손함이 확인되어
2026-07-05 폐기 — SPEC_05 §11 STEP 3B)
멀티모델 확장은 이 baseline이 유효함을 검증한 후 단계적으로 추가한다.
