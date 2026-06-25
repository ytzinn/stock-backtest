# SPEC_05 — Ablation Test & 성과측정 & Fitness & 튜닝 & 과최적화 방지

> **관련 파일**: `backtest/metrics.py`, `backtest/tuner.py`, `backtest/reports.py`
> **선행 조건**: SPEC_04 완료 (엔진 단일 실행 성공 확인)
> **Claude Code 지시**:
>   1. Ablation Test 13개 시나리오(A_random~H_no_stability)를 반드시 구현하라.
>      A/B/C/C_no_r6는 500회 반복 실행 + 전략 percentile 계산.
>   2. Random benchmark 500회는 `multiprocessing`으로 병렬 실행하라.
>   3. Walk-forward W6/W7은 Final Holdout이다. 튜닝 완료 후 1회만 열람하라.
>      코드 수준에서 W6/W7 결과를 튜닝 루프에서 참조하지 않도록 막아라.
>   4. 상장폐지 청산 3개 시나리오 결과를 CAGR 범위로 병기하라.

---

# 11. Ablation Test (Phase 2 필수 실행)

Phase 2에서 각 레이어의 독립적 Alpha 기여도를 분해하기 위해 13개 시나리오를 비교한다.
레이어를 하나씩 추가하는 누적 구조로 설계해 원인 분리를 명확히 한다.
`_no_r6` 변형은 R6(adjROE < r) 필터 없이 실행 → R6의 독립 기여도 측정.

**설계 원칙**: Hard Filter와 재무안정성 필터의 기여도를 독립적으로 측정하기 위해
두 레이어를 별도 시나리오로 분리한다. "Hard Filter만 통과한 랜덤"과 "Hard + Stability 통과한 랜덤"을
각각 두어야 재무안정성 필터 자체가 Alpha에 기여하는지 확인할 수 있다.

| 태그 | Hard Filter | 재무안정성(R6포함) | 팩터 스크리닝 | 모멘텀 | RIM 필터 | 비고 |
|------|------------|-----------------|-------------|--------|---------|------|
| A_random | ❌ | ❌ | ❌ | ❌ | ❌ | 전체 DQ PASS 종목 랜덤 |
| B_hard_random | ✅ | ❌ | ❌ | ❌ | 랜덤 N개 | Hard Filter 통과 종목 랜덤 |
| C_stability_random | ✅ | ✅ | ❌ | ❌ | 랜덤 N개 | Hard + Stability(R6포함) 통과 랜덤 |
| C_no_r6 | ✅ | ✅ (R6 제외) | ❌ | ❌ | 랜덤 N개 | R6 없는 랜덤 벤치마크 |
| D_rim_only | ✅ | ✅ | ❌ | ❌ | ✅ | RIM 단독 효과 측정 |
| D_no_r6 | ✅ | ✅ (R6 제외) | ❌ | ❌ | ✅ | R6 기여도 측정 |
| E_screener_rim | ✅ | ✅ | ✅ | ❌ | ✅ | 팩터 스크리닝 기여도 측정 |
| E_no_r6 | ✅ | ✅ (R6 제외) | ✅ | ❌ | ✅ | 팩터×R6 교호작용 |
| F_momentum_rim | ✅ | ✅ | ❌ | ✅ | ✅ | 모멘텀 기여도 측정 |
| F_no_r6 | ✅ | ✅ (R6 제외) | ❌ | ✅ | ✅ | 모멘텀×R6 교호작용 |
| G_full | ✅ | ✅ | ✅ | ✅ | ✅ | 전체 조합 |
| G_no_r6 | ✅ | ✅ (R6 제외) | ✅ | ✅ | ✅ | 전체 조합 R6 제외 |
| H_no_stability | ✅ | ❌ | ✅ | ✅ | ✅ | 재무안정성 필터 전체 제거 |

**Phase 2 Ablation 완료 결과 (2026-06-21):**

| 시나리오 | CAGR (순) | Alpha(KS) | Sharpe | MDD | 판정 |
|---------|---------|-----------|--------|-----|------|
| C_stability_random | 5.83%(중앙) / p95=10.91% | — | — | — | 벤치마크 |
| D_rim_only | 11.47% (10.55%) | -2.34% | 0.429 | -32.78% | ✅ D>C_p95 |
| E_screener_rim | 5.29% (4.39%) | -8.52% | 0.210 | -35.26% | ❌ E<D |
| **F_momentum_rim** | **14.09% (13.02%)** | +0.28% | **0.518** | **-28.06%** | ✅ F>D |
| G_full | 8.07% (7.02%) | -5.74% | 0.314 | -26.34% | ❌ G<D |
| H_no_stability | 9.45% (8.37%) | -4.36% | 0.335 | -40.63% | MDD↑ |
| KOSPI 벤치마크 | 13.81% | — | — | — | |

**Random benchmark 실행 방식 (A/B/C 공통):**
단일 시드 1회 실행은 통계적 의미가 없으므로, 랜덤 시나리오는 500회 반복 실행하여
성과 분포를 산출하고 전략이 몇 percentile에 해당하는지 표시한다.

```python
# backtest/engine.py

ABLATION_CONFIGS = {
    'A_random':            {'use_hard': False, 'use_stability': False, 'use_screener': False,
                            'use_momentum': False, 'use_rim_filter': False, 'random_n': 20},
    'B_hard_random':       {'use_hard': True,  'use_stability': False, 'use_screener': False,
                            'use_momentum': False, 'use_rim_filter': False, 'random_n': 20},
    'C_stability_random':  {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': False, 'use_rim_filter': False, 'random_n': 20},
    'D_rim_only':          {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': False, 'use_rim_filter': True},
    'E_screener_rim':      {'use_hard': True,  'use_stability': True,  'use_screener': True,
                            'use_momentum': False, 'use_rim_filter': True},
    'F_momentum_rim':      {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': True,  'use_rim_filter': True},
    'G_full':              {'use_hard': True,  'use_stability': True,  'use_screener': True,
                            'use_momentum': True,  'use_rim_filter': True},
}

RANDOM_TAGS    = frozenset({'A_random', 'B_hard_random', 'C_stability_random', 'C_no_r6'})
RANDOM_REPEATS = 500   # 랜덤 시나리오 반복 횟수

for tag, config in ABLATION_CONFIGS.items():
    if tag in RANDOM_TAGS:
        results = [run_backtest(config, ablation_tag=tag, seed=i) for i in range(RANDOM_REPEATS)]
        save_random_distribution(tag, results)   # 분포 저장 → experiments/ablation/{tag}_dist.csv
    else:
        run_backtest(config, ablation_tag=tag)   # 결과 → experiments/ablation/{tag}.csv


def report_strategy_percentile(strategy_cagr: float, random_tag: str) -> float:
    """
    전략 CAGR이 랜덤 분포의 몇 percentile인지 반환.
    보고서 예시:
      전략 CAGR: 14.2%
      C_stability_random 500회 중앙값: 8.1% / 95th percentile: 13.5%
      전략 percentile: 96.8%  ← 이 값이 90% 미만이면 전략 재검토 필요
    """
    dist = load_random_distribution(random_tag)
    return percentileofscore(dist['cagr'], strategy_cagr)
```

**Ablation Test 판정 기준:**
- C > B (p95 기준): 재무안정성 필터 자체가 Alpha에 기여함 (단순 종목 축소 이상의 효과 존재)
- D > C_p95: RIM이 랜덤 대비 Alpha를 냄 → RIM 유효성 확인 (**핵심 관문**)
- E > D: 팩터 스크리닝이 추가 Alpha를 냄 → 스크리닝 유지 확정
- F > D: 모멘텀이 추가 Alpha를 냄 → 모멘텀 유지 확정
- G ≈ E 또는 G ≈ F: 팩터 스크리닝과 모멘텀 중 하나가 중복 → 제거 검토
- D_no_r6 - D: R6 필터가 성과를 저해하는지 확인 (낮아야 R6 유효)
- H_no_stability MDD: 재무안정성 필터의 위험 관리 효과 (H MDD > F MDD이면 유효)
- **D의 CAGR이 C_stability_random 95th percentile 미만이면 RIM 효과 통계적으로 불충분**
- ablation_tag는 `backtest_runs` 테이블에 기록되어 run 간 비교 가능

**Phase 2 판정 결과 (2026-06-21):**
- ✅ D(11.47%) > C_p95(10.91%): RIM 통계적 유효
- ✅ F(14.09%) > D(11.47%): 모멘텀 유효
- ❌ E(5.29%) < D(11.47%): 팩터 스크리닝 성과 저해 → Phase 3 가중치 재조정 후 재검증
- ❌ C_median(5.83%) < B_p95(11.34%): 재무안정성 Alpha 기여 미미 (단, MDD 관리 기여)
- H MDD(-40.63%) >> F MDD(-28.06%): 재무안정성 필터 리스크 관리 효과 확인

---

# 12. 성과 측정 (`backtest/metrics.py`)

```python
metrics = {
    'cagr':              compute_cagr(returns),
    'sharpe':            compute_sharpe(returns, rf=0.0263),
    'alpha_kospi':       compute_alpha(returns, benchmark='KOSPI'),       # 배당 미반영 KOSPI 지수
    'alpha_kosdaq':      compute_alpha(returns, benchmark='KOSDAQ'),      # 배당 미반영 KOSDAQ 지수
    'alpha_vs_random':   compute_alpha_vs_random(returns, random_tag='C_stability_random'),
    'mdd':               compute_max_drawdown(nav_series),
    'turnover':          compute_turnover(portfolios),
    'robustness':        compute_robustness(returns_by_period),   # Alpha 양수 구간 비율
    'random_percentile': report_strategy_percentile(cagr, 'C_stability_random'),
}
```

**벤치마크 3개 정의:**

| 벤치마크 | 목적 | 비고 |
|---------|------|------|
| KOSPI | 대형주 시장 대비 Alpha | 배당 미반영, 지수 adj_close 기준 |
| KOSDAQ | 소형·성장주 시장 대비 Alpha | 배당 미반영. 전략 유니버스에 KOSDAQ 종목 포함 시 소형주 효과 분리 목적 |
| 유니버스 랜덤 (C_stability_random 500회 중앙값) | Hard + Stability 통과 종목 대비 종목 선택 Alpha | **가장 중요한 벤치마크.** 전략이 시장을 이긴 것인지, 유니버스 자체가 좋은 것인지 분리 |

**Alpha 해석 우선순위:**
KOSPI/KOSDAQ Alpha가 높아도 `alpha_vs_random`이 낮으면, 전략의 기여가 아니라 유니버스 필터의 기여일 수 있음.
종목 선택 알파를 증명하려면 `alpha_vs_random > 0`이 핵심 관문.

**성과 보고서 포맷 (리밸런싱 리포트):**
```
전략 CAGR: 14.2%  |  Sharpe: 1.24  |  MDD: -21.3%
Alpha vs KOSPI:    +9.1%
Alpha vs KOSDAQ:   +5.3%
Alpha vs 랜덤:     +6.8%  (C_stability_random 500회 중앙값 7.4% 대비)
전략 percentile:   96.8%  (C_stability_random 500회 분포 기준)
```

**성과 보고 시 명시 사항**: *"배당 미반영. adj_close(액면분할·무상증자 수정주가) 기준 수익률.
KOSPI/KOSDAQ 벤치마크도 동일하게 배당 미반영으로 통일하여 상대 비교 공정성 유지."*

**Robustness**: 21개 리밸런싱 구간 중 Alpha 양수 비율. 특정 연도 쏠림 없이 고를수록 높음.

---

# 13. Fitness Function

```python
fitness = (
    0.25 * metrics['cagr']       +
    0.20 * metrics['sharpe']     +
    0.25 * metrics['alpha']      -
    0.15 * abs(metrics['mdd'])   -
    0.05 * metrics['turnover']   +
    0.10 * metrics['robustness']
)
```

Fitness Function 가중치 자체는 튜닝 대상에서 제외 (순환 최적화 방지).

clamp 방식 (과최적화 방지):
```python
cagr_clamped   = max(-0.5, min(1.0,  metrics['cagr']))
sharpe_clamped = max(0.0,  min(3.0,  metrics['sharpe']))
mdd_clamped    = max(-1.0, min(0.0,  metrics['mdd']))
```

구체적 cap 값은 Phase 2 결과 확인 후 조정.

---

# 14. 자동 튜닝 머신 (`backtest/tuner.py`)

## 14-1. Phase별 튜닝 파라미터

**Phase 2 튜닝 대상 (총 4개):**

| 파라미터 | 초기값 | 범위 | 근거 |
|---------|--------|------|------|
| RIM β 보정 | 0.0 | [-0.02, +0.02] | 전체 할인율 수준 조정 |
| RIM 밸류에이션 필터 임계값 | 0.05 | [-0.10, +0.20] | 편입 기준 민감도 |
| 스크리닝 컷오프 비율 | 0.20 | [0.10, 0.40] | 후보군 크기 |
| 포트폴리오 종목 수 | 20 | [10, 30] | 분산 수준 |

**Phase 2 고정값 (튜닝 제외):**

| 파라미터 | 고정값 | 고정 이유 |
|---------|--------|----------|
| 모멘텀 short_window | 20 | 4개 동시 튜닝 시 과거 추세에 맞춘 필터로 전락. Phase 4 민감도 분석에서만 확인 |
| 모멘텀 long_window | 60 | 동일 |
| 모멘텀 confirm_days | 5 | 동일 |
| 모멘텀 slope_lookback | 20 | 동일 |
| 업종 집중 상한 | 25% | 리스크 관리 하드 룰. 알파 민감도 낮음 |
| 거래대금 최소 기준 | 1억원 | 유동성 하드 룰. 알파 민감도 낮음 |

**Phase 3+ 추가 튜닝 대상 (분류기 활성화 후):**

| 파라미터 | 초기값 | 범위 |
|---------|--------|------|
| 팩터 가중치 매출YoY | 1/6 | [0.0, 0.4] |
| 팩터 가중치 영업이익YoY | 1/6 | [0.0, 0.4] |
| 팩터 가중치 GP/A | 1/3 | [0.1, 0.5] |
| (1/PBR 가중치 = 1 - 나머지 3개 합) | 1/3 | — |
| Classifier STABLE 경계값 | 0.5 | [0.3, 0.7] |
| 메인/보조 모델 weight (w1) | 0.7 | [0.5, 0.9] (Phase 5) |

Phase 3에서 팩터 가중치 4개 추가 시, Phase 2 고정값(업종 집중 상한, 거래대금 기준 등) 중 민감도 낮은 항목을 하드 룰로 유지하여 총 튜닝 파라미터 수를 관리한다.

## 14-2. 튜닝 방식

```
1단계: 초기값으로 Ablation Test 전체 실행 (A~F)
          ↓
2단계: F_full 기준으로 Bayesian Optimization
       (optuna, n_trials=100, TPE sampler, 파라미터 4개)
          ↓
3단계: Rolling Walk-forward Validation
```

## 14-3. Rolling Walk-forward (v4.3 변경)

단순 IS/OOS 분할에서 Rolling Walk-forward로 변경.
W6/W7은 **Final Holdout**으로 지정 — 전략 파라미터 확정 전까지 결과를 열람하지 않는다.

| 윈도우 | Train (IS) | Test (OOS) | 용도 |
|--------|-----------|-----------|------|
| W1 | 2015~2018 (8개 구간) | 2019 (2개 구간) | 튜닝용 |
| W2 | 2016~2019 | 2020 | 튜닝용 |
| W3 | 2017~2020 | 2021 | 튜닝용 |
| W4 | 2018~2021 | 2022 | 튜닝용 |
| W5 | 2019~2022 | 2023 | 튜닝용 |
| **W6** | **2020~2023** | **2024** | **Final Holdout** |
| **W7** | **2021~2024** | **2025** | **Final Holdout** |

**튜닝 절차**:
1. W1~W5 (5개 윈도우)로 Bayesian Optimization 실행
2. OOS Alpha 양수 비율 5/5 이상 후보 전략 선정
3. 인간 검토 → 파라미터 확정
4. 확정 후 W6/W7 Final Holdout 평가 (1회만 허용)

**최종 평가 기준 (W1~W5 기준, 4개 동시 충족 시 채택):**

| 지표 | 기준 |
|------|------|
| 평균 OOS Alpha | > 0% |
| OOS Alpha 양수 비율 | W1~W5 중 4개 이상 |
| 최악 OOS MDD | > -40% |
| 파라미터 안정성 | 윈도우 간 최적 파라미터 분산 < 20% |

**Final Holdout 통과 기준**: W6/W7 OOS Alpha 모두 > 0%

---

# 15. Fitness Sensitivity Analysis (Phase 4)

```python
def run_fitness_sensitivity_analysis(base_params: dict):
    """
    각 가중치 항목을 ±0.05씩 변화시켜 최적 파라미터 안정성 측정.
    결과: experiments/sensitivity/fitness_sensitivity.csv
    """
    base_weights = {
        'cagr': 0.25, 'sharpe': 0.20, 'alpha': 0.25,
        'mdd': 0.15, 'turnover': 0.05, 'robustness': 0.10
    }
    ...
```

판정 기준:
- `params_stable=True` 비율 > 80%: Fitness Function 안정적 → 최종 확정
- 60~80%: 불안정 항목 가중치 재검토
- < 60%: Fitness Function 재설계 필요

---

# 16. Reasoning Log / XAI (`backtest/reports.py`)

```python
{
    "run_id": 101,
    "phase": "phase2_rim_only",
    "ablation_tag": "B_value_momentum",
    "change": "rim_threshold 0.05 → 0.10",
    "reason": "임계값 완화 시 편입 종목 수 +15%, Alpha 2.1%p 개선, MDD 변화 없음",
    "confidence": "HIGH"
}
```

리포트 포함 항목:
- 파라미터 변경 내용 및 근거
- 단계별 필터 탈락 종목 수 (Hard → 재무안정성 → 스크리닝 → 모멘텀 → 밸류에이션)
- Ablation Test A~G 성과 비교표 (누적 레이어별 Alpha 기여도, 랜덤 시나리오는 500회 분포 요약 포함)
- Walk-forward 윈도우별 OOS Alpha
- 모멘텀 필터 제외 종목 수 (구간별)
- 상장폐지 종목 청산 발생 건수 및 낙관/기준/보수 시나리오별 수익률 범위
- **공시 지연 제외 종목 수 (리밸런싱 기준일 기준 PIT 미충족)** ← v4.3 추가
- **공시 지연 제외 종목의 이후 1기 수익률 (제외하지 않았을 때 성과 추정)** ← v4.3 추가
- **공시 지연 제외가 Alpha에 기여했는지 별도 집계** ← v4.3 추가

---

# 17. 과최적화 방지 장치

- Phase 2 튜닝 파라미터 4개 상한 엄수 (모멘텀 파라미터 고정, 업종·거래대금 하드 룰 고정)
- 팩터 가중치는 Phase 2에서 동일가중 고정 (RIM baseline 오염 방지)
- Ablation Test로 성과 원인 분해 (A~G 7개 시나리오, 랜덤 시나리오 500회 반복)
- Rolling Walk-forward 7개 윈도우 (단일 IS/OOS보다 강건)
- OOS Alpha 양수 비율 5/7 미달 시 자동 폐기
- Fitness Function 가중치 튜닝 제외
- Phase 4 Sensitivity Analysis로 Fitness Function 안정성 검증
- 해석 불가 규칙 채택 금지

---

