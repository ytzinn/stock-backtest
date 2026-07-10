# SPEC_08 — 레짐 유닛 (Phase B: Signal → Tilt)

> **문서 성격**: Claude Code 자율 구현용 핸드오프 스펙
> **버전**: v0.3 (구현 전 코드베이스 대조 검토 + OOS 폴드/체결 지연 정밀도 확정)
> **선행 문서**: SPEC_07_regime.md (Phase A), `experiments/runs/2026.07.07._REGIME_PHASE_A.md`
> **대상 저장소**: `stock-backtest` (`/opt/stock-backtest/`, DB `localhost:5433`)
> **라벨링 관례**: `[검증된 사실]` · `[Claude 의견]` · `[확실하지 않은 사실]` · `[VERIFY]` · `[ASSUMPTION]`

---

## 개정 이력 (v0.2 → v0.3)

SPEC_07(Phase A)이 문서만 보고 구현했다가 실제 DB 대조에서 10건의 버그(룩어헤드·크래시 포함)가
나온 전례가 있어, 이번엔 **구현 착수 전** 코드베이스와 대조 검토했다. 반영 사항:

1. **Phase A 자산 재사용 확인**: `strategy_returns_monthly`에 필요한 9개 컬럼 전부 존재 확인.
   단, `analyze.py::load_monthly_returns()`는 그 중 일부만 SELECT하므로 Phase B는 자체 로더를
   작성한다(§4-1 신규). `mtm_monthly.py`의 `_nav_path()`/`_build_largecap_sleeve()`는 프라이빗이라
   **동작 변경 없이 공개 별칭만 추가**해 재사용한다(§0 규칙 1 해석 명확화, 아래 5번).
2. **OOS 폴드 설계 확정** (§6): B-0(고정정책)은 워밍업(36개월≈6구간) 이후 전체를 단일 OOS로
   취급 — 파라미터가 전부 사전고정이라 fold별 튜닝 자체가 없으므로 fold 분할이 무의미하다.
   **B-1(nested정책)만 실제 확장창(expanding window) fold**를 도입한다.
3. **체결 지연(signal_date≠execution_date) 정밀 반영 확정** (§3-1): 근사(기존 월말 수익률
   재사용) 대신 **정밀** — 매 신호일마다 다음 거래일을 조회해 실제 지연 구간 수익률을 가격에서
   재계산한다. 이를 위해 `nav_path()`(공개 별칭)의 `obs_dates`에 월말 대신 지연 반영
   execution_date 리스트를 넣는 방식으로 재사용한다(신규 로직 최소화).
4. **`indicators_run_id` 고정**: Phase B의 신호 소스는 검증된 base run
   **`ind_d937165660ed`**로 고정한다(§5 config_phaseB.py). 민감도 sweep run들은 신호 소스로 쓰지 않음.
5. **Phase A 파일 additive 변경 허용 범위 명확화** (§0 규칙 1): "수정 금지"는 "동작 변경 금지"로
   해석한다. 아래는 **동작 변경 없는 순수 추가**로 허용:
   - `mtm_monthly.py`: `_nav_path`→`nav_path`, `_build_largecap_sleeve`→`build_largecap_sleeve`,
     `_load_period_holdings`→`load_period_holdings` 공개 별칭 추가(기존 프라이빗 이름도 유지)
   - `data_access_regime.py`: 신규 함수 `next_trading_day(conn, after_date) -> date` 추가
6. **`schema_phaseB.sql` 헤더**: 처음부터 psycopg2 실행 우선으로 작성(SPEC_07에서 psql-우선
   코멘트가 CLAUDE.md 위반으로 지적된 전례 재발 방지).
7. **turnover/cost 모델**: 기존 엔진(`engine.py`, 티켓개수 기반 단일비용률)과 다른 방식임을
   명시 — 이 코드베이스에 전례 없는 **weight-delta(`|Δs_t|`) 기반 비대칭 비용**을 새로 구현한다.

---

## 개정 이력 (v0.1 → v0.2)

외부 리뷰(채택 13 / 부분채택 2 / 반려 0) + 2층 구조 반영:
- (3-1) 착수 전제에 **G4 PASS + G3의 D PASS/F FAIL** 명시
- (3-2) B-Gate를 **D/F 시나리오별 독립 판정**으로. 교차 부호 일관성은 구속조건에서 빼고 **참고 지표로 격하**
- (3-3) 옵션 A를 **방어형 회피 전략**으로 명확화 + 옵션 A·B **둘 다 그리드에 포함**
- (3-4) K/S_MIN **실효 범위 명시** + K 티어(0.075/0.15/0.25 → 실효 0.85/0.70/0.50)
- (3-5) **B-0 고정정책 OOS / B-1 nested정책 OOS 분리**
- (3-6) **signal lag**(signal_date ≠ execution_date) 명시
- (3-7) **overlay 빈도(monthly/quarterly/semiannual) 민감도** 승격
- (3-8) 대체 sleeve **연구용(largecap_cw)/실행용(KOSPI) 분리**
- (3-9) **turnover 분해**(overlay / base / alt), overlay만 incremental 차감
- (3-10) B-Gate에 **경제적 다지표 표** — 단 단일 임계값은 [ASSUMPTION] 약증거, 구속은 강건성에
- (3-11) **#22 기여도 비율** 정량 보고
- (3-12) D의 **size_mom 보조형(D_v2)을 exploratory**로
- (3-13) live forward **N=3 확정**
- (3-14) always_on 복제 게이트 **월별+반기누적 둘 다**
- (3-15) `overlay_returns` **컬럼 확장**
- **신규 R8**: 탐색 그리드는 넓게, **채택은 사전 고정 구속조건 통과만**(그리드 최댓값 줍기 금지) + 다중검정 경고

---

## 한눈에 보기 (TL;DR)

### 배경
[검증된 사실] Phase A에서 `value_spread`가 6개월 리드-랙 양의 계수로 소형 value 상대성과를 사전 예측함을 확인(D·F 모두 G1·G1b·G2·G2b·G4 PASS, 5개 민감도 강건). `size_mom_6m`은 **D는 G3 PASS, F는 FAIL**. 그러나 신호는 강하지 않고 **관계 강도의 절반 이상이 #22 한 구간에 의존**(G2b에서 #22 제외 시 계수 0.436→0.192).

### 이 신호의 정체
[Claude 의견] value_spread는 **dip-buying형 평균회귀 약신호**(스프레드 넓다=방금 value가 얻어맞았다=반등 베팅). 개별 사건 결과는 모른다. 느리고(3~6개월), 한 에피소드 의존적.

### 목적 & 방법
[Claude 의견] 약신호를 **파산하지 않게만** 쓴다. 연속·유계 tilt / 레버리지 금지 / 선별-tilt 이원 구조. **연산 여유가 있으니 조합을 넓게 다 돌려 지형을 보되(Layer 1), 채택은 그리드 최댓값이 아니라 사전 고정 구속조건 통과 여부로만(Layer 2).** "넓게 측정, 좁게 행동."

> [Claude 의견] 한 문장: **"다 재보되, 줍는 건 강건성 통과한 것만. 약신호는 약하게만."**

---

## 0. Claude Code 최우선 지시

1. Phase B는 기존 백테스트를 **변형**(overlay 추가)하나 **base 전략을 덮어쓰지 말 것** — 새 pipeline/config로 분리, `run_id`/`config_hash`로 구분.
2. Look-ahead 금지(`available_from <= t`). 정규화도 **t 이하 history만**. 신호 산출일과 체결일을 **분리**(§3-1).
3. §1 구속 규칙 R1~R8은 **협상 불가**. 어떤 그리드 결과 해석도 이를 무효화하지 못한다.
4. **그리드 최댓값을 채택 근거로 삼지 말 것**(R8). 채택은 §6 Layer 2 사전 고정 조건 통과만.
5. 배포는 git 워크플로우. scp 금지.

---

## 1. 구속 규칙 (협상 불가)

| # | 규칙 | 근거 |
|---|------|------|
| **R1** | tilt는 **연속·유계 함수만**. binary on/off·전량 스위치 금지 | 개별 사건 불확실 |
| **R2** | **Walk-forward OOS 통과 전 강한 배분 금지**. 그 전 보수 모드 강제 | in-sample ≠ tradability |
| **R3** | tilt 파라미터는 **전체표본 최적화 금지**. B-0 사전고정 → B-1 nested만 | tilt 함수 과적합 방지 |
| **R4** | 신호 정규화 **PIT**(확장창/롤링, 미래 미사용) | 룩어헤드 침투 경로 |
| **R5** | **F 파이프라인 tilt는 `value_spread` 단독**. size_mom 미사용 | F에서 G3 일관 FAIL |
| **R6** | Walk-forward에서 **#22 기여도 정량 분리**. ex-#22 부가가치 명시 | 관계가 #22에 절반 의존 |
| **R7** | **레버리지 금지**. 소형 value share `s_t ≤ 1.0`, 나머지는 대체 sleeve | 약신호+레버리지=파산 |
| **R8** | 탐색 그리드는 넓게 돌리되 **채택은 Layer 2 사전 고정 구속조건 통과만**. grid argmax 줍기 금지. N개 탐색 시 다중검정 할인·축별 강건성 우선 | 표본 ~21 + 조합 多 → 운으로 이기는 조합 필연 발생 |

---

## 2. 아키텍처 — 선별-tilt 이원 구조

```
[반기·상시 — 변경 없음]           [월별 overlay — Phase B 신규]
종목 선별 (RIM + 필터)            신호(t) → 정규화(PIT) → lag → share s_t
   └─ 소형 value sleeve ──────────────────┐
                                          ▼
   포트 = s_t·(소형 value) + (1−s_t)·(대체 sleeve)      ※ D/F 별도 평가
```

- 선별(반기)은 그대로. tilt(월별)만 "소형 value에 얼마 실을지" 조절.
- 대체 sleeve: 연구용 `largecap_cw` / 실행용 `KOSPI(KS11)` 둘 다 평가(3-8).

---

## 3. Tilt 함수 설계

### 3-1. 신호 정규화(PIT) + 실행 시차 (R4 + 3-6)

```
signal_date    = 월말 마지막 거래일 t   (value_spread(t): available_from ≤ t)
mean_t, std_t  = expanding_stats(v_0..v_t)   # t 이하 history만, WARMUP_M 이후 활성
z_t            = clamp((v_t − mean_t)/std_t, −Z_CAP, +Z_CAP)   # Z_CAP=2
execution_date = t 이후 첫 거래일           # ★ 같은 close 신호→같은 close 체결 금지
return_interval= execution_date → 다음 조절일(OVERLAY_FREQ에 따라 다음 월말+1 or 다음 분기/반기)
```

`[Claude 의견]` 확장창 z를 base, 롤링(예 60개월)을 민감도로. 어느 쪽도 **미래 미사용** 절대 조건.

`[검증된 사실 — v0.3 확정]` **체결 지연은 근사가 아니라 정밀 반영한다.** Phase A의
`strategy_returns_monthly`는 월말→월말 구간 수익률을 저장하지만, signal_date≠execution_date이므로
overlay 수익률은 **execution_date_t → execution_date_{t+1}** 구간으로 다시 계산해야 한다.

구현: `data_access_regime.py`에 신규 함수 `next_trading_day(conn, after_date) -> date`를 추가해
`execution_date = next_trading_day(conn, t)`를 구한다. 구간 수익률은 `mtm_monthly.py`의
`_nav_path()`를 **공개 별칭 `nav_path()`**로 노출해 그대로 재사용한다 — `obs_dates` 인자에
월말 리스트 대신 지연 반영 execution_date 리스트를 넣으면 동일한 고정수량+상폐haircut
로직이 그대로 맞는 구간 수익률을 낸다(§4-1 참조, 신규 로직 최소화). 대체 sleeve가 KOSPI면
`kospi_return(execution_date_t, execution_date_{t+1})`, largecap_cw면 `build_largecap_sleeve()`
(공개 별칭) + `nav_path()` 조합으로 동일 패턴.

### 3-2. share 매핑 — 옵션 A·B 둘 다 (R1·R7, 3-3)

```
s_t = clamp( s_neutral + K · z_t , S_MIN, S_MAX )    # S_MAX ≤ 1.0 (R7)
large_share = 1 − s_t
```

- **옵션 A — 방어형**: `s_neutral=1.0, S_MAX=1.0`. z 양수는 1.0에 막힘 → **하방으로만 tilt**.
  - 실제 동작: **저스프레드(불리) 구간 회피** — value가 성장 대비 비쌀 때 소형 value를 줄이고 대체 sleeve로.
  - `[Claude 의견]` **주의 두 가지**: ① Phase A가 실증한 우위는 사실 고스프레드(dip→반등) 쪽인데 옵션 A는 그 tail을 못 취한다 — **실증 edge의 절반을 의도적으로 포기**하는 설계다(R7상 dip을 레버리지로 사는 게 금지라 정당하나, 문서에 명시해 해석 혼선 방지). ② 저스프레드에서 대형주로 옮기는 것은 은근히 **대형주 모멘텀 추종** 성격 → 평가 시 감안.
  - 평가지표: CAGR만이 아니라 **MDD·변동성·대형주 쏠림 방어 효과**를 함께 본다.
- **옵션 B — 양방향**: `s_neutral<1.0`(예 0.8), `S_MAX=1.0`. 고스프레드에 소형 value를 100%까지↑, 저스프레드에 ↓.
  - 비용: 중립 시에도 대형주 상시 일부 보유 → **unconditional value premium 일부 포기**.

`[Claude 의견]` 둘 다 그리드에 넣어 지형을 본다(R8). 채택은 §6 Layer 2 통과 여부로만.

### 3-3. 실효 범위 (명목 ≠ 실효, 3-4)

| K | 실효 하한(옵션 A, Z_CAP=2) | 비고 |
|---|---|---|
| CONSERVATIVE_K=0.075 | s_t ≥ **0.85** | 보수 모드 기본 |
| STANDARD_K=0.15 | s_t ≥ **0.70** | 표준 |
| AGGRESSIVE_K=0.25 | s_t ≥ **0.50** | **OOS 통과 후만** |

`[검증된 사실]` `S_MIN=0.5`는 K=0.25에서만 실제로 걸리는 **극단 하드캡**이다. 명목 범위와 실효 범위를 리포트에 함께 표기.

### 3-4. 보수 모드 (R2)
```
if not WALKFORWARD_PASSED:
    K = CONSERVATIVE_K   # 실효 0.85~1.0 "종잇장 모드"
    옵션 B 비활성(옵션 A만)
```

---

## 4. DB / 코드 (신규, base 불변)

```
backtest/regime/
├── schema_phaseB.py    # DDL 실행기 (psycopg2 — psql 없음, schema_regime.py와 동일 패턴)
├── schema_phaseB.sql   # overlay_returns DDL
├── config_phaseB.py    # 그리드 축·파라미터·비용, indicators_run_id 고정(ind_d937165660ed)
├── tilt.py             # 신호 정규화(PIT)+lag+share 매핑 (§3)
├── overlay_engine.py   # always_on / tilt 모드 재조립, 옵션 A·B, run_id/config_hash
├── grid.py             # §6 Layer 1 탐색 그리드 실행
└── walkforward.py      # B-0 고정정책 / B-1 nested정책 OOS
tests/regime/test_tilt.py, test_walkforward.py, test_grid.py
```

### 4-1. Phase A 파일 additive 변경 (동작 무변경, §0 규칙 1 재해석)

`mtm_monthly.py`, `data_access_regime.py`는 이미 STEP A에서 검증된 로직을 담고 있어 Phase B가
동일 계산(고정수량 NAV, 상폐 haircut, 대형주 sleeve)을 다시 구현하면 SPEC_07 리뷰에서 지적된
"3중 중복" 문제가 재발한다. 아래 변경만 예외적으로 허용한다 — **기존 동작·시그니처는 그대로 두고
공개 이름만 추가**하는 것이라 "수정 금지" 원칙과 충돌하지 않는다:

- `mtm_monthly.py`: 파일 끝에 `nav_path = _nav_path`, `build_largecap_sleeve = _build_largecap_sleeve`,
  `load_period_holdings = _load_period_holdings`, `periods = _periods` 별칭 4개 추가(기존
  프라이빗 이름도 그대로 유지 — Phase A 자체 코드는 계속 프라이빗 이름을 쓰므로 완전히
  non-breaking). `periods`는 overlay_engine.py가 21개 구간 정의를 중복 구현하지 않도록 추가.
- `data_access_regime.py`: 신규 함수 `next_trading_day(conn, after_date: date) -> date` 추가
  (`SELECT MIN(date) FROM price_history WHERE date > %s`). 기존 함수는 무변경.

`overlay_engine.py`는 이 별칭들을 `from backtest.regime.mtm_monthly import nav_path, build_largecap_sleeve, load_period_holdings`로 가져와 §3-1의 지연 반영 execution_date 시퀀스를 `obs_dates`로 넘겨
재사용한다.

```sql
-- schema_phaseB.sql (기존 불변, 신규만)
-- 적용: venv/bin/python -m backtest.regime.schema_phaseB (psycopg2 스크립트로 실행 — 서버 PATH에 psql 없음)
CREATE TABLE IF NOT EXISTS overlay_returns (
    run_id          TEXT,
    config_hash     TEXT,
    scenario        TEXT,     -- D_rim_only | F_momentum_rim (PRIMARY만)
    variant         TEXT,     -- 'D_v1'(vs단독) | 'D_v2'(vs+size_mom) | 'F_v1'(vs단독)
    tilt_option     TEXT,     -- 'A_defensive' | 'B_two_sided'
    mode            TEXT,     -- 'always_on' | 'tilt' | 'tilt_conservative'
    normalization   TEXT,     -- 'expanding_z' | 'rolling_pct_60m'
    overlay_freq    TEXT,     -- 'monthly' | 'quarterly' | 'semiannual'
    alt_sleeve      TEXT,     -- 'largecap_cw' | 'kospi'
    signal_date     DATE,
    execution_date  DATE,     -- ★ signal_date ≠ execution_date (lag)
    period_start    DATE,
    period_end      DATE,
    date            DATE,
    s_t             DOUBLE PRECISION,
    z_t             DOUBLE PRECISION,
    size_mom_z      DOUBLE PRECISION,   -- D_v2 실험용
    port_return     DOUBLE PRECISION,   -- overlay 적용(gross)
    base_return     DOUBLE PRECISION,   -- always-on(비교군)
    alt_return      DOUBLE PRECISION,
    overlay_turnover DOUBLE PRECISION,  -- |Δs| (sleeve 이동)
    overlay_cost    DOUBLE PRECISION,   -- 2·|Δs|·leg_bps (비대칭)
    net_port_return DOUBLE PRECISION,   -- 비용 차감
    net_base_return DOUBLE PRECISION,
    is_oos          BOOLEAN,
    episode_tag     TEXT,               -- 'normal' | 'period22' | 'live_forward'
    PRIMARY KEY (run_id, config_hash, scenario, variant, tilt_option, mode, date)
);
```

`[검증된 사실 — 구현 중 발견·수정]` 원안 PK `(run_id, scenario, variant, tilt_option, mode, date)`는
`config_hash`가 빠져 있어 grid.py가 normalization/overlay_freq/alt_sleeve/K를 바꿔가며 도는
조합들을 구분하지 못하고 서로 조용히 덮어쓸 수 있었다(Phase A 리뷰에서 나온 config_hash
설계 취지와 동일한 함정). **`config_hash`를 PK에 추가**하고, `config_phaseB.py::config_hash()`가
전역 스칼라뿐 아니라 호출 시점의 그리드 조합 축(`K`, `NORMALIZATION`, `OVERLAY_FREQ`,
`ALT_SLEEVE`)도 키워드 인자로 받아 해시에 포함하도록 확장했다. `run_id`는 전체 Phase B
실행에 공통인 상수(`PHASEB_RUN_ID='phaseb_v1'`)로 두고, 조합 구분은 `config_hash`가 전담한다.

---

## 5. 파라미터 & 그리드 축 (config_phaseB.py)

```python
"""R3: 전체표본 최적화 금지. B-0 사전고정 → B-1 nested만."""
# 신호 소스 — v0.3 확정: 검증된 base run 고정, 민감도 sweep run은 신호 소스로 쓰지 않음
INDICATORS_RUN_ID  = "ind_d937165660ed"
MTM_RUN_ID         = "mtm_v1"
NORMALIZATION_GRID = ["expanding_z", "rolling_pct_60m"]
Z_CAP, WARMUP_M    = 2.0, 36
# tilt
TILT_OPTION_GRID   = ["A_defensive", "B_two_sided"]
S_NEUTRAL_A, S_NEUTRAL_B = 1.0, 0.8
S_MIN, S_MAX       = 0.5, 1.0            # R7: S_MAX ≤ 1.0
K_GRID             = [0.075, 0.15, 0.25] # 실효 0.85/0.70/0.50 (0.25는 OOS 통과 후만)
OVERLAY_FREQ_GRID  = ["monthly", "quarterly", "semiannual"]
# 시나리오/변형 (3-2, 3-12)
VARIANTS = {"D_rim_only": ["D_v1", "D_v2"],   # v2=+size_mom exploratory
            "F_momentum_rim": ["F_v1"]}       # R5: value_spread 단독만
# 대체 sleeve (3-8)
ALT_SLEEVE_GRID    = ["largecap_cw", "kospi"] # research / tradable
# 비용 (3-10 보강 — [Claude 의견] 소형주는 낙관 금지, 비대칭)
SMALL_LEG_BPS      = 50   # 소형 value 매매 (유동성 열위)
LARGE_LEG_BPS      = 10   # 대형/KOSPI 매매
# 라이브 (3-13)
LIVE_FORWARD_MIN_PERIODS = 3
```

`[Claude 의견]` `COST_BPS=30` 단일값(v0.1)은 소형주엔 낙관적 → **비대칭(SMALL 50 / LARGE 10)** 로 교체. `[VERIFY]` 실제 체결 환경으로 보정.

---

## 6. 검증 게이트 — 2층 구조 (R8)

### Layer 1 — 탐색 그리드 (넓게, 눈으로 본다)

`grid.py`가 §5 축의 전 조합을 **B-0 고정정책 OOS**로 돌려 표 하나에 전부 깐다:
- net CAGR / MDD / Sharpe / Calmar (비용 차감 후)
- always-on 대비 개선폭
- `ex22_alpha`, `period22_share` (R6)
- 조합 수 N 명시(다중검정 인지)

> `[Claude 의견]` Layer 1의 목적은 **지형 파악**이다. 어떤 축(옵션 A/B, 빈도, 정규화)이 결과를 실제로 움직이는지 본다. **여기서 최댓값을 뽑지 않는다.**

### Layer 2 — 졸업 후보 (좁힌다, 사전 고정 조건)

그리드를 **보기 전에 고정**한 구속조건을 통과한 것만 후보. D·F **별도 판정**(3-2):

```
[구속 조건 — 전부 충족해야 후보]
 C1. ex22_alpha > 0                          # #22 없이도 부가가치 (R6)
 C2. period22_share < 50%                    # 단일 에피소드 의존 아님 (R6)
 C3. 비용 차감 후 net 개선이 (B-0: 워밍업 이후 전/후 반기 부호 안정 / B-1: fold간 부호 안정)  # 특정 구간 의존 아님
 C4. always-on 대비 net 개선 (경제적, 아래 [ASSUMPTION] 참조)

[경제적 기준 — 다지표 표로 보고, C4의 판정 보조] (3-10)
 아래 중 하나 [ASSUMPTION, 약증거이므로 구속은 C1~C3에 둔다]:
  · net CAGR +0.5%p 이상, 또는
  · net CAGR −0.2%p 이내이며 MDD 2%p 이상 개선, 또는
  · net Sharpe/Calmar 개선 + #22 제외 후 방향 유지
 ★ #22 제외 시 net 개선이 음수로 뒤집히면 후보 불가(=C1)
```

> `[Claude 의견]` +0.5%p 같은 수치는 **[ASSUMPTION]이고 표본이 작아 약증거**다. 진짜 무게는 C1~C3(강건성)에 있다. "임계 통과=강한 증거"로 오해 금지.

**교차 부호 일관성(D·F 동시)**: v0.1의 구속조건에서 제외 → **참고 지표로만** 기록(3-2).

### B-0 / B-1 분리 (3-5, v0.3에서 폴드 경계 확정)

`[검증된 사실 — v0.3 확정]` 21개 구간뿐이라 진짜 fold 분할은 **B-1에서만** 의미가 있다:

- **B-0 Fixed-policy OOS**: K/범위/정규화 **전부 사전 고정 상수**(그리드에서 조합만 바꿀 뿐 데이터로
  튜닝하지 않음)이므로 fold별 최적화 자체가 없다 — **fold 분할 없이, 워밍업(WARMUP_M=36개월≈6구간)
  이후 전체를 단일 OOS 구간으로 취급**한다. Layer 1·2, C1~C4는 전부 이 워밍업 이후 구간 기준으로
  계산한다. C3("OOS fold 부호 안정")는 fold가 없으므로 **반기(period) 단위 부호 안정**(예: 워밍업
  이후 구간을 전/후 절반으로 나눠 둘 다 같은 부호인지)으로 재정의한다.
- **B-1 Nested-policy OOS**: B-0 통과 후에만. **확장창(expanding window) fold**를 도입 —
  fold i는 [워밍업 끝 ~ 구간 i]까지의 과거 데이터만으로 K∈{0.075,0.15,0.25} 중 최선(과거 fold
  net utility/MDD-adj 기준)을 선택해 **다음 구간(i+1)에만** 적용, 한 구간씩 굴려나간다(미래 fold
  절대 미참조 — R3).

### #22 기여도 보고 (3-11)
```
total_oos_alpha = Σ(net_port − net_base)  over OOS
period22_alpha  = 위 합의 #22 구간 부분
ex22_alpha      = total − period22
period22_share  = period22_alpha / total_oos_alpha
경고: share>50% → "single episode dependent"
차단: ex22_alpha ≤ 0 → 강한 배분 졸업 금지
```

### overlay 빈도 민감도 (3-7)
`value_spread`는 t+1 약(D t값 0.69) / t+6 강(2.12) = **3~6개월 신호**. 매월 조절은 신호보다 자주 움직여 비용·노이즈↑. **B-Gate 2에서 비용 차감 후 monthly가 약하면 quarterly 우선**.

### B-Gate 3 — 라이브 forward (3-13)
```
LIVE_FORWARD_MIN_PERIODS = 3
보수 모드 해제: B-0·비용 게이트 PASS + 라이브 3개 폐쇄 반기 중 2개↑ 개선
               (또는 3개 누적 net 개선 & MDD 악화 없음)
```

**졸업 분기:**
- Layer 2 통과(C1~C4) + B-Gate 2·3 → 보수 모드 해제, 정식 tilt(K↑ 허용). D/F 각각 판정.
- FAIL(특히 C1) → tilt 접고 **무신호 고정 분산**(대형·성장 상시 병행)으로 선회.

---

## 7. STEP 시퀀스

```
[STEP B-1] schema_phaseB.py/.sql + config_phaseB.py + mtm_monthly.py/data_access_regime.py
           additive 변경(nav_path/build_largecap_sleeve/load_period_holdings 별칭,
           next_trading_day 신규) — §4-1
[STEP B-2] tilt.py — 정규화(PIT)+lag+share. test_tilt: 룩어헤드/유계/lag 검증
[STEP B-3] overlay_engine.py — always_on / tilt 모드
   ★ 게이트(3-14): (1) always_on 월별 return == strategy_returns_monthly.port_return
                   (2) always_on 월별 누적곱 == 기존 반기 수익률 (105건 오차 0 재현)
                   불일치 시 tilt 계산 전 중단
[STEP B-4] grid.py — Layer 1 전 조합 B-0 고정정책 OOS. N 명시
[STEP B-5] walkforward.py — Layer 2 구속조건 판정(D/F 별도) + #22 기여도 + 비용
           (통과 시) B-1 nested OOS
[STEP B-6] 리포트 experiments/runs/..._REGIME_PHASE_B.md
   - Layer 1 지형표 / Layer 2 후보 / #22 기여도 / 빈도·sleeve·옵션 A·B 대비 / 다중검정 주석
[STEP B-7] (조건부) 라이브 forward 누적 추적
```

---

## 8. 리스크

1. **Asness 함정**: value-spread 타이밍이 "value에 더 베팅"과 겹쳐 순증분이 작을 수 있음 → 비용 차감 후 판정.
2. **#22 단일 의존**: R6+#22 기여도 보고로 강제 정량화. ex22_alpha≤0이면 접는다.
3. **느린 타이머**: 스프레드 다년 확대 시 tilt 헛돎 → 유계·보수로 손실 제한.
4. **다중검정(신규 R8)**: 조합 N개 탐색 → 운으로 이기는 게 반드시 나옴. argmax 채택 금지, 축별 강건성·사전 고정 조건으로만.
5. **비용 낙관**: 소형주 유동성 열위 → 비대칭 비용(SMALL 50/LARGE 10), 실제값 [VERIFY].
6. **옵션 A는 실증 edge의 절반만 취함**: 고스프레드 dip 쪽 미포착 + 저스프레드→대형주 이동의 모멘텀 추종 성격.
7. **표본 부족**: OOS 반기 소수 → t값류 강한 주장 금지, 부호·강건성 위주.

---

## 9. 테스트

```
tests/regime/test_tilt.py
  - z_t 정규화가 t 이후 데이터 미사용(PIT)
  - signal_date < execution_date (lag, 같은 close 체결 아님)
  - s_t 유계 [S_MIN,S_MAX], S_MAX≤1.0 (R1·R7); K별 실효 하한 = 1−K·Z_CAP
  - 보수 모드에서 K=CONSERVATIVE_K, 옵션 B 비활성 (R2)
tests/regime/test_grid.py
  - 각 조합이 config_hash로 분리 저장, 상호 미덮어씀 (R8)
  - N(조합 수)이 리포트에 기록됨
tests/regime/test_walkforward.py
  - B-0 고정정책: fold 최적화 없음 / B-1: 미래 fold 미참조 (R3)
  - #22 포함/제외 분기 실행 + period22_share·ex22_alpha 계산 (R6)
  - always_on 월별+반기누적 복제 게이트 (3-14)
  - overlay_cost가 비대칭 leg_bps로 차감 (3-9)
```

---

## 10. 한 줄 결론

Phase A가 확인한 `value_spread`는 **dip-buying형 약신호**다. 그래서 Phase B는 연속·유계 tilt로만, 레버리지 없이, D·F를 별도로 다룬다(F는 value_spread 단독). **연산 여유가 있으니 옵션 A·B·빈도·정규화·sleeve를 다 돌려 지형을 보되(Layer 1), 채택은 그리드 최댓값이 아니라 ex-#22 부가가치·비용 차감·fold 강건성이라는 사전 고정 조건 통과 여부로만(Layer 2) 한다.** 통과 못 하면 tilt를 접고 무신호 고정 분산으로 선회한다. **다 재보되, 줍는 건 강건성 통과한 것만.**
