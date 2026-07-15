# AUDIT_02 — Pass 1: 읽기 전용 감사

> **모델**: Fable 5 (`/model fable`)
> **선행**: Pass 0 게이트 통과
> **절대 제약**: **코드를 한 줄도 수정하지 않는다.** 산출물은 `TECH_DEBT.md` 하나.

---

## 0. 읽는 순서 (반드시 지켜라)

```
1) CLAUDE.md            — 운영 제약만 파악. 설계 의도는 아직 읽지 마라.
2) 저장소 트리 + 실행 entrypoint 목록화
3) **코드만 보고** 실행 그래프를 직접 그린다
4) 그 다음에 MASTER.md 와 루트의 SPEC_*.md 를 glob 으로 전부 읽고
   "문서가 주장하는 계약" vs "코드가 실제로 하는 일" 을 대조한다
```

**왜:** 문서를 먼저 읽으면 "원래 그렇게 설계됐겠지"라는 전제로 코드를 해석하게 된다.
정합성 감사에서는 이 앵커링이 치명적이다.
SPEC 파일 개수를 가정하지 마라 — 실제 존재하는 파일을 glob 으로 읽어라.

---

## 1. 판정 규칙

### 중복을 예단하지 마라
```
코드가 유사하다는 이유만으로 중복 부채로 판정하지 마라.
성능·격리·배치처리·운영대상 차이로 의도적으로 분리된 것일 수 있다.

예: backtest/regime/data_access_regime.py 는 2,000종목 × ~126개월 배치 조회를 위한
    전용 구현이다. 종목별 조회 함수를 그 규모로 호출하면 성능이 무너진다.
    → 중복 부채가 아니다.

의도적 분리로 판정되면 다음 두 가지만 평가하라:
  (a) 공통 계약을 공유하는가 (상수·haircut·정렬 규칙 등)
  (b) drift 방지 테스트가 있는가
```

### 폐기 코드는 삭제 대상이 아니다
```
factor_screener.py, ablation.py 의 E_*/G_*, StabilityFilter R2/R3 는
"폐기하되 실험 기록용으로 보존"이 명시적 의도다.

판정할 것은 삭제 여부가 아니라:
  - 실행 경로에서 **완전히 격리**됐는가
  - 실수로 다시 조립될 수 있는가
    (v5.2 에서 configs/phase2_rim.py 가 F 가 아닌 G_full 로 잘못 조립된 사고가 실제로 있었다)
격리가 불완전하면 격리 방법을 제안하라.
```

### 라벨링
```
모든 문장에 [검증된 사실] / [Claude 의견] / [확실하지 않은 사실] 을 붙여라.
코드를 직접 읽고 확인한 것만 [검증된 사실] 이다.
추측을 "이럴 것이다"로 쓰지 마라. 확인 못 했으면 [확실하지 않은 사실] + 확인 방법을 적어라.
```

### 항목 포맷
```
ID: {CORR|SSOT|CONTRACT|DOC|PERF}-{모듈}-{번호}
Commit: {SHA}
Location: backtest/engine.py::_calc_period_return    ← 심볼 필수 (라인은 수정 즉시 무효)
Lines: 200-251                                        ← 보조
Expected contract:
Actual behavior:
Result impact: Y / N / Conditional / Unknown
Affected scenarios:
Evidence:
Label:
```

### 우선순위
| 등급 | 정의 |
|------|------|
| P0-A | 숫자가 틀렸음이 **재현된** 항목 |
| P0-B | **조용한 숫자 오염이 가능한 구조.** 재현 못 했어도 P0-B다 |
| P1 | 재현성·provenance·문서↔구현 불일치·정책 미결 |
| P2 | 성능·운영·실제 중복 |
| P3 | 스타일·네이밍 |

> 조회 실패 시 0을 반환하는 구조는 "아직 안 터졌다"는 이유로 P3에 넣지 마라. **P0-B다.**

---

# Pass 1A — 데이터 lineage · PIT · 상장/상폐

**범위**: `ingest/` + `backtest/data_access.py`

## Claude Code 지시

```
[A-1] 룩어헤드 가드 전수 조사
  백테스트가 읽는 모든 조회 경로에서 available_from <= rebalance_date 가
  **강제되는지**, 아니면 호출자 규율에만 의존하는지 판정하라.
  - SQL 안에 조건이 있는가, 파이썬 호출부에 있는가, 아예 없는가
  - as_of 파라미터가 optional 이거나 기본값이 있는 함수 → 전부 P0-B
  - 정정공시(amendment_from) 처리가 리밸런싱일 기준으로 원본값을 쓰는가

[A-2] 함수 계약 감사  ★ v5.3 haircut 버그의 근본 원인 유형
  이름/시그니처가 약속하는 것과 실제 반환값이 다른 함수를 전수 조사하라.
  기지 사례: get_close_price(ticker, as_of) 는 "종가"가 아니라
            "as_of 이하 최신 거래일의 종가"를 반환한다.
            상폐로 가격이 끊겨도 None 이 아니라 마지막 가격을 반환한다.
            → 이 계약이 문서화되지 않아 상폐 판정에 잘못 쓰였다.
  같은 유형이 더 있는지 data_access.py, data_access_regime.py 전체를 훑어라.
  판단 기준: "이 함수를 처음 보는 사람이 이름만 보고 오해할 수 있는가?"

[A-3] 상장/상폐 이벤트
  - is_delisted_at() 이 stock_listing_events 기준으로만 판정하는가 (가격 유무와 무관하게)
  - stock_listing_history 를 참조하는 잔존 코드가 있는가 (CLAUDE.md 상 사용 금지)
  - 티커 재사용(코드 재활용) 케이스에서 잘못된 이벤트를 집는 경로가 있는가
  - ★ 미해결 데이터 이슈: 10년+ 소형주 홀딩에서 상폐 플래그가 0건인 것이 구조적으로
    가능한지 SQL 로 확인하라. 불가능하다면 수집 경로 결함이다. (MASTER 미결 항목)

[A-4] PIT 생성 경로
  - financials_pit 의 fallback_used=TRUE 가 법정마감+5일로 일관 적용되는가
  - load_pit_series() / load_pit_series_ttm() 의 TTM 계산에서 thstrm_add_amount 처리
  - CFS/OFS 우선순위 규칙이 한 곳에 정의돼 있는가

[A-5] 실패 처리
  ingest/ 와 data_access 에서 조회·네트워크 실패 시 예외를 던지지 않고
  기본값(0, None, 빈 리스트)을 반환하는 지점을 전부 찾아라.
  금융 백테스트에서 조용한 기본값은 오염 경로다. 전부 P0-B 후보.
```

---

# Pass 1B — 파이프라인 · 포트폴리오 · 수익률 · metrics

**범위**: `backtest/` (filters, models, portfolio, engine, metrics, ablation, configs)

## Claude Code 지시

```
[B-1] 실행 그래프 작성
  코드만 보고 다음을 직접 추적해 그려라. 문서를 베끼지 마라.
    BacktestPipeline 조립 → 필터 순서 → RIMModel → build_portfolio()
    → BacktestEngine._calc_period_return() → metrics
  configs/ 의 각 config 가 실제로 어떤 필터를 조립하는지 **한 줄씩 확인**하라.
  (v5.2: phase2_rim.py 가 F 가 아닌 G_full 로 조립되고 있었다)

[B-2] 기지 항목 등급 확정  ← AUDIT_00 §5 참조
  아래 항목들의 등급을 코드 확인으로 확정하라. 재현은 Pass 2 에서 한다.

  CORR-ENGINE-001  build_portfolio() 의 weight 를 _calc_period_return() 이 소비하는가?
                   현재 `for ticker in portfolio:` 로 key 만 순회하고
                   `sum(stock_returns) / len(stock_returns)` 단순평균으로 보인다.
                   → 등가중 동안은 우연히 일치. 다음 상황에서 즉시 오류:
                     업종/KOSDAQ 비중 제한, 레짐 overlay 비등가중, 가격 결측,
                     현금 비중, 목표 미달 종목 수
                   MAX_STOCK_WEIGHT 폐지로 끝난 문제가 아니라 **남아 있는 인터페이스 계약 부채**다.

  CORR-ENGINE-002  상폐 opt/cons 조정의 n 계산이 종목 순회 순서에 의존하는가?
                   n = len(portfolio) 로 시작 → 가격 결측 시 n -= 1 → 상폐 종목에서 1/n 사용.
                   가격결측 종목이 상폐 종목보다 앞/뒤 어디에 있느냐로 값이 달라지는지 확인.
                   ★ 순회 순서 = RIM 상승여력 정렬 순서다.
                     정렬 tie-break 규칙을 바꾸면 **편입 종목이 완전히 동일해도** 숫자가 바뀐다.
                     → "정렬 안정성"을 별도 항목으로 등록하라.

  CORR-METRIC-001  turnover = sold / max(len(prev), len(curr), 1)
                   ★ 반드시 확인: 이 값이 **거래비용 계산에 입력되는가?**
                     입력됨   → 모든 시나리오 net 수익률 오류 → P0-A
                     리포트전용 → P1
                   또한 prev 는 있는데 curr 가 비면 0 을 반환하는 경로도 확인.

  CORR-METRIC-002  CAGR 이 실제 캘린더일수가 아니라 구간수÷2 로 연수 계산.
                   metrics.py 단일 정의인가, 복제본이 있는가.

  CORR-ENGINE-003  마지막 리밸런싱 구간 종료일이 date.today() 로 결정되는가.
                   → 같은 코드·같은 DB 인데 실행 날짜가 다르면 결과가 달라진다.
                   해법 제안: engine.run(rebalance_dates, valuation_date=date(...)) 로 주입.
                   ★ CORR-METRIC-002 와 결합돼 있다. 함께 고치지 않으면 결과 변동을 두 번 겪는다.

  CORR-BENCH-001   KOSPI/KOSDAQ 조회 실패 시 경고만 남기고 0.0 을 반환하는가.
                   → 네트워크 장애가 "정상 수익률 0%"로 들어가 alpha·robustness·benchmark CAGR 오염.
                   → 백테스트는 성공 상태로 끝난다. 아무도 모른다.
                   해법 제안: BenchmarkDataUnavailable 예외, 또는 allow_missing_benchmark=False 기본값.

  CONTRACT-PF-001  portfolio.py docstring("후보 5개 미만 → 빈 포트폴리오") vs
                   구현(n==0 일 때만 빈 dict). 명백한 계약 불일치.
                   **정답을 임의로 정하지 마라. 정책 결정 항목으로 분류하라.**

[B-3] SSOT 감사
  다음 상수/규칙이 각각 어디서 정의되고 어디서 소비되는지 매핑하라:
    RF, RK, OMEGA, VB_CAP        (backtest/configs/constants.py)
    DELISTING_HAIRCUT            (backtest/engine.py)
    regime 파라미터              (backtest/regime/config_regime.py)
    rebalance_dates              (하드코딩)
    거래비용 파라미터
  복제된 정의, import 하지 않고 재선언한 값, 문서에만 있고 코드에 없는 값을 전부 찾아라.
  시나리오 정의가 ablation.py 와 configs/ 두 곳에 있어 어긋날 수 있는 구조인지 판정하라.

[B-4] 격리 검증 (삭제 아님)
  factor_screener.py, E_*/G_* 시나리오, StabilityFilter R2/R3 가
  CANONICAL 실행 경로에서 완전히 격리됐는지, 실수로 재조립될 수 있는지 판정하라.

[B-5] 문서 ↔ 코드 대조
  이제서야 MASTER.md 와 SPEC_*.md 를 읽는다.
  B-1 에서 코드로 그린 실행 그래프와 문서가 주장하는 파이프라인을 대조하라.
  불일치는 전부 항목으로 등록하라. 어느 쪽이 맞는지는 판정하지 말고 둘 다 기록하라.
  (예: ablation.py docstring "7개 시나리오" vs 실제 정의 개수)
```

## 게이트
- [ ] `TECH_DEBT.md` 에 P0-A / P0-B 목록이 확정됐다
- [ ] 모든 항목에 심볼 위치와 commit SHA가 있다
- [ ] 모든 문장에 라벨이 붙어 있다
- [ ] "중복"으로 분류된 항목마다 의도적 분리 여부를 검토한 흔적이 있다
- [ ] 프로덕션 코드가 수정되지 않았다 (`git status` 로 확인)
