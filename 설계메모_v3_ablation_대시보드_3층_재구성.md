# 설계 확정본 v3 — Ablation 대시보드 3층 재구성

> **작성일**: 2026-08-14 (v3 — 저장소 실측 대조 반영)
> **선행본**: v2(2026-08-13)를 대체. 변경 내역은 §11.
> **검증**: 본 v3의 사실 주장은 작업 트리에서 **파일을 실제로 열어 세거나 계산해** 확인함 `[검증된 사실]`.
> **성격**: 구현 스펙. 신규 판정 아님 — 방대해진 프로젝트를 따라잡기 위한 도식화·기록.
> **표기**: `[검증된 사실]` / `[Claude 의견]` / `[확실하지 않은 사실]`
> **저장 위치**: `docs/wiki/` (예정. 현재는 저장소 루트).
>
> ⚠️ **이 문서는 2026-08-14 시점의 설계다 — 현행 상태가 아니다.** 이후 3개 세션에서
> 인벤토리가 **16축 → 17축**(`daily_nav` 신설)이 됐고, 여기 없는 기제가 셋 생겼다
> (주장 검증기 `dashboard/claims.py` · 사각지대 탐침 · 도달범위 래칫).
> **현행 상태와 남은 작업은 `docs/검토/NEXT_SESSION_DASHBOARD_3LAYER.md`** 를 보라.
> 이 문서는 왜 이렇게 설계했는지의 기록으로 유지한다.

---

## 0. 목적
새 결과 생성이 아니라 **이미 쌓인 결과를 따라잡기 위한 도식화·기록.** 판정 규율은 신규 실행 때
작동하는 것이지 만들어둔 걸 구경할 때가 아니다 — 이 구분이 자동 스캔 등 여러 결정을 가른다.

**단, v3에서 하나가 추가됐다.** 대조 과정에서 현 대시보드가 CANONICAL과 **다른 수익률을 표시 중**임이
드러났다(§4-1). 이건 "따라잡기"가 아니라 **오염 수리**다. 재구성보다 먼저 처리한다.

---

## 1. 현황 (저장소 대조 확인) `[검증된 사실]`

- **멀티페이지 Streamlit**: `dashboard/app.py`=Dev Health, `dashboard/pages/ablation.py`=성과.
  페이지 파일은 현재 **ablation.py 하나뿐**.
- **현행 채택 (`docs/CANONICAL.md` / `scripts/live/freeze_rebalance.py` 기준, 2026-08-12 재산출)**:
  - base tag `F_pbr_ma200` · **artifact key `F_pbr_ma200_n13`** · **n=13** · MA200 · 1/PBR 랭킹
  - 안정성 규칙 **{R1, R2, R5, R6}**
  - 구간 CAGR(gross) 20.3329% / net 18.6891% / **완결 구간 20개**
  - 일별 net CAGR 18.5525% / **일별 net MDD −58.12%** / 일별 net Sharpe 0.725
  - 게이트: **G1 PASS · G2 PASS · G5 FAIL** (일별 MDD −58.12% vs 한계 −45%)
- **산출물 규모 (실측)**:

  | 항목 | 개수 |
  |---|---|
  | `experiments/ablation/*.json` (holdings 제외) | 75 |
  | 그중 요약 파일 `summary`·`summary_A`·`summary_C` | 3 |
  | **실제 태그 성과 파일** | **72** |
  | 그중 일별 NAV 보유 (`daily_nav/summary.json`) | **14** |
  | 그중 `n_stocks` 필드 보유 | **4** (`F_pbr_ma200_n10/n12/n13/n20`) |
  | 현 대시보드가 화면에 노출하는 태그 (`ALL_TAGS`) | **13** |

  → 만들어 둔 72개 중 **13개만 보인다.** 재구성의 정량 근거.
- **데이터 스키마**: `ablation/{key}.json` 상위 필드 —
  `tag, run_at, seed, cagr, net_cagr, alpha, alpha_kosdaq, sharpe, net_sharpe, mdd,
  robustness, benchmark_cagr, kosdaq_cagr, avg_turnover, cagr_optimistic,
  cagr_conservative, n_periods` (+ 신규 4개만 `n_stocks`).
  `_periods.csv`(구간별 14컬럼) / `_holdings.json` / `_dist.csv`.
- **정본 소스 파일들**: `scripts/make_canonical.py::collect()`(freeze 상수를 AST로 읽음),
  `experiments/ARTIFACTS_MANIFEST.json`(대용량 산출물 git 미추적, 서버가 원본),
  `tests/baselines/SCENARIO_REGISTRY.json`.
- **포트**: 운영 8501 (헬스 스냅샷·스크린샷 근거). **단, systemd 유닛이 repo에 없어 Git만으론 검증 불가** — §10 참조.

---

## 2. 목표 — 3층
| 층 | 내용 | 현 상태 |
|---|---|---|
| main | 조건별 ablation 성과. 드롭다운 = 시리즈(변수 축) 선택 | 시리즈 ① 전용(13태그) → 재구성 |
| sub1 — 왜-지도 | 각 시리즈에서 어떤 methodology를 왜 택했나 (decision history) | 인라인 씨앗 → 추출·심화 |
| sub2 — 용어사전 | 공용 용어 | 운영하며 점진 추가 |

---

## 3. 아키텍처 — Catalog + 다대다 membership + ScenarioRef

**v1의 "파일→시리즈 분류"는 폐기.** 한 태그가 여러 시리즈의 baseline으로 재사용되므로
(`F_pbr_no_r3r4`=PBR룰·캘린더·랭킹분해의 공통 baseline; `D_rim_only`=레이어+LOO 양쪽)
태그→시리즈는 **1:N**이다. "분류"가 아니라 **catalog + membership**으로 간다.

```
[experiments/ · robustness/ · calendar_sens/ · runs/ · analysis/]
        │  scan — "무슨 산출물이 존재하는가?"만 답
        ▼
[ArtifactCatalog]   각 항목: path, artifact_key, exists_local, git_tracked, manifested, generated_at
        │  membership — "이 축으로 무엇을 비교하나?"
        ▼
[SERIES 등록 대장]  members[] = ScenarioRef 목록 (다대다 허용)
        ▼
[SeriesViewModel] → [A 제네릭 뷰 / B 진단 뷰] → Streamlit main
```

### 3-1. 식별자 분리 (필수) `[검증된 사실 — 코드에 이미 존재]`
`tag` 하나를 유일키로 쓰면 **깨진다.** `freeze_rebalance.py:145` 가 이미
`artifact_key = f'{tag}_n{N_STOCKS}'` 로 분리한다. 접미사 없는 `F_pbr_ma200` 은 n20이고
현행 채택은 `F_pbr_ma200_n13`.

> **대조 확인**: `F_pbr_ma200.json` 과 `F_pbr_ma200_n20.json` 의 `cagr·mdd·sharpe` 가
> **소수점 끝까지 동일**(0.1616777229420734 / −0.3897996042557925 / 0.552164686927748).
> 접미사 없는 파일 = n20 확정.

**이 분리를 안 해서 n=13 설정이 n=20 tape를 읽은 실사고가 있었다** (2026-08-12,
`freeze_rebalance.py:148-177` 주석에 경위 기록: manifest의 expected_turnover가
0.9231이어야 할 자리에 0.9500이 들어감. 에러는 나지 않았다).

```python
@dataclass
class ScenarioRef:
    base_tag: str        # "F_pbr_ma200"
    artifact_key: str    # "F_pbr_ma200_n13"  ← 파일 조회는 무조건 이걸로
    params: dict         # {"n_stocks": 13}
```

### 3-2. 태그 수집 = 파일 스캔 자동 (제외 규칙 필수) `[v3 수정]`
glob으로 존재하는 artifact를 catalog화. 시리즈 membership은 명명 규칙으로 후보를 채우되
**할당은 등록 대장이 소유**(수동 override 허용). 기록 목적이므로 자동 수집이 맞다.

**단, 그냥 훑으면 유령 태그가 생긴다.** `experiments/ablation/` 에는 여러 태그 결과를 묶은
요약 파일 `summary.json` · `summary_A.json` · `summary_C.json` 이 함께 있어, 이름만 보면
`summary` 라는 태그가 존재하는 것처럼 잡힌다. **스캐너에 제외 규칙을 넣는다.**

```python
EXCLUDE_STEMS = {'summary', 'summary_A', 'summary_C'}   # 태그가 아니라 묶음 요약
EXCLUDE_SUFFIX = ('_holdings', '_periods', '_dist')     # 부속 산출물
```

반대 방향도 하나 있다. **`F_pbr_no_r3r4_parent` 가 실재하는데 §5 인벤토리 어디에도 없다.**
무결성 검사 4번(목록에 없는 파일 경고)이 잡아줄 것이나, 미리 소속을 정해두는 편이 낫다. `[확인 요망]`

### 3-3. 구현 결과 (2026-08-14) `[완료]`

```
dashboard/artifacts.py   ScenarioRef · Artifact · ArtifactCatalog · build_catalog()
dashboard/series.py      Status · SeriesSpec · Series · SERIES(16축) · resolve() · unassigned()
dashboard/pages/series_explorer.py   main 층 (드롭다운 = 축 선택)
```

**카탈로그 76항목** = 파일 72 + **summary-only 4**. 후자가 설계에 없던 발견이다:
`A_random`·`B_hard_random`·`C_stability_random`·`C_no_r6` 는 500회 반복의 분포 집계라
**단일 실행 산출물 파일이 없고** `summary.json` 에만 있다. `source` 필드로 구분한다 —
뭉개면 "파일이 없다"와 "원래 파일로 존재하지 않는다"를 구별할 수 없다.

**멤버십 총합 93 / 실제 태그 76** — 다대다가 실제로 작동한다는 뜻이다
(`D_rim_only` 5축, `F_momentum_rim` 5축, `F_pbr_no_r3r4` 3축…). 미배정은
`F_pbr_no_r3r4_parent` **1개뿐**이고, 이는 위에서 예고한 바로 그 태그다.

**캘린더 변형은 `params` 로 분리했다.** `F_pbr_no_r3r4_A` 의 base_tag 는
`F_pbr_no_r3r4` + `params={'calendar':'A'}` 다. 단, **떼어낸 나머지가 실제
`ABLATION_CONFIGS` 키일 때만** 뗀다 — 이름 끝만 보고 자르면 `_A` 로 끝나는 멀쩡한
태그를 망가뜨린다. "잘랐더니 아는 config 가 나왔다"가 파싱이 옳았다는 증거다.

**검사 10개** (`tests/integrity/test_series_manifest.py`): 등록 대장 태그 실재 / base_tag ∈
ABLATION_CONFIGS / B형 경로 해석 / 미배정 warning / **다대다가 실제로 쓰이는지** /
캘린더 접미사 분리 / A형 멤버 ≥2 / 두 축의 키 중복 소유 금지 / 근거 문서 실재 / id 유일.

> **근거 문서 검사가 즉시 값을 했다.** 등록 대장을 쓰면서 SPEC 파일명 4개를 틀리게 적었다
> (`SPEC_05_ablation.md` → 실제 `SPEC_05_backtest.md` 등). 죽은 링크는 근거가 아니라
> 근거인 척이고, 이 저장소는 같은 실수를 이미 두 번 커밋한 적이 있다.

---

## 4. A형/B형 + 지표 SSOT + 동적 구간

| 유형 | 정의 | 데이터 | 렌더 |
|---|---|---|---|
| **A형** | 태그 성과 비교 | `ablation/{key}.json` + `_periods.csv` | 제네릭 뷰 |
| **B형** | 검정/진단 산출물 | `calendar_sens/*.json`, `runs/*_grid.csv`, `analysis/*.json`, `*.png` | 전용 요약 + 원본 링크 |

### 4-1. `[v3 격상 — 최우선]` 대시보드의 자체 재계산을 **삭제**한다

> v2는 이 문제를 "MDD·Sharpe 값이 두 가지니 어느 쪽이 정본인지 표시하자"(라벨링)로 적었다.
> **과소평가였다. 수익률부터 틀리고 있다.**

현 대시보드는 성과 JSON을 읽지 않고 구간별 CSV에서 지표를 **직접 다시 계산**한다
(`dashboard/pages/ablation.py:95` `compute_metrics_from_csv`). 그 계산을 재현한 결과:

| | CAGR | MDD | Sharpe | 구간 수 |
|---|---|---|---|---|
| **대시보드가 화면에 띄우는 값** | **18.4770%** | −34.1378% | 0.6307 | **21** |
| 성과 JSON `F_pbr_ma200_n13.json` | **20.3329%** | −34.1378% | 0.6670 | **20** |
| `docs/CANONICAL.md` 공식 수치 | **20.3329%** | (일별) −58.12% | (일별) 0.725 | **20** |

**CAGR이 1.86%p 어긋난다.** 원인 둘:

1. **구간을 세는 기준이 다르다.** 대시보드는 `n_gate > 0`(게이트 통과 종목 1개 이상)으로
   걸러 21구간을 잡는데, 공식 수치는 **완결 구간만** 세어 20이다. 아직 안 끝난 마지막 구간이
   섞여 들어간다. (CSV 원본은 23행. 23 → 21(n_gate) → 20(완결) 세 층이 서로 다르다.)
2. **연수 계산이 다르다.** 대시보드는 `years = 구간수 ÷ 2`. CLAUDE.md 규칙은
   "CAGR 연수는 **실제 캘린더 경과일수** 기준"이다.

**처방: 계산식을 고치는 게 아니라 계산을 없앤다.** 성과 JSON에 엔진이 산출한
`cagr / net_cagr / mdd / sharpe / n_periods` 가 이미 전부 들어 있다. 그대로 읽는다.
`_periods.csv` 는 **구간별 그래프 그릴 때만** 쓴다.

> **`summary.json` 이 이미 정답을 갖고 있었다.** 2026-08-12 생성분에 68개 태그의 엔진
> 산출값이 전부 들어 있고, 대시보드는 그걸 읽어놓고 **CSV 재계산으로 덮어쓰고 있었다.**
> 즉 고칠 것은 "새 데이터 연결"이 아니라 **"덮어쓰기 제거"** 였다.

#### 실제 오염 규모 (운영 서버 실측, 2026-08-14) `[검증된 사실]`

`_periods.csv` 는 git 미추적이라 개발 PC엔 10개뿐이고, 레거시 D~H 태그 CSV가 없어
**개발 PC에서는 폴백이 걸려 정상값이 뜬다.** CSV가 실재하는 **운영 서버(63개)에서만
발현**한다. 서버에서 옛 계산식을 재현한 결과:

| 태그 | 화면에 뜨던 값 | 산출물 실제값 | 차이 |
|---|---|---|---|
| D_rim_only | 7.11% | 9.54% | +2.43%p |
| E_screener_rim | 5.78% | 6.84% | +1.06%p |
| F_momentum_rim | 11.16% | 15.42% | +4.26%p |
| G_full | 7.61% | 7.80% | +0.20%p |
| H_no_stability | 10.21% | 14.72% | +4.50%p |
| D_no_r6 | 6.51% | 8.79% | +2.29%p |
| **E_no_r6** | **20.42%** | **9.76%** | **−10.66%p** |
| F_no_r6 | 10.29% | 14.99% | +4.69%p |
| G_no_r6 | 9.44% | 11.73% | +2.29%p |

**수치 오차로 끝나지 않았다. 판정 배지가 뒤집혀 있었다:**

- **`G > D (전체 기여)` 가 ✅ 로 표시**됐다 (7.61% > 7.11%). 산출물 기준 실제 판정은
  **❌** (7.80% < 9.54%). `summary.json` 의 엔진 판정도 `'G>D (전체 기여)': False` 다.
  **화면만 반대로 말하고 있었다.**
- R6 민감도 표의 E행은 `20.42 − 5.78 = +14.64%p` 로 "R6가 수익을 크게 갉아먹는다"고
  읽히지만, 실제는 `9.76 − 6.84 = +2.92%p` 다. **5배 과장.**

전형적인 silent corruption이다 — 에러 없음, 그럴듯한 숫자, 반대 결론.

### 4-2. `[v3 수정]` 함께 사라지는 CLAUDE.md 규칙 위반 2건

`dashboard/pages/ablation.py` 는 `backtest.metrics` 도 `backtest.configs.constants` 도
import하지 않고 전부 자체 구현한다. 영구 규칙 위반이다:

| 위반 | 위치 | 규칙 |
|---|---|---|
| 무위험수익률 `0.0263` 을 파일에 직접 적음 | `ablation.py:106` | "상수는 재선언 금지, `configs/constants.py` 에서 **import만**" |
| CAGR·Sharpe·MDD·robustness 산식 자체 구현 | `ablation.py:110-123` | "지표 산식은 `backtest/metrics.py` **단일 정의**, 복제 금지" |

§4-1의 처방(재계산 삭제)이 이 둘을 동시에 해소한다. **이건 편의 개선이 아니라 규칙 위반
해소다** — 우선순위에서 밀리면 안 된다.

### 4-3. `[v3 수정]` 구간 수는 "동적 계산"이 아니라 **artifact에서 읽는다**

> v2는 "`n_periods = len(valid_periods)` 로 동적"이라고 적었다. **그대로 하면 버그가 재생산된다.**
> `valid` 를 지금처럼 `n_gate > 0` 으로 정의하면 다시 21이 나온다.

- 구간 수는 **성과 JSON의 `n_periods` 를 읽는다.** 화면에서 세지 않는다.
- `periods_per_year` 하드코딩(=2)은 제거하되, 이는 **시리즈 파라미터로 빼는 게 아니라
  §4-1로 인해 쓸 일 자체가 없어진다.** (구간 막대그래프는 연율화가 필요 없다.)
- "21구간" 같은 고정 문자열 금지. `ablation.py:96` docstring에 실제로 `"(21구간 기준)"` 이
  박혀 있다.
- 축마다 구간 수가 다르다는 사실은 유효 (실측: 현행 n13=20 / 캘린더 A=41 / C=20).

### 4-4. `[v3 신설]` MDD·Sharpe의 기준은 **행이 아니라 열 단위로 고정**

CANONICAL은 "Sharpe·MDD의 SSOT는 일별 NAV(SPEC_13 §9-1)"라고 못박는다. 원칙은 옳다.
**문제는 커버리지다:**

```
태그 성과 파일   72개
일별 NAV 있음    14개
일별 NAV 없음    58개
```

v2 규칙("있으면 일별 NAV, 없으면 엔진 산술로 라벨")을 A형 비교표에 적용하면
**한 열 안에서 행마다 정의가 달라진다.** 두 기준의 차이는 라벨로 가릴 수준이 아니다:

```
F_pbr_ma200_n13  엔진 산술(구간 기준) : −34.14%
F_pbr_ma200_n13  일별 NAV 기준        : −58.12%     ← 24%p
```

열을 정렬하는 순간 순위가 뒤집힌다. 일별 NAV를 가진 태그만 유독 나빠 보이고, 없는 태그가
좋아 보인다. **사람 눈은 라벨보다 숫자 크기를 먼저 본다.**

| 지표 | 정본 | 화면 규칙 |
|---|---|---|
| CAGR(gross/net) · Alpha · Robustness · turnover | `ablation/{key}.json` | 그대로 표시 |
| **MDD · Sharpe (A형 비교표)** | **엔진 산술 단일 기준으로 전 행 통일** | 열 제목에 `(구간 기준)` 명시 |
| **MDD · Sharpe (단일 태그 상세 · 현행 배너)** | **일별 NAV** (`daily_nav/summary.json`) | 여기서만 노출 |
| 구간별 그래프 | `{key}_periods.csv` | 연율화 없이 원값 |

- 시리즈 멤버가 **전원** 일별 NAV를 보유할 때만 열 전체를 일별 기준으로 전환 가능.
- **행별 폴백은 금지.**

### 4-5. verdict = 문자열 유지 + 최소 구조화
```python
"status": {"code": "CLOSED_FAIL", "label": "FAIL — 캘린더 축 종결",
           "as_of": "2026-08-10", "source": "docs/설계/SPEC_13_....md"}
```
→ 후에 "종결만/탐색만/현행 연결만 보기" 필터가 쉬워진다.

### 4-6. B형은 확장 포인트만 미리
MVP는 "요약+원본 링크". 단 인터페이스에 `renderer="calendar_sensitivity"` 키를 두어
후에 `regime_overlay / calendar_bootstrap / time_overfit / live_decomposition` 전용 뷰를 붙일 수 있게.

---

## 5. 정본 인벤토리 — **16축** (+ 부록)

> 정본: 사용자 확인표(2026-08-13). A/B=§4.

| # | 형 | 시리즈 | 무엇을 바꿨나 | 대표 태그 | 출처 |
|---|---|---|---|---|---|
| 1 | A | 레이어 ablation | 필터 레이어 A~H | A/B/C_random, D_rim_only, E_screener_rim, F_momentum_rim, G_full, H_no_stability | SPEC_05 |
| 2 | A | R6 단독 (LOO) | R6 on/off | C/D/E/F/G_no_r6 | SPEC_05 |
| 3 | A | 안정성 룰 개별 (D 기준) | R1~R5 중 하나 제거 | D_no_r1…r5 | SPEC_05 부록A |
| 4 | A | 안정성 룰 조합 (F 기준) | R2·R3·R4 조합 | F_no_r2/r3/r4/r2r3/r2r4/r3r4/r2r3r4 | 2026-07-07 |
| 5 | A | 안정성 필터 전체 on/off | stability 통째 | F_no_stability_clean, D_no_stability, H_no_stability | SPEC_05 부록A |
| 6 | A | 모멘텀 기준 그리드 | MA·n·cd·sl·mktresid·52w·absret·signcount | F_pbr_ma5_20~ma300, 52w70/75/80, absret126, mktresid126, signcount126, ma_double_adapter (**23종 — 실측 확인**) | SPEC_12 |
| 7 | A | PBR-경로 안정성 룰 조합 | 1/PBR 랭킹 위 R룰 조합 | F_pbr_no_r3r4, _no_r1r2r3r4, _no_r1r3r4, _no_r2r3r4, _no_r3r4r5, _no_r3r4r6, _nostab, _r6only | SPEC_10/11 |
| 8 | A | 랭킹 신호 분리 | RIM vs PBR vs 팩터 | D_pbr_only, D_factor_only, D_pbr_no_r3r4 | SPEC_05/10 |
| 9 | A | 스크리너 단일 팩터 (폐기) | 단일 팩터 | E_rev/op/gpa/pbr_only | ARCHIVE |
| 10 | A | 채택 후보 대조군 | 귀무·EW 벤치 | C_pbr_path_random, U_pbr_path_ew | SPEC_10 |
| 11 | A | 캘린더 — 위상/빈도 | 안A(빈도, n_periods=41) vs 안C(위상, 20) vs 현행 반기(20) | F_pbr_no_r3r4_A/_C, U_pbr_path_ew_A/_C | SPEC_13 §7 (두 후보 FAIL, 축 종결) |
| 12 | B | 레짐/타이밍 오버레이 (Phase A/B) | Signal→Tilt 그리드 | runs/2026-07-1x_phaseB_*.csv, runs/*_REGIME_PHASE_*.md, *.png | SPEC_07/08 |
| 13 | B | 캘린더 민감도 (block-bootstrap) | 쌍대 bootstrap contrast | calendar_sens/stage_b.json, runs/…_CALENDAR_SENS_B.md | SPEC_14 Stage B |
| 14 | B | 캘린더 민감도 (시간분할 과적합) | 전/후반 순위 역전 (TIME_OVERFIT_CONFIRMED) | calendar_sens/{time_split,plateau,rank_stability,integrity_gates}.json | SPEC_14 Stage A |
| 15 | B | 성과 분해 / 라이브 전환 | 희생자·룰멤버십·선호스캔·dryrun | analysis/{momentum_decomposition,preferred_scan,rule_membership}.json, live/dryrun/manifest.yaml | SPEC_11 |
| **16** | **A** | **포트폴리오 종목 수(n_stocks) 민감도** | **종목 수 k** | **F_pbr_ma200_n10/n12/n13/n20** | **SPEC_10 / 현행 n=13** |

**#16 주의**: 접미사 없는 `F_pbr_ma200` = n20. 현행 채택은 n13. artifact_key로만 조회할 것.
(#6의 23종은 `F_pbr_ma*`·`52w`·`absret`·`mktresid`·`signcount` 27개에서 #16의 n변형 4개를 뺀 값.)

### 부록 — 재발행 이정표 (시리즈 아님 → 데이터 계보 배너)
`07.14 AUDIT_BEFORE_AFTER` → `07.17 FROZEN_SNAPSHOT` → `07.18 PIT_OFFICIAL` →
`07.30 TTM_REISSUE(61태그)` → **`08.12 현행 설정(n13) 재산출`**.

---

## 6. 현행 상태 배너 — `collect()` 공용화

수치를 산문에 박으면 낡는다. **대시보드는 CANONICAL.md를 파싱하지도, 로직을 복제하지도 않는다.**
이미 있는 `make_canonical.py::collect()`(freeze_rebalance.py의 `DEFAULT_TAG·N_STOCKS` 를
AST로 읽고 gate·daily_nav·manifest·hash까지 수집)를 **공용 모듈로 승격**해
`make_canonical` 과 대시보드가 **같은 수집 함수**를 소비한다.

```
backtest/canonical_state.py  (collect() / validate())
        ↑                         ↑
scripts/make_canonical.py    dashboard 배너
```

배너는 **두 블록**으로 분리 (하드코딩 금지, 전부 collect() 결과):
```
[현행 채택]  F_pbr_ma200 / n=13 / 재산출 2026-08-12 / G1 PASS·G2 PASS·G5 FAIL
[데이터 계보] 2026-07-18 PIT → 07-30 TTM 재발행 → 08-12 현행 재산출
```

### 6-1. 구현 결과 (2026-08-14) `[완료]`

```
backtest/canonical_state.py   collect() / check() / gate_verdicts() / material_stamps() / momentum_label()
        ↑                              ↑
scripts/make_canonical.py      dashboard/canonical_banner.py
   (마크다운 렌더러)                  (화면 렌더러)
```

- 재생성 결과가 **바이트 동일**함을 확인 (`make_canonical --check` 종료코드 0).
- 배너 실측값이 CANONICAL.md 와 전항목 일치: 20.33% / 18.69% / 20구간 / 18.55% /
  −58.12% / 0.725 / G1 PASS · G2 PASS · G5 FAIL.

**설계에서 두 가지를 바꿨다:**

1. **`[데이터 계보]` 블록을 "재료 산출 일자"로 대체.** `07-18 PIT → 07-30 TTM → 08-12`
   라는 계보 문자열은 **어떤 산출물에도 없다.** 배너에 넣으려면 결국 코드에 박아야 하고,
   그건 이 절이 없애려던 바로 그 병이다. 대신 `material_stamps()` 가 재료별 산출 일자
   (구간 지표·일별 지표·게이트·라이브 신호)를 읽어 표시한다 — 계보를 산문으로 주장하는
   대신 **재료가 스스로 말하게** 한다. 이정표 서술은 이 메모 §5 부록에만 남긴다.
2. **`check()` 결과를 배너가 숨기지 않는다.** 문서 쪽은 정합성 경고 시 종료 코드 1 로
   끝나는데, 화면만 조용히 정상인 척하면 **화면이 더 위험한 소비자**가 된다. 경고가
   있으면 성적보다 먼저 띄운다. 게이트는 `미산출`과 `FAIL` 을 색까지 달리해 구분한다.

**지키는 검사** (`tests/integrity/test_canonical_is_current.py`):
문서가 현재 산출물과 바이트 일치하는가 + 배너와 문서가 **같은 `collect` 객체**를
소비하는가(값 비교가 아니라 동일성 — 누가 사본을 만들면 걸린다).

---

## 7. 왜-지도 리치 포맷

포맷: 변수 / 답하는 질문 / 막는 실패 모드 / 판정·종결 상태 / **여기서 내려진 결정(decision history)** /
탐색 경고 / 내 이해(칸 쪼갬). **판정 재현이 아니라 "6개월 뒤 따라잡기"가 목적.**

### 예시 — 시리즈 ③ (안정성 룰 개별 LOO)

```
## 변수
R1~R5 중 하나를 끈다 (기준 파이프라인 = D_rim_only, RIM 경로).

## 답하는 질문
6개 안정성 룰 중 어느 것이 일하고 어느 것이 사족인가?

## 막는 실패 모드
"안정성 필터가 통째로 기여한다"는 뭉뚱그린 착각.

## 여기서 내려진 결정 (history — 이게 핵심)
· 당시(RIM 경로, phase2_rim.py): R2(R1과 중복)·R3(역효과) 제거 → {R1,R4,R5,R6}.
· 후속: RIM 랭킹 폐기 → PBR 경로 탐색 + 캘린더·시간 민감도 검증.
· **현행(PBR 경로, F_pbr_ma200_n13): {R1,R2,R5,R6}** ← CANONICAL/freeze 기준.
  ※ phase2_rim.py의 {R1,R4,R5,R6}는 폐기된 RIM 경로의 과거 결정. 현행과 "충돌"이 아니라 "다른 계보".

## 내 이해
| 세부 | 라벨 |
| LOO로 룰 기여 분리 논리 | [검증된 사실] |
| "사족" 판정 문턱(Δcagr) | [확실하지 않은 사실] ← 공부 대기열 |
| n≈20에서 룰별 Δ 유의성 | [확실하지 않은 사실] |
```

---

## 8. 무결성 검사 (구현 전 계약) `[v3 전면 재작성]`

### 8-0. 무결성 검사란 무엇인가

이 프로젝트의 기존 테스트 두 종류는 **계산이 맞는가**를 본다:

- `tests/characterization/` — "지금 코드가 이런 답을 낸다"를 기록. 버그를 고치면 정당하게 깨짐.
- `tests/oracle/` · `tests/integration/` — "이 답이 옳다"를 증명. 깨지면 수정이 틀린 것.

**무결성 검사는 다른 걸 본다: 파일과 목록이 서로 안 어긋나는가.**

새 대시보드는 "어떤 시리즈에 어떤 태그가 들어간다"는 **등록 대장**을 두고,
화면은 그 목록을 보고 `experiments/` 의 파일을 찾아 읽는다. 그러면 둘이 따로 놀 수 있다 —
목록엔 있는데 파일이 없거나, 파일 이름은 맞는데 안에 든 게 **다른 설정의 결과**이거나.
**계산은 전부 정상인데 화면만 거짓말하는 상태**가 된다.

무결성 검사는 `pytest` 로 도는 자동 검사이되 **전략 로직을 전혀 계산하지 않는다.** 목록을
한 줄씩 읽어 "이 경로 진짜 있나?", "이 파일 속 설정이 목록에 적힌 것과 같나?"만 확인하고,
어긋나면 실패시킨다. **지도와 실제 땅이 갈라지는 순간 빌드를 세우는 장치다.**

이 프로젝트에 특히 필요한 이유는 2026-08-12 사고다. `F_pbr_ma200` 이라는 이름 하나가
n=20 결과와 n=13 결과를 둘 다 가리킬 수 있었고, n=13으로 운영하면서 n=20 파일을 읽어
회전율이 92.31%여야 할 자리에 95.00%가 기록됐다. **아무 에러도 나지 않았다.**

### 8-1. 검사 목록

| # | 검사하는 것 | 막는 사고 |
|---|---|---|
| 1 | 목록에 적힌 모든 파일 경로가 실제로 존재하나 | 화면에 빈 칸이 뜨는데 이유를 모름 |
| 2 | 목록의 base_tag가 `ABLATION_CONFIGS` 에 실제로 정의돼 있나 | 오타난 태그가 조용히 무시됨 |
| **3** | **파일 이름의 종목 수와 파일 안의 종목 수가 같나** | **2026-08-12 사고 (아래 8-2)** |
| 4 | `experiments/` 에 있는데 목록엔 없는 파일이 있나 | 실험 결과를 만들어 놓고 잊어버림 (`F_pbr_no_r3r4_parent`) |
| 5 | 한 태그가 여러 시리즈에 들어가도 에러가 안 나나 | 구조가 다대다인데 코드가 1:1을 가정 |
| 6 | 캘린더 A안·C안 파일의 빈도 정보가 서로 맞나 | 분기 결과를 반기로 착각해 CAGR 왜곡 |
| 7 | **A형 비교표의 MDD·Sharpe 열에서 기준이 섞이지 않나** | §4-4. 일별(−58.12%)과 구간(−34.14%)이 한 열에 공존 |
| 8 | 전용 화면이 없는 B형 자료도 최소한 원본 링크로는 뜨나 | 자료가 화면에서 사라짐 |
| 9 | 왜-지도가 인용한 문서 경로가 실재하나 | 죽은 링크 |

### 8-2. `[v3 수정]` 3번 검사에는 **세 번째 경우**가 필요하다

3번이 하려는 일은 이것이다: 파일 이름이 `F_pbr_ma200_n13.json` 이면 **그 파일을 열어서**
안에 적힌 `n_stocks` 가 정말 13인지 대조한다. 다르면 실패.

**문제는 대조할 값이 대부분의 파일에 없다는 것이다** (실측):

```
n_stocks 필드 있음:  4개  (F_pbr_ma200_n10 / _n12 / _n13 / _n20)
n_stocks 필드 없음: 68개  (D_rim_only, F_pbr_no_r3r4, F_pbr_ma200, ...)
```

v2 문안대로 만들면 68개에서 "비교할 게 없으니 넘어감"으로 **조용히 통과**한다. 그런데
사고를 낸 파일이 바로 그 그룹의 `F_pbr_ma200.json`(접미사 없음 = 실제로는 n20)이었다.
**잡으려던 대상만 정확히 빠져나가는 검사**가 된다.

| 파일 안의 `n_stocks` | 검사 동작 |
|---|---|
| 있고, 이름의 n과 같다 | 통과 |
| 있고, 다르다 | **실패** (이름-내용 불일치) |
| **없다** | **"레거시 = n20으로 간주한다"를 명시적으로 단언하고, 해당 목록을 warning 출력** ← v2에 빠진 것 |

**근본 처방**: `run_ablation` 이 성과 JSON에 `n_stocks` 를 **항상** 기록한다. `[정정]` 이건
이미 커밋 `0dfa0b1`(2026-08-12)에서 들어가 있었다 — 그래서 그 이후 실행한 4개만 필드를
갖고 있는 것이다. 기존 68개는 재실행 없이는 못 채우니 warning 목록으로 관리한다.

### 8-3. `[신설 2026-08-14]` 검사 6번 — 캘린더 메타데이터

검사 6번은 **만들 수가 없었다.** "안 A 는 분기다"라는 사실이 태그 이름 `_A` 에만 있고
산출물 17개 필드 어디에도 캘린더가 없었다 — `n_stocks` 와 **똑같은 병**이다. 대조할
내용이 없으니 검사가 성립하지 않는다.

`run_ablation.calendar_metadata()` 가 실행이 **실제로 쓴 앵커**에서 파생해 기록한다.
`--calendar` 라벨이 아니라 앵커에서 뽑는다 — 라벨은 사람이 넘기는 값이라 틀릴 수 있지만
앵커는 엔진이 순회한 것이다. `report_types` 분포가 캘린더를 **내용으로** 식별한다:

| 캘린더 | 앵커 | report_types |
|---|---|---|
| SEMIANNUAL | 23 | FY 12, H1 11 |
| A (분기) | 46 | FY 12, H1 11, **Q1 12, Q3 11** |
| C (위상) | 23 | **Q1 12, Q3 11** |

캘린더가 섞인 실행은 `id` 를 `'+'` 로 이어 **드러낸다.** 섞인 앵커의 결과는 어느 캘린더의
성적도 아니므로 하나로 뭉개면 안 된다.

**기록 이전 76개는 어떻게 하나.** 전부 비어 있으므로, 기록분이 0개면 조용히 통과시키지
않고 경고한다(§8-2의 교훈). 그동안은 **구간 수로 교차 검증**한다 — 분기(안 A)는 반기보다
구간이 많아야 하고(41 > 20), 위상만 옮긴 안 C 는 같아야 한다(20 = 20). 필드가 없어도
성립하는 검사라, 재실행 전까지 이게 유일한 방어선이다.

---

## 9. 우선순위 `[v3 재정렬]`

**0순위 — 오염 수리 (지금 화면에 틀린 숫자가 떠 있다):**
- §4-1 `compute_metrics_from_csv` 삭제 → 성과 JSON 직접 읽기 (CAGR 1.86%p 오차 제거)
- §4-2 RF 재선언·산식 복제 제거 (CLAUDE.md 영구 규칙 위반 2건)

**MVP 필수 (correctness / 오염 방지):**
- §3-1 ScenarioRef(tag≠artifact_key) + Catalog·다대다 membership
- §3-2 스캐너 제외 규칙 (`summary*` 유령 태그)
- §4-3 구간 수는 artifact에서 읽기 (동적 재계산 금지)
- §4-4 MDD·Sharpe 기준 **열 단위 고정**, 행별 폴백 금지
- §5 #16 n_stocks 축
- §6 `collect()` 공용화 (현행 배너 동적)
- §8-2 무결성 검사 **3번 + 세 번째 경우** (최소 이것만이라도)

**선택 (지금 싸면 하고 아니면 나중):**
- §4-5 verdict status 구조화 · §4-6 B형 renderer 키
- §8-1 나머지 검사 · artifact provenance 전체 UI
- `run_ablation` 이 `n_stocks` 를 항상 기록하도록 변경
- systemd 유닛 repo 편입(§10)

---

## 10. 남은 확인 사항

> **해결됨 (2026-08-14)**
> - **`F_pbr_no_r3r4_parent`** — `rank_mode='pbr_parent'`. PBR 분모를 자본총계가 아니라
>   **지배기업소유주지분**으로 바꾼 변형이다(SPEC_11 §3). "부모 실행"이 아니라 **랭킹
>   신호를 바꾼 것**이라 `ranking_signal` 축에 배정했다. 미배정 0.
> - **`phase2_rim.py` 계보** — `active_rules={'R1','R4','R5','R6'}` 인 **별도 파일**임을
>   확인했다(`backtest/configs/phase2_rim.py:41`). 현행 PBR 경로 `{R1,R2,R5,R6}` 와
>   충돌이 아니라 다른 계보라는 §7 서술이 **확정**됐다.
> - **캘린더 메타데이터** — 아래 §8-3 참조. 검사 6번의 선행 조건이었던 스키마 변경 완료.
> - **레거시 페이지** — `ablation.py` 를 삭제하고 자산을 시리즈 탐색으로 흡수했다
>   (구간별·랜덤 분포 탭, 필터 설명은 레이어 축의 `notes`, 한글 라벨은 `series.LABELS`).
>   FDR 네트워크 조회는 통째로 뺐다 — `periods.csv` 에 `kosdaq_return` 이 이미 있다.

- **46/23 ↔ 41/20**: 설계표는 캘린더 안A=46·안C=23인데 실제 JSON은 `n_periods=41·20`.
  "계획 슬롯 vs 유효 구간"으로 보이나 미확정 `[확실하지 않은 사실]`. → §4-3(artifact에서 읽기)로
  화면은 해소되지만, 머릿속 모델(46/23)과 산출물(41/20)의 어긋남 자체를 한 번 정합시켜 둘 것.
- **23 → 21 → 20 세 층**: 구간 CSV 23행 / `n_gate>0` 21 / 완결 20. 이 세 숫자의 정의를
  용어사전(sub2)에 못박아야 §4-1 같은 혼동이 재발하지 않는다. `[v3 신설]`
- **`F_pbr_no_r3r4_parent`**: 인벤토리 미등재. 어느 시리즈 소속인지 확정 필요. `[확인 요망]`
- **phase2_rim.py 계보**: RIM 경로(폐기) config가 현행 PBR 경로와 별도인지 확인. 별도라면 §7의
  "충돌 아님·다른 계보" 서술이 확정됨. `[확인 요망]`
- **systemd 유닛**: repo에 없음 → `deploy/backtest-dashboard.service` 편입 시 "문서 8502·운영 8501" 재발 방지.
- **B형 전용 뷰 범위** / **캘린더 sub-axis 화면 표현**(탭 vs 하위 드롭다운).

---

## 11. 변경 이력

### v2 → v3 (저장소 실측 대조)

| 항목 | v2 | v3 |
|---|---|---|
| **지표 처리** | "정본 표시(라벨링)" | **재계산 자체를 삭제.** 대시보드 CAGR 18.4770% vs 공식 20.3329% — **1.86%p 오염 발견**, 0순위로 격상 |
| **규칙 위반** | 언급 없음 | **RF 재선언(`ablation.py:106`)·산식 복제(`:110-123`)** = CLAUDE.md 영구 규칙 2건 위반 명시 |
| **구간 수** | `len(valid_periods)` 동적 계산 | **artifact의 `n_periods` 를 읽음.** v2 문안은 `n_gate>0`=21을 재생산해 버그 유지 |
| **MDD/Sharpe 기준** | 있으면 일별 NAV, 없으면 라벨 | **열 단위 고정.** 일별 NAV 보유 14/72뿐 — 행별 폴백 시 한 열에 −34.14%와 −58.12% 공존 |
| **무결성 검사 3번** | "artifact_key n == JSON n" | **필드 부재(68/72)를 세 번째 경우로 명시.** v2 문안은 사고 낸 파일들에서 공허하게 통과 |
| **파일 스캔** | glob 자동 수집 | **`summary*` 제외 규칙.** 유령 태그 3개 발생 확인 |
| **무결성 검사** | 목록만 제시 | **"무엇인가"를 §8-0에 정의** (기존 테스트 2종과의 차이) |
| **산출물 규모** | 미기재 | 태그 72 / 일별 NAV 14 / `n_stocks` 필드 4 / 화면 노출 13 **실측 기재** |

### v1 → v2 (외부 리뷰 반영)

| 항목 | v1 | v2 |
|---|---|---|
| 현행 기준선 | "07-30 재발행, 61태그" (낡음) | 08-12 / F_pbr_ma200_n13 / {R1,R2,R5,R6}, collect() 동적 |
| 식별자 | `tags: [...]` | ScenarioRef(base_tag, artifact_key, params) |
| 스캔 | 파일→시리즈 분류 | Catalog + 다대다 membership |
| A형 지표 | period CSV 재계산(반기 하드코딩) | 지표별 SSOT 표 + 동적 n_periods |
| 인벤토리 | 15축 | 16축 (n_stocks 민감도 추가) |
| 배너 | 문자열 하드코딩 | make_canonical.collect() 공용 모듈 |
| 왜-지도 예시 | {R1,R4,R5,R6} (낡음) | {R1,R2,R5,R6} + decision history + 계보 주석 |
| verdict | 문자열 | 문자열 + code/as_of/source |
| 테스트 | 언급 없음 | 무결성 테스트 절 신설 |

> **v3의 교훈**: v2의 사실 주장은 "저장소를 clone해 대조"한 것이었지만 **파일을 열어 세거나
> 계산해 보지는 않았다.** 그래서 이름과 구조는 맞췄어도 ① 대시보드가 실제로 뱉는 숫자,
> ② 필드·파일의 실제 개수를 놓쳤다. **구조 대조와 값 대조는 다른 작업이다.**
