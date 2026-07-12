# AUDIT_03 — Pass 2·3: 재현과 수정

> **선행**: `TECH_DEBT.md` 에 P0-A/P0-B 목록이 확정돼 있어야 한다.
> **선행**: `CLAUDE.md` AUDIT MODE 블록이 적용돼 있어야 한다 (`AUDIT_04`).

---

# Pass 2 — 재현 + 영향 행렬

**모델**: Fable 5 또는 Opus 4.8 / **effort xhigh**
**제약**: 여전히 **프로덕션 코드를 수정하지 않는다.** `tests/` 만 추가한다.

## 핵심: 재현 "스크립트"가 아니라 **실패하는 테스트**를 커밋한다

일회성 재현 스크립트는 수정 후 버려진다. 실패하는 테스트를 먼저 커밋하면
수정 후에도 남아서 **영구 회귀 방지**가 된다.

## Claude Code 지시

```
TECH_DEBT.md 의 P0-A / P0-B 항목만 다룬다. P1 이하는 건드리지 마라.

각 항목마다:

1. 버그를 재현하는 **최소 실패 테스트**를 작성한다.
   - 위치: tests/oracle/ (수학적 오류) 또는 tests/integration/ (SQL·PIT 경계 오류)
   - 현재 코드에서 **실제로 실패하는지** 반드시 확인하라.
     실패하지 않으면 그 항목은 재현 실패다 — TECH_DEBT 에 "재현 불가"로 표시하고
     등급을 재검토하라. 논증만으로 P0-A 로 승격시키지 마라.

2. 영향받는 실제 구간·시나리오 목록을 산출한다.
   - 어느 리밸런싱 구간에서 조건이 실제로 발생했는가
   - 어느 시나리오 태그가 영향받는가
   - 발생 건수 (예: "189개 조합 중 1건")

3. 수정 후보별로 shadow run 을 돌리고 **차이표**를 만든다.
   ★ CAGR 만 보지 마라. 아래 전부를 비교하라:

       편입 종목 변경 여부 (Jaccard 유사도, 구간별)
       구간별 수익률
       gross / net
       optimistic / conservative
       turnover
       CAGR / Sharpe / MDD
       robustness
       benchmark

   ★ **편입 종목이 바뀌었다면 CAGR 변화가 작아도 중대 결함이다.**
     룩어헤드나 시나리오 조립 오류는 최종 숫자 변화가 작아도 선택 자체가 오염된 것이다.

   ※ selection tape 와 aggregate tape 를 분리해뒀으므로:
       selection 불변 + aggregate 변경 → 산술 수정
       selection 변경                  → 필터/모델 수정
     이 구분을 차이표에 명시하라.

4. 항목 간 **결합**을 판정한다.
   기지 결합:
     CORR-ENGINE-003 (valuation_date 주입) ↔ CORR-METRIC-002 (CAGR 캘린더일수)
       → 열린 구간 #23 을 통해 결합. 따로 고치면 결과 변동을 두 번 겪는다.
       → closed-period baseline 으로 #23 을 정식 기준에서 제외하면 동시 소거된다.
         이 접근이 유효한지 검증하고, 유효하면 두 항목을 하나의 PR 로 묶어라.

   다른 결합이 있는지도 판정하라. 특히 정렬 tie-break 변경은
   CORR-ENGINE-002(순서 의존성)와 selection tape 전체에 파급된다.

5. 수정 순서를 제안하라. 근거를 적어라.

권장 첫 대상: CORR-ENGINE-002 (상폐 opt/cons 순서 의존성)
  - 재현이 명확하다
  - Pass 0C 에서 오라클 테스트(O-3 순서 독립성)를 이미 만들어뒀다
  - 다른 항목과의 결합이 상대적으로 적다

산출물:
  tests/oracle/, tests/integration/ 에 실패 테스트 (커밋)
  TECH_DEBT.md 갱신 (재현 결과, 영향 행렬, 수정 순서)
  IMPACT_MATRIX.md (차이표)
```

## 게이트
- [ ] 각 P0 항목마다 실패하는 테스트가 커밋됐거나, "재현 불가"로 강등됐다
- [ ] 차이표에 편입 종목 변경 여부가 포함돼 있다
- [ ] 결합 항목이 식별되고 묶음 단위가 정해졌다
- [ ] 프로덕션 코드가 수정되지 않았다

---

# Pass 3 — 수정

**모델**: Sonnet 5 (단순 수정) / Opus 4.8·Fable 5 + xhigh (engine·PIT·metrics)
**단위**: **항목당 1 커밋이 아니라 항목당 1 PR**

## 왜 PR인가

항목마다 바로 master 에 push 하고 서버가 pull 하면,
**감사 중간 상태나 잘못된 baseline 갱신이 서버 기준값이 된다.**
CLAUDE.md 기본 규칙은 세션 종료 시 자동으로 master push + 서버 pull 을 수행하므로,
**AUDIT MODE 블록을 먼저 적용하지 않으면 이 위험이 실제로 발생한다.**

## 브랜치 흐름

```
audit/{ITEM_ID} 브랜치
    │
    ├─ 커밋 1: 버그를 재현하는 실패 테스트 (수정 없이)
    │           → CI/로컬에서 실제로 실패하는 것을 확인
    │
    ├─ 커밋 2: 최소 수정
    │           → tests/oracle/ + tests/integration/ 전부 통과해야 한다
    │             여기가 깨지면 수정이 틀린 것이다
    │           → tests/characterization/ 이 깨지는 것은 정상일 수 있다
    │
    ├─ 전체 테스트 + 결과 diff 표 작성
    │
    ├─ push origin audit/{ITEM_ID}      ← master 로 push 하지 않는다
    │
    ├─ PR 생성 (본문에 diff 표 + 근거 첨부)
    │
    ▼
[사용자 승인]
    │
    ├─ 커밋 3 (별도): baseline 갱신     ← 승인 후에만
    │
    ├─ master merge
    ├─ 서버 pull
    └─ 서버 shadow run
```

## Claude Code 지시

```
{ITEM_ID} 하나만 수정한다. 다른 항목은 건드리지 마라.
CLAUDE.md AUDIT MODE 규칙에 따라 master push · 서버 pull 을 자동 수행하지 않는다.

1. git checkout -b audit/{ITEM_ID}

2. 커밋 1 — 실패 테스트
   Pass 2 에서 만든 실패 테스트를 이 브랜치에 올린다 (이미 있으면 확인만).
   pytest 로 **실제로 실패하는지** 확인하고 로그를 남겨라.

3. 커밋 2 — 최소 수정
   요구된 항목만 고친다. "겸사겸사" 리팩터링 금지.
   수정 후:
     pytest -m "not integration"   → 통과 필수
     pytest -m integration         → 통과 필수
     ※ tests/oracle/ 또는 tests/integration/ 이 깨지면 **수정이 틀린 것이다.** 멈춰라.

4. characterization 이 깨진 경우
   ★ 자동으로 갱신하지 마라. 절대로.
   멈추고 다음을 제시한 뒤 사용자 승인을 기다려라:
     - 어떤 시나리오의 어떤 지표가 얼마나 바뀌었는가
     - 편입 종목이 바뀌었는가 (selection tape diff)
     - 이 변화가 "버그 수정으로 인한 정당한 변화"인 이유
   승인 후 **별도 커밋**으로 baseline 을 갱신한다.

5. git push origin audit/{ITEM_ID}
   PR 을 생성하고 본문에 IMPACT_MATRIX 의 해당 행을 첨부한다.
   master 로 직접 push 하거나 서버에서 pull 하지 마라.

6. TECH_DEBT.md 에서 해당 항목 상태를 갱신한다 (PR 링크 포함).
```

## 수정 시 함께 심을 것 (재발 방지)

각 수정 PR에 아래를 함께 넣는다:

```
[계약 명시]  data_access 의 모든 조회 함수 docstring 첫 줄에 정확한 반환 계약을 적는다.
             예:
             """as_of 이하 최신 거래일의 종가. 상장폐지로 가격이 끊겨도 None 이 아니라
             마지막 가격을 반환한다. 상폐 판정에 이 함수를 쓰지 마라 → is_delisted_at() 사용."""

[예외 명시]  조회·네트워크 실패 시 조용한 기본값(0, None) 대신 예외를 던진다.
             기본값이 필요하면 호출자가 명시적으로 allow_missing=True 를 넘기게 한다.

[SSOT]       상수를 재선언하지 말고 import 한다. 테스트도 마찬가지다.

[결정성]     순회 순서에 의존하는 계산을 제거한다. 제거가 어렵다면 정렬 키를
             명시하고 tie-break 를 고정한다.
```

---

# 감사 종료 후 — CLAUDE.md 영구 규칙

감사가 끝나면 AUDIT MODE 블록을 제거하고, 아래를 **영구 규칙**으로 남긴다.

```markdown
### 코드 정합성 규칙 (영구)
- data_access 의 모든 조회 함수는 docstring 첫 줄에 **정확한 반환 계약**을 명시한다.
- 조회·네트워크 실패 시 조용한 기본값(0, None)을 반환하지 않는다. 예외를 던진다.
- 계산했지만 소비되지 않는 파라미터를 남기지 않는다.
- 지표 산식(CAGR/Sharpe/MDD/turnover)은 metrics.py 단일 정의. 복제 금지.
- 상수(RF/RK/OMEGA/VB_CAP/DELISTING_HAIRCUT/거래비용)는 재선언 금지, import 만.
- 순회 순서에 의존하는 계산 금지. 불가피하면 정렬 키와 tie-break 를 명시한다.
- 엔진은 date.today() 를 내부에서 호출하지 않는다. valuation_date 를 주입받는다.
- 백테스트 결과에 영향을 주는 코드를 수정하면:
    pytest -m "not integration" + pytest -m integration 전부 통과해야 한다.
    tests/characterization/ 이 깨지면 **자동 갱신 금지** — 사용자 승인 후 별도 커밋.
```
