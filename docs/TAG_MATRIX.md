# 태그 조건 매트릭스

> **생성물이다. 손으로 고치지 마라.** `scripts/make_tag_matrix.py` 가 만든다.
> 값은 플래그를 다시 해석한 것이 아니라 `build_ablation_pipeline` 으로 **실제
> 조립한 파이프라인**에서 읽는다 — 해석 규칙의 단일 정의가 그 함수이기 때문이다.

성과 수치는 없다. 이 문서는 **코드의 순수 함수**이고, 수치의 권위는
`docs/CANONICAL.md` 와 산출물에 있다.

## 읽는 법

- **안정성 룰** — 실제로 적용되는 룰 집합. `stability_r6`·`stability_rules` 를
  해석한 결과다.
- **밸류에이션 컷** — RIM 적정가 대비 고평가 종목을 빼는 단계. 랭킹을 1/PBR 로
  바꾸면 **함께 사라진다** — 두 조건이 한 몸이라 "랭킹만의 효과"를 잴 수 없다.
- **모멘텀** — `✓` 가 아니라 **판정 기준**을 적는다. 기준이 다르면 통과하는
  종목이 달라 **유니버스가 다르기** 때문이다. `✓` 로 뭉갰을 때 짝 대조군 열이
  레거시 `MA 20/60` 풀을 MA200·52주·절대수익 태그의 짝이라고 불렀다.
  파라미터가 같으면 클래스가 달라도 같은 이름이다 — `F_pbr_ma_double_adapter` 는
  레거시와 같은 산식을 부르고 gross·net 이 소수점 6자리까지 같다.
- **종목 수** — n 은 태그가 아니라 **실행이 정하는 값**이라 표기가 두 갈래다.
  - `{K} (기록)` — 산출물이 자기 안에 적어 둔 값. 종목 수를 독립변수로 쓸어 본
    행들(`F_pbr_ma200_n10`·`_n12`·`_n13`·`_n20`)이 이것이다. **이름이 아니라
    내용을 읽는다** — 이름과 내용이 어긋난 사고가 이미 있었다.
  - `산출물 키 참조` — 태그로는 안 정해진다. `run_ablation --n-stocks` 가 정해
    산출물 키의 `_n{K}` 접미사와 `n_stocks` 필드에 남는다. 접미사가 없으면
    기본값이다. 현행 채택안이 태그 `F_pbr_ma200` · 산출물 **`F_pbr_ma200_n13`
    (n=13)** 인 것이 그 예이고, 이 구별이 사라져서 2026-08-12 에 n=13 운영이
    n=20 산출물을 읽었다.
  - `고정` — 태그가 값을 박아 둔 것 (무작위 추첨의 `random_n`).
  - `상한 없음` — 필터 통과 **전 종목**을 담는다 (랭킹이 없으므로 상한도 없다).
- **캘린더** — 리밸런싱 앵커. 파생 키를 행으로 올리면서 함께 만든 열이다 —
  없으면 `F_pbr_no_r3r4_A`(분기)와 `_C`(위상 이동)가 **11개 열 전부 같게** 뜬다.
- **짝 대조군** — 조건이 같은 무작위 추첨 시나리오. `D ≥ C_p95` 같은 관문은
  **이 열에 값이 있을 때만** 물을 수 있다. `없음` 이면 유니버스가 다른 분포에
  대보게 되므로 관문 판정을 내리면 안 된다 (SPEC_10 §1).
  **이 열은 종목 수를 맞춰 주지 않는다.** 태그 단위에서는 알 수 없기 때문이다 —
  같은 `C_pbr_path_random` 이 n=20 벌(p95 14.15%)과 n=13 벌(p95 15.61%)로 두 벌
  있다. 관문을 물 때는 `experiments/robustness/gate_results_*.json` 의
  `draws_n_stocks` 가 대상의 `n_stocks` 와 같은지 반드시 확인하라.

  > **`[해소 2026-08-15]`** 2026-08-15 이전에는 채택안(MA200)의 관문이 레거시
  > `MA 20/60` 풀(`C_pbr_path_random`)에 걸려 있었다. `pools.json`(07-29, n=20)과
  > `pools_n13.json`(08-12, n=13)이 **md5 동일**인 것이 증거다 — 08-12 재추첨은
  > `--n-pick` 만 바꿨고 유니버스는 다시 짓지 않았다(`run_random_pool.py` 의 대상
  > 태그가 하드코딩이라 모멘텀을 바꿀 수단이 없었다). 지금은 채택안 설정에서
  > 파생된 `C_pbr_ma200_random` 으로 다시 뽑았다 — 풀이 8,229 → **6,445 종목**으로
  > 좁아졌고 p95 는 15.61% → **15.47%** 다. **판정은 그대로 G1 PASS**
  > (20.33% ≥ 15.47%, 귀무분포 백분위 99.4%).

  > ⚠️ **G2 는 아직 같은 불일치가 남아 있다.** 벤치마크 `U_pbr_path_ew` 의 모멘텀이
  > `MA 20/60` 이라, 채택안(MA 200)을 **다른 유니버스의 동일가중**과 견준다.
  > 사전등록 게이트의 벤치마크 교체는 별도 결정 사항이라 그대로 뒀다.
- **소속 축** — 대시보드 등록 대장(`dashboard/series.py`)에서 이 태그를 쓰는 축.
  비어 있으면 화면 어디에도 안 뜬다. 등록 대장은 **산출물 키**로 배정하므로
  (`F_pbr_ma200_n13`, `F_pbr_no_r3r4_A`) 접미사를 되돌려 센다 — 안 그러면 현행
  채택안의 소속 축에서 관문·종목 수 축이 빠진다.
- **왜 만들었나** — 축과 조건만으로는 알 수 없는 것만 적는다. 모멘텀 그리드나
  룰 조합처럼 **축이 곧 이유인 태그는 비워 둔다** (열이 이미 답하는 것을 다시
  적으면 중복이고, 중복한 설명은 갈라진다). 내용은 `dashboard/tags.py` 소유.

총 **81개** 행 — 설정 73개 + 실행 파라미터로 파생된 산출물 키 8개.

## 파생 키도 행으로 싣는다

`n_stocks`·캘린더는 설정이 아니라 **실행 때 정해진다**(`run_ablation --n-stocks K`,
`--calendar A`). 그래서 `ABLATION_CONFIGS` 에는 부모 태그만 있는데, 설정만 실었더니
종목 수를 독립변수로 쓸어 본 네 실행과 캘린더 변형 넷이 **행 자체가 없었다.**
설명 절만 두는 것으로는 부족했다 — 표를 훑는 사람은 보이는 행이 전부라고 읽는다.

파생 행은 부모의 조건을 그대로 물려받고 **달라지는 축만** 다르다. `종목 수` 는
산출물이 기록한 값(`{K} (기록)`), `캘린더` 는 앵커다. 조건이 궁금하면 부모 행과
나란히 놓고 보면 된다.

> 이 절 때문에 문서가 **카탈로그에 의존한다.** 그래도 `--check` 는 의미를 잃지
> 않는다: 여기엔 성과 수치가 없어서 같은 태그를 재실행해도 안 바뀌고, **새 n 값이
> 생길 때만** 바뀐다 — 그때는 바뀌는 게 맞다.

| 태그 | 분류 | 랭킹 신호 | 안정성 룰 | Hard | 스크리너 | 모멘텀 | 밸류에이션 컷 | 종목 수 | 캘린더 | 짝 대조군 | 소속 축 | 왜 만들었나 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `A_random` | RANDOM | 무작위 추첨 | — | — | — | — | n/a | 20 고정 (추첨) | 반기 (기본) | — | layers | 무작위 20종목 선택, seed x rebalance_date 복합 시드, 500회 반복. 결과: A_random_dist.csv (experiments/ablation/, gitignore됨).  |
| `B_hard_random` | RANDOM | 무작위 추첨 | — | ✓ | — | — | n/a | 20 고정 (추첨) | 반기 (기본) | — | layers | HardFilter만 통과 후 무작위 선택, 500회 반복. B_hard_random_dist.csv.  |
| `C_no_r6` | RANDOM | 무작위 추첨 | R1·R2·R3·R4·R5 | ✓ | — | — | n/a | 20 고정 (추첨) | 반기 (기본) | — | r6_loo | 이름과 달리 코드상 RANDOM_TAGS에 포함(use_rim_filter=False, random_n=20) — Hard+Stability(R6 제외) 통과 후 무작위 선택. C_no_r6_dist.csv.  |
| `C_pbr_ma200_random` | — | 무작위 추첨 | R1·R2·R5·R6 | ✓ | — | MA 200 | n/a | 20 고정 (추첨) | 반기 (기본) | — | benchmarks | **없음** |
| `C_pbr_path_random` | RANDOM | 무작위 추첨 | R1·R2·R5·R6 | ✓ | — | MA 20/60 | n/a | 20 고정 (추첨) | 반기 (기본) | — | benchmarks | SPEC_10 §3-1 이 **채택 후보 전용으로 새로 만든 짝 대조군.** 기존 `C_stability_random` 은 룰이 전 6개인 데다 모멘텀을 안 태워서, 채택안(룰 {R1,R2,R5,R6} + 모멘텀)의 관문으로 쓰면 "유니버스가 좁아서"와 "랭킹이 좋아서"가 섞인다. 그래서 **모멘텀 통과 풀에서 무작위 20종목**을 1,000회 뽑는다 (p95 추정 안정화). G1 관문의 귀무분포가 이것이다. |
| `C_stability_random` | RANDOM | 무작위 추첨 | R1·R2·R3·R4·R5·R6 | ✓ | — | — | n/a | 20 고정 (추첨) | 반기 (기본) | — | layers, r6_loo | Hard+Stability(기본 전체 6룰) 통과 후 무작위 선택, 500회 반복. C_stability_random_dist.csv.  |
| `D_factor_only` | DIAGNOSTIC | 팩터 복합 | R1·R2·R3·R4·R5 | ✓ | — | — | n/a | 산출물 키 참조 (기본 20) | 반기 (기본) | `C_no_r6` | ranking_signal | RIM 없이 FactorScreener 4팩터 합산 점수로 직접 랭킹 — 신호분리 대조군 (스크리너 자체는 폐기됐으나 진단 목적 보존).  |
| `D_no_r1` | DIAGNOSTIC | RIM 상승여력 | R2·R3·R4·R5·R6 | ✓ | — | — | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | stability_loo_d | R1 단독 leave-one-out.  |
| `D_no_r2` | DIAGNOSTIC | RIM 상승여력 | R1·R3·R4·R5·R6 | ✓ | — | — | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | stability_loo_d | R2 단독 leave-one-out — R2 폐기 결정의 근거.  |
| `D_no_r3` | DIAGNOSTIC | RIM 상승여력 | R1·R2·R4·R5·R6 | ✓ | — | — | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | stability_loo_d | R3 단독 leave-one-out — R3 폐기 결정의 근거.  |
| `D_no_r4` | DIAGNOSTIC | RIM 상승여력 | R1·R2·R3·R5·R6 | ✓ | — | — | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | stability_loo_d | R4 단독 leave-one-out.  |
| `D_no_r5` | DIAGNOSTIC | RIM 상승여력 | R1·R2·R3·R4·R6 | ✓ | — | — | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | stability_loo_d | R5 단독 leave-one-out.  |
| `D_no_r6` | DIAGNOSTIC | RIM 상승여력 | R1·R2·R3·R4·R5 | ✓ | — | — | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | `C_no_r6` | r6_loo, ranking_signal | R6(가치파괴 구간 제외) 단독 leave-one-out.  |
| `D_no_stability` | DIAGNOSTIC | RIM 상승여력 | — | ✓ | — | — | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | `B_hard_random` | stability_all | SPEC_05 부록A — StabilityFilter 완전 제거 대조군 (D 계열, 스크리너 없음).  |
| `D_pbr_no_r3r4` | DIAGNOSTIC | 1/PBR | R1·R2·R5·R6 | ✓ | — | — | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | ranking_signal | 모멘텀을 뺀 1/PBR 경로 — 현행 룰 {R1,R2,R5,R6} 을 유지한 채 모멘텀만 없앤 구성이다. 랭킹 신호 분리에서 "모멘텀 없는 층"의 PBR 쪽 값을 준다. |
| `D_pbr_only` | DIAGNOSTIC | 1/PBR | R1·R2·R3·R4·R5 | ✓ | — | — | — | 산출물 키 참조 (기본 20) | 반기 (기본) | `C_no_r6` | ranking_signal | RIM 업사이드 랭킹 대신 1/PBR 랭킹 — RIM 알파가 저PBR 재포장인지 신호분리 검증.  |
| `D_rim_only` | DIAGNOSTIC | RIM 상승여력 | R1·R2·R3·R4·R5·R6 | ✓ | — | — | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | `C_stability_random` | layers, r6_loo, stability_loo_d, stability_all, ranking_signal | RIM 유효성(D>C) 판정용 핵심 대조군. 스크리너/모멘텀 없이 Hard+Stability(전체 6룰 기본)+RIM만.  |
| `E_gpa_only` | ARCHIVE | RIM 상승여력 | R1·R2·R3·R4·R5·R6 | ✓ | ✓ gpa | — | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | screener_single | 폐기된 스크리너의 단일 팩터(gpa) 변형.  |
| `E_no_r6` | ARCHIVE | RIM 상승여력 | R1·R2·R3·R4·R5 | ✓ | ✓ | — | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | r6_loo | 폐기된 스크리너 경로의 R6 leave-one-out.  |
| `E_op_only` | ARCHIVE | RIM 상승여력 | R1·R2·R3·R4·R5·R6 | ✓ | ✓ op_yoy | — | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | screener_single | 폐기된 스크리너의 단일 팩터(op_yoy) 변형.  |
| `E_pbr_only` | ARCHIVE | RIM 상승여력 | R1·R2·R3·R4·R5·R6 | ✓ | ✓ inv_pbr | — | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | screener_single | 폐기된 스크리너의 단일 팩터(inv_pbr) 변형.  |
| `E_rev_only` | ARCHIVE | RIM 상승여력 | R1·R2·R3·R4·R5·R6 | ✓ | ✓ rev_yoy | — | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | screener_single | 폐기된 스크리너의 단일 팩터(rev_yoy) 변형.  |
| `E_screener_rim` | ARCHIVE | RIM 상승여력 | R1·R2·R3·R4·R5·R6 | ✓ | ✓ | — | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | layers, r6_loo, screener_single | FactorScreener 폐기(2026-07-05, phase2_rim.py:7 주석). 원칙 5에 따라 삭제하지 않고 기록 보존.  |
| `F_momentum_rim` | DIAGNOSTIC | RIM 상승여력 | R1·R2·R3·R4·R5·R6 | ✓ | — | MA 20/60 | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | layers, r6_loo, stability_combo_f, stability_all, ranking_signal | 모멘텀 기여도(F>D) 판정용. 단, stability_rules 미지정 → 기본값(R1~R6 전체)이라 CANONICAL(R1,R4,R5,R6)과 필터 구성이 다르다. GAPS.md DOC-ABL-002 참조.  |
| `F_no_r2` | DIAGNOSTIC | RIM 상승여력 | R1·R3·R4·R5·R6 | ✓ | — | MA 20/60 | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | stability_combo_f | F 계열에서 R2 단독 제외.  |
| `F_no_r2r3` | ARCHIVE | RIM 상승여력 | R1·R4·R5·R6 | ✓ | — | MA 20/60 | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | stability_combo_f | **2026-07 시점의 채택 파이프라인**이었다 (RIM 랭킹 경로). 현행 채택안은 1/PBR + MA200 + n=13 이라 계보가 다르다 — 여기 수치를 현행 성적으로 인용하지 마라. phase2_rim.py:55 주석은 ’채택 파이프라인 F_momentum_rim 구조’라고 적혀 있으나 이는 오기(誤記)다. F_momentum_rim 태그는 stability_rules 키가 없어 StabilityFilter 기본값(_ALL_RULES = R1~R6, R2/R3 포함)으로 빌드되므로 실제 프로덕션 설정과 다르다. 프로덕션과 필터 구성이 정확히 일치하는 태그는 F_no_r2r3 뿐이었다. GAPS.md DOC-ABL-002 참조.  |
| `F_no_r2r3r4` | DIAGNOSTIC | RIM 상승여력 | R1·R5·R6 | ✓ | — | MA 20/60 | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | stability_combo_f | F 계열에서 R2+R3+R4 동시 제외 (조합 확인용, 채택안 아님).  |
| `F_no_r2r4` | DIAGNOSTIC | RIM 상승여력 | R1·R3·R5·R6 | ✓ | — | MA 20/60 | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | stability_combo_f | F 계열에서 R2+R4 동시 제외 (조합 확인용, 채택안 아님).  |
| `F_no_r3` | DIAGNOSTIC | RIM 상승여력 | R1·R2·R4·R5·R6 | ✓ | — | MA 20/60 | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | stability_combo_f | F 계열에서 R3 단독 제외.  |
| `F_no_r3r4` | DIAGNOSTIC | RIM 상승여력 | R1·R2·R5·R6 | ✓ | — | MA 20/60 | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | `C_pbr_path_random` | stability_combo_f, ranking_signal | F 계열에서 R3+R4 동시 제외 (조합 확인용, 채택안 아님).  |
| `F_no_r4` | DIAGNOSTIC | RIM 상승여력 | R1·R2·R3·R5·R6 | ✓ | — | MA 20/60 | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | stability_combo_f | F 계열에서 R4 단독 제외 (참고용, R4는 채택 유지 규칙).  |
| `F_no_r6` | DIAGNOSTIC | RIM 상승여력 | R1·R2·R3·R4·R5 | ✓ | — | MA 20/60 | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | r6_loo, ranking_signal | F 계열에서 R6 제외 leave-one-out.  |
| `F_no_stability_clean` | DIAGNOSTIC | RIM 상승여력 | — | ✓ | — | MA 20/60 | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | stability_all | SPEC_05 부록A — StabilityFilter 완전 제거 대조군 (F 계열, 스크리너 없음). H_no_stability(스크리너 포함으로 교란)의 정정판.  |
| `F_pbr_52w70` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | 52주 고가 70% | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | momentum_grid | 축 설명 참조 |
| `F_pbr_52w75` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | 52주 고가 75% | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | momentum_grid | 축 설명 참조 |
| `F_pbr_52w80` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | 52주 고가 80% | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | momentum_grid | 축 설명 참조 |
| `F_pbr_absret126` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | 절대수익 126d | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | momentum_grid | 축 설명 참조 |
| `F_pbr_ma100` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 100 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | momentum_grid | 축 설명 참조 |
| `F_pbr_ma120_200` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 120/200 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | momentum_grid | 축 설명 참조 |
| `F_pbr_ma150` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 150 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | momentum_grid | 축 설명 참조 |
| `F_pbr_ma200` | CANONICAL | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 200 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | `C_pbr_ma200_random` | momentum_grid, benchmarks, n_stocks | 축 설명 참조 |
| `F_pbr_ma200_n10` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 200 | — | 10 (기록) | 반기 (기본) | `C_pbr_ma200_random` | momentum_grid, benchmarks, n_stocks | 축 설명 참조 |
| `F_pbr_ma200_n12` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 200 | — | 12 (기록) | 반기 (기본) | `C_pbr_ma200_random` | momentum_grid, benchmarks, n_stocks | 축 설명 참조 |
| `F_pbr_ma200_n13` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 200 | — | 13 (기록) | 반기 (기본) | `C_pbr_ma200_random` | momentum_grid, benchmarks, n_stocks | 축 설명 참조 |
| `F_pbr_ma200_n20` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 200 | — | 20 (기록) | 반기 (기본) | `C_pbr_ma200_random` | momentum_grid, benchmarks, n_stocks | 축 설명 참조 |
| `F_pbr_ma2060_cd3` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 20/60 cd3 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | momentum_grid | 축 설명 참조 |
| `F_pbr_ma2060_cd7` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 20/60 cd7 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | momentum_grid | 축 설명 참조 |
| `F_pbr_ma2060_sl10` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 20/60 sl10 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | momentum_grid | 축 설명 참조 |
| `F_pbr_ma2060_sl30` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 20/60 sl30 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | momentum_grid | 축 설명 참조 |
| `F_pbr_ma20_120` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 20/120 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | momentum_grid | 축 설명 참조 |
| `F_pbr_ma250` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 250 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | momentum_grid | 축 설명 참조 |
| `F_pbr_ma300` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 300 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | momentum_grid | 축 설명 참조 |
| `F_pbr_ma5_120` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 5/120 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | momentum_grid | 축 설명 참조 |
| `F_pbr_ma5_20` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 5/20 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | momentum_grid | 축 설명 참조 |
| `F_pbr_ma5_60` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 5/60 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | momentum_grid | 축 설명 참조 |
| `F_pbr_ma60_120` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 60/120 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | momentum_grid | 축 설명 참조 |
| `F_pbr_ma60_200` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 60/200 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | momentum_grid | 축 설명 참조 |
| `F_pbr_ma_double_adapter` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 20/60 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | `C_pbr_path_random` | momentum_grid | 축 설명 참조 |
| `F_pbr_mktresid126` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | 시장초과 126d | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | momentum_grid | 축 설명 참조 |
| `F_pbr_no_r1r2r3r4` | — | 1/PBR | R5·R6 | ✓ | — | MA 20/60 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | pbr_rules | 축 설명 참조 |
| `F_pbr_no_r1r3r4` | — | 1/PBR | R2·R5·R6 | ✓ | — | MA 20/60 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | pbr_rules | 축 설명 참조 |
| `F_pbr_no_r2r3r4` | — | 1/PBR | R1·R5·R6 | ✓ | — | MA 20/60 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | pbr_rules | 축 설명 참조 |
| `F_pbr_no_r3r4` | DIAGNOSTIC | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 20/60 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | `C_pbr_path_random` | momentum_grid, pbr_rules, ranking_signal, calendar_phase | **PBR 경로의 공통 기준선.** 룰 조합·캘린더·랭킹 분해·모멘텀 그리드가 전부 이 태그를 baseline 으로 쓴다. 2026-07-18 에 채택 후보로 지목됐고, 이후 모멘텀 기준을 MA200 으로 바꾸고 종목 수를 13 으로 줄인 것이 현행 채택안이다 — 즉 **현행안의 직계 조상이지 현행안이 아니다.** |
| `F_pbr_no_r3r4_A` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 20/60 | — | 산출물 키 참조 (기본 20) | 안A (분기 빈도) | `C_pbr_path_random` | momentum_grid, pbr_rules, ranking_signal, calendar_phase | 축 설명 참조 |
| `F_pbr_no_r3r4_C` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 20/60 | — | 산출물 키 참조 (기본 20) | 안C (위상 이동) | `C_pbr_path_random` | momentum_grid, pbr_rules, ranking_signal, calendar_phase | 축 설명 참조 |
| `F_pbr_no_r3r4_parent` | DIAGNOSTIC | 1/PBR (지배지분) | R1·R2·R5·R6 | ✓ | — | MA 20/60 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | `C_pbr_path_random` | ranking_signal | PBR 분모를 자본총계가 아니라 **지배기업소유주지분**으로 바꾼 랭킹 변형 (`rank_mode=pbr_parent`, SPEC_11 §3). 이름의 `_parent` 를 "부모 실행"으로 오독하기 쉬워 한동안 어느 축에도 배정되지 않은 채 남아 있었다. |
| `F_pbr_no_r3r4_rimcut` | DIAGNOSTIC | 1/PBR | R1·R2·R5·R6 | ✓ | — | MA 20/60 | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | `C_pbr_path_random` | ranking_signal | SPEC_14 §14-1 **랭킹×컷 2×2 의 한 칸** — 1/PBR 랭킹에 밸류에이션 컷을 켠 설정. 현행안(`F_pbr_no_r3r4`, 컷 없음)에 컷만 더한 것이라 `C_RIMCUT`(랭킹 고정, 컷 효과)의 변량이고, `C_RANK_CUT`(컷 켠 상태의 랭킹 효과)의 기준이기도 하다. |
| `F_pbr_no_r3r4r5` | DIAGNOSTIC | 1/PBR | R1·R2·R6 | ✓ | — | MA 20/60 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | pbr_rules | SPEC_14 캘린더 민감도의 `C_R5` contrast 를 만들려고 **새로 생성한 태그** (룰 {R1,R2,R6}). 현행안에서 R5 만 더 뺀 구성이며, 그 전에는 R5 단독 대조가 아예 존재하지 않아 contrast 를 구성할 수 없었다. |
| `F_pbr_no_r3r4r6` | — | 1/PBR | R1·R2·R5 | ✓ | — | MA 20/60 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | pbr_rules | 축 설명 참조 |
| `F_pbr_nostab` | — | 1/PBR | — | ✓ | — | MA 20/60 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | pbr_rules | 축 설명 참조 |
| `F_pbr_only` | DIAGNOSTIC | 1/PBR | R1·R2·R3·R4·R5 | ✓ | — | MA 20/60 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | ranking_signal | RIM 랭킹 자리에 1/PBR 만 넣은 모멘텀 경로 대조군. 2026-07-18 판정의 head-to-head 쌍 `F_no_r6 vs F_pbr_only` 의 한쪽이다 — 그 쌍은 **양쪽 다 R6 가 꺼져 있어** 랭킹만 견줄 수 있게 맞춰져 있다. |
| `F_pbr_r6` | DIAGNOSTIC | 1/PBR | R1·R2·R3·R4·R5·R6 | ✓ | — | MA 20/60 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | pbr_rules, ranking_signal | 1/PBR 랭킹에 안정성 룰을 **R1~R6 전부** 켠 설정. `F_momentum_rim`(RIM, 같은 전 6룰)과 룰이 정확히 같아, 2026-07-18 판정에서 "R1~R6 동일조건" head-to-head 쌍으로 쓰였다. |
| `F_pbr_r6only` | — | 1/PBR | R6 | ✓ | — | MA 20/60 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | pbr_rules | 축 설명 참조 |
| `F_pbr_signcount126` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | 부호수 126d | — | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | momentum_grid | 축 설명 참조 |
| `F_rimrank_no_r3r4` | DIAGNOSTIC | RIM 상승여력 | R1·R2·R5·R6 | ✓ | — | MA 20/60 | — | 산출물 키 참조 (기본 20) | 반기 (기본) | `C_pbr_path_random` | ranking_signal | SPEC_14 §14-1 **랭킹×컷 2×2 의 한 칸** — RIM 랭킹인데 밸류에이션 컷은 끈 설정. 기본 태그들에서는 랭킹을 바꾸면 컷이 함께 따라 움직여 "랭킹만의 효과"를 잴 수 없어서, 컷을 독립 스위치로 빼고 만든 신규 태그다. `C_RANK_NOCUT`(컷 끈 상태의 랭킹 효과)의 변량 쪽. |
| `G_full` | ARCHIVE | RIM 상승여력 | R1·R2·R3·R4·R5·R6 | ✓ | ✓ | MA 20/60 | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | layers, r6_loo, stability_all | 스크리너+모멘텀+RIM 풀 파이프라인. 스크리너 폐기로 더 이상 채택 후보 아님.  |
| `G_no_r6` | ARCHIVE | RIM 상승여력 | R1·R2·R3·R4·R5 | ✓ | ✓ | MA 20/60 | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | r6_loo | G_full의 R6 leave-one-out. 동일 사유로 ARCHIVE.  |
| `H_no_stability` | ARCHIVE | RIM 상승여력 | — | ✓ | ✓ | MA 20/60 | ✓ | 산출물 키 참조 (기본 20) | 반기 (기본) | **없음** | layers, stability_all | SPEC_05 부록A 주석(backtest/ablation.py:72-74)에 명시: use_screener=True까지 같이 꺼져 stability·screener 두 축이 동시에 달라 교란됨. F_no_stability_clean/D_no_stability로 대체됨.  |
| `U_pbr_path_ew` | DIAGNOSTIC | 동일가중 전체 | R1·R2·R5·R6 | ✓ | — | MA 20/60 | n/a | 상한 없음 (전 종목) | 반기 (기본) | `C_pbr_path_random` | benchmarks, calendar_phase | SPEC_10 §3-2 — **적격 유니버스 전체를 동일가중**으로 담은 대조군. "종목을 고른 것"이 아니라 "이 유니버스에 그냥 다 넣었을 때"를 재는 기준선이라, G2(net 초과) 판정의 상대가 된다. 구간 승률 비교의 3종 기준(KOSPI·KOSDAQ·이것) 중 하나이기도 하다. |
| `U_pbr_path_ew_A` | — | 동일가중 전체 | R1·R2·R5·R6 | ✓ | — | MA 20/60 | n/a | 상한 없음 (전 종목) | 안A (분기 빈도) | `C_pbr_path_random` | benchmarks, calendar_phase | **없음** |
| `U_pbr_path_ew_C` | — | 동일가중 전체 | R1·R2·R5·R6 | ✓ | — | MA 20/60 | n/a | 상한 없음 (전 종목) | 안C (위상 이동) | `C_pbr_path_random` | benchmarks, calendar_phase | **없음** |

## 관문을 물을 수 없는 태그

짝이 맞는 무작위 대조군이 없는 태그가 **54개** 있다. 이들에게 `D ≥ C_p95` 형태의 관문을 물으면, 룰 구성이 다른 유니버스에서 뽑은 분포와 견주는 것이라 판정이 성립하지 않는다.

- `D_no_r1`
- `D_no_r2`
- `D_no_r3`
- `D_no_r4`
- `D_no_r5`
- `D_pbr_no_r3r4`
- `E_gpa_only`
- `E_no_r6`
- `E_op_only`
- `E_pbr_only`
- `E_rev_only`
- `E_screener_rim`
- `F_momentum_rim`
- `F_no_r2`
- `F_no_r2r3`
- `F_no_r2r3r4`
- `F_no_r2r4`
- `F_no_r3`
- `F_no_r4`
- `F_no_r6`
- `F_no_stability_clean`
- `F_pbr_52w70`
- `F_pbr_52w75`
- `F_pbr_52w80`
- `F_pbr_absret126`
- `F_pbr_ma100`
- `F_pbr_ma120_200`
- `F_pbr_ma150`
- `F_pbr_ma2060_cd3`
- `F_pbr_ma2060_cd7`
- `F_pbr_ma2060_sl10`
- `F_pbr_ma2060_sl30`
- `F_pbr_ma20_120`
- `F_pbr_ma250`
- `F_pbr_ma300`
- `F_pbr_ma5_120`
- `F_pbr_ma5_20`
- `F_pbr_ma5_60`
- `F_pbr_ma60_120`
- `F_pbr_ma60_200`
- `F_pbr_mktresid126`
- `F_pbr_no_r1r2r3r4`
- `F_pbr_no_r1r3r4`
- `F_pbr_no_r2r3r4`
- `F_pbr_no_r3r4r5`
- `F_pbr_no_r3r4r6`
- `F_pbr_nostab`
- `F_pbr_only`
- `F_pbr_r6`
- `F_pbr_r6only`
- `F_pbr_signcount126`
- `G_full`
- `G_no_r6`
- `H_no_stability`

## 등록 대장에 없는 태그

축 어디에도 안 들어간 태그가 **0개**. 화면에 안 뜨므로 만들어 두고 잊기 쉽다.

- (없음)

## 왜 만들었는지 안 적힌 태그

축이 설명해 주지도 않고 개별 설명도 없는 태그가 **1개**. 조건표만으로는 "왜 이 조합을 굳이 만들었나"를 알 수 없는 자리다. 설명은 `dashboard/tags.py` 에 추가한다.

- `C_pbr_ma200_random`

## 조건이 완전히 같은 행

모든 조건 열이 같아 **이 표에서 구별되지 않는** 묶음이 2개 있다. 구별이 안 되면 짝 대조군도 같은 답을 받으므로, 둘 중 하나를 다른 하나의 대조군으로 쓰면 아무것도 안 재는 셈이 된다.

정상인 경우도 있다 — 같은 산식을 다른 배관으로 부르는 쌍이 그렇다. 그래서 실패로 다루지 않고 여기 드러내기만 한다. **모르는 묶음이 보이면 열이 하나 모자란 것이다** (단일 팩터 스크리너 넷이 그 상태로 오래 있었다).

- `F_pbr_ma200` · `F_pbr_ma200_n20`
- `F_pbr_ma_double_adapter` · `F_pbr_no_r3r4`
