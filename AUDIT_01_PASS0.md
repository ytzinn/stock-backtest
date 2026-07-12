# AUDIT_01 — Pass 0: 안전망 구축

> **선행**: `AUDIT_00_MASTER.md` §1 절대 원칙을 먼저 읽어라.
> **선행**: `CLAUDE.md`에 AUDIT MODE 블록이 적용돼 있어야 한다 (`AUDIT_04`).
> **공통 제약**: Pass 0 전체에서 **프로덕션 코드를 한 줄도 수정하지 않는다.** `tests/`와 `scripts/audit/`만 추가한다.

---

# Pass 0A — 재현성 인벤토리

**모델**: Sonnet 5 / effort high
**목표**: 기존 결과를 기준선으로 쓰기 전에, 그것이 **어떤 코드·데이터·날짜**로 만들어졌는지 확정한다.

## Claude Code 지시

```
목표: 기존 백테스트 결과의 provenance를 확정한다. 코드는 수정하지 않는다.

1. tests/baselines/AUDIT_MANIFEST.json 을 생성하라. 포함 항목:
   - git commit SHA (HEAD), branch
   - Python 버전, requirements.txt 주요 패키지 실제 설치 버전
   - DB migration 적용 상태 (ingest/migrations/apply.py 기준)
   - price_history 의 MAX(date)
   - financials_pit 의 MAX(available_from)
   - stock_listing_events 행 수
   - experiments/ablation/ 의 실제 파일 목록 + 각 파일 SHA256 + run_at
   - 엔진이 마지막(열린) 구간 종료일을 무엇으로 결정하는지
     → date.today() 를 내부에서 호출한다면 그 사실 자체를 GAPS.md 에 P0-B 로 기록하라.
       (같은 코드·같은 DB인데 실행 날짜가 다르면 결과가 달라짐 = 재현성 결함)

2. backtest/ablation.py 의 ABLATION_CONFIGS 에 정의된 **전체 태그**를 열거하라.
   개수를 미리 가정하지 마라. 코드에서 직접 세라.
   각 태그를 4분류하고 tests/baselines/SCENARIO_REGISTRY.json 으로 저장하라:
     CANONICAL  — 현재 채택 파이프라인(Hard→Stability(R1,R4,R5,R6)→Momentum→RIM)과 필수 대조군
     DIAGNOSTIC — 신호분리·단일팩터·leave-one-out 진단용
     ARCHIVE    — 폐기된 FactorScreener(E_*/G_*) 등, 기록 보존용
     RANDOM     — seed가 필요한 분포 시나리오

3. 정의(ABLATION_CONFIGS) vs 실제 결과 파일(experiments/ablation/) 을 대조하라:
   - 정의됐으나 결과 파일 없음
   - 결과 파일은 있으나 정의 없음
   - 결과 파일들의 run_at 이 서로 다른 코드 버전에서 생성된 것으로 보이는 경우
   - 코드/문서 불일치 (예: ablation.py docstring 의 시나리오 개수 vs 실제 정의 수)

4. MASTER.md 의 SPEC 파일 목록 vs 루트에 실제 존재하는 SPEC_*.md 파일 목록을 대조하라.
   개수를 가정하지 말고 glob 으로 실제 파일을 읽어라. 불일치는 GAPS.md 에 기록.

산출물:
  tests/baselines/AUDIT_MANIFEST.json
  tests/baselines/SCENARIO_REGISTRY.json
  GAPS.md   (라벨링 필수: [검증된 사실]/[Claude 의견]/[확실하지 않은 사실])
```

## 게이트
- [ ] 모든 결과 파일이 어떤 commit에서 나왔는지 특정됐거나, 특정 불가로 명시됐다
- [ ] CANONICAL 시나리오가 무엇인지 확정됐다
- [ ] 열린 구간 종료일 결정 방식이 기록됐다

---

# Pass 0B — 특성화 baseline

**모델**: Sonnet 5 / effort high
**목표**: 기존 구현의 **동작을 기록**한다. **이 값은 정답이 아니다.**

## 핵심 설계: decision tape 2계층 분리

기존 `export_portfolios.py`의 holdings JSON은 **반올림된 값**만 저장한다(진입/청산가 정수, 수익률 6자리, weight 미저장).
이 값으로는 원래 계산에 쓰인 부동소수점 가격을 복원할 수 없어 정확한 회귀 재생이 불가능하다.

또한 선택(어떤 종목)과 산술(어떻게 합산)을 한 파일에 섞으면,
산술 버그를 고칠 때마다 selection까지 재생성해야 하고 "종목은 그대로인데 숫자만 바뀐 건가?"를 구분할 수 없다.

```
tests/baselines/selection/{scenario}.json   ← 선택 결과. 원시 float, 반올림 금지
tests/baselines/aggregate/{scenario}.json   ← 위를 입력으로 계산된 지표
```

**수정 후 diff 판독:**
- selection 불변 + aggregate 변경 → **산술 수정**
- selection 변경 → **필터/모델 수정**

## Claude Code 지시

```
목표: 기존 구현의 동작을 기록한다. 이 값은 "정답"이 아니라 "현재 동작"이다.
     tests/characterization/ 의 디렉토리 README 와 모든 테스트 docstring 첫 줄에 다음을 명시하라:

       "characterization baseline — 승인된 정답이 아니라 기존 구현의 동작 기록.
        버그 수정 시 정당하게 깨진다. 깨졌다고 자동으로 되돌리지 마라."

1. decision tape 를 2계층으로 생성한다. 반올림 금지, 원시 float 그대로.

   tests/baselines/selection/{scenario}.json
     구간별로:
       rebalance_date, end_date
       holdings[]: ticker, weight, entry_price(raw float), exit_price(raw float),
                   entry_price_date, exit_price_date, is_delisted, delisting_date

   tests/baselines/aggregate/{scenario}.json
     구간별 수익률(gross/net), turnover, transaction_cost, benchmark_return,
     전체: CAGR, Sharpe, MDD, cagr_optimistic, cagr_conservative, robustness

2. baseline 을 두 층으로 나눈다.

   closed_period  — 완전히 종료된 구간만. **열린 마지막 구간(#23) 제외.**
                    이것이 장기 회귀 테스트의 유일한 공식 기준이다.
   live_snapshot  — valuation_date 를 고정 문자열로 박은 스냅샷.
                    참고용. 회귀 기준으로 쓰지 마라.

   ※ 이렇게 나누는 이유: 엔진이 date.today() 로 열린 구간을 끝내면 실행일마다 결과가 달라진다.
     또 CAGR 산식을 캘린더일수 기준으로 고치면 열린 구간이 부분 연도가 되어 값이 또 변한다.
     closed_period 로 #23 을 제외하면 두 문제가 동시에 소거된다.

3. 대상 시나리오는 SCENARIO_REGISTRY.json 의 CANONICAL 전체 + DIAGNOSTIC 중 현재 결론의
   근거가 된 것들. ARCHIVE 는 선택. RANDOM 은 seed 고정 가능 여부를 먼저 확인하고,
   불가능하면 baseline 대상에서 제외하고 그 사실을 GAPS.md 에 기록하라.

4. 입력·출력 파일의 SHA256 을 AUDIT_MANIFEST.json 에 추가하라.

5. tests/characterization/test_*.py 작성:
   - selection tape 를 입력으로 aggregate 계산 경로만 재현해 aggregate baseline 과 대조
   - pytest 마커 없이 fast suite 에 포함 (DB 미접속)
```

## 게이트
- [ ] `pytest -m "not integration"` 전부 통과
- [ ] characterization 디렉토리/docstring에 "정답 아님" 경고가 명시돼 있다
- [ ] selection/aggregate가 물리적으로 분리돼 있다
- [ ] closed_period baseline에 열린 구간이 포함돼 있지 않다

---

# Pass 0C — 독립 오라클 + 통합 테스트 ★ 가장 어려움

**모델**: **Fable 5** 또는 Opus 4.8 / **effort xhigh**
**목표**: "기존 값과 같은가"가 아니라 **"수학적·경제적으로 옳은가"** 를 검증한다.

> 이 단계가 이번 감사에서 가장 어렵다. 테스트 코드를 쓰는 건 기계적이지만,
> **무엇을 정답으로 삼을지 정하는 오라클 설계**는 그렇지 않다.

## Claude Code 지시 — `tests/oracle/` (DB 미접속, 손계산 가능한 합성 케이스만)

```
tests/characterization/ 와 완전히 분리한다.
버그 수정 시 characterization 은 깨질 수 있지만 oracle 은 깨지면 안 된다.

[O-1] RIMModel.fair_value()
  반환 계약이 총액이 아니라 **주당 적정가**임을 명시적으로 검증하라.
      FV_total     = equity × clamp(1 + (adjROE − r) / (1 + r − ω), 0, VB_CAP)
      FV_per_share = FV_total / shares
  케이스:
    - adjROE == r 일 때 V/B == 1  (초과이익 0 → 장부가)
    - adjROE > r  일 때 V/B > 1
    - clamp 하한 0, 상한 VB_CAP=5.0 경계
    - fv_total <= 0 방어 경로 (R6 이후 PIT 타이밍 불일치 케이스)
    - shares 로 나누는 단계가 누락되면 실패하도록 설계
  ω=0.62, RF=0.0263, RK=0.0873 은 backtest/configs/constants.py 에서 import 하라.
  테스트 안에 숫자를 다시 하드코딩하지 마라 (SSOT).

[O-2] weighted portfolio return
  build_portfolio() 가 반환한 weight 가 **실제로 소비되는지** 검증.
  비등가중 케이스(예: 0.5 / 0.3 / 0.2)로 기대값을 손계산해 대조하라.
  현재 구현이 sum/len 단순평균이라면 이 테스트는 실패해야 정상이다.
  → 실패하면 xfail 처리하지 말고 그대로 두고 CORR-ENGINE-001 의 증거로 쓴다.

[O-3] 상폐 3시나리오 (base / optimistic / conservative)
  ★ 순서 독립성 검증이 핵심이다.
  - 가격결측 종목과 상폐 종목이 **동시에** 존재하는 포트폴리오를 만든다.
  - 두 종목의 순서를 뒤바꾼 두 포트폴리오가 **동일한 결과**를 내는지 검증한다.
  - 현재 구현은 n = len(portfolio) 로 시작해 가격 결측 시 n -= 1 하고,
    이후 상폐 종목에서 1/n 을 조정 비중으로 쓴다 → 순서 의존 가능성.
  - 순회 순서는 RIM 상승여력 정렬 순서다. 즉 정렬 tie-break 를 바꾸면
    편입 종목이 완전히 동일해도 opt/cons 값이 바뀐다.
  - haircut 비율은 backtest.engine.DELISTING_HAIRCUT 를 import 하라 (SSOT).

[O-4] turnover
  케이스: 이전 5종목(각 20%) → 신규 20종목(기존 5종목 전부 잔류, 각 5%)
  현재 산식 sold / max(len(prev), len(curr), 1) 은 0 을 반환한다.
  실제로는 기존 종목을 20%→5% 로 줄이고 15종목을 신규 매수하는 대규모 재조정이다.
  올바른 정의를 결정하고 그 정의로 테스트를 써라:
      turnover = 0.5 × Σ_{t ∈ prev ∪ curr} | w_new[t] − w_old[t] |
  현금 비중을 별도로 다룰 경우 정의를 문서화하라.
  ※ 반드시 확인: turnover 가 거래비용 계산에 입력되는가?
     - 입력된다  → 모든 시나리오의 net 수익률이 틀렸다 (P0-A)
     - 리포트 전용 → 지표만 오도 (P1)
     SPEC_08 이 소형/대형 비대칭 거래비용을 설계 중이므로, 지금 P1 이어도
     Phase B 진입 전에는 P0 가 된다.

[O-5] CAGR / Sharpe / MDD
  - CAGR: **실제 캘린더일수 기준**으로 손계산한 값과 대조.
    현행 "구간수 ÷ 2" 관례가 산출하는 값도 함께 계산해 **차이를 기록**하라(수정 전 영향 추정용).
  - Sharpe: 반기 수익률의 연율화 규약을 명시하고 그 규약대로 검증.
  - MDD: 반기 시점 기준인지 월별 MTM 기준인지 명시. 두 정의가 다른 값을 낸다.

[O-6] 최소 편입 종목 수  ← 정책 결정 항목
  portfolio.py docstring: "후보 5개 미만이면 빈 포트폴리오 반환"
  실제 구현:              n == 0 일 때만 빈 dict, 1~4개면 그대로 편입
  **정답을 임의로 정하지 마라.** 테스트를 xfail(strict=False) 로 두고,
  TECH_DEBT.md 에 CONTRACT-PF-001 정책 결정 항목으로 올려라.
  선택지: (a) 5종목 미만 → 현금 100% (b) 그대로 전액 투자
          (c) 부족분만 현금 (d) 차선 종목으로 보완
  실측: 189개 조합 중 5종목 미만은 1건뿐 → 결과 영향은 작을 가능성 (검증 필요)
```

## Claude Code 지시 — `tests/integration/` (합성 PostgreSQL)

DB 없는 단위 테스트로는 다음을 **절대 잡을 수 없다**: SQL의 `available_from` 조건 누락,
CFS/OFS 우선순위, `amendment_from` 처리, `DISTINCT ON` 정렬 방향, 상폐 이벤트 날짜 경계.
`load_pit_series()`는 이 로직을 SQL 내부에서 처리한다.

```
[안전 가드 — 반드시 지켜라]
  - **포트 5433(운영 DB)에 절대 접속하지 마라.** 임시 컨테이너를 5434 이상에 띄운다.
  - 세션 종료 시 컨테이너를 파기한다.
  - 실데이터 복사 금지. 전부 손으로 만든 합성 데이터.

[범위 — 폭주 방지]
  - 종목 5~10개, 리밸런싱 2~3구간.
  - 테이블은 백테스트가 **읽는** 것만 생성:
      stocks, financials_pit, price_history, market_cap_history,
      stock_listing_events, universe_gate_pit
  - 목표는 커버리지가 아니라 아래 **SQL 경계 계약 6개**다.

[I-1] available_from 경계
      available_from == rebalance_date 인 행이 포함되는가 / 제외되는가.
      의도된 계약을 먼저 문서에서 확인하고, 코드가 그대로인지 검증.
[I-2] amendment_from 경계
      정정공시가 리밸런싱일 이후에 나온 경우, 원본값(original_amount)이 쓰이는가.
      → 이게 틀리면 룩어헤드다. 최우선.
[I-3] 상장폐지일 경계
      상폐일 전일 / 당일 / 익일 각각에서 is_delisted_at() 판정.
      가격이 끊긴 것과 상폐된 것이 구분되는지 (v5.3 버그 회귀 방지).
[I-4] CFS ↔ OFS fallback
      CFS 없을 때 OFS 로 떨어지는가. 둘 다 있을 때 우선순위가 일관적인가.
[I-5] load_pit_series() 의 DISTINCT ON 정렬 방향
      의도한 "최신 1건"이 실제로 최신인지. 정렬 역전 시 실패하도록.
[I-6] fallback_used = TRUE 지연
      법정마감 + 5일이 적용되어 항상 실제 공시일보다 늦은지.

[실행 분리]
  pytest -m "not integration"   # fast suite — 모든 커밋
  pytest -m integration         # PR 게이트
  ※ "DB 없이 전부 통과"는 fast suite 의 요건이지 전체 테스트의 요건이 아니다.
```

## 게이트
- [ ] `pytest -m "not integration"` 통과
- [ ] `pytest -m integration` 통과 (또는 실패 항목이 TECH_DEBT에 P0로 등록됨)
- [ ] oracle 테스트 중 **실패하는 것**이 있다면 → 그것이 곧 P0 후보다. 고치지 말고 기록만 하고 Pass 1로 넘어가라
- [ ] `tests/oracle/`와 `tests/characterization/`가 물리적으로 분리돼 있다
