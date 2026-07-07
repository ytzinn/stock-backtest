# SPEC_07 — 레짐 진단 유닛 (Phase A: Regime Dashboard)

> **문서 성격**: Claude Code 자율 구현용 핸드오프 스펙
> **버전**: v0.3 (구현 전 코드베이스 대조 검토 반영)
> **선행 문서**: MASTER.md, SPEC_01_infra.md, SPEC_04_models.md, SPEC_05_backtest.md, SPEC_06_phases.md
> **대상 저장소**: `stock-backtest` (독립 저장소, 서버 경로 `/opt/stock-backtest/`, 백테스트 DB `localhost:5433`)
> **라벨링 관례**: `[검증된 사실]` = 코드/데이터로 확인됨 · `[Claude 의견]` = 설계 판단 · `[확실하지 않은 사실]` = 코드베이스에서 Claude Code가 검증 필요

---

## 개정 이력 (v0.2 → v0.3)

구현 착수 전 §0 `[VERIFY]` 항목을 실제 코드베이스와 대조한 결과 반영:

1. **[중대] 홀딩스 소스에 상폐 haircut 버그 발견** — `scripts/export_portfolios.py:65-69`가 `d2d619e`에서 `engine.py`에 고친 것과 **동일한 버그 패턴**(`get_close_price() is None`을 상폐 트리거로 사용 → 도달 불가능)을 그대로 가지고 있음. 저장된 홀딩스 JSON의 상폐 종목 청산가가 haircut 미적용 상태일 가능성이 높음. **STEP A-0**으로 이 스크립트를 먼저 고치고 재실행하는 단계를 신규 추가 (§7-2, §8)
2. 홀딩스 저장 위치를 xlsx(9시트) → **`experiments/ablation/{tag}_holdings.json`**로 정정. weight 필드 없음 — `n_portfolio`로 나눈 동일가중(1/n) 암묵 (§2, §7-2)
3. equity PIT 조회 키를 영문(`EquityAttributableToOwnersOfParent`) → 실제 한글 키(`지배기업소유주지분` > `지배기업소유주지분_1` > `자본총계`)로 정정 (§2)
4. **#23(2026-04-03~, 진행 중인 반기)** 을 대시보드에는 참고 표시하되 **G1~G4 게이트 판정에서는 제외**함을 명시. 근거: 이 구간은 `date.today()` 기준 열린 stub이라 재실행 시마다 값이 바뀌고, h=6 리드랙에 필요한 관측치가 아직 없음 (§9)
5. 대형주 sleeve(§5-1) 유니버스에 `universe_gate_pit` PASS 조건을 **적용하지 않음**을 명시 확정 (§4-1 그대로: 금융제외+상장+데이터존재만). RIM 전략의 투자가능판정과 섞이면 벤치마크가 왜곡되므로

---

## 개정 이력 (v0.1 → v0.2)

외부 리뷰를 항목별로 검토(채택 9 / 부분채택 2 / 반려 0)해 반영:
1. 저장소명 `korean-stock-backtest` → **`stock-backtest`** (MASTER.md·SPEC_01 확인, v0.1은 기억 오류)
2. 월별 MTM을 **초기 수량 고정 NAV 방식**으로 명확화 (§7-2)
3. 반기 복제 게이트에 **구간 종료 stub 포함** + 구간 컬럼 추가 (§6·§8)
4. 신규 테이블에 **run_id / config_hash** 추가 (민감도 run 덮어쓰기 방지, §6)
5. **statsmodels 의존성** 명시 (HAC 회귀, §7-4·§11)
6. 대형주 sleeve **EW/CW 분리 + CW를 주 벤치마크로 승격** + `mega_cap_concentration` 보조 지표 (§4·§5-1)
7. `size_mom_6m` **formation 시점(t−6M) 고정** (§4-3)
8. Phase B 게이트 **수치화 + #22·#23 제외 강건성**; 단 **HAC t값은 구속조건이 아닌 참고조건으로 격하** (§9)
9. FactorScreener 폐기 반영 **PRIMARY / ARCHIVE 시나리오 분리** (§7-1·§9)
10. 리포트 `.md`는 **git 커밋(옵션 B)**, 대용량 플롯/CSV만 ignore (§7-5)
11. **테스트 파일** 추가 (§11)

---

## 한눈에 보기 (TL;DR)

### 배경 — 왜 이걸 하는가
[검증된 사실] 소형 가치주 전략(RIM 기반)이 **특정 국면에서만** 강한 알파를 내고, 최근 두 구간(#22·#23)에서는 KOSPI가 대형 반도체주 중심으로 급등하며 상대 알파가 음수가 됐다. 이건 전략이 망가진 게 아니라 **"소형 value가 유리한 국면 vs 대형·성장이 압도하는 국면"이 따로 있다**는 신호다.

### 목적 — 무엇을 알아내려는가
[Claude 의견] "소형 value 적합 국면을 **미리 판단**할 수 있는가?"를 확인하는 것. 단, 곧바로 비중 조절 모델을 붙이지 않는다. 검증 없이 레이어를 얹으면 FactorScreener 폐기(2026-07-05)를 반복할 위험이 있어서다. 그래서 **Phase A는 "판단 가능한지 측정만" 한다.** 실제 비중 조절(tilt)은 이 측정이 통과한 뒤 Phase B에서 한다.

### 달성 방법 — 어떻게 확인하는가
1. **넓게 측정, 좁게 행동**: Phase A에선 인하우스 지표를 다 계산해 뭐가 작동하는지 데이터로 고르고, Phase B에선 통과한 최소 신호(1~2개)로만 움직인다.
2. **표본 늘리기 (핵심 트릭)**: 반기 21개 관측치는 너무 적다. 전략은 그대로 두고 **기존 보유 바스켓을 월말마다 재평가(MTM)**해 관측치를 ~126개로 늘린다. (초기 수량 고정 NAV 방식 — §7-2)
3. **관계 확인**: 각 지표가 **상대수익(소형 value − 대형주 시총가중)**과 관계있는지 그려보고 상관·리드랙으로 본다. 최종 판정은 자기상관 착시를 피하려 **21개 반기 앵커에서 교차확인**하고, **#22·#23을 빼도 유지되는지** 확인한다.
4. **신규 데이터 불필요**: 지표 전부 기존 DB로 계산된다. 신용 스프레드·수출(외부 API)은 필요성이 입증되기 전까지 확보하지 않는다.
5. **선등록 게이트로 판정**: 결과를 보기 전에 통과 조건(수치 기준 포함)을 고정한다. 통과하면 Phase B(비중 조절), 실패하면 분류기를 접고 "대형주 병행 분산"으로 선회.

### 지표 (전부 인하우스)
| family | 지표 | 한 줄 의미 |
|---|---|---|
| 저평가 분산 | `value_spread` (**1순위**) | 성장주 대비 가치주가 얼마나 싼가 |
| 저평가 분산 | `size_val_gap` | 소형이 대형 대비 얼마나 싼가 |
| 저평가 분산 | `illiq_discount` | 저유동성주 할인 폭 |
| 로테이션 | `size_mom_6m` (**필수 검증**) | 최근 돈이 소형↔대형 어디로 쏠렸나 (#22·#23의 직접 원인) |
| 시장 상태 | `breadth_ma200` | 시장 상승 폭이 넓은가 좁은가 |
| 보조(해석용) | `mega_cap_concentration` | 상위 소수 종목 시총 쏠림 정도 (Phase B 행동 신호 아님) |

> `[Claude 의견]` 한 문장 요약: **"기존 데이터만으로, 전략을 건드리지 않고, 소형 value 적합 국면이 측정 가능한지부터 진단한다. 비중 조절은 그다음이다."**

---

## 0. 이 문서를 읽는 Claude Code에게 (최우선 지시)

이 스펙에는 내가 실제 코드를 다 열어보지 못해 **가정한 부분**이 있다. 아래 표시가 붙은 항목은 **구현 전에 실제 DB/코드에서 확인**하고, 스펙과 다르면 실제 코드를 따르되 그 사실을 커밋 메시지와 리포트에 남겨라.

- `[VERIFY]` — 실제 테이블 컬럼명·홀딩스 저장 위치 등을 확인해야 하는 지점
- `[ASSUMPTION]` — 내가 가정한 설계값. 근거가 있으면 유지, 데이터가 반박하면 수정

**절대 규칙 (Phase A 불변식):**
1. 종목 선별 로직·리밸런싱 시점·기존 백테스트 산출물을 **일절 수정하지 않는다.** Phase A는 읽기 전용 진단이다.
2. **Look-ahead 금지.** CLAUDE.md 규칙대로 모든 데이터 조회는 **`available_from <= 기준일`** 조건을 건다. 특히 재무(equity)는 PIT 원칙(SPEC_02) 준수.
3. 신규 코드는 전부 `backtest/regime/` 하위에 격리한다. 기존 모듈을 import는 하되 **수정 금지**.
4. 배포는 CLAUDE.md의 git 워크플로우(commit → push → 서버 pull) 준수. **scp 직접 배포 금지.**

---

## 1. 목적과 범위

### 1-1. 배경 (합의된 문제 정의)

[검증된 사실] 최근 두 구간(#22 2025-08-20~, #23 2026-04-03~)에서 KOSPI가 대형·성장주(반도체 메가캡) 중심으로 급등하며 소형 가치주 전략의 상대 알파를 음수로 끌어내렸다. 이는 전략 붕괴가 아니라 **벤치마크 편중/스타일 로테이션** 문제다 (2026 KOSPI 시총 상위 4종목 49.49% 비중).

### 1-2. 핵심 설계 철학: "넓게 측정, 좁게 행동"

[Claude 의견] Phase A(진단)와 Phase B(행동)의 층을 분리하는 것이 이 설계의 핵심이다. 이는 "다입력 확률 분류기를 21개 표본에 학습"시키는 것과 **정반대**다. 학습 모델에 전부 넣는 게 아니라, **하나씩 따로 관계를 보고** Phase B에서 쓸 최소 신호를 고른다.

### 1-3. Phase A 범위

**한다:** 월별 MTM 재평가(전략 불변) · 인하우스 지표 계산 · 관계 분석 · 대시보드/리포트 · Phase B 게이트 판정
**안 한다:** 선별/리밸런싱/비중 로직 변경 · tilt 실행(관측만) · 학습 분류기 · 외부 API 확보(Tier 2, §3-2)

---

## 2. 전제 조건 (기존 자산)

[검증된 사실] 백테스트 DB(`localhost:5433`, DB명 `backtest`)와 `stock-backtest` 저장소에서 확인된 자산:

| 자산 | 용도 | 이 스펙에서의 활용 |
|------|------|------------------|
| `price_history` | 일별 OHLCV + `adj_close` + `is_suspended` | MTM, breadth, 유동성, 모멘텀 |
| `market_cap_history` | 시총(+`shares`); 상폐분 `supplement_delisted` 보완 | PBR, 사이즈 decile, 대형주 sleeve, 쏠림 |
| `financials_pit` | PIT 재무(`fallback_used`, `amendment_from`); `available_from` | PBR 분모(book equity) |
| `universe_gate_pit` | 시점별 유니버스 통과 판정 | 지표 계산 유니버스 |
| `stocks` | 종목 마스터(상장+상폐, `is_financial`) | 금융업 제외 |
| `stock_listing_events` | 상장/상폐 이벤트 | 기준일 상장 여부 판단 |
| `backtest/ablation.py` | 13개 시나리오 정의 | 시나리오 태그 소스 |
| `scripts/export_portfolios.py` | 기간별 편입 종목·가격 추출 | **홀딩스 소스 후보** |

`[VERIFY]` — v0.3에서 확인 완료:
- **홀딩스 저장 위치**: `experiments/ablation/{tag}_holdings.json` (`scripts/export_portfolios.py`가 생성, DB 테이블 아님). 구조: 구간별 `{rebalance_date, next_date, n_portfolio, holdings:[{ticker, name, entry, exit, ret, delisted}]}`. **weight 필드 없음** — 종목당 가중치는 `1/n_portfolio` (동일가중, `backtest/portfolio.py:build_portfolio` 확인).
- `financials_pit` equity 조회: 실제 키는 한글 — `지배기업소유주지분` → 없으면 `지배기업소유주지분_1` → 없으면 `자본총계` (`backtest/models/rim.py:48-50` 확인). `_숫자` suffix는 당기/전기 구분이 아니라 DART 응답 이름 충돌 fallback (CLAUDE.md 참조).
- `SCENARIOS` 태그: `backtest/ablation.py` 확인 결과 `D_rim_only`, `F_momentum_rim`(PRIMARY), `E_screener_rim`, `G_full`, `H_no_stability`(ARCHIVE) 전부 정의됨 — 스펙과 일치.

`[중대 — VERIFY 과정에서 신규 발견]` **`scripts/export_portfolios.py`에 상폐 haircut 버그**:
```python
# export_portfolios.py:65-69 (현재)
delisted = False
if exit_ is None:                          # ← get_close_price()는 date<=as_of 최신값을
    last = _last_known_price(conn, ticker, next_date)   #   반환하므로 상폐 후에도 거의 None이 안 됨
    exit_ = last * DELISTING_HAIRCUT if last else None  #   → 이 분기 사실상 도달 불가능
    delisted = True
```
`d2d619e`(2026-07-05)에서 `backtest/engine.py`의 동일 패턴을 `is_delisted_at()` 명시 판정으로 고쳤으나, 이 스크립트는 미수정 상태로 남아있음. 즉 **저장된 홀딩스 JSON의 상폐 종목 청산가가 haircut 미적용**일 가능성이 높다. 이 JSON을 MTM의 entry price·티커 목록 입력으로 쓰기 전에 **반드시 STEP A-0에서 이 스크립트를 고치고 재실행**한다 (§7-2, §8).

[검증된 사실] 21개 유효 리밸런싱 시작일:
```
2016-04-05, 2016-08-18, 2017-04-05, 2017-08-18, 2018-04-04, 2018-08-20,
2019-04-03, 2019-08-20, 2020-04-03, 2020-08-20, 2021-04-05, 2021-08-19,
2022-04-05, 2022-08-18, 2023-04-05, 2023-08-18, 2024-04-03, 2024-08-20,
2025-04-03, 2025-08-20, 2026-04-03
```
(2015-04-03·2015-08-19는 TTM 미충족으로 빈 구간 → 제외. #22=2025-08-20, #23=2026-04-03)

[검증된 사실] KOSPI 벤치마크는 `fdr.DataReader('KS11')` (기존 코드 관례).

---

## 3. 데이터 인벤토리

### 3-1. Tier 1 (Phase A — 전부 인하우스, 신규 API 불필요)

| 지표 | family | 계산 소스 |
|------|--------|----------|
| `value_spread` | 저평가 분산 | `market_cap_history` + `financials_pit` |
| `size_val_gap` | 저평가 분산 | `market_cap_history` + `financials_pit` |
| `illiq_discount` | 저평가 분산 | `price_history` + PBR |
| `size_mom_6m` | 로테이션 | `market_cap_history` + `price_history` |
| `breadth_ma200` | 시장 상태 | `price_history` |
| `mega_cap_concentration` | 보조(해석) | `market_cap_history` |

### 3-2. Tier 2 (Phase A에서 제외 — 조건부 보류)

[Claude 의견] 신용 스프레드(ECOS)·수출/반도체(관세청)는 가장 회의적인 '시장 상태' family에 속하고, 그 family는 `breadth_ma200`으로 이미 대표 진단이 된다. 확보 비용(키 발급·시차 처리)이 0이 아니고 한계효용이 미입증이므로 **필요성이 데이터로 확인된 뒤에** 확보한다. 재검토 트리거: "신용 스프레드가 breadth가 못 보는 **선행(leading)** 정보를 추가하는가"를 검증할 필요가 생겼을 때.

---

## 4. 지표 정의 (family 구조 + 산식)

모든 지표는 **월말(month-end) 스냅샷**으로 계산한다.

### 4-1. 공통 유니버스
`[ASSUMPTION]` 각 월말 t의 유니버스 = 상장 중(`stock_listing_events`) · 금융 제외(`stocks.is_financial=false`) · `market_cap_history`·`financials_pit`(available_from ≤ t) 모두 존재. 상폐 종목의 보완 시총(`supplement_delisted`) 포함. 결측 제외 비율은 `dropped_pct`로 매 시점 기록.

### 4-2. PBR 계산 (저평가 family 공통 분모)
```
PBR(ticker, t) = market_cap(ticker, t) / book_equity_pit(ticker, t)
  book_equity_pit = t 시점 available_from 이하 최신 지배기업소유주지분
                    (없으면 자본총계 fallback — RIM과 동일 규칙)
```
- 재무는 반기 갱신 → 보고서 사이 구간은 직전 값 유지. PBR ≤ 0 또는 equity ≤ 0 제외.

### 4-3. 지표별 산식

| 지표 | 산식 | 값↑ 해석 |
|------|------|---------|
| `value_spread` | `log( median(PBR of Q5) / median(PBR of Q1) )`. Q1=저PBR(가치), Q5=고PBR(성장) | 성장 대비 가치 저평가 폭 확대 → value 유리 |
| `size_val_gap` | `log( median(PBR of 대형 decile) / median(PBR of 소형 decile) )` (시총 decile) | 소형이 대형 대비 쌈 |
| `illiq_discount` | `log( median(PBR of 고유동성 Q5) / median(PBR of 저유동성 Q1) )`. 유동성=직전 20영업일 평균 거래대금(close×volume) | 저유동성주 할인 심화 |
| `size_mom_6m` | **t−6M 시점에 형성한** 소형/대형 decile 바스켓의 **t−6M→t 동일가중 수익 차** (소형 − 대형) | 최근 소형 로테이션 진행 |
| `breadth_ma200` | 유니버스 중 `adj_close(t) > MA200(t)` 종목 비율 | 시장 폭 확대(risk-on) |
| `mega_cap_concentration` | 전체 유니버스 시총 대비 상위 10개 종목 시총 합 비중 | 초대형 쏠림 심화 |

**리뷰 반영 (7번) — `size_mom_6m` formation 고정** [검증된 사실]: decile을 t 현재 시총으로 잡고 과거 6M 수익을 재면 "최근 오른 종목이 대형 decile로 들어가는" 기계적 왜곡이 생긴다. 따라서 **t−6M 시점의 시총으로 bucket을 고정**한 뒤 그 바스켓의 t−6M→t 수익을 측정한다(형성·측정 모두 t 이하 정보 → 룩어헤드 없음). t−1M formation은 §8 민감도로만 확인.

`[ASSUMPTION]` 분위수(5분위/decile)·룩백(6M, 200일, 20일)·상위 N(10)은 초기값. §8에서 흔든다.

---

## 5. 진단 표본 확장 — 월별 MTM

[Claude 의견] 21개 반기 관측치로는 관계를 볼 수 없다. 핵심 장치는 **기존 반기 포트폴리오를 월말마다 재평가**해 표본을 ~126개(2016-04~2026-06)로 늘리는 것이다. 반기 구간 내 보유 종목·수량은 고정 → **전략은 변하지 않는다.**

`[검증된 사실 수준의 주의]` 한 반기 6개 월수익은 같은 바스켓이라 **자기상관**이 있다. 유의성은 **Newey-West(HAC)** 로 보정하고, 최종 판정은 **21개 반기 앵커에서 교차확인**한다. 월별=관계의 모양, 반기=독립성 확인.

### 5-1. 대형주 sleeve — EW / CW 분리 (리뷰 6번)

[Claude 의견] `[ASSUMPTION]` 각 리밸런싱 날짜에 유니버스(금융 제외) 시총 상위 decile을 sleeve로 구성, 반기 보유, 월별 MTM. **`universe_gate_pit` PASS 조건은 적용하지 않는다** (v0.3 확정) — 그 게이트는 RIM 전략의 투자가능판정(유동성 등)이라 벤치마크에 섞으면 "우리가 걸러낸 유니버스 안에서의 대형주"가 되어 정직한 시장 벤치마크가 아니게 된다. §4-1 공통 유니버스(금융제외+상장+데이터존재)와 동일 기준으로 통일. **두 가지 가중으로 분리**한다:
- `largecap_cw_return` (**주 벤치마크**): 시총가중. 실제로 우리를 이긴 것이 시총가중 지수 내 초대형 쏠림이므로 이게 더 정직한 상대 기준.
- `largecap_ew_return` (보조): 동일가중. "대형주 스타일" 자체.

따라서 **핵심 종속변수 `rel_vs_large = port_return − largecap_cw_return`**. `rel_vs_large_ew`, `rel_vs_kospi`는 보조.

> [Claude 의견] 동일가중 top decile만 쓰면 **정작 문제의 초대형 쏠림을 희석**한다. CW를 주 기준으로 두는 이유.

---

## 6. DB 스키마 (신규 2개 테이블 — 리뷰 3·4번 반영)

```sql
-- backtest/regime/schema_regime.sql
-- 기존 스키마 불변. 진단 전용 테이블만 추가.
-- 리뷰 4번: run_id/config_hash로 민감도 run이 base를 덮어쓰지 않게 함.

CREATE TABLE IF NOT EXISTS regime_indicators (
    run_id       TEXT,               -- 지표 계산 run 식별자
    config_hash  TEXT,               -- config_regime.py 파라미터 해시 (민감도 구분)
    date         DATE,
    indicator    TEXT,               -- value_spread | size_val_gap | illiq_discount
                                     --  | size_mom_6m | breadth_ma200 | mega_cap_concentration
    value        DOUBLE PRECISION,
    universe_n   INTEGER,
    dropped_pct  DOUBLE PRECISION,   -- 결측 제외 비율 (생존편향 로그)
    PRIMARY KEY (run_id, date, indicator)
);

CREATE TABLE IF NOT EXISTS strategy_returns_monthly (
    source_run_id     TEXT,          -- MTM run 식별자
    holdings_source   TEXT,          -- 홀딩스 출처(xlsx 경로 or DB 테이블)
    delisting_scenario TEXT,         -- 'base_70pct' 등 (기존 백테스트 청산 가정)
    largecap_rule     TEXT,          -- 'top_decile' 등
    scenario          TEXT,          -- D_rim_only 등
    -- 리뷰 3번: 구간 정보로 stub 포함 여부를 명확히
    period_start      DATE,
    period_end        DATE,          -- 실제 next_rebalance_date (월말 아님)
    return_start      DATE,          -- 이번 관측치 수익 시작
    return_end        DATE,          -- 이번 관측치 수익 종료 (월말 또는 period_end stub)
    date              DATE,          -- return_end 와 동일(정렬 편의)
    port_return       DOUBLE PRECISION,
    largecap_cw_return DOUBLE PRECISION,   -- 주 벤치마크
    largecap_ew_return DOUBLE PRECISION,
    kospi_return      DOUBLE PRECISION,
    rel_vs_large      DOUBLE PRECISION,     -- port - largecap_cw (핵심)
    rel_vs_large_ew   DOUBLE PRECISION,
    rel_vs_kospi      DOUBLE PRECISION,
    n_holdings        INTEGER,
    PRIMARY KEY (source_run_id, scenario, date)
);
```

> `[Claude 의견]` `macro_series`(Tier 2)는 Phase A에서 **의도적으로 제외**. Tier 2 진입 시 별도 마이그레이션.

---

## 7. 파일별 작업 (신규 모듈)

전부 `backtest/regime/` 하위. 기존 모듈 수정 금지.

```
backtest/regime/
├── __init__.py
├── schema_regime.sql          # §6 DDL
├── schema_regime.py           # (v0.3 추가) DDL 실행기 — psql 없음(CLAUDE.md), psycopg2로 실행
├── config_regime.py           # 파라미터 단일 소스 (config_hash 산출)
├── data_access_regime.py      # (v0.3 추가) 유니버스/시총/book equity/유동성 배치 조회 헬퍼
├── mtm_monthly.py             # STEP A-2: 월별 재평가 (고정수량 NAV)
├── indicators_inhouse.py      # STEP A-3: 지표
├── analyze.py                 # STEP A-5: 관계 분석 (HAC)
└── dashboard.py               # STEP A-6: 플롯 + 리포트
tests/regime/                  # §11 — 구현 완료(2026-07-07), 25개 테스트 로컬 통과
```

구현 완료(2026-07-07): 위 전체 파일 작성, 로컬 py_compile + pytest(25개, DB 미접속 환경에서
가능한 범위 — 순수 로직·SQL 가드 문구·monkeypatch 기반 오케스트레이션) 전부 통과. 실제 DB
대조(STEP A-1~A-7 실행)는 서버 배포 후 진행.

### 7-1. `config_regime.py`

```python
"""레짐 진단 파라미터 단일 소스. §8 민감도에서 이 값만 흔든다.
config_hash = 이 파라미터 집합의 해시 → regime_indicators.config_hash 로 기록."""
PBR_QUANTILES     = 5
SIZE_DECILES      = 10
LIQ_QUANTILES     = 5
MOM_LOOKBACK_M    = 6       # size_mom_6m
MOM_FORMATION     = "t_minus_6m"   # 리뷰 7번: bucket 형성 시점. 기본 t-6M
BREADTH_MA_DAYS   = 200
LIQ_LOOKBACK_D    = 20
MEGACAP_TOP_N     = 10
MONTH_END_RULE    = "last_trading_day"

# 리뷰 9번: FactorScreener 폐기 반영 — Phase B 판단은 PRIMARY 기준으로만
PRIMARY_SCENARIOS = ["D_rim_only", "F_momentum_rim"]
ARCHIVE_SCENARIOS = ["E_screener_rim", "G_full", "H_no_stability"]  # 분석은 하되 판단 제외
# [VERIFY] 실제 ablation.py 시나리오 태그와 일치 확인
```

### 7-2. `mtm_monthly.py` (핵심 — 리뷰 2·3번)

```python
"""
기존 반기 홀딩스를 월말마다 재평가(MTM). 전략 불변, 읽기 전용.

★ 리뷰 2번 — 반드시 '초기 수량 고정 NAV' 방식.
   매월 목표비중으로 되돌리면(월별 리밸런싱) '전략 불변'이 깨진다.

로직:
  for scenario in PRIMARY_SCENARIOS + ARCHIVE_SCENARIOS:
    for period in 21_periods:
        holdings = load_holdings(scenario, period)     # {ticker: weight} at rebalance
        # 초기 매수 수량 고정 (NAV0 = 1.0 정규화)
        p0 = price_at(holdings.keys(), period.rebalance_date)   # available_from 준수
        shares = {t: holdings[t] / p0[t] for t in holdings}     # 고정
        obs_dates = month_end_dates(period.rebalance_date, period.next_rebalance_date)
        # ★ 리뷰 3번: 마지막 관측치는 반드시 next_rebalance_date(stub) 포함
        obs_dates = ensure_last(obs_dates, period.next_rebalance_date)
        nav_prev = 1.0
        for d in obs_dates:
            px = price_at(shares.keys(), d)            # 결측 carry-forward
            nav = sum(shares[t] * px[t] for t in shares)
            port_ret = nav / nav_prev - 1
            large_cw = largecap_sleeve_return(period, prev_d, d, weight='cw')
            large_ew = largecap_sleeve_return(period, prev_d, d, weight='ew')
            kospi    = kospi_return('KS11', prev_d, d)
            upsert(strategy_returns_monthly, ...,
                   period_start=period.rebalance_date,
                   period_end=period.next_rebalance_date,
                   return_start=prev_d, return_end=d, date=d,
                   port_return=port_ret,
                   largecap_cw_return=large_cw, largecap_ew_return=large_ew,
                   kospi_return=kospi,
                   rel_vs_large=port_ret - large_cw,
                   rel_vs_large_ew=port_ret - large_ew,
                   rel_vs_kospi=port_ret - kospi,
                   n_holdings=len(shares))
            nav_prev = nav; prev_d = d

결측/거래정지/상폐 처리 [ASSUMPTION]:
  - is_suspended/가격 결측: 직전 유효 adj_close carry-forward.
  - 구간 내 상폐: 기존 백테스트 기준 시나리오(종가×70%)와 동일 청산 가정. 리포트 명시.

★ 상폐 판정 필수 규칙 (v0.3 추가 — engine.py d2d619e와 동일 함정 회피):
  가격이 있는지 없는지(get_close_price is None)가 아니라 **is_delisted_at(conn, ticker, d)**
  로 명시 판정한다. get_close_price()는 date<=as_of 최신값을 반환해 상폐 후에도 절대
  None이 되지 않으므로, "가격 없으면 상폐"식 분기는 도달 불가능한 코드가 된다.
  매 월말 d에서 티커별로:
    if is_delisted_at(conn, ticker, d):
        px = _last_known_price(conn, ticker, d) * DELISTING_HAIRCUT   # 최초 상폐월 1회만 적용
        (이후 obs_date는 shares를 0으로 처리하거나 루프에서 제외 — 반복 haircut 금지)
    else:
        px = get_close_price(conn, ticker, d)   # 정상 carry-forward
  홀딩스 JSON(entry/exit/delisted 필드)은 STEP A-0 수정·재실행 후에도 **참고용으로만** 쓰고,
  월별 경로상 가격 판정은 위 로직으로 mtm_monthly.py가 자체 계산한다(이중 안전장치).
"""
```

**★ A-2 복제 게이트 (리뷰 3번 강화)** [검증된 사실 수준의 근거]:
고정 수량·동일 초기가중이면 `NAV_T/NAV_0 = Σ(1/n)(p_iT/p_i0)` = 개별 총수익 평균 → **기존 엔진의 반기 수익률과 정확히 일치**한다(월별 리밸런싱 방식은 불일치). 따라서 검증 = **stub 포함 월수익 누적곱이 기존 반기 수익률과 일치**(부동소수 오차 내). 불일치 시 MTM 로직 결함 → 진행 중단.

### 7-3. `indicators_inhouse.py`

```python
"""
지표를 월말마다 계산 → regime_indicators upsert (run_id, config_hash 포함).
look-ahead 금지: available_from <= t 데이터만.

  pbr_cross_section(t): market_cap_history + financials_pit(PIT equity) → PBR, 금융제외, PBR<=0 제외, dropped_pct
  value_spread(t):   log(median(pbr|Q5)/median(pbr|Q1))
  size_val_gap(t):   log(median(pbr|대형decile)/median(pbr|소형decile))
  illiq_discount(t): log(median(pbr|고유동Q5)/median(pbr|저유동Q1)), 유동성=20d 평균 거래대금
  size_mom_6m(t):    form buckets at t-6M (MOM_FORMATION) → ew_return(소형,t-6M→t) - ew_return(대형,t-6M→t)
  breadth_ma200(t):  mean(adj_close(t) > MA200(t))
  mega_cap_concentration(t): sum(top-10 시총) / sum(전체 유니버스 시총)
"""
```

### 7-4. `analyze.py` (리뷰 5·8번)

```python
"""
regime_indicators × strategy_returns_monthly 병합 → 관계 분석.
의존성: statsmodels (HAC). requirements.txt에 statsmodels>=0.14.2 추가 (§11).

산출:
  1) 리드-랙: indicator_t vs rel_vs_large_{t+h}, h∈{1,3,6}
     - OLS + Newey-West(HAC, maxlags≈6) → 계수 부호 + t값(참고)
     - Spearman rank corr (참고)
  2) 21개 반기 앵커 교차확인 + ★#22·#23 제외 재실행(리뷰 8번)
  3) 핫구간 태깅: 21구간을 rel_vs_large로 정렬, 상위 quartile='hot'
     → hot vs cold 에서 각 지표 중앙값 비교
  4) 저평가 family 대표성: value_spread vs (size_val_gap, illiq_discount) 상관행렬
  판단은 PRIMARY_SCENARIOS 기준. ARCHIVE는 참고 표로만.
"""
```

### 7-5. `dashboard.py` (리뷰 10번)

```python
"""
플롯 + 마크다운 리포트.
플롯(지표별): 상단=지표+누적 rel_vs_large 이중축 / 하단=산점도 indicator_t vs rel_vs_large_{t+1} + 회귀선
리포트: experiments/runs/YYYY.MM.DD._REGIME_PHASE_A.md
  - §9 게이트 판정 표 포함, 라벨링 관례 사용

★ 리뷰 10번 — .gitignore 옵션 B:
  - 리포트 .md 는 git 커밋(기존 experiments/runs/*.md 관례와 동일)
  - 대용량 플롯 PNG / CSV 만 ignore. [VERIFY] .gitignore에 아래 추가 확인:
      experiments/runs/*.png
      experiments/runs/*.csv
"""
```

---

## 8. 실행 STEP 시퀀스 + 검증

기존 SSH/DB 패턴 준수, **포트 5433**, 배포는 git 워크플로우.

```
[STEP A-0] ✅ 완료 (커밋 48a9adc, 2026-07-07) — export_portfolios.py 상폐 haircut 버그 수정
  변경:  scripts/export_portfolios.py, get_close_price() is None 트리거를
         is_delisted_at()(engine.py d2d619e와 동일 패턴) 명시 판정으로 교체 완료
  검증:  2026.07.07. portfolio_holdings.xlsx(D_rim_only)에서 구간 내 실제 상폐 2건
         (066110: 69→48, -30.00%=정확히 0.70 haircut / 001880: 11650→10045, -13.78%=
         상폐직전가 14350×0.70과 일치) 확인. 나머지 5종목은 보유구간 이후 상폐라
         해당 없음(정상). RIM 유효성 판정 재역전(D 11.99%→11.66%)과도 정합.
  → mtm_monthly.py는 이 정정된 JSON을 참고용 entry price로 사용 가능하나,
    §7-2의 자체 is_delisted_at() 이중 판정 로직은 그대로 유지한다(안전장치).

[STEP A-1] 스키마 생성
  실행:  python -m backtest.regime.schema_regime  (또는 psql -f)
  기대:  regime_indicators, strategy_returns_monthly 생성

[STEP A-2] 월별 MTM (고정수량 NAV)
  실행:  python -m backtest.regime.mtm_monthly
  게이트: ★ stub 포함 월수익 누적곱 == 기존 반기 수익률 (오차 내). 불일치 시 중단.

[STEP A-3] 인하우스 지표
  실행:  python -m backtest.regime.indicators_inhouse
  기대:  지표 6종 × ~126개월, dropped_pct 로그 정상(>30% 시점 flag)

[STEP A-4] (Tier 2 — Phase A 제외. 건너뜀)

[STEP A-5] 관계 분석
  실행:  python -m backtest.regime.analyze
  기대:  리드랙(월별+반기앵커+#22·#23제외), 핫/콜드 분포, family 상관행렬

[STEP A-6] 대시보드 + 리포트
  실행:  python -m backtest.regime.dashboard
  기대:  플롯 + experiments/runs/..._REGIME_PHASE_A.md (§9 게이트 표 채움)

[STEP A-7] 민감도 (config_regime.py 흔들기 → run_id/config_hash 분리 저장)
  대상:  PBR_QUANTILES, MOM_LOOKBACK_M, MOM_FORMATION, BREADTH_MA_DAYS
  기대:  §9 결론이 파라미터에 강건. base run 미덮어씀 확인.
```

---

## 9. Phase B 진입 게이트 (선등록 — 사후 조정 금지, 리뷰 8·9번)

[Claude 의견] `value_spread`를 1순위 가설, `size_mom_6m`을 필수 직교 검증으로. **판단은 PRIMARY_SCENARIOS(D, F) 기준.** 
**HAC t값은 구속조건이 아니라 참고조건이다** — 실효 표본이 ~21이라 t값은 약한 증거이고, 자기상관이 만든 가짜 정밀도에 속을 수 있다. 부호 안정성·강건성·hot/cold 차이가 1차 구속조건.

| # | 조건 | 유형 |
|---|------|------|
| **G1** | `value_spread`와 `rel_vs_large_{t+1,t+3,t+6}` 중 **최소 2개 horizon**에서 회귀계수 부호가 이론과 일치(spread↑→value 유리, 양의 부호) | 1차 구속 |
| **G1b** | **hot** quartile의 `value_spread` 중앙값 > **cold** quartile 중앙값 | 1차 구속 |
| **G1c** | 월별 HAC t값·Spearman 부호 — 기록만, **통과 판정에 사용하지 않음** | 참고 |
| **G2** | 21개 반기 앵커에서도 계수 부호가 월별과 동일 | 1차 구속 |
| **G2b** | ★ **#22·#23을 제외해도 부호 유지** (문제의식이 그 두 구간에서 출발했으므로 여기에만 의존하면 순환논리) | 1차 구속 |
| **G3** | `size_mom_6m`(t−6M formation)이 hot/cold를 **동행/선행**으로 구분 | 필수 |
| **G4** | 결론이 §8 민감도에 강건 | 필수 |

**v0.3 확정 — #23 처리**: #23(2026-04-03~)은 아직 진행 중인 반기(`period_end`가 `date.today()` 기준 열린 stub)라 재실행 시마다 수익률이 바뀌고, h=6 리드랙 관측치도 아직 형성되지 않는다. 따라서 **G1·G1b·G2·G3의 회귀·hot/cold 분류·복제 게이트 판정은 #1~#22(21개 폐쇄 구간)만 사용**한다. #23은 대시보드·리포트에 참고용으로만 표시하고 "게이트 판정에 미포함, 진행 중" 라벨을 명시한다. G2b의 "#22·#23 제외"는 이미 판정 모집단(#1~#22) 안에서 #22를 제외하는 의미로 유지(#23은 애초에 모집단 밖).

**분기:**
- **G1·G1b·G2·G2b PASS →** Phase B 착수. 행동 신호 우선순위 ①`value_spread` ②`size_mom_6m`. tilt는 binary 아닌 **연속(예: 0.5x~1.5x)**, 파라미터 최소.
- **FAIL(특히 G2b) →** `[Claude 의견]` 분류기 아이디어를 **여기서 접는다.** tilt 대신 "대형·성장 sleeve 상시 병행 운용으로 자연 분산"으로 선회.

---

## 10. 리스크 / 주의사항

1. **표본 부족**: 반기 앵커 n=21. 월별(~126)은 자기상관 → 유의성 과대평가 주의. t값 참고 격하(§9).
2. **생존편향**: 상폐 종목 시총 공백 → `supplement_delisted` 우선, `dropped_pct` 로그.
3. **Look-ahead**: `available_from <= t`. 재무·formation·룩백 모두 t 이하만.
4. **book equity staleness**: 반기 사이 PBR 고정 → 재무 갱신 직후 점프 가능(실제 투자 조건과 동일하므로 편의는 아님).
5. **다중검정**: 지표 6종 → 우연 유효 위험. `value_spread`만 사전등록 1순위, 나머지는 탐색적. 필요 시 Bonferroni류 언급.
6. **핫구간 정의 임의성**: quartile 기준은 임의 → §8에서 tertile/median으로 강건성 확인.

---

## 11. 테스트 (리뷰 11번 — 신규)

의존성: `requirements.txt`에 **`statsmodels>=0.14.2`** 추가 (HAC). `[VERIFY]` 이미 없는 경우에만.

```
tests/regime/test_mtm_monthly.py
  - ★ stub 포함 월수익 누적곱 == 기존 반기 수익률 (핵심 회귀)
  - 월중 거래정지/가격결측 carry-forward 처리
  - 상폐 청산이 기존 백테스트 기준 시나리오와 일치
  - 고정수량 방식임을 검증(월별 리밸런싱이면 실패하는 케이스 포함)

tests/regime/test_indicators_inhouse.py
  - PBR 계산에 available_from > t 데이터가 섞이지 않음
  - size_mom_6m formation date(t-6M)가 t 이후 정보를 쓰지 않음
  - dropped_pct 계산 + 30% 초과 시 warning

tests/regime/test_analyze.py
  - value_spread(primary) vs exploratory 지표 구분
  - 민감도 run(config_hash 상이)이 base run을 overwrite 하지 않음
  - #22·#23 제외 분기(G2b)가 실제로 실행됨
```

---

## 12. Claude Code 실행 가이드 (요약)

1. §0 절대 규칙 + §2 `[VERIFY]`(v0.3에서 확인 완료 — 홀딩스는 JSON, equity는 한글 키, 시나리오 태그 일치)를 리포트 상단에 기록.
2. **STEP A-0(export_portfolios.py haircut 버그 수정+재실행)을 가장 먼저**, 별도 커밋으로 완료. STEP A-1→A-2→A-3 순. **A-2 복제 게이트 통과 전 A-3로 넘어가지 말 것.**
3. A-4는 건너뜀(Tier 2).
4. A-5·A-6로 분석·리포트. §9 게이트 표를 반드시 채움. **판단은 PRIMARY_SCENARIOS 기준.**
5. 모든 리포트 문장에 `[검증된 사실]`/`[Claude 의견]`/`[확실하지 않은 사실]` 라벨.
6. 배포는 git commit→push→서버 pull. scp 금지.
7. 이 문서는 **tilt 실행을 포함하지 않는다.** Phase B 착수는 §9 게이트에 따른다.

---

## 13. 한 줄 결론

Phase A는 **"소형 value 적합 국면이 측정 가능한가"를 기존 데이터만으로, 전략을 건드리지 않고 진단**하는 단계다. 넓게 재되(인하우스 지표), 행동은 선등록 게이트(특히 #22·#23 제외 강건성)를 통과한 최소 신호로만(Phase B) 좁힌다. 신규 데이터·외부 API는 필요성이 입증되기 전까지 확보하지 않는다.
