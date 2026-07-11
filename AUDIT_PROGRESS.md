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
- **상태**: ⏳ 미시작 — 시작 전 `/model fable` 또는 `/model opus` + `/effort xhigh`로 전환 필요 (사용자 확인 필요, Sonnet 5로 진행 시 AUDIT_00 §3 위반)

---

## Pass 1A/1B — 데이터·엔진 감사 (`AUDIT_02_PASS1.md`)

- **모델 요구사항**: Fable 5
- **상태**: ⏳ 미시작

## Pass 2 — 재현·영향분석 (`AUDIT_03_PASS2_3.md`)

- **모델 요구사항**: Fable 5 / Opus 4.8 / effort xhigh
- **상태**: ⏳ 미시작

## Pass 3 — 수정·PR (`AUDIT_03_PASS2_3.md`)

- **모델 요구사항**: Sonnet 5(단순 수정) / Opus 4.8·Fable 5(engine/PIT/metrics, effort xhigh)
- **상태**: ⏳ 미시작
