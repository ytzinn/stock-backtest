# GAPS — Pass 0A/0B 결과

> AUDIT_01_PASS0.md § Pass 0A/0B 산출물. 프로덕션 코드는 한 줄도 수정하지 않았다(신규 스크립트/테스트만 추가).
> 라벨: `[검증된 사실]` = 코드/DB/파일을 직접 읽고 확인. `[Claude 의견]` = 판단. `[확실하지 않은 사실]` = 확인 방법을 함께 기재.

---

## 게이트 체크 (AUDIT_01 Pass 0A)

- [x] 모든 결과 파일이 어떤 commit에서 나왔는지 특정됐거나, 특정 불가로 명시됐다
      → **부분 특정 불가.** 결과 JSON에 git_sha가 기록되지 않아(PROV-ABL-002) run_at 시각과 `git log` 시각의 수동 대조로만 근사 가능. 이번 감사에서 66daf42/48a9adc/d2d619e 세 커밋과 대조해 PROV-ABL-001을 특정했다.
- [x] CANONICAL 시나리오가 무엇인지 확정됐다
      → `F_no_r2r3` (아래 §2, DOC-ABL-002). `phase2_rim.py` 주석이 가리키는 `F_momentum_rim`이 아니다.
- [x] 열린 구간 종료일 결정 방식이 기록됐다
      → `backtest/engine.py:69`, `date.today()`. 아래 §1.

---

## 1. 재현성 — 열린 구간 종료일

**[검증된 사실]** `backtest/engine.py:69`
```python
next_date = rebalance_dates[i + 1] if i + 1 < len(rebalance_dates) else date.today()
```
마지막(열린) 리밸런싱 구간의 종료일이 `date.today()`다. 같은 코드·같은 DB라도 실행 날짜가 다르면 그 구간 수익률이 달라진다.
→ CORR-ENGINE-003 (AUDIT_00 §5 기지 목록과 일치, 코드 재확인 완료). Pass 0B에서 `closed_period`(#23 등 열린 구간 제외) / `live_snapshot` 이층 분리로 대응 예정.

---

## 2. SSOT 드리프트 — CANONICAL 시나리오 오라벨 (신규 발견)

**[검증된 사실]** `backtest/configs/phase2_rim.py:55`
```python
# 기본 인스턴스 (채택 파이프라인 F_momentum_rim 구조)
PHASE2_PIPELINE = build_phase2_pipeline()
```
이 주석은 틀렸다. `build_phase2_pipeline()`이 실제로 만드는 `StabilityFilter(active_rules={'R1','R4','R5','R6'})`는:

- `ablation.py`의 `F_momentum_rim` 태그와 **다르다** — 이 태그는 `stability_rules` 키가 없어 `StabilityFilter.__init__`의 기본 경로(`stability_filter.py:34`, `_ALL_RULES` = R1~R6 전체, R2/R3 포함)를 탄다.
- `ablation.py`의 `F_no_r2r3` 태그와 **정확히 일치한다** — `stability_rules: {'R1','R4','R5','R6'}` 명시(`ablation.py:110-112`), `use_hard/use_momentum/use_rim_filter` 나머지 플래그도 동일, `build_ablation_pipeline`의 기본 파라미터(`beta_adj=0.0, omega=OMEGA, rim_threshold=0.05, n_stocks=20`)도 `build_phase2_pipeline`의 기본값과 동일.

**결론:** 프로덕션과 필터 구성이 실제로 같은 ablation 결과 파일은 `F_momentum_rim.json`이 아니라 `F_no_r2r3.json`이다. 지금까지 누군가 "채택 파이프라인 결과"를 확인한다며 `F_momentum_rim.json`을 봤다면, R2/R3가 켜진 채로 계산된 다른 설정의 결과를 본 것이다.

- **ID(가안)**: DOC-ABL-002
- **AUDIT_00 §5 기지 목록과의 관계**: SSOT-SCEN-001("시나리오 정의가 ablation.py와 configs/ 두 곳에 존재")의 구체적 재현 사례. 신규 발견.
- **예상 등급**: P1 (문서-구현 불일치) — 단, Pass 1B에서 "이 오라벨을 근거로 실제 배포 판단(어느 시나리오가 채택됐다고 보고했는가)이 내려진 적 있는지" 확인 필요. 있다면 P0-B로 승격 검토.

---

## 3. Ablation 결과 파일 provenance — holdings vs summary 시각 불일치 (신규 발견)

**[검증된 사실]** `run_ablation.py`(요약 지표, `engine.run()` 직접 호출)와 `export_portfolios.py`(편입종목 상세, 완전히 별도 재실행)는 서로 다른 스크립트이며 서로 다른 시각에 실행됐다. 13개 존재하는 `{tag}_holdings.json` 중 11개가 짝인 `{tag}.json`과 몇 시간~2일 떨어진 시각에 생성됐다(전체 타임스탬프는 `tests/baselines/ABLATION_FILE_INVENTORY.json` 참조).

**가중 사실**: `D_no_r6_holdings.json` / `E_no_r6_holdings.json` / `F_no_r6_holdings.json` / `G_no_r6_holdings.json` 4개는 **2026-07-04 07:39~07:49 UTC**에 생성됐다. 이는 `export_portfolios.py`의 상장폐지 판정 버그 수정(commit `48a9adc`, 2026-07-06 21:18 UTC, 커밋 메시지: "engine.py와 동일한 버그 — get_close_price(next_date) is None을 상폐 트리거로 썼으나 date<=as_of 최신값 반환으로 인해 절대 None이 되지 않아 상폐 미판정")보다 이르다. 즉 **이 4개 holdings 파일의 상폐 종목 청산가·수익률 표시는 신뢰할 수 없다.**

**집계 지표는 별개다**: `engine.py` 자체의 동일 버그는 `d2d619e`(2026-07-05 05:06 UTC)에서 이미 수정됐고, 모든 ablation summary(`{tag}.json`)의 run_at은 이보다 늦다(최초 2026-07-05 12:44 UTC). CAGR/Sharpe 등 집계 지표는 이 버그의 영향을 받지 않는다 — **holdings 표시 전용 문제**다.

- **ID(가안)**: PROV-ABL-001 (holdings 오염), PROV-ABL-002 (결과 JSON에 git_sha 미기록 — 재발 방지책)
- **예상 등급**: 집계 지표에 영향 없음 확인됐으므로 **P1** (holdings 파일 4개 재생성 필요, Pass 3 대상). `export_portfolios.py`/`run_ablation.py`에 git_sha 기록 추가는 별도 P1.
- **Pass 0B와의 관계**: decision tape 2계층 분리(selection/aggregate) 설계가 이 문제를 구조적으로 방지한다 — 지금 이 발견이 그 설계의 필요성을 뒷받침한다.

---

## 4. DB 마이그레이션 — 이력 테이블 부재

**[검증된 사실]** `ingest/migrations/apply.py`는 SQL을 실행만 하고 적용 이력을 남기지 않는다. `schema_migrations`/`migration_history`류 테이블이 DB에 없음을 직접 조회로 확인(`information_schema.tables`). 마이그레이션 파일은 현재 `v8_xbrl_original.sql` 1개뿐이라 컬럼 존재 여부(`financials_pit.original_amount`, `amendment_from`)로 우회 확인했고 **적용된 상태**임을 확인했다. 그러나 마이그레이션이 늘어나면 "이 DB가 어떤 스키마 버전인지"를 코드만으로 확정할 방법이 없다.

- **ID(가안)**: PROV-DB-001
- **예상 등급**: P1 (재현성)

---

## 5. price_history 최신 데이터 지연

**[검증된 사실]** `price_history` MAX(date) = 2026-05-22. 오늘(2026-07-12) 기준 약 7주 전. 원인(ingest cron 실패, 서버 작업 순서 등)은 Pass 1A 범위 — 이번 Pass 0A는 사실만 기록한다.

- **ID(가안)**: PROV-PRICE-001
- **예상 등급**: 미정 (Pass 1A에서 원인 확인 후 등급 부여)

---

## 6. MASTER.md ↔ 실제 SPEC 파일 목록 불일치 (신규 발견)

**[검증된 사실]** `MASTER.md` 31~36행 표는 `SPEC_01_infra.md`~`SPEC_06_phases.md` 6개만 나열한다. 저장소 루트에는 `SPEC_*.md` 10개가 실존한다(glob으로 직접 확인):

| 파일 | MASTER.md 표에 있는가 |
|---|---|
| SPEC_01_infra.md | 있음 |
| SPEC_02_ingest.md | 있음 |
| SPEC_03_universe.md | 있음 |
| SPEC_04_models.md | 있음 |
| SPEC_05_backtest.md | 있음 |
| SPEC_05_부록A_StabilityFilter검증.md | **없음** |
| SPEC_06_phases.md | 있음 |
| SPEC_07_regime.md | **없음** |
| SPEC_08_regime_phaseB.md | **없음** |
| SPEC_08_B05_timing_vs_deconcentration.md | **없음** |

- **ID(가안)**: DOC-SPEC-001
- **예상 등급**: P1 (문서-구현 불일치)
- AUDIT_00 §5 기지 목록에 없던 신규 발견.

---

## 7. ablation.py docstring 시나리오 개수 (재확인)

**[검증된 사실]** `ABLATION_CONFIGS`를 `ast` 파싱으로 직접 세어 확인(가정 없이):

- 총 **33개** 태그 (docstring은 "7개 시나리오"라고 적음 — DOC-ABL-001, AUDIT_00 §5 기지 목록과 일치)
- `RANDOM_TAGS`(코드 SSOT, `ablation.py:124`) = 4개: `A_random`, `B_hard_random`, `C_stability_random`, `C_no_r6`
- 나머지 29개는 결정론적 태그

4분류(CANONICAL/DIAGNOSTIC/ARCHIVE/RANDOM) 전체는 `tests/baselines/SCENARIO_REGISTRY.json` 참조. 분류 근거:
- **CANONICAL(1)**: `F_no_r2r3` — §2 참조.
- **ARCHIVE(9)**: `use_screener=True`인 태그 전부(`E_screener_rim`, `E_no_r6`, `E_rev_only`, `E_op_only`, `E_gpa_only`, `E_pbr_only`, `G_full`, `G_no_r6`, `H_no_stability`) — FactorScreener는 2026-07-05 폐기(`phase2_rim.py:7` 주석). AUDIT_00 원칙 5(폐기 코드는 삭제 대상 아님)에 따라 보존, 실행 경로에서 격리됐는지만 확인.
- **DIAGNOSTIC(19)**: 나머지 — leave-one-out(R1~R5 단일/조합 제외), 신호분리(D_pbr_only/D_factor_only), StabilityFilter 완전제거 대조군(D_no_stability/F_no_stability_clean).
- **RANDOM(4)**: 코드 `RANDOM_TAGS` frozenset 그대로 채택 — 해석 여지 없음.

**[Claude 의견]** `H_no_stability`는 `backtest/ablation.py:72-74` 주석에서 이미 "stability·screener 두 축이 동시에 달라 교란됨"이라고 자체 폐기 사유가 적혀 있어 ARCHIVE 분류에 이견 여지가 적다. 반면 `D_pbr_only`/`D_factor_only`를 DIAGNOSTIC으로 분류한 것은 판단이 개입됐다 — 코드가 이 둘에 대해 ARCHIVE/DIAGNOSTIC을 명시하지 않으므로, "신호분리 목적"이라는 docstring 설명(`ablation.py:151-159, 192-199`)에 근거했다.

---

## 8. 실행 환경 — dev PC와 서버의 패키지 버전 차이

**[검증된 사실]** dev PC(Windows, 코드 작성 전용) `pip freeze` 기준 `pykrx==1.2.7`, 서버(Ubuntu, 실행 전용) 및 `requirements.txt` 기준 `pykrx==1.0.47`. CLAUDE.md 규칙상 실행은 서버에서만 하므로 결과 오염 경로는 아니다. 다만 Pass 0C에서 로컬(dev PC) pytest 실행 시 pykrx 의존 코드가 있다면 버전 차이를 인지해야 한다.

- **예상 등급**: P3 (정보성, 조치 불필요 — CLAUDE.md 실행 환경 분리 원칙이 이미 보호막)

---

## Pass 0B — 특성화(characterization) baseline

**모델**: Sonnet 5 / effort high (AUDIT_00 §3와 일치)

**작업**: `scripts/audit/characterize_baseline.py` 작성 후 서버에서 실행 — 프로덕션 코드
(`backtest.engine`/`backtest.ablation`/`backtest.pipeline`/`backtest.portfolio`)를 **재구현하지 않고
그대로 import해 호출**했다. `engine.run()`이 실제로 사용한 `portfolio` dict를 그대로 받아 종목별
원시 entry/exit price만 추가 조회하는 방식이라, 두 번째 파이프라인 실행으로 인한 유니버스
불일치 위험이 없다.

**대상 시나리오 5개** (CANONICAL 전체 + DIAGNOSTIC 중 결론 근거):
`F_no_r2r3`(CANONICAL, §2 참조), `D_rim_only`(RIM 유효성 핵심 대조군),
`D_no_r2`/`D_no_r3`(R2/R3 폐기 결정의 직접 근거), `D_no_stability`(StabilityFilter 완전제거 대조군).

**제외**: ARCHIVE(9개, 선택사항이라 이번 라운드 제외) / RANDOM(4개, seed 고정 가능함을 코드로 확인했으나
500회 반복 분포 전체를 raw tape로 남기는 건 규모가 너무 커 이번 범위에서 제외 — 필요 시 단일 대표
seed로 축소 캡처하는 방안을 Pass 1 이후 검토).

**자체 교차검증**: 캡처 스크립트가 selection tape(raw price)로 재계산한 `gross_return`을 같은 실행
안에서 `engine.run()`의 실제 출력과 대조한다. **5개 시나리오 × 23개 구간 전부 tol=1e-9 이내 일치**
(`cross_check_mismatches: []`). 스크립트 자체가 `_calc_period_return`과 다르게 동작할 위험을
실행 시점에 자동으로 잡아내는 장치다.

**신뢰성 확인(부수 발견)**: `D_rim_only`(기본 stability_rules=R1~R6 전체)와 `D_no_r2`
(R1,R3,R4,R5,R6 — R2 제외)의 전체 성과지표(CAGR/net_CAGR/Sharpe/alpha)가 **완전히 동일**했다.
`phase2_rim.py:7-8` 주석의 "R2는 R1과 완전 중복 — 결과에 영향 없음" 주장을 독립적으로 재실행한
raw 데이터로 재확인한 것이다. `D_no_r3`(R3 제외)는 값이 달라져 "R3는 역효과"라는 주장도 일관됐다.
이는 새 버그가 아니라 **기존 문서 주장이 유지된다는 확인**이다.

**게이트**:
- [x] `pytest -m "not integration"` 전부 통과 (91 passed, 로컬 dev PC)
- [x] `tests/characterization/README.md` + 각 테스트 파일 상단에 "정답 아님" 경고 명시
- [x] selection(`tests/baselines/selection/`)과 aggregate(`tests/baselines/aggregate/`) 물리적 분리
- [x] closed_period 판정 — `is_open_period` 플래그로 마킹(파일 자체는 미분리, 테스트에서
      `is_open_period=False`로 필터링해 재계산). 열린 구간(#23, rebal_date=2026-04-03)은
      5개 시나리오 모두 정확히 1개로 존재함을 `test_closed_period_baseline_excludes_open_period`가 검증.

**산출물**: `tests/baselines/selection/{F_no_r2r3,D_rim_only,D_no_r2,D_no_r3,D_no_stability}.json`,
`tests/baselines/aggregate/{같은 5개}.json`, `tests/baselines/PASS0B_FILE_HASHES.json`,
`tests/characterization/test_ablation_aggregate.py`(20 테스트), `tests/characterization/README.md`,
`scripts/audit/characterize_baseline.py`.

**설계 메모**: `overall_metrics_all_periods_including_open`은 열린 구간(#23)을 포함한 engine.run()의
실제 출력 그대로다(재계산 안 함). closed_period 전용 CAGR/Sharpe 등이 필요하면 이후 별도 테스트로
`is_open_period=False` 구간만 골라 재계산해야 한다 — 이번 Pass 0B는 그 재계산 로직 자체는
아직 작성하지 않았다(gross_return/turnover 검증만 구현). **Pass 1 이후 closed_period 전용 CAGR
검증이 필요하면 이 갭을 메워야 한다.**

---

## 요약 — Pass 1로 넘길 항목

| ID | 요지 | 예상 등급 | Pass 1 확인 필요 사항 |
|---|---|---|---|
| DOC-ABL-002 | phase2_rim.py 주석이 CANONICAL을 F_momentum_rim이라 오라벨 (실제는 F_no_r2r3) | P1 (조건부 P0-B) | 이 오라벨을 근거로 실제 판단/보고가 내려진 적 있는지 |
| PROV-ABL-001 | D/E/F/G_no_r6 holdings 4개, export_portfolios.py 상폐버그 수정 이전 생성 | P1 | 집계 지표엔 영향 없음(확인됨) — holdings 재생성만 필요 |
| PROV-ABL-002 | ablation 결과 JSON에 git_sha 미기록 | P1 | 재발 방지책 채택 여부 |
| PROV-DB-001 | 마이그레이션 이력 테이블 부재 | P1 | 마이그레이션 2개 이상 시점에 재검토 |
| PROV-PRICE-001 | price_history 7주 지연 | 미정 | Pass 1A에서 원인 확인 |
| DOC-SPEC-001 | MASTER.md SPEC 목록에 4개 파일 누락 | P1 | — |
| GAP-0B-001 | closed_period 전용 CAGR/Sharpe/MDD 재계산 로직이 characterization 테스트에 아직 없음(gross_return/turnover만 구현) | P2 (테스트 커버리지) | closed_period 지표가 실제로 필요해지는 시점(Pass 1B/2)에 추가 |

P0-A/P0-B 후보는 이번 Pass 0A/0B에서 새로 재현되지 않았다(모두 조사 단계). AUDIT_00 §5 기지 목록의 CORR-*/CORR-METRIC-*/CONTRACT-PF-001은 Pass 1B(파이프라인·수익률·metrics 감사)에서 다룬다 — Pass 0 범위는 provenance 확정과 특성화 baseline 구축까지다.

Pass 0B 특성화로 독립 재확인된 것: `_calc_period_return`의 단순평균(가중치 미사용, CORR-ENGINE-001)과
`_calc_turnover`의 `sold/max(len(prev),len(curr),1)` 산식(CORR-METRIC-001)이 **현재 코드에서
실제로 그렇게 동작함**을 raw selection tape 기반 재계산으로 확인했다(이 산식이 옳은지는 판단하지
않음 — 그건 Pass 1B/tests/oracle의 몫이다).
