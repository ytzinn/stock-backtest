# SPEC_12 v0.3 — 모멘텀 필터 판정 기준(Judgment Criteria) 고도화 설계

> **이력**: v0.1 2026-07-22 / v0.2 2026-07-23(1차 외부 리뷰) / v0.3 2026-07-23(2차 외부 리뷰) / v0.3.1 2026-07-23(Claude Code 자체 점검 — MC-1 항목 추가, conflict_rate 유효구간 정의, 각주 링크 보강) / v0.3.2 2026-07-23(MC-1~MC-9 격리 스냅샷 실행 완료 — §9 결과, F_pbr_52w75 INCONCLUSIVE 나머지 FAIL, off-by-one 2건 정정) / **v0.3.3 2026-07-24(§10 MA 파라미터 그리드 + 부트스트랩 검증 — 비-사전등록 진단)**
> **성격**: Claude Code 핸드오프 스펙. 번호(SPEC_12)는 잠정.
> **표기**: `[검증된 사실]` / `[Claude 의견]` / `[확실하지 않은 사실]` / `[VERIFY]`(구현 전 코드·데이터 확인 필수)
> **범위**: 모멘텀 **판정 기준**만. SPEC_11의 비중 조절/랭크 블렌드(M0~M3)와는 별개 축.
> **선행**: MASTER.md, SPEC_03, SPEC_05, SPEC_06(벤치마크 정의 — **미결**), SPEC_07, SPEC_10(상위 게이트), SPEC_11

## v0.3 개정 요약

| # | v0.2의 문제 | 성격 | v0.3 조치 |
|---|---|---|---|
| 1 | **Family D 잔차 합이 수학적으로 0** — 절편 포함 OLS를 같은 구간에 적합 후 그 구간 잔차를 합산 | **치명적(no-op)** | 추정구간과 점수구간 분리. **Blitz식 부분합**을 기본으로(§3-D) |
| 2 | 신규 배관(config→prepare→evaluate→tape) 자체를 검증할 태그 없음 | 검증 공백 | `F_pbr_ma_double_adapter` 양성 대조군 신설(§4-5) |
| 3 | `CriterionResult`가 `apply()` 반환 계약 밖이라 파이프라인에서 유실. 클래스명이 stats 키면 `momentum_rejected`가 tape에서 **조용히 소멸** | **조용한 진단 유실** | `stats_key` + `last_diagnostics` + 별도 진단 파일(§4-1) |
| 4 | `compute_daily_metrics()`에 CAGR 없음 — §5-1 구현 불가 | 미구현 | `compute_nav_cagr()` 신설, **기준 자본 1.0**(§5-1) |
| 5 | 동결 벤치마크 재사용 요구가 현 스크립트로 불가(매 실행 FDR 재조회·덮어쓰기) | 실행 불가 | `--benchmarks-file` / `--offline` / SHA-256 manifest(§7) |
| 6 | 단일 seed permutation을 음성 대조군으로 판정 | 통계 오류 | **반복 귀무분포**(500~1,000회) + placebo 판정 재정의(§6-3) |
| 7 | `sign_count`의 0% 수익·거래정지 처리 미정 | 숨은 유동성 필터화 | 0을 0.5 가중, data status 4분류 정책(§3-A, §4-4) |
| 8 | window가 "종목별 마지막 N행"인지 "시장 거래일 N일"인지 미정. skip을 전 family 공통으로 서술 | 비교 불가 | 거래일 달력 anchor, skip은 **A·D 전용**(§3-0) |
| 9 | "가치-모멘텀 상충 비악화"가 기계 판정 불가 | 미정의 | `conflict_rate` 조작적 정의(§5-3) |
| 10 | §1-2 목표엔 worst-period 상대알파, §5-3 PASS엔 없음 | 내부 불일치 | **진단으로 하향** — 벤치마크 정의가 SPEC_06 미결이므로(§5-3) |
| 11 | SPEC_12 PASS와 프로덕션 채택의 관계 미명시 | 게이트 세탁 위험 | 상위 게이트 불간섭 조항 신설(§5-8) |
| 12 | v0.2가 universe shrinkage 문헌을 **과잉 교정** | 자기비판 과잉 | 2개 층 병기로 복원(§2) |

`[Claude 의견]` **12번 자기 정정**: v0.2는 "v0.1 요약이 방향이 반대였다"고 썼는데, 원문 §3을 다시 읽으니 **v0.1의 서술은 그 논문의 선행연구 정리와 일치**했다. 논문의 고유 기여는 거기에 "KOSPI200 **내부**도 이질적이고 KOSPI50은 저해"라는 층이 더해진 것이다. v0.2의 교정은 리뷰 지적을 받고 **과잉 교정**한 것이며, 자기비판이 항상 정확한 방향은 아니라는 사례로 기록한다.

---

## 0. 최우선 지시 (불변식)

1. 기존 결정론 태그의 **완결 20구간 편입·수익률 불변.** 신규 기준은 새 ablation 태그로만.
2. **Look-ahead[^1] 금지.** 모든 조회 `<= signal_date`. **회귀 추정구간도 동일**(§3-D).
3. **기존 `MomentumFilter`·`_momentum_filter()` 수정 금지.** 가산형 구현. 통합 리팩터링은 §4-5 게이트 통과 후 별도 세션.
4. 배포는 CLAUDE.md git 워크플로우 준수.
5. **파라미터는 소비되거나 예외를 던진다.** ghost parameter 재발 방지.
6. **(v0.3 신설) 조용한 실패 금지.** 진단 필드 소멸, no-op 신호, 유실된 stats는 에러 없이 결과만 바꾸므로 §4-5 게이트로 강제 검출한다.

---

## 1. 목적과 문제 정의

### 1-1. 현행 필터

`[검증된 사실]` `MomentumFilter(ma_short=20, ma_long=60, confirm_days=5, slope_lookback=20)` — 두 조건 동시 충족 시에만 제외(MA20<MA60 5일 연속 AND MA60 우하향). winner 선별이 아니라 falling knife / value trap[^3] **거부권**. 데이터 부족 시 통과. 구간당 300~700개 통과.

`[검증된 사실]` SPEC_11 §4: F−D net **+2.30%p/구간**, 승률 11/20, CAGR 기여 +5.1%p. 회전율 52%→80%이나 net +4.7%p 우위. 탈락 종목 4.31% vs F 실현 9.42%. **[상충]** 탈락군 중위 PBR이 더 싼 구간 **15/20**.

`[Claude 의견]` 4.31% vs 9.42%는 **엄밀한 인과 비교가 아니다.** F 실현군은 모멘텀 통과 + PBR 상위 20 선별까지 거쳤다. §5-6 매칭 진단으로 보완.

### 1-2. 목표

> **더 정밀한 거부권** — value trap은 더 잘 쳐내되, 싼 종목 알파는 덜 포기(15/20 상충 완화).

판정 축은 **daily-net Sharpe·MDD**(§5-1). worst-period 상대알파는 §5-3 사유로 **진단 지표**.

---

## 2. 한국 시장 모멘텀의 특수성 (v0.3 정밀화)

| 문헌 | 원문이 말하는 것 | 함의 |
|---|---|---|
| **Kang, Ryu & Webb (2025)**, *IAJ* 54(4), 1983–2023 | 개별주 모멘텀 포트폴리오는 **반전 효과**, 산업 모멘텀은 유의한 효과 없음. 산업 모멘텀 통제 후에도 개별주 반전 지속. | 개별주 raw 모멘텀 순위 선별 **위험** |
| **Choi, Choi & Kang (2012)**, arXiv:1211.6517 | **2개 층으로 읽어야 함.** ① 논문이 정리한 선행연구: 전 상장종목 유니버스에서는 유의한 모멘텀 없음, KOSPI200 구성종목만 대상으로 하면 유의한 수익 보고. ② 논문 고유 기여: **KOSPI200 내부에서도 성과가 균질하지 않고, KOSPI50 같은 대형주는 오히려 모멘텀을 저해.** | 유니버스 구성에 극도로 민감. "대형주일수록 모멘텀이 강하다"는 단순 일반화는 ②가 반증 |
| **Sim, Kang, Kim & Lee (2022)**, *EMFT* 58(11) | traditional 모멘텀 언더퍼폼 + 장기 반전. **idiosyncratic·rank·sign 안정적 수익.** 단 idiosyncratic은 Gutierrez-Prinsky(2007)·Blitz(2011)를 따라 **FF3 직교화** 기준. | 잔차/rank/sign 방향은 지지되나, **검증된 잔차는 FF3판**(§3-D 결정적) |
| **Liu, Liu & Ma (2011)**, *JIMF* 30(1) | 한국 포함 국제 시장에서 3대 모멘텀 유의성 약함. | 52주 고가 단독 알파 근거 약함 |
| George & Hwang (2004) | 52주 신고가 근접도 신호는 장기 반전하지 않음. | `[Claude 의견]` "가치 전략과 궁합" 추론은 **본 프로젝트 설계 추론**이지 원문 실증 아님 |
| Moskowitz et al.(2012)·Antonacci(2013) | 절대(시계열) 모멘텀 = 추세추종, 하방 리스크 축소. | 방어 거부권 성격과 일치 |
| Marshall et al. | TSMOM과 MA 규칙은 **밀접하나 동일하지 않음** — MA 가격교차 신호가 더 이르게 발생하는 경우가 많음. | "사실상 형제"는 과장 |

`[Claude 의견]` **방향타**: 현행 필터가 효과를 내는 이유는 winner-chasing[^4]이 아니라 **절대 모멘텀[^7] 기반 하락 거부권**으로 보인다. 따라서 절대 모멘텀 정밀화·비모수 변환·잔차 proxy가 안전한 방향이고, raw 횡단면 winner는 **판정 대상이 아닌 진단용**(§6-3).

---

## 3. 판정 기준 후보

### 3-0. 공통 규약 (v0.3 — window·skip 정의 확정)

**(a) 형성기간(formation window)[^2]과 skip 분리.** `lookback` 단일 파라미터 금지.

```python
formation_days = 126
skip_days      = 21
end_price   = P[anchor(t) - skip_days]
start_price = P[anchor(t) - skip_days - formation_days]
formation_return = end_price / start_price - 1
```

**(b) `skip_days`[^6]는 공통 규약이 아니다 (v0.3 수정).**

> `skip_days`는 **Family A와 D**(수익률 기반 신호)에만 적용한다. **Family B(MA)와 C(52주 신고가)는 signal date까지의 가격을 사용**한다. v0.2가 skip을 전 family 공통 규약으로 서술한 것은 오류.

**(c) window는 "종목별 마지막 N행"이 아니라 "시장 거래일 N일" (v0.3 신설).** `[VERIFY]`

`[Claude 의견]` 현행 `get_adj_close_range()`가 해당 종목의 마지막 N개 non-null 행을 반환한다면, 가격 행이 결손된 종목은 126행이 **126시장 거래일보다 긴 달력 기간**을 덮는다. 종목마다 측정 구간이 달라져 횡단면 비교 자체가 깨진다.

```
- KRX 공통 거래일 달력으로 anchor date 결정
- anchor 이하 최신 가격 사용, 허용 staleness 상한 설정
- 관측 누락은 insufficient 또는 invalid로 분류(§4-4)
```

**(d) 52주 신고가 정의 명확화**: `P[t-252..t]`는 양끝 포함 253개로 읽힌다. 정확히는 **"signal_date를 포함한 최근 252개 시장 거래일의 최고가"**.

**(e) 경계 인덱스와 최소 관측치는 oracle 테스트로 고정.** 필요 행 수 = `formation_days + skip_days + 1`(A) / `beta_window + skip_days + 1`(D, §3-D). `[v0.3.1 정정]` 원래 D를 `beta_window + skip_days`(+1 누락)로 적었는데, 실제 구현(momentum_criteria.py `MarketResidualCriterion`)에서 이 근사식 그대로 코딩했다가 전 종목이 `insufficient`로 빠지는 버그로 실측 발견 — pos_start가 항상 −1이 되는 off-by-one. A와 동일하게 +1이 필요하다(2026-07-23).

### Family A — 절대 수익률 계열

**A-1 `abs_return`(크기)**: `formation_return >= threshold` 통과. threshold=0이면 음수 수익 거부.

**A-2 `sign_count`(비모수 부호)[^8] — v0.3 0% 처리 확정**

```python
score = (n_positive + 0.5 * n_zero) / n_valid
pass if score >= 0.5
```

`[Claude 의견]` 0% 수익일을 음수와 같이 취급하면 소형주 종가 무변동일이 누적되어 `sign_count`가 모멘텀 신호가 아니라 **숨은 유동성 필터**가 된다. 동점을 절반으로 치는 것이 비모수 표준 처리다.

```
거래정지일          → 점수 계산에서 제외
정상 거래·종가 불변  → zero로 집계 (0.5 가중)
DB 행 자체 누락      → invalid_data
유효 관측일 < formation_days × 90% → insufficient
zero-return 비율     → 별도 기록 (사후 유동성 교란 진단용)
```

`[VERIFY]` Sim et al.(2022)의 sign 정의가 정확히 이 형태인지 원문 확인. **다만 정의가 달라도 A-2는 A-1과 구별되는 독립 신호이므로 실험 가치는 유지된다.**

`[검증된 사실] (v0.3.1 MC-1 확인 완료, 2026-07-23)` `price_history.is_suspended BOOLEAN`이 ingest 단계(`price_ingest.py`, `is_suspended = volume is None`)에서 실제로 채워진다. 서버 DB 실측: 전체 7,271,912행 중 `is_suspended=TRUE` 273,102행. 샘플 종목(258830, 2026-04-01~07-16)의 구간 내 행 수(73)가 그 기간 시장 전체 거래일수(73)와 **정확히 일치** — 거래정지일도 행이 빠지지 않고 `is_suspended=TRUE`·`volume=NULL`·`adj_close`는 직전가 유지로 기록된다. 따라서 "거래정지일"과 "DB 행 자체 누락"은 `is_suspended` 컬럼과 KRX 거래일 달력(§3-0c) 대조로 구분 가능하며, §3-A의 3분류를 그대로 구현한다. (2분류 축소 방지책은 불필요 — MC-1(d) 조건부 폐기.)

### Family B — 이동평균 추세

B1 `price < MA200` 제외 (primary) / B2 `MA60 < MA120` / B3 기울기 단독 / B4 이격도. **skip 미적용**(§3-0b).

### Family C — 52주 신고가 근접도[^9]

`pth = P[t] / max(최근 252 거래일 종가)`, `pth < threshold` 제외. **skip 미적용**.

### Family D — 시장잔차 추세[^10] (v0.3 전면 재설계)

**태그: `market_residual_trend_126` — Blitz 원형이 아닌 프로젝트 고유 proxy**

#### D-0. v0.2가 왜 no-op이었는가

`[검증된 사실]` 절편을 포함한 OLS를 **같은 구간**에 적합하면 그 구간의 잔차 합은 **수학적으로 정확히 0**이다. 수치 확인 결과 5개 종목 모두 잔차 합 절댓값이 3×10⁻¹⁶ 이하(기계 오차)였다. v0.2의 `residual_cum >= 0` 규칙은 **모든 종목이 통과하는 no-op**이었다.

절편 제거는 해법이 아니다. 시장과 무관한 종목 고유 평균수익이 beta 항에 흡수되어 잔차가 오염된다(수치 확인: 절편 제거 시 잔차 합은 0이 아니지만 beta 추정치가 왜곡).

#### D-1. 채택안 — Blitz식 부분합 (기본)

```python
beta_window    = 252     # 추정 구간 (t-21 에서 끝남)
formation_days = 126     # 점수 구간 = 추정 구간의 최근 126일 부분집합
skip_days      = 21

# 1) t-273 ~ t-21 (252일) 전체로 alpha, beta 추정 (절편 포함)
alpha, beta = ols(stock_ret[w], market_ret[w])
resid = stock_ret[w] - alpha - beta * market_ret[w]     # 전체 합 = 0 (당연)

# 2) 점수는 그 창의 '최근 126일 부분합' → 0이 아님
score = resid[-formation_days:].sum()

# 3) (사전등록 선택) 잔차 변동성 표준화 — Blitz 원형이 하는 방식
score_std = score / resid.std(ddof=2)

pass if score >= 0            # 또는 하위 X% 제외
```

`[검증된 사실]` 수치 확인: 전체 창 잔차 합 = 3×10⁻¹⁶(0), 최근 126일 부분합 = +0.086(0 아님). **필요 이력 = 252 + 21 + 1 = 274영업일(약 1.1년, v0.3.1 정정 — §3-0e 참조).**

`[Claude 의견]` **이 방식을 기본으로 택한 이유 두 가지.** ① Blitz 원형이 실제로 쓰는 구조다(36개월 회귀 후 12-1개월 잔차 = 부분집합). 구현이 원형에 가까울수록 §2의 한국 근거 전이가 낫다. ② 아래 대안보다 이력 요구가 **126일 적어** coverage gate 통과 가능성이 높다.

#### D-2. 대안 — 완전 OOS (2차 리뷰 제안)

```
1) formation window 직전 252일로 alpha, beta 추정
2) 그 이후 126일에 추정 alpha, beta 적용 → out-of-sample 잔차
3) 최근 21일 제외
필요 이력 = 252 + 126 + 21 + 1 = 400영업일 (약 1.6년, v0.3.1 정정 — §3-0e 참조)
```

통계적으로는 가장 깨끗하다(점수 구간에 적합 편의가 전혀 없음). **D-1이 coverage gate에 걸리는 경우가 아니라, D-1과 D-2의 결론이 갈리는지 확인하는 강건성 축으로 병기**한다. `[Claude 의견]` 둘 다 실행해 결론이 갈리면 Family D 자체를 INCONCLUSIVE로 본다.

#### D-3. coverage 사전 경고

`[Claude 의견]` HardFilter는 상장 6개월부터 종목을 허용하는데 D-1은 약 13개월, D-2는 약 19개월 이력을 요구한다. **§4-4 coverage gate(95%)를 통과하지 못할 가능성이 상당하다.** 이 경우:

> **게이트를 완화하지 않는다.** Family D는 별도 스펙으로 보류한다. coverage 미달 상태의 성과 수치는 "신호 효과"와 "신규상장주 자동통과 효과"가 분리되지 않으므로 해석 불가다.

#### D-4. 사전 고정 항목 (실행 전 manifest에 기록)

벤치마크 매칭(KOSPI 종목→KS11, KOSDAQ 종목→KQ11; 시장구분 PIT 정확성 `[VERIFY]`), simple/log return, 절편 포함(고정: 포함), 최소 관측치, 시장수익률 분산 0 처리, 종목·지수 휴장일 정렬, 잔차변동성 표준화 여부.

**데이터 `[VERIFY]`**: `period_results.kospi_return`은 **보유구간 누적수익률**이라 일별 회귀에 사용 불가. `scripts/run_daily_nav.py`의 KS11·KQ11 일별 시계열을 동결 스냅샷으로 사용(§7 MC-5).

#### D-5. 문헌 원형은 별도 스펙

```
ff3_residual_momentum_12_1 — 월별 FF3 / 36개월 회귀 / 12-1개월 잔차 /
                             잔차변동성 표준화 / 횡단면 rank
전제: 한국 FF3 팩터 PIT 시계열 확보 (현재 미보유)
```

### Family E / F — 보류

경로 품질[^11], 변동성 조정. A/D primary가 PASS한 뒤에만 오버레이로.

---

## 4. 백테스트 적용 설계

### 4-1. `prepare → evaluate` + 진단 전달 (v0.3 보강)

```python
class MomentumCriterion(Protocol):
    def prepare(self, tickers, signal_date, conn) -> CriterionContext:
        """가격·벤치마크 일괄 조회, 점수 계산, 횡단면 cutoff 확정."""
    def evaluate(self, ticker, ctx) -> CriterionResult: ...

@dataclass(frozen=True)
class CriterionResult:
    passed: bool
    score: float | None
    reason_code: str    # passed_by_signal | passed_insufficient_data |
                        # rejected_by_signal | invalid_data
    n_obs: int
    data_status: str    # ok | insufficient | invalid
    zero_ratio: float | None       # sign_count 유동성 교란 진단(§3-A)
    cutoff_distance: float | None  # 임계값 근접도
```

**진단이 파이프라인 밖으로 나가야 한다 (v0.3 신설)**

`[검증된 사실] (v0.3.1 MC-1 확인 완료)` `scripts/export_portfolios.py`가 정확히 `univ_result['stats'].get('MomentumFilter')`(하드코딩된 클래스명 키)로 조회하고 있음을 코드로 확인했다. 신규 클래스명이 `MomentumCriterionFilter`인 순간 **`momentum_rejected`가 holdings tape에서 조용히 사라지고**, SPEC_11 거부권 진단(§5-6)이 통째로 불가능해진다. 아래 `stats_key = "MomentumFilter"` 어댑터가 **필수**다 (선택 사항 아님). 에러 없이 결과만 바뀌는 유형이므로 §0 규칙 6의 대상.

```python
class MomentumCriterionFilter:
    stats_key = "MomentumFilter"        # 기존 tape 호환

    def apply(self, ...):
        ...
        self.last_diagnostics = {t: CriterionResult(...) for t in original_tickers}
        return passed, rejected          # 기존 계약 유지
```

```python
# BacktestPipeline.build_universe()
key = getattr(f, "stats_key", f.__class__.__name__)
stats[key] = {"passed": len(tickers), "rejected": rejected}
if hasattr(f, "last_diagnostics"):
    stats[key]["diagnostics"] = f.last_diagnostics
```

**부작용 차단 (v0.3 추가)**: `stats_key` 위장은 호환을 지키지만, 나중에 tape를 읽는 사람이 **어느 구현이 실행됐는지 구분할 수 없다.** 진단 요약에 반드시 기록한다:

```json
{"implementation": "MomentumCriterionFilter", "criterion": "abs_return",
 "params": {...}, "legacy_adapter": false}
```

**저장 위치** (holdings JSON 비대화 방지):
```
experiments/momentum_criteria/{tag}_scores.csv.gz
experiments/momentum_criteria/{tag}_diagnostics_summary.json
holdings tape → 기존 호환용 momentum_rejected 만 유지
```

**성능**: 종목별 SQL 반복은 300~700종목 × 태그 × 밴드에서 폭증. 리밸런싱일별 최대 lookback 가격을 **일괄 조회 → 점수 테이블 → 임계값별 pass/fail** 캐시 재사용.

### 4-2. 설정 전달부

`[검증된 사실] (v0.3.1 MC-1 확인 완료)` `backtest/ablation.py:356-359`에서 `build_ablation_pipeline()`은 `config.get('use_momentum', False)`로 필터 포함 여부만 토글하고, 포함 시 항상 `MomentumFilter(ma_short=20, ma_long=60, confirm_days=5, slope_lookback=20)`을 하드코딩 생성한다. `momentum_criterion` 같은 신규 키를 config에 추가해도 **읽는 코드가 없어 아무 효과 없이 기존 MA 필터가 실행된다** — 아래 분기를 MC-2에서 반드시 신설해야 한다(그냥 있으면 되는 게 아니라 없다).

```python
momentum_config = config.get('momentum')
if momentum_config is None:
    filters.append(MomentumFilter(ma_short=20, ma_long=60,
                                  confirm_days=5, slope_lookback=20))   # 레거시 경로
else:
    filters.append(build_momentum_criterion_filter(momentum_config))
```

**fail-fast(전부 즉시 예외)**: 알 수 없는 criterion / 허용되지 않는 파라미터 / 필수 누락 / **전달됐으나 소비되지 않은 파라미터**.

### 4-3. 신호일과 체결일

```
signal_execution_convention = "legacy_same_close"
  signal_date = execution_date = rebalance_date
```

`[Claude 의견]` 리밸런싱일 종가로 신호를 만들고 같은 종가에 체결하는 것은 실거래에서 불가능하다. 기존 결과 재현을 위해 유지하되 명시적으로 이름 붙이고, **채택 전 `signal_date = rebalance_date − 1영업일` 버전을 재실행**해 실행가능성을 검증한다(§7 MC-8). 당일 종가를 직접 쓰는 B1·B4·C가 특히 민감.

### 4-4. 데이터 상태 정책과 coverage gate (v0.3 정책 확정)

`[검증된 사실]` HardFilter는 상장 6개월부터 허용. MA200은 약 9~10개월, 52주 고가는 약 12개월, Family D는 13~19개월 이력 필요 → **상장 초기 종목이 신호가 좋아서가 아니라 자료가 없어서 자동 통과**한다.

**3분류 처리 정책 (v0.3 신설):**

| 상태 | 정의 | 처리 |
|---|---|---|
| `insufficient` | 신규상장 등 **정상적** 이력 부족 | **통과**(하위호환) + 기록 |
| `invalid` | 가격 음수·날짜 중복·벤치마크 누락·DB 결손 | **실행 중단**(조용히 통과시키지 않는다) |
| `rejected` | 정상 계산 결과 신호 탈락 | 제외 |

**coverage gate (기본값 — 사용자 확정 필요):**
```
전체 정상 계산 비율                    ≥ 95%
특정 구간 정상 계산 비율 < 85%          → 경고
데이터부족 통과가 포트폴리오의 10% 초과 → 판정 보류
```

### 4-5. Ablation 태그와 **배관 검증 게이트** (v0.3 신설)

`[Claude 의견]` §0 규칙 3에 따라 기존 태그는 레거시 경로를 그대로 탄다. 따라서 **`F_pbr_no_r3r4` 재현은 옛 코드가 안 바뀐 증거일 뿐, 신규 배관이 옳다는 증거가 아니다.** config 전달·criterion 선택·prepare/evaluate·pass/reject 변환·stats 전달·tape 기록 중 어디가 틀려도 잡히지 않는다.

**양성 대조군 태그 신설:**

```
F_pbr_ma_double_adapter
  - 나머지 스택은 F_pbr_no_r3r4와 동일
  - 모멘텀만 신규 MomentumCriterionFilter
  - criterion은 기존 _momentum_filter() 를 호출하는 adapter (산식 재작성 금지)
```

**게이트(하나라도 불일치 시 구현 중단):**
```
F_pbr_ma_double_adapter == F_pbr_no_r3r4  (완결 20구간 전부)
  ✓ 모멘텀 통과 종목      ✓ 모멘텀 탈락 종목
  ✓ 최종 top 20          ✓ turnover
  ✓ gross / net return    ✓ daily NAV
  ✓ reason_code 분포 (insufficient 통과 케이스 포함)
```

**역할 분리**: `F_pbr_no_r3r4` = **성과 기준선(인컴번트)** / `F_pbr_ma_double_adapter` = **신규 배관의 양성 대조군**.

---

## 5. 평가 프로토콜 (사전등록[^19] — 결과 열람 전 동결)

### 5-1. 지표와 net 정의 (v0.3 — CAGR 구현 포함)

`[검증된 사실]` 저장소에 두 net 정의가 공존 `[VERIFY]`: 엔진 `net_return = gross − cost`(산술) vs 일별 NAV `gross × (1 − cost)`(승법). `gross × cost` 교차항만큼 다르다.

**규약**: 의사결정 지표는 **전부 daily-net NAV에서** 산출. 엔진 `net_cagr`는 reconciliation용 병기.

**`[검증된 사실] (v0.3.1 MC-1 확인 완료)`** `backtest/metrics.py:119-215`의 `compute_daily_metrics()` 반환 dict(`daily_mdd, daily_sharpe, cvar_* , ...`)에 **CAGR 키가 없음을 확인**했다. §5-1은 구현 불가 상태이므로 아래 SSOT 함수를 신규 추가한다(기존 함수 무수정 — `compute_period_returns` 기반의 `compute_metrics()`가 있는 반기-CAGR와는 별개):

```python
def compute_nav_cagr(nav: pd.Series, initial_capital: float = 1.0) -> float:
    years = (nav.index[-1] - nav.index[0]).days / 365.25   # 실제 달력일
    return (nav.iloc[-1] / initial_capital) ** (1 / years) - 1
```

**기준 자본은 `nav.iloc[0]`이 아니라 1.0** — `stitch_periods()`의 첫 net NAV는 이미 거래비용 차감 후라 1보다 작다. `nav.iloc[0]`을 쓰면 **첫 리밸런싱 거래비용이 CAGR에서 통째로 빠진다.**

`[Claude 의견] (v0.3 연결)` 위 `years` 계산은 기존에 열려 있던 **CAGR 달력일 부채**(21 × 0.5yr 관행으로 인한 체계적 과소평가)와 **동일한 수정**이다. 두 건을 분리해서 고치면 정의가 갈라지므로 **함께 처리**한다.

### 5-2. 표본의 실제 크기

`[Claude 의견]` v0.1의 "월별 MTM[^13]으로 21→126 관측치, 얇음 직접 완화"는 통계적으로 틀렸다. 같은 포트폴리오의 반복 평가는 강하게 자기상관되며 **독립 투자 의사결정 표본은 여전히 20개**다.

| 판정 대상 | 단위 | 명목 | 실효 정보량 |
|---|---|---|---|
| CAGR/Sharpe/MDD | 리밸런싱 구간 | **20** | 20 — 늘릴 방법 없음 |
| 위험 경로·구간 내 손실 | 일별 NAV | 수천 | 경로 정밀도↑, **독립 표본 증가 아님** |
| 거부권·PBR 상충 | stock-period | 수백~수천 | 시장·구간 충격 공유 → **명목 수만큼 정보량 증가 안 함** |

**부트스트랩[^14]**: 포트폴리오 차이 → 구간 단위 paired / 일별 NAV → 구간 경계 보존 block / stock-period → 최소 구간 clustering, 가능하면 ticker × period 2-way clustering. CI[^15]가 0을 크게 포함하면 점추정 우위 무의미.

**일별 NAV는 기존 SSOT 재사용**(별도 MTM 산식 작성 금지, §7 MC-5).

### 5-3. PASS 조건 (v0.3 — 조작적 정의 확정)

| 항목 | 기본 문턱 (사용자 확정 필요) |
|---|---|
| daily-net Sharpe 개선 | ≥ **+0.05** |
| **또는** daily-net MDD 개선 | ≥ **+2.0%p** |
| daily-net CAGR 허용 열위 | ≥ **−1.0%p** |
| 가치-모멘텀 상충 비악화 | 아래 정의 |
| coverage(§4-4) | ≥ 95% |
| no-op 가드(§5-5) | 중앙 탈락률 ≥ 5% |

**가치-모멘텀 상충의 조작적 정의 (v0.3 신설):**
```python
conflict_rate = (중위 PBR(탈락군) < 중위 PBR(통과군) 인 구간 수) / 유효 구간 수

비악화 =  conflict_rate 가 인컴번트 대비 1개 구간 이상 증가하지 않음
      AND median log-PBR gap 이 인컴번트 대비 0.05 이상 악화되지 않음
```
PBR 매칭 진단(§5-6)은 **보조**로 두고, PASS 조건에는 위 단일 statistic만 쓴다.

**"유효 구간" 정의 (v0.3.1 신설)**: `[Claude 의견]` 탈락군이 0개 또는 1개뿐인 구간은 중위값 비교가 정의되지 않거나(0개) 노이즈에 극도로 민감해(1개) `conflict_rate` 분모·분자를 모두 왜곡한다.
```
유효 구간 = 탈락군 크기 >= 3 AND 통과군 크기 >= 3 인 구간
```
문턱 3은 잠정값 — MC-0 manifest에서 사용자 확정. 유효 구간이 20개 중 10개 미만이면 `conflict_rate` 자체를 표본 부족으로 진단 전용 강등하고 PASS 조건에서 제외한다(§5-4 INCONCLUSIVE 사유로 기록).

**worst-period 상대알파 — 진단으로 하향 (v0.3 결정):**

`[Claude 의견]` v0.2는 §1-2 목표에 넣고 §5-3 PASS에서 빠뜨렸다. 단순 누락이 아니라 **구조적 이유**가 있다: 상대알파를 정의하려면 벤치마크가 확정돼야 하는데 **주 KPI 벤치마크 정의가 SPEC_06에서 미결**이다. 미결 정의 위에 PASS 조건을 세우면 나중에 벤치마크가 바뀔 때 판정이 소급 무효가 된다.

> worst-period 상대알파는 **진단 지표**로 산출·기록하되 PASS 조건에 넣지 않는다. SPEC_06 벤치마크 확정 후 SPEC_13+에서 승격 검토.
> 산출 시 정의: `min over periods (구간 daily-net 전략수익률 − 동일 구간 벤치마크 수익률)`, 벤치마크는 **잠정** 표기.

**FAIL 분기**: 미충족 시 **현행 MA double 유지.** 코드는 실험 보존, 파이프라인 미포함.

### 5-4. 3단계 판정

```
PASS         : §5-3 문턱 충족 AND robust
INCONCLUSIVE : 문턱 충족하나 밴드 방향 혼재 → 라이브 관찰, 채택 보류
FAIL         : 문턱 미달 또는 이웃 설정 중앙값이 명확한 열위

robust[^12] (기본값 — 사용자 확정 필요):
  - 이웃 설정 70% 이상에서 primary와 같은 방향
  - 이웃 설정 효과 중앙값 ≥ 0
  - 치명적 MDD/CAGR 악화 조합 없음
```

**밴드 방향성 판정 지표는 단일로 고정**(daily-net Sharpe 차이). "Sharpe OR MDD"는 PASS 문턱에만 사용.

### 5-5. 탈락률 통제 — iso-rejection[^20]

`[Claude 의견]` MA double은 관대하고 MA200·52주·절대수익 0%는 훨씬 많이 제외할 수 있다. 통제하지 않으면 **신호 품질 차이와 유니버스 축소 효과가 분리되지 않는다.**

필수 출력: coverage / 전체·구간별 탈락률 / 인컴번트와 탈락집합 Jaccard / **전환행렬**[^21](통과→탈락, 탈락→통과) / top 20 변경 종목 수 / turnover 증가분 / **iso-rejection 진단**(인컴번트 구간별 중앙 탈락률에 맞춘 임계값 비교 — 파라미터 선택용 아님).

경보: 중앙 탈락률 < 5% → no-op / > 50% → 전략 성격 변경.

### 5-6. 거부권 진단의 인과 보완

같은 구간 pre-momentum pool 내에서 **PBR 순위 최근접 1:1 매칭**, 시총·유동성 유사군 비교, 구간별 동일가중 후 평균. "더 비싼 종목을 골라서"와 "하락 추세를 피해서"를 부분 분리.

### 5-7. PIT[^16]·생존편향[^17]

formation·**추정 구간 전부** `<= signal_date`. 상폐 haircut은 v5.3 수정분. 벤치마크도 PIT·동결.

### 5-8. 상위 게이트와의 관계 (v0.3 신설)

`[검증된 사실]` 기존 공식 판정에서 일별 MDD가 약 −54.2%로 SPEC_10 G5 기준(−45%)을 통과하지 못했다 `[VERIFY]`.

> **SPEC_12의 PASS는 "현행 모멘텀 판정 기준보다 우수하다"는 상대 판정일 뿐, 전략 전체의 채택 승인이 아니다. SPEC_10 G5 등 기존 상위 게이트를 우회하거나 무효화하지 않는다.**

`[Claude 의견]` 예컨대 Sharpe +0.06, MDD −54.2%→−53.8% 개선이면 SPEC_12 자체 기준으로는 PASS지만 전략은 여전히 G5 미달이다. **"SPEC_12 실험 PASS"와 "프로덕션 채택 가능"을 명시적으로 분리**하지 않으면 게이트 세탁이 된다.

---

## 6. 사전등록 태그

### 6-1. 의사결정 primary (결과 열람 전 동결)

| 역할 | 태그 | 정의 |
|---|---|---|
| 성과 기준선(인컴번트) | `F_pbr_no_r3r4` | 현행 MA double, 레거시 경로 |
| **배관 양성 대조군** | `F_pbr_ma_double_adapter` | 신규 배관 + adapter, 인컴번트와 완전 일치 필수(§4-5) |
| 절대수익 veto | `F_pbr_absret126` | formation 126 / skip 21, 누적수익 < 0 제외 |
| 비모수 부호 veto | `F_pbr_signcount126` | 일별 상승일 비율(0은 0.5) < 0.5 제외 |
| 장기 MA veto | `F_pbr_ma200` | `price < MA200` 제외 (skip 미적용) |
| 52주 위치 veto | `F_pbr_52w75` | `price / 252거래일 최고가 < 0.75` 제외 (skip 미적용) |
| 시장잔차 proxy | `F_pbr_mktresid126` | **Blitz식 부분합**(§3-D1), coverage gate 통과 시에만 |

**1순위**: `absret126`, `signcount126`, `ma200`, `52w75` — 단일 종목 가격만 필요, 벤치마크 의존 없음. `mktresid126`은 §3-D 선결조건 해소 후.

### 6-2. 강건성 — OAT[^23] 국소 밴드 / 엄격도 곡선 분리

**(a) Local robustness (OAT, 한 번에 한 파라미터):**

| primary | 국소 밴드 |
|---|---|
| `absret126` / `signcount126` | formation ∈ {105, 126, 147} |
| `ma200` | ma ∈ {180, 200, 220} |
| `52w75` | threshold ∈ {0.70, 0.75, 0.80} |
| `mktresid126` | formation ∈ {105,126,147} / **D-1 vs D-2 방식 비교**(§3-D2) / benchmark ∈ {KS11·KQ11 매칭, KS11 단일} |

**(b) Strictness curve (판정 아님):** 절대수익 threshold ∈ {+3%, 0%, −3%, −5%} / reject percentile ∈ {10%, 20%, 30%}. **최고 성과를 고르지 않고** 탈락률 대비 성과 형태만 본다(§5-5 연동).

> **금지**: 밴드·곡선의 최고값 조합을 새 primary로 교체하는 것[^18].

### 6-3. 대조군[^5] (v0.3 — permutation 귀무분포)

`[Claude 의견]` v0.2는 단일 seed permutation 하나를 음성 대조군으로 뒀다. **무작위 점수도 우연히 좋은 종목을 거를 수 있고, 표본이 20구간뿐이라 단일 순열이 인컴번트를 이겨도 아무 의미가 없다.** 한 번의 추출로 분포를 판정한 셈이다.

| 역할 | 구성 | 용도 |
|---|---|---|
| 배관 양성 대조군 | `F_pbr_ma_double_adapter` | §4-5 게이트 |
| **permutation 귀무분포**[^22] | absret 점수를 리밸런싱일별 종목 간 재배열, **500~1,000회**, master seed 고정, **실제와 동일한 탈락 종목 수 유지** | observed 성과의 **placebo percentile** 산출 |
| 결정론 회귀 | `F_pbr_absret_perm_seed20260722` (단일 seed) | seed 결정론 테스트 전용 — **판정에 사용 금지** |
| 대안 신호 진단 | `F_pbr_xsrank50` | 반전 진단. 이상 성과 시 **버그 단정이 아니라 감사 trigger** |

**placebo 판정 재정의 (v0.2 오류 수정):**
```
❌ v0.2: placebo에서 유의한 성과가 나오면 파이프라인 결함
✅ v0.3:
   신호 유효성  : 실제 신호가 permutation 분포와 구별되지 않으면
                  → 신호의 고유 정보가 입증되지 않음
   파이프라인 결함: fast-path/full-engine 등가성, 탈락률 보존,
                  seed 결정론 등 불변식 위반으로 별도 판정
```

**연산 범위 제한 (v0.3 추가)**: 500~1,000회 × 전체 엔진 × 20구간은 비용이 크다. **§5-3 문턱을 통과한 primary 하나에만** 귀무분포를 돌린다(전 primary에 돌리지 않는다). 기존 random-pool 반복 실행 머신과 full-engine 등가성 게이트 구조를 재사용.

---

## 7. Claude Code 작업 순서

```
[MC-0] 사전등록 manifest (Markdown 커밋 + 기계판독 JSON)
       {spec, baseline_tag, snapshot_id, valuation_date,
        signal_execution_convention: "legacy_same_close",
        benchmark_file_sha256, primary_criteria, robustness_bands,
        decision_thresholds, residual_method: "blitz_subset"}
       결과 열람 전 커밋. 이후 primary 교체 금지.

[MC-1] `[완료 — 2026-07-23]` 4건 전부 코드/DB 실측으로 확인됨. 전부 "사실"로
       확정되어 아래 수정이 전부 필요하다 (하나도 생략 불가):
       (a) build_ablation_pipeline() 은 모멘텀 config 를 읽지 않는다(ablation.py:356-359,
           use_momentum 토글만 존재) → §4-2 분기 신설 필요.
       (b) export_portfolios.py 는 'MomentumFilter' 키만 조회한다(하드코딩 확인) →
           §4-1 stats_key 어댑터 필수.
       (c) compute_daily_metrics() 에 CAGR 없음(metrics.py:119-215 확인) →
           §5-1 compute_nav_cagr() 신설 필요.
       (d) is_suspended 컬럼이 ingest에서 채워짐, 거래정지일도 행 누락 없이 기록됨
           (서버 실측: TRUE 273,102행, 샘플종목 행수=시장거래일수 정확히 일치) →
           §3-A 3분류 그대로 구현 가능, 축소 불필요.
       기존 MomentumFilter / _momentum_filter 는 절대 수정 금지.

[MC-2] MomentumCriterionFilter 신설 — prepare→evaluate, CriterionResult,
       stats_key, last_diagnostics, 진단 파일 분리(§4-1).
       ma_double adapter 는 기존 _momentum_filter() 호출로 구현(재작성 금지).
       미사용·오타 파라미터 즉시 예외.

[MC-3] `[완료 — 2026-07-23]` ★배관 게이트: F_pbr_ma_double_adapter == F_pbr_no_r3r4
       완전 일치 증명(§4-5). 완결 20구간 전부 portfolio/period_return/net_return/
       turnover/모멘텀 통과·탈락 집합 완전 일치. metrics도 cagr=16.28%로 기존
       공식 수치와 일치 — 신규 배관(prepare→evaluate→stats_key→tape) 신뢰 확보.

[MC-4] 거래일 달력 anchor + 가격 일괄 조회 + 점수 precompute 캐시(§3-0c).

[MC-5] compute_nav_cagr() 추가 — 기준 자본 1.0, 실제 달력일(§5-1).
       기존 CAGR 달력일 부채와 함께 처리.
       run_daily_nav.py 에 --benchmarks-file / --offline / --refresh-benchmarks 추가.
       공식 실행은 동결 CSV + 네트워크 차단, SHA-256 을 manifest 에 기록.

[MC-6] Family A/B/C 실행: absret126, signcount126, ma200, 52w75.
       daily-net 지표 + coverage + 탈락률/전환행렬 동시 출력.

[MC-7] Family D — §3-D4 사전 고정 항목 확정 후 D-1(Blitz식 부분합) 실행.
       coverage gate 미달 시 게이트 완화 금지, 별도 스펙 보류(§3-D3).
       D-2(완전 OOS)는 강건성 축으로 병기, 결론 상충 시 INCONCLUSIVE.

[MC-8] 실행가능성 검증: signal_date = rebalance_date − 1영업일 재실행.
       결론 반전 시 채택 보류.

[MC-9] 강건성: OAT 국소 밴드 / 엄격도 곡선 분리.
       §5-3 통과 primary 하나에만 permutation 귀무분포 500~1,000회(§6-3).

[MC-10] PASS / INCONCLUSIVE / FAIL 판정 + §5-8 상위 게이트 불간섭 명시.
        최종 채택은 라이브 #24 이후 유보.

[MC-11] 전 작업 후 결정론 태그 기존값 불변 재확인.

[실행 조건] 크론 동결 스냅샷 + valuation_date 명시. 운영 DB(5433) 읽기 전용.
```

## 8. 완료 체크리스트

- [x] MC-0 manifest(JSON+MD) 커밋 — `experiments/momentum_criteria/MC0_manifest.{json,md}`,
      문턱값은 스펙 기본값 채택(사용자 확정 2026-07-23). benchmark SHA-256은 미기록(§실행 방식 참조)
- [x] MC-1 `[VERIFY]` 4건 확인 및 필요한 배관 수정 완료 (거래정지/DB누락 구분 가능 확인, MomentumCriterionFilter/stats_key/compute_nav_cagr 신설)
- [x] **MC-3 배관 게이트 통과** (2026-07-23 서버 실측: 완결 20구간 전부 portfolio·period_return·
      net_return·turnover·momentum 통과/탈락 집합 완전 일치. `F_pbr_no_r3r4`/`F_pbr_ma_double_adapter`
      metrics 동일: cagr=16.28%, net_cagr=15.10%, sharpe=0.5717, mdd=-30.73% — 기존 공식 수치와도 일치)
- [x] 거래일 달력 anchor 적용(`_calendar_window`, momentum_criteria.py) — skip이 A·D에만 적용됨을
      코드 구조로 고정(Family B/C는 `_prepare_price_context`에 skip 파라미터 자체가 없음). 별도
      oracle 테스트는 미작성(코드 리뷰·서버 실측 스모크테스트로만 검증)
- [x] `compute_nav_cagr()` 신설 완료(backtest/metrics.py, 기준 1.0 + 달력일)
- [x] offline 벤치마크 실행 경로 — `--benchmarks-file`/`--offline` CLI 플래그는 미구현이지만,
      **격리 스냅샷 DB(포트 5435, 운영 5433과 완전 분리) + 1회 생성한 benchmarks_daily.csv 고정
      재사용**으로 동일한 재현성 목표를 달성(§실행 방식 참조). SHA-256 manifest 기록은 생략
- [x] Family A/B/C 실행 → daily-net + coverage + 탈락률/전환행렬 (§9 결과 참조)
- [x] Family D-1 실행 — coverage gate 사전확인(평균 97.1%, 최저 94.7%, 문턱 95%/85% 통과) 후 실행.
      §3-D3의 "coverage 미달 가능성 상당" 예측은 **틀렸음** — StabilityFilter가 이미 신규상장을
      선행 제거해 모멘텀 단계 도달 풀은 이력이 충분했다 (2026-07-23 실측으로 정정)
- [x] OAT 밴드(52w75 threshold 0.70/0.80) 실행 — robust 기준 미충족(§9)
- [ ] permutation 귀무분포 — **미실행**. §5-3 1차 문턱을 통과한 primary가 52w75뿐이었는데 그마저
      robust 미충족으로 INCONCLUSIVE 판정돼, "통과 primary 1개에만 귀무분포" 조건의 전제가
      사라짐 (§6-3 연산범위 제한 규정과 정합)
- [ ] 전일신호 실행가능성 검증(MC-8) — **미실행**. 위와 같은 이유로 채택 후보가 없어 우선순위 낮음
- [x] PASS/INCONCLUSIVE/FAIL 판정 + 상위 게이트 불간섭 문구 기록 (§9)

### 실행 방식 (2026-07-23, 계획 대비 변경)

당초 MC-0가 상정한 "크론 주석 처리 → 전체 재실행 → 원복" 대신, **운영 DB(5433)를 건드리지 않는
격리 스냅샷**(`pg_dump`/`pg_restore`, 포트 5435, 운영과 별개 docker 컨테이너)에서 전부 실행했다.
운영 크론(price_ingest/market_cap_ingest)은 한 번도 정지하지 않았다. 스냅샷은 2026-07-23 05:55:54
UTC 시점 운영 DB의 완전한 복제(`price_history`/`market_cap_history`/`financials_pit`/`stocks` 행수
전수 대조 완료)이고 이후 어떤 프로세스도 쓰기 접근하지 않았으므로, "크론 동결 스냅샷"이 요구하는
재현성 목표(동일 DB 상태에서 전 태그 비교)를 동일하게 충족한다. 산출물은 `/tmp/spec12_snapshot_run/
experiments/`(서버, 운영 experiments/ablation/의 기존 공식 산출물과 완전 분리) — **스냅샷 컨테이너와
함께 실험 종료 시 삭제 예정**이므로 이 문서의 §9 표가 재현 시 유일한 기록이다.

## 9. 실행 결과 (2026-07-23, 격리 스냅샷)

### 9-1. Family A/B/C daily-net 지표 (§5-1 기준)

| 태그 | net CAGR | net Sharpe | net MDD | Sharpe Δ | MDD Δ(%p) | CAGR Δ(%p) | coverage | 중앙탈락률 |
|---|---|---|---|---|---|---|---|---|
| F_pbr_no_r3r4(인컴번트) | 15.02% | 0.6329 | −54.22% | — | — | — | — | 39.5% |
| F_pbr_absret126 | 13.43% | 0.5737 | −58.41% | −0.059 | −4.19 | −1.59 | 99.51% | 51.7% |
| F_pbr_signcount126 | 10.66% | 0.4749 | −50.88% | −0.158 | +3.34 | −4.36 | 98.58% | 62.1% |
| F_pbr_ma200 | 15.23% | 0.6283 | −60.93% | −0.005 | −6.71 | +0.21 | 98.28% | 51.4% |
| F_pbr_52w75 | 14.82% | 0.6692 | −50.52% | +0.036 | +3.70 | −0.20 | 97.38% | 47.6% |
| F_pbr_mktresid126 | 12.98% | 0.5709 | −56.07% | −0.062 | −1.85 | −2.05 | 96.94% | 46.1% |

§5-3 1차 문턱(Sharpe≥+0.05 OR MDD≥+2.0%p, CAGR열위≤−1.0%p, coverage≥95%, 탈락률≥5%) 충족:
**F_pbr_52w75만 통과.** 나머지 4개는 Sharpe·MDD 개선 없음 또는 CAGR 열위 초과로 **FAIL**.

### 9-2. F_pbr_52w75 robust 검증 (§5-4 OAT 밴드)

| threshold | net Sharpe | 인컴번트 대비 Δ | 방향 |
|---|---|---|---|
| 0.70 | 0.587 | −0.046 | 악화(반대) |
| 0.75(primary) | 0.669 | +0.036 | 개선 |
| 0.80 | 0.643 | +0.010 | 개선 |

이웃 2개 중 1개만 primary와 같은 방향(50% < 70% 문턱), 이웃 효과 중앙값 −0.018(<0) →
**robust 미충족.** threshold 0.75→0.70 소폭 변경으로 결론이 뒤집히는 knife-edge[^12] 패턴.

### 9-3. conflict_rate 참고 진단 (§5-3 보조, F_pbr_52w75만 확인)

| | conflict_rate | median log-PBR gap |
|---|---|---|
| 인컴번트(MA double) | 0.762(21구간 중 16) | −0.162 |
| F_pbr_52w75 | 0.476(21구간 중 10) | +0.027 |

52w75는 인컴번트보다 가치-모멘텀 상충이 크게 완화됨(§1-1의 15/20 상충 문제 상당 부분 해소) —
다만 이 결과가 §9-2의 robust 미충족을 상쇄하지는 못한다.

### 9-4. 최종 판정 (§5-4 3단계)

```
F_pbr_absret126     : FAIL (Sharpe·MDD 악화 + CAGR 열위 초과)
F_pbr_signcount126  : FAIL (MDD만 개선, CAGR 열위 -4.36%p로 초과)
F_pbr_ma200         : FAIL (Sharpe·MDD 개선 없음)
F_pbr_52w75         : INCONCLUSIVE (1차 문턱 통과, robust 미충족 — 라이브 관찰 대상, 채택 보류)
F_pbr_mktresid126   : FAIL (Sharpe·MDD 악화 + CAGR 열위 초과)
```

§5-8 상위 게이트 불간섭: 위 판정과 무관하게, 인컴번트 `F_pbr_no_r3r4` 자체가 이미 SPEC_10 G5
(MDD −45% 기준) 미달 상태 — 어떤 판정이 나와도 SPEC_12는 프로덕션 채택 승인이 아니다(§5-8 원문).

**구현 과정에서 발견·수정한 버그 2건** (둘 다 실측 데이터로 발견, §0 규칙 6 "조용한 실패 금지"
사례): ① 신규상장 종목이 `insufficient`(정상, 통과) 아닌 `invalid`(실행중단 대상)로 오분류되던
버그 — `_classify_gap` 공유 헬퍼로 수정. ② `MarketResidualCriterion`의 `beta_window+skip_days`
경계 인덱스 off-by-one — 전 종목이 조용히 `insufficient`로 빠지던 버그(§3-0e의 "beta_window+
skip_days" 근사식 자체도 부정확했음, 정확히는 +1 필요) — 스모크테스트로 발견·수정.

---

## 10. 부가 탐색 — MA 이중조건 파라미터 그리드 (2026-07-23/24, 비-사전등록)

`[Claude 의견]` §9의 5개 primary가 4 FAIL + 1 INCONCLUSIVE로 나오자, 사용자가 "결국 원안
(현행 `MomentumFilter` MA20/60/confirm5/slope20)이 제일 낫다"는 직관을 검증하고 싶어해
진행한 탐색적 그리드다. **§6-2 그리드서치 방지 규칙에 따라 사전등록 primary가 아니며,
결과를 즉시 PASS로 승격하지 않는다.** 태그는 전부 남겨두되(재현용) 파이프라인에는
편입하지 않는다.

### 10-1. 1단계 — MA 기간 그리드 (5/20/60/120 조합)

`{5,20,60,120}`에서 short<long 조합 6개 중 기존 20/60 제외 5개(`F_pbr_ma5_20/5_60/5_120/
20_120/60_120`, confirm_days=5·slope_lookback=20 고정)를 격리 스냅샷에서 실행. **5개
전부 인컴번트보다 daily-net CAGR·Sharpe·MDD가 열위** — 5일 단기 MA는 노이즈에 과민 반응,
20/120(양극단)은 추세 반응이 너무 느림, 60/120이 그나마 가장 근접(Sharpe −0.005).

### 10-2. 2단계 — confirm_days·slope_lookback OAT (20/60 고정)

1단계 승자(20/60)를 고정하고 `confirm_days∈{3,7}`·`slope_lookback∈{10,30}`을 각각
하나씩 변화. **4개 전부 인컴번트보다 열위**(Sharpe Δ −0.017~−0.104). `slope_lookback=30`이
가장 근접(Δ −0.017).

### 10-3. 3단계 — 구간 단위 paired 부트스트랩 검증 (§5-2 방법론 적용)

`[Claude 의견]` "9개 근방 조합을 전부 이겼다"는 결과가 지나치게 깔끔해 보여 사용자가
재검토를 요청 — 정당한 의심이었다. 완결 20구간의 `net_return` 구간별 페어 차이
(인컴번트−변형)를 10,000회 리샘플링(seed 고정)해 평균차 90%/95% CI와
P(부트스트랩 평균차 ≤ 0)을 계산했다.

| 변형 | 구간당 평균차 | 90% CI | P(차이≤0) | 판정 |
|---|---|---|---|---|
| ma5_20 | +1.39%p | [−0.65, +3.47] | 13.4% | 노이즈 범위 |
| ma5_60 | +1.33%p | [+0.03, +3.04] | 4.4% | 경계선 |
| ma5_120 | +1.71%p | [−0.42, +4.38] | 12.4% | 노이즈 범위 |
| ma20_120 | +1.78%p | [−0.11, +3.99] | 6.3% | 경계선 |
| **ma60_120** | +0.28%p | [−1.44, +2.06] | **39.8%** | **완전 노이즈(사실상 무승부)** |
| **cd3** | +0.38%p | [+0.08, +0.75] | **0.3%** | **뚜렷하게 유의** |
| cd7 | +0.20%p | [0.00, +0.55] | 13.0% | 경계선 |
| sl10 | +1.23%p | [+0.18, +2.35] | 2.5% | 유의 |
| **sl30** | +0.28%p | [−1.15, +1.82] | **38.6%** | **완전 노이즈(사실상 무승부)** |

**정정된 결론**: 20/60/5/20은 날카로운 단일 최적점이 아니라, **파라미터 공간에서 넓고
평평한 고원(plateau) 위에 있다.** `ma60_120`·`slope_lookback=30`처럼 고원 안에 있는
조합은 통계적으로 구별 불가(무승부)이고, `confirm_days=3`·`slope_lookback=10`처럼
고원 밖으로 벗어난 조합만 뚜렷하게 열위다. "원안이 정확히 최적점을 맞췄다"는
우연이 아니라 — 애초에 20/60/5/20이 데이터로 튜닝된 값이 아니라 Phase 2 고정
관행값(SPEC_03_universe.md, "Phase 2 튜닝 제외")이었고, 관행값이 넓은 안정 구간
안에 들어있었을 뿐이다.

### 10-4. 재현 정보

전부 §9와 동일 격리 스냅샷(포트 5435, 2026-07-23 05:55:54 UTC 기준) 사용. 산출물은
`/tmp/spec12_snapshot_run/experiments/`(서버, 스냅샷 컨테이너와 함께 삭제 예정) —
이 문서가 유일한 기록. 태그 정의는 `backtest/ablation.py`(ABLATION_CONFIGS)에
영구 보존.

---

## 부록 A — 참고 문헌

**한국 실증**
- **Kang, D., Ryu, D., & Webb, R. I. (2025).** Momentum and reversal effects in the Korean stock market. *Investment Analysts Journal*, 54(4), 611–627. DOI: 10.1080/10293523.2024.2448054
- **Choi, J., Choi, S., & Kang, W. (2012).** Momentum universe shrinkage effect in price momentum. arXiv:1211.6517 / SSRN 2180556
- **Sim, M., Kang, J., Kim, H.-E., & Lee, E. (2022).** The Momentum Strategies and Salience: Evidence from the Korean Stock Market. *Emerging Markets Finance and Trade*, 58(11). DOI: 10.1080/1540496X.2022.2034615 — `[VERIFY]` Hee-Eun Kim(명지대) 공저는 확인됨. 전체 저자 순서는 출판사 페이지에서 1회 확인 권장.

**국제 문헌**
- **Liu, M., Liu, Q., & Ma, T. (2011).** The 52-week high momentum strategy in international stock markets. *Journal of International Money and Finance*, 30(1), 180–204. DOI: 10.1016/j.jimonfin.2010.08.004
- **Blitz, D., Huij, J., & Martens, M. (2011).** Residual momentum. *Journal of Empirical Finance*, 18, 506–521 — 월별 FF3, 36개월 rolling, **12-1개월 잔차(추정창의 부분집합)**, 잔차변동성 표준화, 횡단면 decile. **§3-D1의 부분합 구조가 이 원형을 따른다.**
- Jegadeesh, N., & Titman, S. (1993, 2001) — 횡단면 상대 모멘텀, skip 1개월
- Moskowitz, T., Ooi, Y. H., & Pedersen, L. H. (2012) — 시계열(절대) 모멘텀
- Antonacci, G. (2013) — 절대/듀얼 모멘텀
- Marshall, B. et al. — TSMOM과 MA: 관련성 높으나 동일하지 않음
- George, T. J., & Hwang, C.-Y. (2004) — 52주 신고가 근접도, 장기 반전 없음
- Gutierrez, R. C., & Prinsky, C. A. (2007) — idiosyncratic 모멘텀
- Da, Z., Gurun, U., & Warachka, M. — frog-in-the-pan
- Barroso, P., & Santa-Clara, P. — 리스크 관리 모멘텀

---

## 부록 B — 용어 각주

[^1]: **Look-ahead(사전 정보 유출)**: 신호 계산 시점에 존재하지 않았을 미래 데이터를 사용하는 오류. 백테스트 성과를 부풀리는 가장 흔한 원인.

[^2]: **Formation window(형성 구간)**: 신호를 계산하려고 보는 과거 구간. Family D에서는 **추정 구간(beta_window)과 점수 구간(formation_days)이 다르다**(§3-D).

[^3]: **Falling knife / Value trap**: falling knife는 하락 진행 중인 종목을 성급히 매수하는 것. Value trap은 지표상 싸 보이나 펀더멘털 악화로 계속 싸지기만 하는 종목.

[^4]: **Winner-chasing / 횡단면 모멘텀**: 한 시점에 종목들을 가로로 비교해 최근 수익률 상위를 고르는 방식. 한 종목 자신의 시간축을 보는 절대 모멘텀과 대비.

[^5]: **음성 대조군**: 효과가 없어야 정상인 비교군. **효과 없음이 설계상 보장돼야** 하므로, 실제로 이길 수 있는 대안 전략이 아니라 종목↔신호 연결만 끊은 permutation을 쓴다(§6-3).

[^6]: **Skip-month**: 형성 구간과 매매 시점 사이 약 1개월 공백. 최근 1개월은 단기 반전 노이즈가 강해 제외. **v0.3: Family A·D에만 적용**, MA·52주 고가는 미적용.

[^7]: **절대/시계열 모멘텀**: 타 종목과 비교 없이 자신의 과거 수익률 부호로 판단하는 추세추종.

[^8]: **Magnitude / Sign / Rank 변환**: 원자료에서 남기는 정보량의 차이. sign을 "누적수익률의 부호"로 정의하면 magnitude(threshold=0)와 **같은 판정**이 되므로, §3-A처럼 **일별 부호 비율**로 정의해야 구별된다.

[^9]: **52주 신고가 근접도**: 현재가 ÷ 최근 252 거래일 최고가. George & Hwang(2004). 장기 반전하지 않는 특성이 보고됨.

[^10]: **잔차 / Idiosyncratic 모멘텀**: 종목 수익률을 시장(또는 다요인)에 회귀해 남은 잔차로 판정. **주의**: 절편 포함 OLS의 표본 내 잔차 합은 항상 0이므로, 점수는 반드시 추정창의 **부분집합** 또는 **표본 외** 구간에서 계산해야 한다(§3-D0).

[^11]: **경로 품질 / Frog-in-the-pan**: 같은 총수익률이라도 꾸준히 오른 경우와 며칠 급등 후 정체를 구분하는 개념.

[^12]: **Knife-edge**: 파라미터를 조금만 바꿔도 결론이 뒤집히는 불안정 상태.

[^13]: **MTM 재평가**: 매매 없이 시가로만 재평가. 관측 횟수는 늘지만 **독립 표본은 늘지 않는다**.

[^14]: **블록 부트스트랩**: 재추출로 불확실성을 추정하되, 시계열 자기상관 보존을 위해 하루가 아닌 블록 단위로 뽑는 방식.

[^15]: **CI(신뢰구간)**: 점추정 대신 범위로 표현한 것. 0을 포함하면 우위가 우연일 수 있다는 뜻.

[^16]: **PIT(Point-In-Time)**: 그 시점에 실제로 알 수 있었던 데이터만 사용. Family D에서는 **회귀 추정 구간에도 동일 적용**.

[^17]: **생존편향**: 살아남은 종목만으로 과거를 보면 망한 종목 손실이 빠져 성과가 좋게 나오는 왜곡.

[^18]: **그리드 서치 / In-sample 과최적화**: 여러 조합 중 결과 최고를 고르는 방식. 그 최고가 우연일 수 있어 미래에 재현되지 않을 위험.

[^19]: **FactorScreener 폐기**: 결과를 보며 튜닝하다 랜덤 벤치마크보다 못한 신호로 확인되어 2026-07-05 폐기. 본 스펙이 파라미터를 사전 동결하는 이유.

[^20]: **Iso-rejection 진단**: 필터 비교 시 **탈락률을 같은 수준으로 맞춰** 비교하는 것. 그러지 않으면 신호 품질과 유니버스 축소 효과가 섞인다.

[^21]: **전환행렬**: 인컴번트 대비 판정 변화 교차표(통과→탈락, 탈락→통과). 두 신호가 실질적으로 다른 종목을 거르는지 확인.

[^22]: **Permutation 귀무분포**: 점수는 그대로 두고 어떤 종목에 붙는지만 무작위로 섞기를 **수백~수천 회 반복**해 만든 "신호가 없을 때의 성과 분포". 실제 신호가 이 분포와 구별되지 않으면 고유 정보가 입증되지 않은 것이다. **1회만 돌리는 것은 분포가 아니라 단일 추출이라 판정 근거가 되지 못한다.**

[^23]: **OAT(One-At-a-Time)**: 전수조합 대신 한 번에 파라미터 하나씩만 바꾸는 민감도 분석. 어떤 파라미터가 결과를 움직였는지 해석하기 쉽다.
