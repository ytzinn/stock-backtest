# AUDIT_00 — 코드 정합성 감사 마스터

> **버전**: v1.0 (2026-07-12)
> **대상 저장소**: `stock-backtest/`
> **성격**: 코드 품질 리팩터링이 아니라 **숫자 정합성 감사(correctness audit)**
> **선행 문서**: `CLAUDE.md`, `MASTER.md`, 루트의 모든 `SPEC_*.md`

---

## 0. 왜 하는가

이 저장소에서 발견된 버그의 계보는 전부 **"숫자가 조용히 틀렸는데 아무도 몰랐다"** 유형이다.

| 사고 | 근본 원인 |
|------|-----------|
| 상폐 haircut 미작동 (v5.3) | `get_close_price()`가 `date<=as_of` 최신값을 반환한다는 **계약이 문서화되지 않음** → 15개 시나리오 JSON 전부 오염 |
| `phase2_rim.py`가 G_full로 조립 (v5.2) | **시나리오 정의의 단일 소스 부재** |
| `MAX_STOCK_WEIGHT` 유령 파라미터 (v5.2) | `_calc_period_return()`이 weight를 소비하지 않음 |
| `overlay_returns`의 `base_return` (SPEC_08_B05) | 이미 블렌딩된 값을 순수 sleeve 수익률로 오인 |

넷 다 **리뷰로 잡을 수 있었지만 테스트가 없어서 못 잡은** 부류다.
따라서 이 감사의 목표는 "코드를 예쁘게" 가 아니라 **"틀린 숫자를 찾아내고, 다시는 조용히 틀리지 않게 만드는 것"** 이다.

---

## 1. 절대 원칙

### 원칙 1 — 기존 결과는 정답이 아니다 ★ 가장 중요

> **기존 결과는 "보존해야 할 동작(behavior)"이지 "정답(truth)"이 아니다.**

haircut 버그가 있던 시점에 결과를 골든값으로 동결했다면, 그 골든 테스트는 버그를 **영구 보존**했을 것이다.
따라서 테스트를 반드시 두 종류로 **물리적으로 분리**한다.

| 종류 | 디렉토리 | 검증하는 것 | 버그 수정 시 |
|------|----------|-------------|--------------|
| **특성화 (characterization)** | `tests/characterization/` | "코드를 안 바꿨을 때 과거와 같은 결과가 나오는가" | **정당하게 깨질 수 있다** |
| **오라클 (oracle)** | `tests/oracle/`, `tests/integration/` | "그 결과가 수학적·경제적으로 옳은가" | **깨지면 수정이 틀린 것이다** |

두 디렉토리를 `tests/golden/` 같은 하나의 이름으로 묶지 마라. Claude Code가 "기존 값과 다르다 = 오류"로 오판한다.

### 원칙 2 — 조사와 수정을 분리한다
Pass 0~2는 **프로덕션 코드를 한 줄도 수정하지 않는다.** 수정은 Pass 3에서만 한다.

### 원칙 3 — 문서보다 코드를 먼저 읽는다
`MASTER.md`/`SPEC_*.md`를 먼저 읽으면 "원래 그렇게 설계됐겠지"라는 전제로 코드를 해석하게 된다.
읽는 순서: **CLAUDE.md(운영 제약) → 저장소 트리·entrypoint → 코드만 보고 실행 그래프 작성 → 그 다음에 문서 읽고 대조.**

### 원칙 4 — 유사함 ≠ 중복
성능·격리·배치처리·운영대상 차이로 **의도적으로 분리된 코드**가 있다.
`backtest/regime/data_access_regime.py`는 2,000종목 × ~126개월 배치 조회 전용 구현이며 중복 부채가 아니다.
의도적 분리로 판정되면 "공통 계약을 공유하는가 / drift 방지 테스트가 있는가"만 평가한다.

### 원칙 5 — 폐기 코드는 삭제 대상이 아니다
`factor_screener.py`, `ablation.py`의 `E_*`/`G_*`, StabilityFilter `R2`/`R3`는
**폐기하되 실험 기록용으로 보존한 것**이다. 삭제하지 마라.
**실행 경로에서 완전히 격리됐는지, 실수로 다시 조립될 수 있는지**만 판정한다.

### 원칙 6 — 라벨링
모든 감사 문장에 `[검증된 사실]` / `[Claude 의견]` / `[확실하지 않은 사실]` 을 붙인다.
**코드를 직접 읽고 확인한 것만 `[검증된 사실]`이다.** 확인 못 했으면 확인 방법을 함께 적어라.

---

## 2. Pass 구조

```
[선행]  CLAUDE.md에 AUDIT MODE 블록 추가          (수동, AUDIT_04 참조)
   │
   ▼
Pass 0A  재현성 인벤토리                          → AUDIT_01
Pass 0B  특성화 baseline (기존 동작 기록)          → AUDIT_01
Pass 0C  독립 오라클 + 합성 DB 통합 테스트         → AUDIT_01   ★ 가장 어려움
   │  게이트: fast suite + oracle 전부 통과
   ▼
Pass 1A  데이터 lineage·PIT·상장/상폐 감사         → AUDIT_02
Pass 1B  파이프라인·포트폴리오·수익률·metrics 감사  → AUDIT_02
   │  게이트: TECH_DEBT.md에 P0-A/P0-B 목록 확정
   ▼
Pass 2   실패 테스트로 P0 재현 + 영향 행렬         → AUDIT_03
   │  게이트: 각 P0 항목마다 실패하는 테스트가 커밋됨
   ▼
Pass 3   항목당 1 PR (테스트 커밋 → 수정 커밋)     → AUDIT_03
   │
   ▼
승인된 결과만 baseline 갱신 → master merge → 서버 shadow run
```

---

## 3. 모델 · effort 배치

| 단계 | 모델 | effort | 근거 |
|------|------|--------|------|
| Pass 0A 인벤토리 | Sonnet 5 | high | 기계적 수집 |
| Pass 0B 특성화 | Sonnet 5 | high | 명세 명확 |
| **Pass 0C 오라클 설계** | **Fable 5** 또는 Opus 4.8 | xhigh | **"무엇이 정답인가"를 정하는 단계. 이번 감사에서 가장 어렵다** |
| Pass 1A/1B 감사 | Fable 5 | — | 장기 자율 조사, 행동 전 검증 |
| Pass 2 재현·영향분석 | Fable 5 / Opus 4.8 | xhigh | 근본원인 조사 |
| Pass 3 단순 수정 | Sonnet 5 | high | |
| Pass 3 engine/PIT/metrics 수정 | Opus 4.8 / Fable 5 | xhigh | 항목 간 결합 위험 |

**지정 방법**
```bash
# 프로젝트 기본값 (.claude/settings.json)
{ "model": "sonnet" }

# 세션 시작 시 상향
/model fable          # Fable 5는 기본 모델이 아니므로 반드시 명시 선택
/model opus
/effort xhigh         # 세션을 넘어 유지됨. /effort 로 현재값 확인 가능

# 또는 실행 시
claude --model opus
```

> `opus` 별칭은 Opus 4.8, `sonnet`은 Sonnet 5로 해석된다(Anthropic API 기준).
> `opusplan`은 플랜 모드에서 Opus, 실행에서 Sonnet을 쓴다.

---

## 4. TECH_DEBT.md 항목 포맷

라인 번호는 코드 수정 즉시 무효가 된다. **심볼과 commit SHA를 필수로 기록한다.**

```
ID: CORR-ENGINE-001
Commit: abcdef1
Location: backtest/engine.py::_calc_period_return    ← 심볼 필수
Lines: 200-251                                       ← 보조
Expected contract: (문서/이름이 약속하는 것)
Actual behavior:   (코드가 실제로 하는 것)
Result impact: Y / N / Conditional / Unknown
Affected scenarios: (해당되면 시나리오 태그)
Evidence: tests/... 또는 scripts/audit/...
Label: [검증된 사실] / [Claude 의견] / [확실하지 않은 사실]
```

### 우선순위 등급

| 등급 | 정의 |
|------|------|
| **P0-A** | 현재 숫자가 틀렸음이 **재현된** 항목 |
| **P0-B** | **조용한 숫자 오염이 가능한 구조.** 아직 재현 못 했어도 P0-B다.<br>예: 조회 실패 시 0 반환 / `date.today()` 내부 호출 / 룩어헤드 가드 부재 / 순회 순서 의존성 |
| **P1** | 재현성·provenance·문서↔구현 계약 불일치·정책 미결 |
| **P2** | 성능·운영·실제 중복 |
| **P3** | 스타일·네이밍 |

**P0-A/P0-B만 Pass 2·3의 대상이다.** P1 이하는 목록화만 하고 감사 종료 후 별도로 처리한다.

---

## 5. 이미 확인된 기지(旣知) 감사 대상

Pass 1을 기다리지 않고도 코드만으로 확인된 항목. Pass 1에서 등급을 확정하라.

| ID(가안) | 내용 | 예상 등급 |
|---|---|---|
| CORR-ENGINE-001 | `build_portfolio()`가 반환한 `weight`를 `_calc_period_return()`이 소비하지 않고 단순평균(`sum/len`)함 | P0-B (등가중 동안은 우연히 일치, 비등가중 도입 시 즉시 오류) |
| CORR-ENGINE-002 | 상폐 opt/cons 조정의 `n` 계산이 **종목 순회 순서**에 의존. 가격결측 종목이 상폐 종목보다 앞/뒤에 있느냐로 값이 달라짐.<br>순회 순서 = RIM 상승여력 정렬 순서 → **정렬 tie-break를 바꾸면 편입종목이 같아도 숫자가 바뀐다** | P0-B → 재현 시 P0-A |
| CORR-METRIC-001 | `turnover = sold / max(len(prev), len(curr), 1)`.<br>5종목 → (기존 5 포함) 20종목이면 turnover 0% 반환. 실제로는 대규모 재조정.<br>올바른 정의: `0.5 × Σ|w_new − w_old|` | **거래비용에 입력되면 P0-A / 리포트 전용이면 P1** — Pass 1에서 소비처 확인 필수 |
| CORR-METRIC-002 | CAGR이 실제 캘린더일수가 아니라 `구간수 ÷ 2`로 연수 계산 | P0-B |
| CORR-ENGINE-003 | 마지막 리밸런싱 구간 종료일을 `date.today()`로 결정 → **같은 코드·같은 DB인데 실행 날짜가 다르면 결과가 달라짐** | P0-B (재현성 결함) |
| CORR-BENCH-001 | KOSPI/KOSDAQ 조회 실패 시 경고만 남기고 `0.0` 반환 → 네트워크 장애가 정상 수익률 0%로 들어가 alpha·robustness 오염 | P0-B |
| CONTRACT-PF-001 | `portfolio.py` docstring은 "후보 5개 미만이면 빈 포트폴리오"라고 하나, 구현은 `n==0`일 때만 빈 dict 반환 | P1 (**정책 결정 항목** — 임의 수정 금지) |
| SSOT-SCEN-001 | 시나리오 정의가 `ablation.py`와 `configs/` 두 곳에 존재 → v5.2 사고의 재발 경로 | P1 |
| DOC-ABL-001 | `ablation.py` docstring은 "7개 시나리오", 실제 `ABLATION_CONFIGS`는 33개(랜덤 4 + 결정론 29) | P1 |

**결합 주의:** `CORR-ENGINE-003`(valuation_date 주입)과 `CORR-METRIC-002`(CAGR 캘린더일수)는
열린 구간 #23을 통해 결합돼 있다. 따로 고치면 결과 변동을 두 번 겪는다.
→ **closed-period baseline(#23 제외)을 정식 기준으로 삼으면 두 문제가 동시에 소거된다.**

---

## 6. 다음 문서

| 파일 | 내용 |
|------|------|
| `AUDIT_01_PASS0.md` | Pass 0A 인벤토리 / 0B 특성화 / 0C 오라클·통합테스트 |
| `AUDIT_02_PASS1.md` | Pass 1A 데이터 감사 / 1B 백테스트 감사 |
| `AUDIT_03_PASS2_3.md` | Pass 2 재현 / Pass 3 수정·PR |
| `AUDIT_04_CLAUDE_MD.md` | CLAUDE.md에 추가할 AUDIT MODE 블록 (**Pass 0A 전에 반드시 적용**) |
