# SPEC_08 부속 — STEP B-0.5: 타이밍 vs 탈집중 대조 실험

> **문서 성격**: Claude Code 자율 구현용 핸드오프 스펙 (SPEC_08의 부속 실험)
> **버전**: v0.1
> **선행**: SPEC_08_regime_phaseB.md, `experiments/runs/2026-07-10_phaseB.md` (Layer 1·2 완료)
> **대상 저장소**: `stock-backtest` (`/opt/stock-backtest/`, DB `localhost:5433`)
> **브랜치**: `phase-b-signal-tilt` 이어서 (master 미병합 유지)
> **라벨링**: `[검증된 사실]` · `[Claude 의견]` · `[확실하지 않은 사실]` · `[VERIFY]` · `[ASSUMPTION]`

---

## 0. 왜 이 실험이 필요한가 (한 문단)

[검증된 사실] Phase B Layer 2에서 68/144 조합이 C1~C4를 통과했고, `ex22_alpha > 0`(#22 제외해도 부가가치)도 D 74% / F 78%가 통과했다. **그러나 그 알파는 전부 `always_on`(소형 value 100%) 대비로만 측정됐다.**

[Claude 의견] 문제: tilt는 소형 비중을 줄이므로 **평균 노출이 100% 미만**이다. 그리고 #22·#23은 대형주(반도체)가 소형을 압도한 구간이라, **그 시기에 소형 집중을 줄인 것은 무엇이든 이득**이었다. 따라서 현재 알파에는 두 성분이 섞여 있다:

```
현재 측정된 알파 = [타이밍 효과]  +  [탈집중 효과(평균 노출을 낮춘 것 자체)]
```

**이 둘을 분리하지 않으면 "레짐 타이밍이 작동한다"고 말할 수 없다.** 분리 방법은 **평균 노출이 동일하고 타이밍만 없는 상수 배분**을 대조군으로 두는 것이다.

> `[Claude 의견]` 반대 논증도 성립함을 명시해 둔다: 평상시 소형 value의 기대수익 > 대체 sleeve이므로, **아무 때나** 소형을 덜 들면 오히려 손해여야 한다. 그렇다면 이득의 존재 자체가 타이밍의 증거다. **이 논증은 #22·#23 구간에서만 깨진다**(그 구간엔 대체 sleeve가 소형을 이겼으므로). 그래서 **#22 제외 후 상수 대조 비교**가 결정적 판정이 된다.

---

## 1. 이 실험이 하는 일 / 안 하는 일

**한다:**
- 각 tilt 조합의 **평균 노출 `s̄`와 동일한 상수 배분** 시계열을 생성
- `timing_alpha`(= tilt − 상수) 및 **`timing_alpha_ex22`** 계산
- (가) 타이밍 / (나) 탈집중 판정표 산출

**안 한다:**
- **grid.py 전체 재실행 금지.** 새 백테스트를 돌리지 않는다.
- 기존 `overlay_returns` 행 수정/덮어쓰기 금지 (읽기 전용)
- 새 신호·새 파라미터 탐색 (R8 준수: 이건 채택 실험이 아니라 **분해(decomposition) 실험**)

---

## 2. 핵심 통찰 — 재계산이 아니라 재조합

[검증된 사실] 필요한 재료가 **이미 `overlay_returns`에 전부 있다**:
- `base_return` — 소형 value sleeve 수익 (s=1.0에 해당)
- `alt_return` — 대체 sleeve 수익
- `s_t` — 해당 시점 tilt가 적용한 소형 비중
- `episode_tag` / `period_start` — #22 구간 식별
- `net_port_return`, `overlay_cost` — 비용 차감값

따라서 상수 배분 수익은 **새 백테스트가 아니라 기존 열의 가중평균**이다:

```
const_return_t(s̄) = s̄ · base_return_t + (1 − s̄) · alt_return_t
```

`[Claude 의견]` 그래서 이 실험은 무거운 재실행이 아니라 **후처리 스크립트 수준(수 초)**이다. 원 그리드가 1분 8초였으니 비용은 무시 가능.

---

## 3. 산식 정의

### 3-1. 조합별 평균 노출 `s̄`

각 조합 key = `(scenario, variant, tilt_option, normalization, overlay_freq, alt_sleeve, K)`.

```
s̄ = mean(s_t)  over 해당 조합의 mode='tilt' 행 전체 (전 구간)
```

`[ASSUMPTION]` 단순 산술평균(시간가중). `[VERIFY]` `s_t`가 관측 간격이 불균등(monthly/quarterly/semiannual)하므로, **기간 길이 가중평균**이 더 정확하다면 그쪽을 쓰고 리포트에 표기. 두 정의 차이가 크면 둘 다 보고.

### 3-2. 상수 배분 대조군 (비용 포함)

```
const_return_t   = s̄ · base_return_t + (1 − s̄) · alt_return_t
const_cost_t     = 0    # 상수 배분은 sleeve 간 이동이 없음 → overlay turnover 0
                        # [Claude 의견] 엄밀히는 sleeve 내부 드리프트 리밸런싱 비용이 있으나,
                        #   tilt에도 동일하게 존재하므로 비교에서 상쇄. 0으로 둔다.
net_const_return_t = const_return_t
```

`[Claude 의견]` **이 비대칭이 대조군을 보수적으로(=tilt에 유리하게) 만든다** — tilt는 회전비용을 물고 상수는 안 문다. 그럼에도 tilt가 못 이기면 결론은 더욱 확고하다. 이 점을 리포트에 명시.

### 3-3. 타이밍 알파 (핵심 산출물)

```
timing_alpha       = Σ_t ( net_port_return_t − net_const_return_t )        # 전 구간
timing_alpha_ex22  = Σ_{t ∉ #22} ( net_port_return_t − net_const_return_t ) # ★ 결정적 판정
```

보조 지표(전/ex22 각각):
```
net_cagr_tilt, net_cagr_const, cagr_gap = net_cagr_tilt − net_cagr_const
mdd_tilt, mdd_const, mdd_gap
sharpe_tilt, sharpe_const
timing_share = timing_alpha / (net_port_cum − net_alwayson_cum)
   # 기존 always_on 대비 알파 중 '타이밍'이 설명하는 비율.
   # 나머지가 '탈집중' 몫. 분모≈0이면 NULL 처리.
```

### 3-4. 분해 항등식 (검증용)

```
[always_on 대비 알파]  =  [탈집중 효과]              +  [타이밍 효과]
 (tilt − always_on)    =  (const(s̄) − always_on)     +  (tilt − const(s̄))
```
`[검증된 사실 수준]` 이 항등식은 정의상 항상 성립한다. **테스트에서 잔차 ≈ 0 확인**할 것(부동소수 오차 내). 성립 안 하면 구현 오류.

---

## 4. 판정 기준 (사전 고정 — 결과 보기 전 확정)

D / F **각각 별도 판정**(SPEC_08 3-2 원칙 유지). Layer 2 통과 68개 조합을 주 대상으로 하되, 전 216개 조합에 대해서도 계산해 지형을 본다.

| 판정 | 조건 | 해석 |
|---|---|---|
| **(가) 타이밍 실재** | `timing_alpha_ex22 > 0` **이면서** OOS fold 부호 안정 | #22 없이도 타이밍이 상수 대비 우위 → 신호가 진짜 |
| **(나) 탈집중** | `timing_alpha_ex22 ≤ 0` (전체 `timing_alpha`는 양수여도) | 이득이 #22 구간에 소형을 덜 든 데서 옴 → 신호는 우연 |
| **경계** | `timing_alpha_ex22 > 0`이나 조합 소수·부호 불안정 | 판정 보류, 라이브 검증으로 |

**핵심 요약 지표**: Layer 2 통과 조합 중 `timing_alpha_ex22 > 0`인 비율 (D / F 각각).

`[Claude 의견]` R8(다중검정) 유지: **argmax 조합을 채택 근거로 삼지 말 것.** 보는 것은 **"통과 비율"과 "축별 일관성"**이다. 예: `overlay_freq=semiannual`에서 timing_alpha_ex22가 대부분 ≤0이면, "semiannual이 최고였던 건 사실상 상수 배분에 가까웠기 때문"이라는 해석이 가능하다 — **이 확인이 이 실험의 백미**다.

---

## 5. 구현

### 파일
```
backtest/regime/decompose.py     # 신규 (읽기 전용 후처리)
tests/regime/test_decompose.py   # 신규
```
기존 모듈 수정 금지. `overlay_returns`는 **SELECT만**.

### 신규 테이블
```sql
-- schema_decompose.sql
CREATE TABLE IF NOT EXISTS timing_decomposition (
    run_id             TEXT,
    source_run_id      TEXT,      -- 참조한 phaseb run (phaseb_v1)
    scenario           TEXT,
    variant            TEXT,
    tilt_option        TEXT,
    normalization      TEXT,
    overlay_freq       TEXT,
    alt_sleeve         TEXT,
    k                  DOUBLE PRECISION,
    s_bar              DOUBLE PRECISION,   -- 평균 노출
    s_bar_method       TEXT,               -- 'simple' | 'duration_weighted'
    passed_layer2      BOOLEAN,            -- 기존 C1~C4 통과 여부
    -- 전 구간
    timing_alpha       DOUBLE PRECISION,
    deconc_alpha       DOUBLE PRECISION,   -- const(s̄) − always_on (탈집중 몫)
    total_alpha        DOUBLE PRECISION,   -- tilt − always_on (검증: ≈ timing+deconc)
    timing_share       DOUBLE PRECISION,
    -- ★ #22 제외
    timing_alpha_ex22  DOUBLE PRECISION,
    deconc_alpha_ex22  DOUBLE PRECISION,
    -- 보조
    net_cagr_tilt      DOUBLE PRECISION,
    net_cagr_const     DOUBLE PRECISION,
    mdd_tilt           DOUBLE PRECISION,
    mdd_const          DOUBLE PRECISION,
    verdict            TEXT,               -- 'timing' | 'deconcentration' | 'boundary'
    PRIMARY KEY (run_id, scenario, variant, tilt_option, normalization,
                 overlay_freq, alt_sleeve, k)
);
```

### 실행
```
[STEP B-0.5-1] schema_decompose.sql 적용
[STEP B-0.5-2] decompose.py
    - overlay_returns에서 mode='tilt' 조합별 s_t, base_return, alt_return, episode_tag 로드
    - s̄ 산출 → const_return 시계열 생성 → timing/deconc alpha (전구간 · ex22)
    - 216개 전 조합 upsert. Layer 2 통과 68개는 passed_layer2=true 태깅
    ★ 게이트: 분해 항등식 잔차 ≈ 0 (§3-4). 실패 시 중단.
[STEP B-0.5-3] 리포트 experiments/runs/YYYY-MM-DD_phaseB_decompose.md
```

### 리포트 필수 내용
1. **판정 요약**: Layer 2 통과 조합 중 `timing_alpha_ex22 > 0` 비율 (D / F)
2. **분해 표**: total = deconc + timing (전구간 / ex22 각각), 항등식 잔차
3. **축별 분석**: `overlay_freq`·`normalization`별 `timing_alpha_ex22` 부호 분포
   → 특히 **semiannual이 사실상 상수 배분에 가까운지** 확인
4. **s̄ 분포**: 조합별 평균 노출이 실제로 얼마나 1.0보다 낮았는지 (탈집중 폭 정량화)
5. **최종 판정**: (가)/(나)/경계 — 라벨링 관례 적용

---

## 6. 테스트

```
tests/regime/test_decompose.py
  - 분해 항등식: total_alpha ≈ deconc_alpha + timing_alpha (잔차 ~0)
  - s̄ 계산이 mode='tilt' 행만 사용 (always_on 행 오염 없음)
  - const_return이 s̄·base + (1−s̄)·alt 와 정확히 일치
  - ex22 필터가 #22 구간 행만 제외 (episode_tag/period_start 기준)
  - overlay_returns를 SELECT만 하고 UPDATE/DELETE 하지 않음
  - s̄ 두 정의(simple / duration_weighted) 모두 산출되고 차이가 리포트됨
```

---

## 7. 결과에 따른 분기 (사전 합의)

[Claude 의견]

- **(가) 타이밍 실재로 판정** → SPEC_08의 B-1(nested OOS) + B-Gate 3(라이브)로 진행. 이때 비로소 "레짐 타이밍이 작동한다"는 첫 실질 증거.
- **(나) 탈집중으로 판정** → **tilt를 접는다.** 대신 **"소형 집중을 상시 낮춘 무신호 고정 배분"**(예: 소형 s̄ / 대형 1−s̄ 고정)을 정식 후보로 승격해 별도 검증. 이것도 완결된 유효 결론이다 — 원래 게이트 철학("작동 안 하면 과감히 접는다")에 부합하며, 단순하고 견고한 고정 배분이 더 나은 답일 수 있다.
- **경계** → 판정 보류. 강한 배분 금지(보수 모드 유지), 라이브 검증 대기.

---

## 8. 한 줄 결론

이 실험은 **새 백테스트가 아니라 기존 결과의 분해**다. 현재 알파에 섞여 있는 **[타이밍]과 [탈집중]을 갈라내고, #22를 제외한 뒤에도 타이밍이 남는지** 본다. 남으면 신호가 진짜고, 안 남으면 우리는 애초에 "소형 집중을 조금 낮추고 싶었을" 뿐이다. **둘 다 프로젝트의 진전이며, 후자여도 실패가 아니다.**
