# SPEC_05 부록 A — StabilityFilter 독립 검증 & 레이어 알파 분해

> **작성일**: 2026-07-05
> **관련 파일**: `backtest/filters/stability_filter.py`, `backtest/ablation.py`, `scripts/run_ablation.py`, `backtest/metrics.py`
> **선행 조건**: Phase 2 Ablation 완료(2026-07-02 기준), FactorScreener 폐기 반영(2026-07-05)
> **목적**: 현재 채택 파이프라인(Hard→Stability→Momentum→RIM)에서 StabilityFilter가
>   (a) 단독으로 알파/리스크에 기여하는지, (b) 레이어로서 순증 기여하는지, (c) R1~R6 중 어느 룰이
>   실제로 일하는지를 **오염 없는 대조군**으로 규명한다.
> **표기 규칙**: `[검증된 사실]` = 문서/코드로 확인 · `[Claude 의견]` = 해석·판단 · `[확실하지 않은 사실]` = 미확정

---

## 0. 한눈에 보기

- **[검증된 사실]** 현재 `H_no_stability`는 `use_stability=False` **+ `use_screener=True`** 설정이라,
  채택 파이프라인(F: stability on, screener off)에서 stability만 제거한 대조군이 **아니다**.
  screener는 2026-07-05 폐기됨(E vs D −5.7%p). → **F−H 차이는 stability 순수 기여로 해석 불가.**
- **[Claude 의견]** 기존 게이트 `C > B (p95 기준)`는 필터의 작동 메커니즘과 어긋난다.
  StabilityFilter는 상방(p95)이 아니라 **하방(p5)·median을 끌어올리는** 필터다
  (B→C: p5 +2.63%p, median +2.12%p, p95 −0.19%p). → 판정 지표 교체 필요.
- **[Claude 의견]** 필터 내부적으로 R6이 주력(랜덤 median +1.39%p), R1~R5는 하방 방어 중심
  (p5 +1.70%p, median +0.73%p). R1~R5 개별 기여는 **한 번도 격리 측정된 적 없음.**

---

## 1. 배경: 현재 관측된 증거 (2026-07-02 기준)

**[검증된 사실]** StabilityFilter 관련 시나리오 실측치:

| 시나리오 | 구성 | median/CAGR | p5 | p95 | MDD |
|---|---|---:|---:|---:|---:|
| B_hard_random | Hard | 4.68% | −1.18% | 12.13% | — |
| C_stability_random | Hard+Stab(R6 on) | 6.80% | +1.45% | 11.94% | — |
| C_no_r6 | Hard+Stab(R6 off) | 5.41% | +0.52% | 11.04% | — |
| D_rim_only | Hard+Stab+RIM | 11.99% | — | — | −33.9% |
| F_momentum_rim | Hard+Stab+Mom+RIM | 14.63% | — | — | −32.6% |
| H_no_stability | Hard+**Screener**+Mom+RIM | 11.81% | — | — | −37.7% |

**분해 (랜덤 유니버스 기준):**

| 변화 | median | p5 | p95 | 해석 |
|---|---:|---:|---:|---|
| B → C (Stability 전체) | +2.12%p | +2.63%p | −0.19%p | 하방 방어형 |
| C_no_r6 → C (R6 단독) | +1.39%p | +0.93%p | +0.90%p | 수익·하방 동시 |
| B → C_no_r6 (R1~R5 합계) | +0.73%p | +1.70%p | −1.09%p | 하방 방어 편중 |

> **[Claude 의견]** R1~R5는 median 기여는 작으나 p5(바닥)를 크게 들어올린다. 이는 "가치 함정 중
> 재무 파탄 종목을 선제 제거"라는 설계 의도(SPEC_03 §6-2)와 정확히 일치한다. 즉 이 필터의 가치는
> 평균 수익이 아니라 **좌측 꼬리 압축(테일 리스크 관리)**에서 나온다.

---

## 2. 문제 정의: 왜 새 시나리오가 필요한가

**[검증된 사실] 결함 1 — H_no_stability 오염**
`ablation.py`의 `H_no_stability = {use_hard:T, use_stability:F, use_screener:T, use_momentum:T, use_rim_filter:T}`.
채택 파이프라인 F는 `{..., use_stability:T, use_screener:F, ...}`. 두 축(stability, screener)이 동시에
다르므로 F−H는 교란(confounded)된다. screener 자체가 −5.7%p 손해를 주므로 F가 H보다 나은 것이
stability 덕인지 screener 부재 덕인지 분리 불가.

**[Claude 의견] 결함 2 — 판정 지표 불일치**
`run_ablation.py`의 게이트: `C_median > B_p95`. 한 분포의 중앙값을 다른 분포의 95분위와 비교하는 것은
통계적으로 이례적이고, StabilityFilter가 상방을 확장하지 않는 이상 구조적으로 실패한다. 필터의 실제
효과(하방·median 상승)를 측정하려면 median-median 비교 + 유의성 검정이 맞다.

**[검증된 사실] 결함 3 — R1~R5 개별 미측정**
`stability_filter.py`는 `use_r6` 플래그만 노출한다. R1~R5는 개별로 켜고 끌 수 없어, 어느 룰이
일하는지 알 수 없다. R6만 `_no_r6` 변형으로 격리돼 있다.

---

## 3. 검증 시나리오 설계

### 3-1. 신규 Ablation 시나리오 (결정론적 — 500회 반복 불필요)

`backtest/ablation.py`의 `ABLATION_CONFIGS`에 아래를 추가한다. 기존 13개 시나리오는 수정하지 않는다.

```python
# ── StabilityFilter 검증 시나리오 (부록 A) ─────────────────────────────────
# 레이어 알파 분해: 채택 파이프라인(F)·순수 RIM(D)에서 stability를 깨끗이 제거
'F_no_stability_clean': {'use_hard': True,  'use_stability': False, 'use_screener': False,
                         'use_momentum': True,  'use_rim_filter': True},
'D_no_stability':       {'use_hard': True,  'use_stability': False, 'use_screener': False,
                         'use_momentum': False, 'use_rim_filter': True},

# R1~R5 leave-one-out (R6은 기존 _no_r6로 이미 격리됨). 기준 파이프라인 = D_rim_only
'D_no_r1': {'use_hard': True, 'use_stability': True, 'use_screener': False,
            'use_momentum': False, 'use_rim_filter': True, 'stability_rules': {'R2','R3','R4','R5','R6'}},
'D_no_r2': {'use_hard': True, 'use_stability': True, 'use_screener': False,
            'use_momentum': False, 'use_rim_filter': True, 'stability_rules': {'R1','R3','R4','R5','R6'}},
'D_no_r3': {'use_hard': True, 'use_stability': True, 'use_screener': False,
            'use_momentum': False, 'use_rim_filter': True, 'stability_rules': {'R1','R2','R4','R5','R6'}},
'D_no_r4': {'use_hard': True, 'use_stability': True, 'use_screener': False,
            'use_momentum': False, 'use_rim_filter': True, 'stability_rules': {'R1','R2','R3','R5','R6'}},
'D_no_r5': {'use_hard': True, 'use_stability': True, 'use_screener': False,
            'use_momentum': False, 'use_rim_filter': True, 'stability_rules': {'R1','R2','R3','R4','R6'}},
```

**시나리오별 목적:**

| 태그 | 대비 대상 | 측정하는 것 |
|---|---|---|
| `F_no_stability_clean` | F_momentum_rim (14.63%) | **결정적 시나리오.** 채택 파이프라인에서 stability 순수 기여 (CAGR·MDD·Robustness) |
| `D_no_stability` | D_rim_only (11.99%) | 모멘텀 교란 없는 순수 RIM 위에서의 stability 한계 기여 |
| `D_no_r1`~`D_no_r5` | D_rim_only (11.99%) | 각 룰의 개별 기여 (leave-one-out). \|Δ\|이 작고 MDD도 개선 안 되면 가지치기 후보 |

> **[Claude 의견]** 랜덤(C 계열)이 아니라 결정론적(D/F 계열) leave-one-out을 쓰는 이유:
> 룰별 효과를 랜덤 노이즈 없이 단일 실행으로 분리하기 위함. 랜덤 500회는 이미 B/C로 확보돼 있어
> 단독 효과(§4-1)는 그것을 재해석하면 되고, 신규는 결정론적으로 충분하다. 총 신규 실행 7건 —
> 연산 비용 낮음.

### 3-2. 필요한 코드 변경 (`stability_filter.py`)

**[검증된 사실]** 현재 `StabilityFilter.__init__(self, r2_exception=True, use_r6=True)`.
`use_r6` bool을 `active_rules: set[str]`로 일반화한다. 하위 호환 유지.

```python
class StabilityFilter:
    _ALL_RULES = frozenset({'R1', 'R2', 'R3', 'R4', 'R5', 'R6'})

    def __init__(self, r2_exception: bool = True,
                 active_rules: set[str] | None = None,
                 use_r6: bool | None = None):
        self.r2_exception = r2_exception
        if active_rules is not None:
            self.active_rules = frozenset(active_rules)
        elif use_r6 is not None:                      # 하위 호환: 기존 _no_r6 경로
            self.active_rules = self._ALL_RULES if use_r6 else (self._ALL_RULES - {'R6'})
        else:
            self.active_rules = self._ALL_RULES

    # apply() 내부: _financial_stability_filter(..., active_rules=self.active_rules)
    # 각 룰 판정 앞에 `if 'R1' in active_rules:` 가드 추가
```

`ablation.py`의 파이프라인 조립부(`_build_pipeline`)에서:

```python
if config.get('use_stability', False):
    rules = config.get('stability_rules')          # 신규
    if rules is not None:
        filters.append(StabilityFilter(r2_exception=True, active_rules=rules))
    else:
        use_r6 = config.get('stability_r6', True)  # 기존 경로 유지
        filters.append(StabilityFilter(r2_exception=True, use_r6=use_r6))
```

> **[Claude 의견]** 기존 `stability_r6` 키를 쓰는 `C_no_r6`/`D_no_r6`/`E_no_r6`/`F_no_r6`/`G_no_r6`는
> `use_r6` 경로로 그대로 동작하므로 재실행 결과가 바뀌지 않아야 한다. 이걸 회귀 테스트로 확인할 것.

### 3-3. 판정 게이트 개편 (`metrics.py` / `run_ablation.py`)

**기존 게이트 폐기·교체:**

| 기존 (폐기) | 신규 | 근거 |
|---|---|---|
| `C_median > B_p95` | **G-1** median-median + 부트스트랩 95% CI | median-vs-p95는 상방 확장을 요구 → 필터 메커니즘과 불일치 |

**신규 게이트 정의:**

- **G-1 (단독 효과, 랜덤):** B_hard_random vs C_stability_random의 500-샘플 CAGR 배열에 대해
  - median 차이 + 부트스트랩 95% CI (또는 Mann-Whitney U 검정, α=0.05)
  - **부가 지표(1급):** p5 차이(바닥 상승), 랜덤 포트폴리오 MDD 분포 median 차이
  - **통과:** median 차이 CI가 0 초과 **또는** p5 차이 CI가 0 초과 (수익·하방 중 하나라도 유의)
- **G-2 (RIM 레이어 알파, 결정론적):** `D_rim_only − D_no_stability`
  - CAGR Δ, MDD Δ, Robustness Δ 병기
- **G-3 (채택 파이프라인 레이어 알파, 결정론적, 결정적 관문):** `F_momentum_rim − F_no_stability_clean`
  - CAGR Δ, **MDD Δ (1급), Robustness Δ** 병기
- **G-4 (룰별 기여, 결정론적):** `D_rim_only − D_no_rX` (X=1..5), `_no_r6`는 기존값 재사용
  - 각 룰의 CAGR Δ, MDD Δ

---

## 4. 실행 계획

```bash
# 0) 코드 변경 후 회귀 확인: 기존 _no_r6 시나리오 결과 불변 검증
python -m scripts.run_ablation --tags C_no_r6,D_no_r6 --check-regression

# 1) 신규 결정론적 시나리오 7건 (각 1회 실행)
python -m scripts.run_ablation --tags \
  F_no_stability_clean,D_no_stability,D_no_r1,D_no_r2,D_no_r3,D_no_r4,D_no_r5

# 2) 판정 게이트 재계산 (G-1은 기존 B/C 분포 재사용, 신규 검정만 추가)
python -m scripts.run_ablation --recompute-judgements --stability-audit

# 3) 결과 → experiments/runs/2026.07.XX. STABILITY_VALIDATION.md
```

> **[검증된 사실]** G-1은 이미 저장된 `experiments/ablation/B_hard_random_dist.csv`,
> `C_stability_random_dist.csv`(각 500회)를 재사용한다. 새 랜덤 실행 불필요.

---

## 5. 예상 결과와 의사결정 매트릭스

**[Claude 의견]** 기존 증거로 미리 세운 가설:

| 게이트 | 예상 | 근거 |
|---|---|---|
| G-1 median | 유의한 양수 (+2.1%p 근방) | B→C median +2.12%p |
| G-1 p5 | 강한 양수 (+2.6%p 근방) | B→C p5 +2.63%p |
| G-3 CAGR Δ | 소폭 양수~중립 (**불확실**) | F 대비 stability의 순수 CAGR 기여는 미측정. 모멘텀·RIM과 종목이 겹쳐 한계 기여가 작을 수 있음 |
| G-3 MDD Δ | 양수(개선) 유력 | stability의 하방 방어 시그니처 |
| G-4 | R6 > R1≈R4≈R5 > R2≈R3 (**불확실**) | R6 격리치(+1.39%p) 외 개별 미측정 |

**의사결정 매트릭스 (G-3 중심):**

| G-3 CAGR Δ | G-3 MDD Δ | 결정 |
|---|---|---|
| > 0 | 개선 | **stability 전체 유지** (알파·리스크 모두 기여) |
| ≈ 0 | 개선 | **유지 (리스크 정당화)** + G-4로 무효 룰 가지치기 |
| ≈ 0 | 악화/무변 | G-4 결과로 R6만 남기고 R1~R5 축소 검토 |
| < 0 | 악화 | **stability 재설계 또는 R6-only로 축소** |

> **[Claude 의견]** 핵심 판단 원칙: StabilityFilter는 Bayesian 튜닝 대상이 아닌 **경제적 하드 룰**이다.
> 재무 파탄 종목을 배제한다는 해석 가능성 자체가 OOS 강건성·과최적화 방지 가치를 지니므로,
> CAGR 기여가 미미하더라도 MDD·p5(바닥)가 개선되면 유지가 정당하다. **backtest CAGR만으로 제거를
> 결정하지 말 것** — 이는 프로젝트의 "데이터 검증 후 튜닝" 원칙과 동일한 보수주의 계열이다.

---

## 6. 선결 리스크 (반드시 병기)

> **[Claude 의견] 데이터 무결성 우선순위 충돌.** StabilityFilter는 정의상 부실·상장폐지 위험 종목을
> 걸러낸다. 따라서 이 필터의 측정된 가치(특히 하방 방어·MDD)는 **보유 종목의 상장폐지 이벤트가
> 올바르게 반영(70% haircut)됐는지에 민감**하다. 현재 미해결인 "상장폐지 zero-flag" 이슈(10년+ 소형주
> 보유에서 상폐 플래그 0건 — SQL 직접 검증 대기)가 살아 있는 상태에서 G-1~G-4를 확정하면,
> stability의 방어 효과가 **과소 또는 과대 추정될 수 있다.**
>
> **권고:** (1) 상장폐지 SQL 검증을 선행하거나, (2) 불가피하면 G-1~G-4를 **잠정(provisional)**으로
> 표기하고, 상폐 데이터 정정 후 재실행해 결과 변화를 대조한다.

---

## 7. 산출물

- `experiments/runs/2026.07.XX. STABILITY_VALIDATION.md` — G-1~G-4 결과표 + 의사결정 결론
- `backtest/ablation.py` — 신규 7개 시나리오 config (기존 13개 불변)
- `backtest/filters/stability_filter.py` — `active_rules` 일반화 (하위 호환)
- `backtest/metrics.py` / `scripts/run_ablation.py` — G-1 검정 로직 + 게이트 교체
- MASTER §3-6 / SPEC_05 §11 — 판정 게이트 개편 반영 (기존 `C>B (p95)` 게이트 폐기 기록)
