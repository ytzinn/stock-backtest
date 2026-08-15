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
- **짝 대조군** — 조건이 같은 무작위 추첨 시나리오. `D ≥ C_p95` 같은 관문은
  **이 열에 값이 있을 때만** 물을 수 있다. `없음` 이면 유니버스가 다른 분포에
  대보게 되므로 관문 판정을 내리면 안 된다 (SPEC_10 §1).
- **소속 축** — 대시보드 등록 대장(`dashboard/series.py`)에서 이 태그를 쓰는 축.
  비어 있으면 화면 어디에도 안 뜬다.

총 **72개** 태그.

| 태그 | 랭킹 신호 | 안정성 룰 | Hard | 스크리너 | 모멘텀 | 밸류에이션 컷 | 짝 대조군 | 소속 축 |
|---|---|---|---|---|---|---|---|---|
| `A_random` | 무작위 추첨 | — | — | — | — | n/a | — | layers |
| `B_hard_random` | 무작위 추첨 | — | ✓ | — | — | n/a | — | layers |
| `C_no_r6` | 무작위 추첨 | R1·R2·R3·R4·R5 | ✓ | — | — | n/a | — | r6_loo |
| `C_pbr_path_random` | 무작위 추첨 | R1·R2·R5·R6 | ✓ | — | ✓ | n/a | — | **미배정** |
| `C_stability_random` | 무작위 추첨 | R1·R2·R3·R4·R5·R6 | ✓ | — | — | n/a | — | layers, r6_loo |
| `D_factor_only` | 팩터 복합 | R1·R2·R3·R4·R5 | ✓ | — | — | n/a | `C_no_r6` | ranking_signal |
| `D_no_r1` | RIM 상승여력 | R2·R3·R4·R5·R6 | ✓ | — | — | ✓ | **없음** | **미배정** |
| `D_no_r2` | RIM 상승여력 | R1·R3·R4·R5·R6 | ✓ | — | — | ✓ | **없음** | **미배정** |
| `D_no_r3` | RIM 상승여력 | R1·R2·R4·R5·R6 | ✓ | — | — | ✓ | **없음** | **미배정** |
| `D_no_r4` | RIM 상승여력 | R1·R2·R3·R5·R6 | ✓ | — | — | ✓ | **없음** | **미배정** |
| `D_no_r5` | RIM 상승여력 | R1·R2·R3·R4·R6 | ✓ | — | — | ✓ | **없음** | **미배정** |
| `D_no_r6` | RIM 상승여력 | R1·R2·R3·R4·R5 | ✓ | — | — | ✓ | `C_no_r6` | r6_loo, ranking_signal |
| `D_no_stability` | RIM 상승여력 | — | ✓ | — | — | ✓ | `B_hard_random` | stability_all |
| `D_pbr_no_r3r4` | 1/PBR | R1·R2·R5·R6 | ✓ | — | — | — | **없음** | ranking_signal |
| `D_pbr_only` | 1/PBR | R1·R2·R3·R4·R5 | ✓ | — | — | — | `C_no_r6` | ranking_signal |
| `D_rim_only` | RIM 상승여력 | R1·R2·R3·R4·R5·R6 | ✓ | — | — | ✓ | `C_stability_random` | layers, r6_loo, stability_loo_d, stability_all, ranking_signal |
| `E_gpa_only` | RIM 상승여력 | R1·R2·R3·R4·R5·R6 | ✓ | ✓ | — | ✓ | **없음** | **미배정** |
| `E_no_r6` | RIM 상승여력 | R1·R2·R3·R4·R5 | ✓ | ✓ | — | ✓ | **없음** | r6_loo |
| `E_op_only` | RIM 상승여력 | R1·R2·R3·R4·R5·R6 | ✓ | ✓ | — | ✓ | **없음** | **미배정** |
| `E_pbr_only` | RIM 상승여력 | R1·R2·R3·R4·R5·R6 | ✓ | ✓ | — | ✓ | **없음** | **미배정** |
| `E_rev_only` | RIM 상승여력 | R1·R2·R3·R4·R5·R6 | ✓ | ✓ | — | ✓ | **없음** | **미배정** |
| `E_screener_rim` | RIM 상승여력 | R1·R2·R3·R4·R5·R6 | ✓ | ✓ | — | ✓ | **없음** | layers, r6_loo, screener_single |
| `F_momentum_rim` | RIM 상승여력 | R1·R2·R3·R4·R5·R6 | ✓ | — | ✓ | ✓ | **없음** | layers, r6_loo, stability_combo_f, stability_all, ranking_signal |
| `F_no_r2` | RIM 상승여력 | R1·R3·R4·R5·R6 | ✓ | — | ✓ | ✓ | **없음** | **미배정** |
| `F_no_r2r3` | RIM 상승여력 | R1·R4·R5·R6 | ✓ | — | ✓ | ✓ | **없음** | **미배정** |
| `F_no_r2r3r4` | RIM 상승여력 | R1·R5·R6 | ✓ | — | ✓ | ✓ | **없음** | **미배정** |
| `F_no_r2r4` | RIM 상승여력 | R1·R3·R5·R6 | ✓ | — | ✓ | ✓ | **없음** | **미배정** |
| `F_no_r3` | RIM 상승여력 | R1·R2·R4·R5·R6 | ✓ | — | ✓ | ✓ | **없음** | **미배정** |
| `F_no_r3r4` | RIM 상승여력 | R1·R2·R5·R6 | ✓ | — | ✓ | ✓ | `C_pbr_path_random` | ranking_signal |
| `F_no_r4` | RIM 상승여력 | R1·R2·R3·R5·R6 | ✓ | — | ✓ | ✓ | **없음** | **미배정** |
| `F_no_r6` | RIM 상승여력 | R1·R2·R3·R4·R5 | ✓ | — | ✓ | ✓ | **없음** | r6_loo, ranking_signal |
| `F_no_stability_clean` | RIM 상승여력 | — | ✓ | — | ✓ | ✓ | **없음** | stability_all |
| `F_pbr_52w70` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** |
| `F_pbr_52w75` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** |
| `F_pbr_52w80` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** |
| `F_pbr_absret126` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** |
| `F_pbr_ma100` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** |
| `F_pbr_ma120_200` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** |
| `F_pbr_ma150` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** |
| `F_pbr_ma200` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** |
| `F_pbr_ma2060_cd3` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** |
| `F_pbr_ma2060_cd7` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** |
| `F_pbr_ma2060_sl10` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** |
| `F_pbr_ma2060_sl30` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** |
| `F_pbr_ma20_120` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** |
| `F_pbr_ma250` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** |
| `F_pbr_ma300` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** |
| `F_pbr_ma5_120` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** |
| `F_pbr_ma5_20` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** |
| `F_pbr_ma5_60` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** |
| `F_pbr_ma60_120` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** |
| `F_pbr_ma60_200` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** |
| `F_pbr_ma_double_adapter` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** |
| `F_pbr_mktresid126` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** |
| `F_pbr_no_r1r2r3r4` | 1/PBR | R5·R6 | ✓ | — | ✓ | — | **없음** | pbr_rules |
| `F_pbr_no_r1r3r4` | 1/PBR | R2·R5·R6 | ✓ | — | ✓ | — | **없음** | pbr_rules |
| `F_pbr_no_r2r3r4` | 1/PBR | R1·R5·R6 | ✓ | — | ✓ | — | **없음** | pbr_rules |
| `F_pbr_no_r3r4` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid, pbr_rules, ranking_signal, calendar_phase |
| `F_pbr_no_r3r4_parent` | 1/PBR (지배지분) | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | ranking_signal |
| `F_pbr_no_r3r4_rimcut` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | ✓ | `C_pbr_path_random` | **미배정** |
| `F_pbr_no_r3r4r5` | 1/PBR | R1·R2·R6 | ✓ | — | ✓ | — | **없음** | **미배정** |
| `F_pbr_no_r3r4r6` | 1/PBR | R1·R2·R5 | ✓ | — | ✓ | — | **없음** | pbr_rules |
| `F_pbr_nostab` | 1/PBR | — | ✓ | — | ✓ | — | **없음** | pbr_rules |
| `F_pbr_only` | 1/PBR | R1·R2·R3·R4·R5 | ✓ | — | ✓ | — | **없음** | ranking_signal |
| `F_pbr_r6` | 1/PBR | R1·R2·R3·R4·R5·R6 | ✓ | — | ✓ | — | **없음** | pbr_rules, ranking_signal |
| `F_pbr_r6only` | 1/PBR | R6 | ✓ | — | ✓ | — | **없음** | pbr_rules |
| `F_pbr_signcount126` | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** |
| `F_rimrank_no_r3r4` | RIM 상승여력 | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** |
| `G_full` | RIM 상승여력 | R1·R2·R3·R4·R5·R6 | ✓ | ✓ | ✓ | ✓ | **없음** | layers, r6_loo, stability_all |
| `G_no_r6` | RIM 상승여력 | R1·R2·R3·R4·R5 | ✓ | ✓ | ✓ | ✓ | **없음** | r6_loo |
| `H_no_stability` | RIM 상승여력 | — | ✓ | ✓ | ✓ | ✓ | **없음** | layers, stability_all |
| `U_pbr_path_ew` | 동일가중 전체 | R1·R2·R5·R6 | ✓ | — | ✓ | n/a | `C_pbr_path_random` | benchmarks, calendar_phase |

## 관문을 물을 수 없는 태그

짝이 맞는 무작위 대조군이 없는 태그가 **33개** 있다. 이들에게 `D ≥ C_p95` 형태의 관문을 물으면, 룰 구성이 다른 유니버스에서 뽑은 분포와 견주는 것이라 판정이 성립하지 않는다.

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
- `F_pbr_no_r1r2r3r4`
- `F_pbr_no_r1r3r4`
- `F_pbr_no_r2r3r4`
- `F_pbr_no_r3r4r5`
- `F_pbr_no_r3r4r6`
- `F_pbr_nostab`
- `F_pbr_only`
- `F_pbr_r6`
- `F_pbr_r6only`
- `G_full`
- `G_no_r6`
- `H_no_stability`

## 등록 대장에 없는 태그

축 어디에도 안 들어간 태그가 **42개**. 화면에 안 뜨므로 만들어 두고 잊기 쉽다.

- `C_pbr_path_random`
- `D_no_r1`
- `D_no_r2`
- `D_no_r3`
- `D_no_r4`
- `D_no_r5`
- `E_gpa_only`
- `E_op_only`
- `E_pbr_only`
- `E_rev_only`
- `F_no_r2`
- `F_no_r2r3`
- `F_no_r2r3r4`
- `F_no_r2r4`
- `F_no_r3`
- `F_no_r4`
- `F_pbr_52w70`
- `F_pbr_52w75`
- `F_pbr_52w80`
- `F_pbr_absret126`
- `F_pbr_ma100`
- `F_pbr_ma120_200`
- `F_pbr_ma150`
- `F_pbr_ma200`
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
- `F_pbr_ma_double_adapter`
- `F_pbr_mktresid126`
- `F_pbr_no_r3r4_rimcut`
- `F_pbr_no_r3r4r5`
- `F_pbr_signcount126`
- `F_rimrank_no_r3r4`
