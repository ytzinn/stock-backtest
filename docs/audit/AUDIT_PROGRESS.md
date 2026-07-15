# AUDIT 진행 체크리스트

> AUDIT_00_MASTER.md §2 Pass 구조 기준. 각 Pass 시작 시 모델/effort를 표와 대조해 확인한다.
> 갱신 규칙: Pass가 끝날 때마다 이 파일을 갱신하고, 감사 관련 커밋에 포함한다.

---

## 선행 — CLAUDE.md AUDIT MODE 블록

- [x] `CLAUDE.md`에 AUDIT MODE 블록 삽입 (자동 master push/서버 pull 정지, `audit/{ITEM_ID}` 브랜치 강제)
- [x] `.claude/settings.json` 생성 (`{"model": "sonnet"}` — `.gitignore`에 걸려 로컬 전용, 커밋 안 됨)
- 완료일: 2026-07-12 · 브랜치: `audit/pass0a-inventory`

---

## Pass 0A — 재현성 인벤토리 (`AUDIT_01_PASS0.md`)

- **모델 요구사항**: Sonnet 5 / effort high
- **사용 모델**: Sonnet 5 (시스템 정보 기준 일치) · effort는 세션 설정이라 코드로 확인 불가 — 사용자가 `/effort`로 확인 필요
- **상태**: ✅ 완료 (2026-07-12, 커밋 `1d4a5fd`, `a6dd43c`)

게이트:
- [x] 모든 결과 파일이 어떤 commit에서 나왔는지 특정됐거나, 특정 불가로 명시됐다
- [x] CANONICAL 시나리오가 무엇인지 확정됐다 (`F_no_r2r3` — `F_momentum_rim`이 아님, DOC-ABL-002)
- [x] 열린 구간 종료일 결정 방식이 기록됐다 (`engine.py:69`, `date.today()`)

산출물: `GAPS.md`, `tests/baselines/AUDIT_MANIFEST.json`, `SCENARIO_REGISTRY.json`, `ABLATION_FILE_INVENTORY.json`

신규 발견: DOC-ABL-002(CANONICAL 오라벨), PROV-ABL-001(holdings 4건 상폐버그 이전 생성), PROV-ABL-002(git_sha 미기록), PROV-DB-001(마이그레이션 이력 부재), PROV-PRICE-001(price_history 7주 지연), DOC-SPEC-001(MASTER.md SPEC 목록 누락 4건)

---

## Pass 0B — 특성화(characterization) baseline (`AUDIT_01_PASS0.md`)

- **모델 요구사항**: Sonnet 5 / effort high
- **사용 모델**: Sonnet 5 (일치)
- **상태**: ✅ 완료 (2026-07-12)

게이트:
- [x] `pytest -m "not integration"` 전부 통과 (91 passed, 0 failed — 로컬 dev PC)
- [x] characterization 디렉토리/docstring에 "정답 아님" 경고가 명시돼 있다 (`tests/characterization/README.md` + 테스트 파일 상단)
- [x] selection/aggregate가 물리적으로 분리돼 있다 (`tests/baselines/selection/`, `tests/baselines/aggregate/`)
- [x] closed_period baseline에 열린 구간이 포함돼 있지 않다 — `is_open_period` 플래그로 마킹, 테스트가 시나리오당 정확히 1개(#23)임을 검증

작업:
- [x] `scripts/audit/characterize_baseline.py` 작성 — 프로덕션 코드 재사용(재구현 아님), selection/aggregate 2계층 캡처 + 자체 교차검증(재계산 vs engine 실제값)
- [x] CANONICAL(`F_no_r2r3`) 캡처 실행 — 23구간 전부 tol=1e-9 일치
- [x] DIAGNOSTIC 4개(`D_rim_only`, `D_no_r2`, `D_no_r3`, `D_no_stability`) 캡처 실행 — 전부 일치
- [x] `tests/characterization/test_ablation_aggregate.py` 작성 (20 테스트, fast suite, DB 미접속)
- [x] `AUDIT_MANIFEST.json`에 `pass0b_characterization` 섹션 + `PASS0B_FILE_HASHES.json` 추가

**대상 시나리오** (CANONICAL 전체 1개 + DIAGNOSTIC 4개): `F_no_r2r3`, `D_rim_only`, `D_no_r2`, `D_no_r3`, `D_no_stability`.
RANDOM(4개)·ARCHIVE(9개)는 이번 라운드 제외 — 사유는 `GAPS.md` Pass 0B 절 참조.

**부수 확인**: `D_rim_only`(기본 R1~R6)와 `D_no_r2`(R2 제외)의 전체 성과지표가 완전히 동일 —
`phase2_rim.py`의 "R2는 R1과 완전 중복" 주장을 독립 재실행으로 재확인. `D_no_r3`는 다른 값 —
"R3는 역효과" 주장도 일관됨.

새 갭: GAP-0B-001(closed_period 전용 CAGR 재계산 로직 미구현, P2) — `GAPS.md` 참조.

---

## Pass 0C — 독립 오라클 + 통합 테스트 (`AUDIT_01_PASS0.md`) ★ 가장 어려움

- **모델 요구사항**: Fable 5 또는 Opus 4.8 / effort xhigh
- **사용 모델**: Fable 5 (사용자가 `/model fable`로 전환 후 진행 — 일치)
- **상태**: ✅ 완료 (2026-07-12)

게이트:
- [x] `pytest -m "not integration"`: 122 passed + 1 xfail + **의도적 실패 5개**
      → AUDIT_01 게이트 3항("oracle 중 실패 = P0 후보, 고치지 말고 기록만") 적용, 기록 완료
- [x] `pytest -m integration`: 30/30 통과 (합성 PostgreSQL 16, 포트 5434, 실행 후 컨테이너 파기)
- [x] 실패 oracle 전부 TECH_DEBT.md에 P0 항목으로 등재 (CORR-ENGINE-001/002,
      CORR-METRIC-001/002/003)
- [x] `tests/oracle/` ↔ `tests/characterization/` 물리 분리

작업:
- [x] O-1 RIM 오라클 (주당가 계약·clamp 경계·equity 우선순위·shares 나눗셈, 상수 SSOT import)
- [x] O-2 가중수익률 소비 검증 — **실패 = CORR-ENGINE-001 증거** (xfail 아님, 그대로 둠)
- [x] O-3 상폐 3시나리오 순서 독립성 — **실패 = CORR-ENGINE-002 재현.** 단 실데이터(5개 tape)
      공존 스캔 음성 → P0-B 유지
- [x] O-4 turnover 올바른 정의 — **실패 + 거래비용 입력 확인 + 실측 오염 정량화 → P0-A 확정**
      (CANONICAL 누적 거래비용 0.952%p 과소계상)
- [x] O-5 CAGR 캘린더일수 오라클(실패, CORR-METRIC-002) + Sharpe/MDD 규약 명시·검증(통과)
      + **신규 발견 CORR-METRIC-003** (Sharpe zero-variance 가드 오류 → inf)
- [x] O-6 최소종목 정책 — xfail(strict=False) 계류, CONTRACT-PF-001로 TECH_DEBT 등재
- [x] I-1~I-6 통합 테스트 30개 — 룩어헤드 방지 경로 전부 정상 확인.
      **신규 발견 2건**: PIT-AMEND-001(원본 미캡처 시 정정값 룩어헤드), MIX-FSDIV-001(계정 단위 CFS/OFS 혼합)
- [x] TECH_DEBT.md 개설 (P0-A 1건 · P0-B 6건 · P1 다수) — **⚠ 등급은 잠정.**
      AUDIT_00 §2상 "TECH_DEBT.md 확정"은 Pass 1A/1B 게이트다. Pass 0C는 증거 기록 +
      1차 판단까지만 하고 지우지 않은 채 남겼다(CONTRACT-PF-001만 AUDIT_01이 직접 지시).
      Pass 1A/1B가 각 항목을 독립 재검토해 등급을 확정해야 게이트 통과로 본다.
- [x] `scripts/audit/turnover_impact_scan.py` (tape 기반 영향 스캔, 재실행 가능)

---

## Pass 1A/1B — 데이터·엔진 감사 (`AUDIT_02_PASS1.md`)

- **모델 요구사항**: Fable 5
- **사용 모델**: Fable 5 (일치)
- **상태**: ✅ 완료 (2026-07-12)

게이트:
- [x] TECH_DEBT.md에 P0-A/P0-B 목록 확정 — **P0-A 1건 · P0-B 10건** (Pass 0C 잠정 8건 전건
      재검토: 유지 7 + P2 확정 1, 신규 12건 등재). 각 항목 `Pass 1 판정:` 줄이 이중 점검 기록.
- [x] 모든 항목에 심볼 위치 + commit SHA (기준 commit de93559, 헤더 명시)
- [x] 모든 문장에 라벨 ([검증된 사실]/[Claude 의견]/[확실하지 않은 사실])
- [x] "중복" 분류 항목의 의도적 분리 검토 흔적 (SSOT-EQUITY-001에 AUDIT_00 원칙 4 적용 명시,
      data_access_regime은 의도적 분리로 판정 — 중복 부채 아님)
- [x] 프로덕션 코드 미수정 (`git status` 확인)

주요 신규 발견 (Pass 1):
- **CORR-HARD-001** (P0-B): stocks 92%가 listed_date NULL → "상장 6개월" 필터 사실상 미작동
- **CORR-GATE-001/002** (P0-B): dq_gate가 fs_div 비결정 병합 + 정정 반영값으로 판정(게이트
  룩어헤드, 자본총계 부호 플립 후보 145행 실재)
- **CORR-FRESH-001** (P0-B): 신선도 가드 부재 — #23 구간이 07-11 라벨로 05-22 stale 가격 사용 실사례
- **CORR-DA-001** (P0-B): 데이터 미수집과 무거래를 구분 못 해 유니버스 조용한 왜곡 가능
- PIT-AMEND-001 규모 확정: 정정 행의 21.6%(18,676행)가 원본 미캡처 — 룩어헤드 후보 실재
- 해소: 상폐 플래그 0건 미결항목(불가능 확인, v5.3 버그 증상이었음), 티커 재사용 0건,
  지연제출 룩어헤드 0건, stock_listing_history 잔존 0건
- OPS-CRON-001: price 7주 지연 원인 = **가격 수집 크론 자체가 없음** (healthcheck만 등록)

Pass 2로 넘길 재현 과제: PIT-AMEND-001(리밸런싱일 교차 실오염 산출), CORR-GATE-002(게이트
재판정 시뮬), CORR-HARD-001(조기 편입 실사례), CORR-METRIC-001(이미 재현 완료 — 수정 PR 준비만)

## Pass 2 — 재현·영향분석 (`AUDIT_03_PASS2_3.md`)

- **모델 요구사항**: Fable 5 / Opus 4.8 / effort xhigh
- **사용 모델**: Fable 5 (일치)
- **상태**: ✅ 완료 (2026-07-12)

게이트:
- [x] 각 P0 항목마다 실패하는 테스트가 커밋됐거나 "재현 불가"로 강등됐다 —
      **11건 전건 실패 테스트 확보** (Pass 0C 5개 + Pass 2 신규 8개, 재현 불가 0건).
      전부 실행으로 실제 실패 확인.
- [x] 차이표에 편입 종목 변경 여부 포함 — IMPACT_MATRIX §4: 후보 A/B는 selection 불변
      (Jaccard 1.000), PIT-AMEND·HARD·GATE-002 수정은 selection 변경으로 구분 명시
- [x] 결합 항목 식별·묶음 확정 — ENGINE-003+METRIC-002+FRESH-001 (1 PR, #23 동시 소거
      shadow 계산으로 가설 검증), ENGINE-001+002+SORT-001 (연속 PR), PIT-AMEND+GATE-002+
      DOC-PIT-001 (데이터 모델 결정 공유)
- [x] 프로덕션 코드 미수정 (`git status` 확인)

핵심 결과:
- **P0-A 승격 2건**: PIT-AMEND-001 (실편입 26쌍이 룩어헤드 값으로 계산 — 000880·000150 등
  9계정 전부, 원본 소실로 반사실 복원 불가), CORR-HARD-001 (상장 6개월 미만 의심 편입 6건
  재현 — 204270은 상장 ~30일 만에 편입, tape 편입 284종목 전원 listed_date NULL)
- **최종 P0 구성: P0-A 3건 · P0-B 8건**
- 침투 없음 확인: GATE-002 유니버스 오염 후보 실재(리밸당 1~19종목)하나 편입 침투 0건
- 수정 후보 shadow 차이표 (tape 기반): turnover 수정 = net CAGR −0.05~−0.08%p /
  closed-period 채택 = CAGR +0.38~0.49%p (변동의 전부가 #23 제외분, 캘린더 연수 전환
  잔여 효과 +0.01%p 수준 → 결합 가설 실증)
- 수정 순서 9단계 제안 (IMPACT_MATRIX §6) — 1~6 즉시 가능, 7~9는 정책 결정 선행

산출물: `IMPACT_MATRIX.md`, `tests/oracle/test_pass2_contracts.py`(4 실패),
`tests/integration/test_pass2_pit_gate.py`(4 실패), TECH_DEBT.md 갱신

## Pass 3 — 수정·PR (`AUDIT_03_PASS2_3.md`)

- **모델 요구사항**: Sonnet 5(단순 수정) / Opus 4.8·Fable 5(engine/PIT/metrics, effort xhigh)
- **사용 모델**: Fable 5 (전 항목 engine/PIT/metrics 계열 — 일치)
- **상태**: ✅ 코드 수정 완료 (2026-07-12) — **P0 11건 + CONTRACT-PF-001 전부 PR 생성**

PR 체인 (스택 — 아래에서 위로 순서대로 머지):
| # | 브랜치 | 항목 | selection | characterization |
|---|---|---|---|---|
| #1 | audit/CORR-ENGINE-002 | 상폐 순서 독립 + tie-break(SORT-001) | 불변 | 통과 |
| #2 | audit/CORR-ENGINE-001 | weight 소비 | 불변 | 통과 |
| #3 | audit/CORR-ENGINE-003 | +METRIC-002+FRESH-001: valuation_date·closed-period·캘린더 CAGR | 불변 | 통과 (공표 CAGR +0.39~0.50%p — 승인 요망) |
| #4 | audit/CORR-METRIC-001 | turnover 0.5Σ\|Δw\| (P0-A) | 불변 | **정당 깨짐 5건 — baseline 승인 대기** |
| #5 | audit/CORR-BENCH-001 | 벤치마크 예외 전파 | 불변 | 통과 |
| #6 | audit/CORR-DA-001 | 미수집≠무거래 구분 | 불변 | 통과 |
| #7 | audit/CONTRACT-PF-001 | 정책 (b) 확정 (docs만) | 불변 | 통과 |
| #8 | audit/PIT-AMEND-001 | 원본 미상 정정 제외 (P0-A) + 백필 런북 | **변경 가능** | 배포 후 diff |
| #9 | audit/CORR-HARD-001 | 상장필터 프록시 (P0-A) + listed_date 백필 | **변경 확실** | 배포 후 diff |
| #10 | audit/CORR-GATE-001 | 게이트 결정성 + PIT화 + 재판정 런북 | CANONICAL 불변 예상 | 배포 후 diff |

최종 suite 상태 (체인 tip 기준): integration **34/34 전부 통과** (P0 의도적 실패 전량 해소).
fast = 128 passed + characterization 정당 깨짐 5 (PR #4, 승인 대기) + sharpe 1 (CORR-METRIC-003,
P2 — Pass 3 비대상). xfail 0.

배포 순서 (AUDIT MODE): PR 승인 → base 체인부터 순차 merge (#1→#10, 최종적으로
audit/pass0a-inventory → master) → 서버 pull → 런북 실행 (xbrl 백필·listed_date 백필·
dq_gate 재판정) → **전후 ablation 비교** (PIT-AMEND은 사용자 지정 필수 산출물) →
diff 승인 → baseline 재캡처 별도 커밋 → shadow run.
