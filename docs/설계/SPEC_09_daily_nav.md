# SPEC_09 — 일별 NAV 엔진 + 실전 위험지표 재정의

> **작성일**: 2026-07-19
> **세션 스코프**: 이 문서 전체가 Claude Code 1~2세션 분량. §3 정책은 **2026-07-19 사용자 확정 완료** — 즉시 구현 착수 가능.
> **모델 권장**: Fable 5 (`/model fable`), effort xhigh — 수치 정확성이 핵심인 작업.
> **선행 문서**: `2026.07.18._PIT_OFFICIAL.md`(공식 기준선), TECH_DEBT.md(CORR-METRIC-001 수정 완료 확인), AUDIT_00 §1(oracle/characterization 원칙)
> **표기 규칙**: `[검증된 사실]` / `[Claude 의견]` / `[확실하지 않은 사실]`

---

## 1. 배경 — 왜 지금 이것부터인가

**[검증된 사실]** 현재 공식 MDD(-30.7%)와 Sharpe(0.572)는 완결 20개 **반기 구간 수익률**에서 계산된다. 즉:

- MDD는 리밸런싱 시점 NAV 20개 점의 최대 낙폭 — **구간 중간 낙폭은 관측 불가**
- Sharpe는 반기 수익률 20개 × 연 2회 연율화 — 일중·일간 변동성 미반영
- AUDIT_01 O-5가 이미 "반기 시점 기준인지 월별 MTM 기준인지 명시하라"고 지적한 상태

반기 리밸런싱 + 소형주 조합에서 구간 내 스파이크가 없었을 가능성은 낮다. **실전 MDD는 -30.7%보다 나쁠 것으로 전제하고 채택 판단을 해야 한다.** 또한 SPEC_10(강건성 재발행)의 산출물 목록에 일별 NAV가 포함되므로, 이 엔진은 SPEC_10의 선행 의존성이다.

**[검증된 사실] 선행 조건 해소 상태**: CORR-METRIC-001(turnover 산식)은 Pass 3에서 수정 완료(`audit/CORR-METRIC-001`, 0.5×Σ|Δw|). 일별 NAV가 거래비용 로직을 상속해도 안전하다. 단, characterization baseline 5건 갱신 승인 상태를 세션 시작 시 확인할 것.

---

## 2. 산출 목표 지표

일별 NAV 시리즈(gross / net 2종)로부터:

| 지표 | 정의 규약 |
|---|---|
| 일별 MDD | 일별 net NAV 기준 최대 낙폭. 발생 구간(peak일~trough일) 함께 기록 |
| 일별 변동성 | 일별 로그수익률 표준편차 × √252 |
| 일별 Sharpe | (연율화 일별 수익률 − RF) / 연율화 변동성. RF는 constants.py SSOT |
| 최악 월간수익률 | 캘린더 월 단위 수익률 최솟값 (월 경계는 캘린더 월말) |
| CVaR 5% (1M) | 겹치지 않는 1개월 수익률 하위 5% 평균. 표본 부족 시 하위 3개 평균으로 대체하고 그 사실을 명기 |
| CVaR 5% (3M) | 롤링 3개월(겹침 허용) 수익률 하위 5% 평균 — 겹침 사용 사실 명기 |
| Tracking error | 일별 (전략 − 벤치마크) 수익률 표준편차 × √252. 벤치마크: KOSPI, KOSDAQ (SPEC_10의 U_pbr_path_ew 확정 후 추가) |

기존 반기 종점 MDD·Sharpe는 **삭제하지 않는다** — characterization 원칙에 따라 "기록된 동작"으로 유지하고, 리포트에 두 정의를 병기한다(예: "MDD −30.7% (반기 종점) / −XX.X% (일별)").

---

## 3. 정책 확정 기록 (CONTRACT-NAV-001~005, 2026-07-19 사용자 확정)

> CONTRACT-PF-001과 같은 절차로 확정 완료. 각 항목의 **확정안은 굵게 표기** — Claude Code는 확정안을 docstring 계약으로 옮기고, 비채택 선택지는 "검토된 대안"으로 주석에 남긴다.

### CONTRACT-NAV-001 — 거래정지 종목의 일별 가격

| 선택지 | 내용 | 비고 |
|---|---|---|
| (a) 직전 종가 forward-fill | 정지 기간 동안 NAV 기여 동결 | 구간 수익률 계산의 기존 취급(다음 리밸런싱까지 보유 유지)과 정합 |
| (b) NaN 제외 후 잔여 종목 재정규화 | 정지 종목을 일시적으로 NAV에서 제거 | 정지 해제 시 재편입 규칙이 추가로 필요해짐 — 비채택 |

**확정: (a)** — 2026-07-19

### CONTRACT-NAV-002 — 구간 중 상장폐지 종목의 일별 처리

| 선택지 | 내용 | 비고 |
|---|---|---|
| (a) 상폐 이벤트일에 haircut 적용 → 이후 현금성(수익률 0) 보유 | 마지막 거래가 ffill → 상폐일에 `DELISTING_HAIRCUT`(engine.py SSOT) 적용 → 구간말까지 무수익 | 기준(base) 시나리오의 구간 수준 처리를 일별로 자연 확장 |
| (b) 구간말 일괄 haircut | 구간 마지막 날 한 번에 반영 | 구간 수익률과는 일치하나 일별 경로가 비현실적 — 비채택 |

**확정: (a)** — 2026-07-19

낙관/보수 시나리오는 일별 NAV에서는 만들지 않는다 — 기준 시나리오만. (구간 수준 3시나리오는 기존대로 유지.)

### CONTRACT-NAV-003 — 거래비용 반영 시점

| 선택지 | 내용 |
|---|---|
| (a) 리밸런싱일에 `turnover × (COST_SELL + COST_BUY)`를 일괄 차감한 net NAV | 기존 net 수익률 정의와 정합, 검증 게이트(§4) 성립 |
| (b) 매수/매도 각 시점 분리 차감 | 현 엔진은 왕복 비용을 리밸런싱 시점에 합산하므로 (b)는 새 모델링 — 비채택, Phase 3+ |

**확정: (a)** — 2026-07-19

**구현 세부 확정 (2026-07-19 구현 검토, 사용자 확정)**: 일별 net NAV는 리밸런싱일에 **승법 차감** `NAV × (1 − tc)`로 반영한다. 이때 구간 복리 net 수익률은 `gross − tc − gross×tc`가 되어 엔진의 산술 정의(`net = gross − tc`)와 교차항 `gross×tc`(구간당 최대 ~0.1%p)만큼 어긋난다 — 이는 버그가 아니라 정의 차이이며, 일별 net CAGR와 공식 net CAGR(16.28%)의 차이를 reconciliation 리포트에 구간별로 정량 명기한다. 산술 정합 강제(차감계수 `tc/(1+gross)` 소급 보정)는 일별 경로 관점에서 인위적이라 비채택.

### CONTRACT-NAV-004 — 배당

Phase 2 전 구간과의 일관성을 위해 **미반영 유지**로 확정(변경 아님, 명기 — 2026-07-19). 단 리포트에 다음 한 줄을 항상 포함한다: "저PBR 포트폴리오의 배당수익률이 구조적으로 높을 개연성이 있어 절대 수익률은 과소평가됐을 수 있음 — total return 전환은 별도 과제." **[확실하지 않은 사실]** 과소평가 폭은 실측 전 미지.

### CONTRACT-NAV-005 — 리밸런싱일 체결 가격

기존 엔진과 동일하게 리밸런싱일 종가(adj_close) 체결 가정으로 확정(변경 아님, 명기 — 2026-07-19). 일별 NAV의 첫날은 체결 직후 상태.

---

## 4. 정합성 검증 게이트 (핵심)

일별 NAV가 새 진실을 만드는 게 아니라 **기존 구간 수익률의 일별 전개**임을 보장한다:

```
게이트 G-NAV-1 (필수):
  각 완결 구간에 대해
    Π(1 + 일별 gross 수익률) − 1  ≈  engine 기록 gross period_return
  허용 오차: |diff| < 1e-6 (상폐·정지 종목이 없는 구간)
             |diff| < 1e-3 (상폐·정지 종목 포함 구간 — CONTRACT-NAV-001/002의
             일별 근사에서 발생하는 차이. 초과 시 구간별 사유를 reconciliation
             리포트에 기록하고 사용자 승인 대기)

게이트 G-NAV-2 (필수):
  net NAV의 리밸런싱일 차감 **비율** == engine 기록 transaction_cost (tol 1e-9)
  (승법 차감 NAV×(1−tc) — CONTRACT-NAV-003 구현 세부 확정)

게이트 G-NAV-3:
  일별 MDD ≥ 반기 종점 MDD (절대값 기준 같거나 커야 함 — 작으면 구현 버그)

게이트 G-NAV-4 (필수, net 정합):
  각 완결 구간에 대해
    |Π(1 + 일별 net 수익률) − 1 − engine 기록 net_return| ≤ |gross×tc| + 1e-6
  (승법/산술 정의 차이의 교차항 상한 — 초과 시 구현 버그.
   G-NAV-1의 상폐·정지 완화 톨이 적용되는 구간은 그만큼 가산)
```

G-NAV-3은 수학적 필연이다: 일별 경로는 반기 점들을 포함하므로 낙폭이 줄어들 수 없다.

---

## 5. Claude Code 지시

```
[사전 확인]
  - §3 CONTRACT-NAV-001~005는 2026-07-19 전 항목 확정 완료 — 확정안(전부 (a) 및
    기존 정책 명기)을 docstring 계약으로 옮기고 즉시 착수.
  - git log에서 audit/CORR-METRIC-001 병합 확인. characterization baseline
    갱신 승인 상태 확인 (미승인 대기 중이면 그 사실만 기록하고 진행 가능 —
    일별 NAV는 baseline과 독립).
  - DRIFT-INGEST-001 준수: 크론 동결 스냅샷에서만 실행. 10:00~10:45 실행 금지.

[N-1] backtest/daily_nav.py 신규
  입력: holdings tape (ticker, weight, 구간) + price_history(adj_close)
        + stock_listing_events(상폐일) + is_suspended
  출력: 일별 DataFrame — date, nav_gross, nav_net, 종목별 기여 (long format 별도)
  - 상수는 전부 SSOT import (DELISTING_HAIRCUT, COST_SELL/COST_BUY, RF).
    테스트·모듈 내 재하드코딩 금지.
  - CONTRACT-NAV-001~005 확정안을 docstring에 계약으로 명시.
  - **NAV 경로 로직 SSOT (2026-07-19 구현 검토, 사용자 확정)**:
    regime/mtm_monthly.py `_nav_path()`와 의미론 동일(고정수량·상폐 haircut 1회
    동결·진입가 결측 재정규화)이므로, 핵심 계산을 daily_nav.py의 **순수 함수
    (가격 패널 입력, DB 무접촉)**로 일반화하고 mtm_monthly가 이를 위임 호출하도록
    리팩터링한다 (산식 복제 금지 규칙). mtm 불변 검증은 A-2 복제 게이트 재실행.
  - holdings tape의 entry/exit 가격은 정수 반올림값(export_portfolios.py) —
    NAV 계산 가격은 반드시 DB adj_close 직접 사용. tape에 weight 필드 없음 →
    1/n 동일가중 재구성 (mtm_monthly와 동일 관례).
  - 일별 벤치마크(KOSPI/KOSDAQ) 시리즈는 FDR 조회 시점에 CSV로 함께 보존
    (DRIFT-INGEST-001 재현성 — 지수 데이터는 DB 스냅샷 밖에 있으므로).

[N-2] backtest/metrics.py 확장 (기존 함수 수정 금지 — 신규 함수 추가만)
  compute_daily_metrics(nav_df, benchmark_df) → §2 지표 dict
  연율화 규약(√252, 캘린더 월 경계, CVaR 겹침 규약)을 docstring에 명시.
  예외 1건: metrics.py의 RF_ANNUAL=0.0263 하드코딩(상수 재선언 금지 영구 규칙의
  기존 위반분)은 constants.RF import로 교체 — 값 동일이라 수치 무변,
  characterization 안전 (2026-07-19 구현 검토).

[N-3] tests/oracle/test_daily_nav_oracle.py
  합성 케이스 손계산 대조:
  - 3종목 × 10일, 가격 경로 수기 지정 → NAV·MDD 손계산 일치
  - 구간 중 1종목 상폐 (haircut 적용 시점·금액 손계산)
  - 거래정지 3일 (ffill 동작)
  - 리밸런싱 경계에서 비용 차감 (G-NAV-2 축소판)
  - 일별 MDD ≥ 종점 MDD 불변식

[N-4] scripts/run_daily_nav.py
  대상 태그의 holdings에서 일별 NAV 생성 + §4 게이트 전체 실행 +
  reconciliation 리포트 (구간별 diff 표) 출력.
  1차 대상 태그: F_pbr_no_r3r4, F_pbr_r6, F_momentum_rim, D_pbr_only,
  F_no_stability_clean (+ SPEC_10에서 추가되는 태그는 그쪽 세션에서).
  ⚠ holdings tape가 07-18 PIT 기준으로 재생성돼 있지 않으면 (SPEC_10 §2 참조)
  먼저 tape 재생성부터. 07-15/16산 holdings 파일로 일별 NAV를 만들지 마라.

[N-5] 결과 보고
  experiments/runs/2026.XX.XX._DAILY_NAV.md — 태그별
  {반기 종점 MDD vs 일별 MDD, 반기 Sharpe vs 일별 Sharpe, 최악 월간, CVaR} 대조표.
  MDD 격차가 큰 태그는 낙폭 발생 구간(날짜)을 명시.
```

## 6. 완료 체크리스트

- [x] CONTRACT-NAV-001~005 사용자 확정 (2026-07-19, 본 문서 §3) — TECH_DEBT.md 전기(轉記)는 구현 세션에서
- [ ] oracle 신규 테스트 전부 통과, 기존 fast/integration suite 무손상
- [ ] G-NAV-1/2/3 게이트 전 태그 통과 (예외 구간은 reconciliation 리포트 + 사용자 승인)
- [ ] 일별 MDD·Sharpe·CVaR가 공식 리포트에 반기 정의와 병기됨
- [ ] 프로덕션 기존 수치(구간 수익률·기존 지표) 불변 — `git diff`와 characterization으로 확인
