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
- **왜 만들었나** — 축과 조건만으로는 알 수 없는 것만 적는다. 모멘텀 그리드나
  룰 조합처럼 **축이 곧 이유인 태그는 비워 둔다** (열이 이미 답하는 것을 다시
  적으면 중복이고, 중복한 설명은 갈라진다). 내용은 `dashboard/tags.py` 소유.

총 **72개** 태그.

| 태그 | 분류 | 랭킹 신호 | 안정성 룰 | Hard | 스크리너 | 모멘텀 | 밸류에이션 컷 | 짝 대조군 | 소속 축 | 왜 만들었나 |
|---|---|---|---|---|---|---|---|---|---|---|
| `A_random` | RANDOM | 무작위 추첨 | — | — | — | — | n/a | — | layers | 무작위 20종목 선택, seed x rebalance_date 복합 시드, 500회 반복. 결과: A_random_dist.csv (experiments/ablation/, gitignore됨).  |
| `B_hard_random` | RANDOM | 무작위 추첨 | — | ✓ | — | — | n/a | — | layers | HardFilter만 통과 후 무작위 선택, 500회 반복. B_hard_random_dist.csv.  |
| `C_no_r6` | RANDOM | 무작위 추첨 | R1·R2·R3·R4·R5 | ✓ | — | — | n/a | — | r6_loo | 이름과 달리 코드상 RANDOM_TAGS에 포함(use_rim_filter=False, random_n=20) — Hard+Stability(R6 제외) 통과 후 무작위 선택. C_no_r6_dist.csv.  |
| `C_pbr_path_random` | RANDOM | 무작위 추첨 | R1·R2·R5·R6 | ✓ | — | ✓ | n/a | — | **미배정** | SPEC_10 §3-1 이 **채택 후보 전용으로 새로 만든 짝 대조군.** 기존 `C_stability_random` 은 룰이 전 6개인 데다 모멘텀을 안 태워서, 채택안(룰 {R1,R2,R5,R6} + 모멘텀)의 관문으로 쓰면 "유니버스가 좁아서"와 "랭킹이 좋아서"가 섞인다. 그래서 **모멘텀 통과 풀에서 무작위 20종목**을 1,000회 뽑는다 (p95 추정 안정화). G1 관문의 귀무분포가 이것이다. |
| `C_stability_random` | RANDOM | 무작위 추첨 | R1·R2·R3·R4·R5·R6 | ✓ | — | — | n/a | — | layers, r6_loo | Hard+Stability(기본 전체 6룰) 통과 후 무작위 선택, 500회 반복. C_stability_random_dist.csv.  |
| `D_factor_only` | DIAGNOSTIC | 팩터 복합 | R1·R2·R3·R4·R5 | ✓ | — | — | n/a | `C_no_r6` | ranking_signal | RIM 없이 FactorScreener 4팩터 합산 점수로 직접 랭킹 — 신호분리 대조군 (스크리너 자체는 폐기됐으나 진단 목적 보존).  |
| `D_no_r1` | DIAGNOSTIC | RIM 상승여력 | R2·R3·R4·R5·R6 | ✓ | — | — | ✓ | **없음** | stability_loo_d | R1 단독 leave-one-out.  |
| `D_no_r2` | DIAGNOSTIC | RIM 상승여력 | R1·R3·R4·R5·R6 | ✓ | — | — | ✓ | **없음** | stability_loo_d | R2 단독 leave-one-out — R2 폐기 결정의 근거.  |
| `D_no_r3` | DIAGNOSTIC | RIM 상승여력 | R1·R2·R4·R5·R6 | ✓ | — | — | ✓ | **없음** | stability_loo_d | R3 단독 leave-one-out — R3 폐기 결정의 근거.  |
| `D_no_r4` | DIAGNOSTIC | RIM 상승여력 | R1·R2·R3·R5·R6 | ✓ | — | — | ✓ | **없음** | stability_loo_d | R4 단독 leave-one-out.  |
| `D_no_r5` | DIAGNOSTIC | RIM 상승여력 | R1·R2·R3·R4·R6 | ✓ | — | — | ✓ | **없음** | stability_loo_d | R5 단독 leave-one-out.  |
| `D_no_r6` | DIAGNOSTIC | RIM 상승여력 | R1·R2·R3·R4·R5 | ✓ | — | — | ✓ | `C_no_r6` | r6_loo, ranking_signal | R6(가치파괴 구간 제외) 단독 leave-one-out.  |
| `D_no_stability` | DIAGNOSTIC | RIM 상승여력 | — | ✓ | — | — | ✓ | `B_hard_random` | stability_all | SPEC_05 부록A — StabilityFilter 완전 제거 대조군 (D 계열, 스크리너 없음).  |
| `D_pbr_no_r3r4` | DIAGNOSTIC | 1/PBR | R1·R2·R5·R6 | ✓ | — | — | — | **없음** | ranking_signal | 모멘텀을 뺀 1/PBR 경로 — 현행 룰 {R1,R2,R5,R6} 을 유지한 채 모멘텀만 없앤 구성이다. 랭킹 신호 분리에서 "모멘텀 없는 층"의 PBR 쪽 값을 준다. |
| `D_pbr_only` | DIAGNOSTIC | 1/PBR | R1·R2·R3·R4·R5 | ✓ | — | — | — | `C_no_r6` | ranking_signal | RIM 업사이드 랭킹 대신 1/PBR 랭킹 — RIM 알파가 저PBR 재포장인지 신호분리 검증.  |
| `D_rim_only` | DIAGNOSTIC | RIM 상승여력 | R1·R2·R3·R4·R5·R6 | ✓ | — | — | ✓ | `C_stability_random` | layers, r6_loo, stability_loo_d, stability_all, ranking_signal | RIM 유효성(D>C) 판정용 핵심 대조군. 스크리너/모멘텀 없이 Hard+Stability(전체 6룰 기본)+RIM만.  |
| `E_gpa_only` | ARCHIVE | RIM 상승여력 | R1·R2·R3·R4·R5·R6 | ✓ | ✓ | — | ✓ | **없음** | screener_single | 폐기된 스크리너의 단일 팩터(gpa) 변형.  |
| `E_no_r6` | ARCHIVE | RIM 상승여력 | R1·R2·R3·R4·R5 | ✓ | ✓ | — | ✓ | **없음** | r6_loo | 폐기된 스크리너 경로의 R6 leave-one-out.  |
| `E_op_only` | ARCHIVE | RIM 상승여력 | R1·R2·R3·R4·R5·R6 | ✓ | ✓ | — | ✓ | **없음** | screener_single | 폐기된 스크리너의 단일 팩터(op_yoy) 변형.  |
| `E_pbr_only` | ARCHIVE | RIM 상승여력 | R1·R2·R3·R4·R5·R6 | ✓ | ✓ | — | ✓ | **없음** | screener_single | 폐기된 스크리너의 단일 팩터(inv_pbr) 변형.  |
| `E_rev_only` | ARCHIVE | RIM 상승여력 | R1·R2·R3·R4·R5·R6 | ✓ | ✓ | — | ✓ | **없음** | screener_single | 폐기된 스크리너의 단일 팩터(rev_yoy) 변형.  |
| `E_screener_rim` | ARCHIVE | RIM 상승여력 | R1·R2·R3·R4·R5·R6 | ✓ | ✓ | — | ✓ | **없음** | layers, r6_loo, screener_single | FactorScreener 폐기(2026-07-05, phase2_rim.py:7 주석). 원칙 5에 따라 삭제하지 않고 기록 보존.  |
| `F_momentum_rim` | DIAGNOSTIC | RIM 상승여력 | R1·R2·R3·R4·R5·R6 | ✓ | — | ✓ | ✓ | **없음** | layers, r6_loo, stability_combo_f, stability_all, ranking_signal | 모멘텀 기여도(F>D) 판정용. 단, stability_rules 미지정 → 기본값(R1~R6 전체)이라 CANONICAL(R1,R4,R5,R6)과 필터 구성이 다르다. GAPS.md DOC-ABL-002 참조.  |
| `F_no_r2` | DIAGNOSTIC | RIM 상승여력 | R1·R3·R4·R5·R6 | ✓ | — | ✓ | ✓ | **없음** | stability_combo_f | F 계열에서 R2 단독 제외.  |
| `F_no_r2r3` | CANONICAL | RIM 상승여력 | R1·R4·R5·R6 | ✓ | — | ✓ | ✓ | **없음** | stability_combo_f | phase2_rim.py:55 주석은 ’채택 파이프라인 F_momentum_rim 구조’라고 적혀 있으나 이는 오기(誤記)다. F_momentum_rim 태그는 stability_rules 키가 없어 StabilityFilter 기본값(_ALL_RULES = R1~R6, R2/R3 포함)으로 빌드되므로 실제 프로덕션 설정과 다르다. 프로덕션과 필터 구성이 정확히 일치하는 태그는 F_no_r2r3 뿐이다. GAPS.md DOC-ABL-002 참조.  |
| `F_no_r2r3r4` | DIAGNOSTIC | RIM 상승여력 | R1·R5·R6 | ✓ | — | ✓ | ✓ | **없음** | stability_combo_f | F 계열에서 R2+R3+R4 동시 제외 (조합 확인용, 채택안 아님).  |
| `F_no_r2r4` | DIAGNOSTIC | RIM 상승여력 | R1·R3·R5·R6 | ✓ | — | ✓ | ✓ | **없음** | stability_combo_f | F 계열에서 R2+R4 동시 제외 (조합 확인용, 채택안 아님).  |
| `F_no_r3` | DIAGNOSTIC | RIM 상승여력 | R1·R2·R4·R5·R6 | ✓ | — | ✓ | ✓ | **없음** | stability_combo_f | F 계열에서 R3 단독 제외.  |
| `F_no_r3r4` | DIAGNOSTIC | RIM 상승여력 | R1·R2·R5·R6 | ✓ | — | ✓ | ✓ | `C_pbr_path_random` | stability_combo_f, ranking_signal | F 계열에서 R3+R4 동시 제외 (조합 확인용, 채택안 아님).  |
| `F_no_r4` | DIAGNOSTIC | RIM 상승여력 | R1·R2·R3·R5·R6 | ✓ | — | ✓ | ✓ | **없음** | stability_combo_f | F 계열에서 R4 단독 제외 (참고용, R4는 채택 유지 규칙).  |
| `F_no_r6` | DIAGNOSTIC | RIM 상승여력 | R1·R2·R3·R4·R5 | ✓ | — | ✓ | ✓ | **없음** | r6_loo, ranking_signal | F 계열에서 R6 제외 leave-one-out.  |
| `F_no_stability_clean` | DIAGNOSTIC | RIM 상승여력 | — | ✓ | — | ✓ | ✓ | **없음** | stability_all | SPEC_05 부록A — StabilityFilter 완전 제거 대조군 (F 계열, 스크리너 없음). H_no_stability(스크리너 포함으로 교란)의 정정판.  |
| `F_pbr_52w70` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid | 축 설명 참조 |
| `F_pbr_52w75` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid | 축 설명 참조 |
| `F_pbr_52w80` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid | 축 설명 참조 |
| `F_pbr_absret126` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid | 축 설명 참조 |
| `F_pbr_ma100` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid | 축 설명 참조 |
| `F_pbr_ma120_200` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid | 축 설명 참조 |
| `F_pbr_ma150` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid | 축 설명 참조 |
| `F_pbr_ma200` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid | 축 설명 참조 |
| `F_pbr_ma2060_cd3` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid | 축 설명 참조 |
| `F_pbr_ma2060_cd7` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid | 축 설명 참조 |
| `F_pbr_ma2060_sl10` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid | 축 설명 참조 |
| `F_pbr_ma2060_sl30` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid | 축 설명 참조 |
| `F_pbr_ma20_120` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid | 축 설명 참조 |
| `F_pbr_ma250` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid | 축 설명 참조 |
| `F_pbr_ma300` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid | 축 설명 참조 |
| `F_pbr_ma5_120` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid | 축 설명 참조 |
| `F_pbr_ma5_20` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid | 축 설명 참조 |
| `F_pbr_ma5_60` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid | 축 설명 참조 |
| `F_pbr_ma60_120` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid | 축 설명 참조 |
| `F_pbr_ma60_200` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid | 축 설명 참조 |
| `F_pbr_ma_double_adapter` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid | 축 설명 참조 |
| `F_pbr_mktresid126` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid | 축 설명 참조 |
| `F_pbr_no_r1r2r3r4` | — | 1/PBR | R5·R6 | ✓ | — | ✓ | — | **없음** | pbr_rules | 축 설명 참조 |
| `F_pbr_no_r1r3r4` | — | 1/PBR | R2·R5·R6 | ✓ | — | ✓ | — | **없음** | pbr_rules | 축 설명 참조 |
| `F_pbr_no_r2r3r4` | — | 1/PBR | R1·R5·R6 | ✓ | — | ✓ | — | **없음** | pbr_rules | 축 설명 참조 |
| `F_pbr_no_r3r4` | DIAGNOSTIC | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid, pbr_rules, ranking_signal, calendar_phase | **PBR 경로의 공통 기준선.** 룰 조합·캘린더·랭킹 분해·모멘텀 그리드가 전부 이 태그를 baseline 으로 쓴다. 2026-07-18 에 채택 후보로 지목됐고, 이후 모멘텀 기준을 MA200 으로 바꾸고 종목 수를 13 으로 줄인 것이 현행 채택안이다 — 즉 **현행안의 직계 조상이지 현행안이 아니다.** |
| `F_pbr_no_r3r4_parent` | DIAGNOSTIC | 1/PBR (지배지분) | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | ranking_signal | PBR 분모를 자본총계가 아니라 **지배기업소유주지분**으로 바꾼 랭킹 변형 (`rank_mode=pbr_parent`, SPEC_11 §3). 이름의 `_parent` 를 "부모 실행"으로 오독하기 쉬워 한동안 어느 축에도 배정되지 않은 채 남아 있었다. |
| `F_pbr_no_r3r4_rimcut` | DIAGNOSTIC | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | ✓ | `C_pbr_path_random` | **미배정** | SPEC_14 §14-1 **랭킹×컷 2×2 의 한 칸** — 1/PBR 랭킹에 밸류에이션 컷을 켠 설정. 현행안(`F_pbr_no_r3r4`, 컷 없음)에 컷만 더한 것이라 `C_RIMCUT`(랭킹 고정, 컷 효과)의 변량이고, `C_RANK_CUT`(컷 켠 상태의 랭킹 효과)의 기준이기도 하다. |
| `F_pbr_no_r3r4r5` | DIAGNOSTIC | 1/PBR | R1·R2·R6 | ✓ | — | ✓ | — | **없음** | **미배정** | SPEC_14 캘린더 민감도의 `C_R5` contrast 를 만들려고 **새로 생성한 태그** (룰 {R1,R2,R6}). 현행안에서 R5 만 더 뺀 구성이며, 그 전에는 R5 단독 대조가 아예 존재하지 않아 contrast 를 구성할 수 없었다. |
| `F_pbr_no_r3r4r6` | — | 1/PBR | R1·R2·R5 | ✓ | — | ✓ | — | **없음** | pbr_rules | 축 설명 참조 |
| `F_pbr_nostab` | — | 1/PBR | — | ✓ | — | ✓ | — | **없음** | pbr_rules | 축 설명 참조 |
| `F_pbr_only` | DIAGNOSTIC | 1/PBR | R1·R2·R3·R4·R5 | ✓ | — | ✓ | — | **없음** | ranking_signal | RIM 랭킹 자리에 1/PBR 만 넣은 모멘텀 경로 대조군. 2026-07-18 판정의 head-to-head 쌍 `F_no_r6 vs F_pbr_only` 의 한쪽이다 — 그 쌍은 **양쪽 다 R6 가 꺼져 있어** 랭킹만 견줄 수 있게 맞춰져 있다. |
| `F_pbr_r6` | DIAGNOSTIC | 1/PBR | R1·R2·R3·R4·R5·R6 | ✓ | — | ✓ | — | **없음** | pbr_rules, ranking_signal | 1/PBR 랭킹에 안정성 룰을 **R1~R6 전부** 켠 설정. `F_momentum_rim`(RIM, 같은 전 6룰)과 룰이 정확히 같아, 2026-07-18 판정에서 "R1~R6 동일조건" head-to-head 쌍으로 쓰였다. |
| `F_pbr_r6only` | — | 1/PBR | R6 | ✓ | — | ✓ | — | **없음** | pbr_rules | 축 설명 참조 |
| `F_pbr_signcount126` | — | 1/PBR | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | momentum_grid | 축 설명 참조 |
| `F_rimrank_no_r3r4` | DIAGNOSTIC | RIM 상승여력 | R1·R2·R5·R6 | ✓ | — | ✓ | — | `C_pbr_path_random` | **미배정** | SPEC_14 §14-1 **랭킹×컷 2×2 의 한 칸** — RIM 랭킹인데 밸류에이션 컷은 끈 설정. 기본 태그들에서는 랭킹을 바꾸면 컷이 함께 따라 움직여 "랭킹만의 효과"를 잴 수 없어서, 컷을 독립 스위치로 빼고 만든 신규 태그다. `C_RANK_NOCUT`(컷 끈 상태의 랭킹 효과)의 변량 쪽. |
| `G_full` | ARCHIVE | RIM 상승여력 | R1·R2·R3·R4·R5·R6 | ✓ | ✓ | ✓ | ✓ | **없음** | layers, r6_loo, stability_all | 스크리너+모멘텀+RIM 풀 파이프라인. 스크리너 폐기로 더 이상 채택 후보 아님.  |
| `G_no_r6` | ARCHIVE | RIM 상승여력 | R1·R2·R3·R4·R5 | ✓ | ✓ | ✓ | ✓ | **없음** | r6_loo | G_full의 R6 leave-one-out. 동일 사유로 ARCHIVE.  |
| `H_no_stability` | ARCHIVE | RIM 상승여력 | — | ✓ | ✓ | ✓ | ✓ | **없음** | layers, stability_all | SPEC_05 부록A 주석(backtest/ablation.py:72-74)에 명시: use_screener=True까지 같이 꺼져 stability·screener 두 축이 동시에 달라 교란됨. F_no_stability_clean/D_no_stability로 대체됨.  |
| `U_pbr_path_ew` | DIAGNOSTIC | 동일가중 전체 | R1·R2·R5·R6 | ✓ | — | ✓ | n/a | `C_pbr_path_random` | benchmarks, calendar_phase | SPEC_10 §3-2 — **적격 유니버스 전체를 동일가중**으로 담은 대조군. "종목을 고른 것"이 아니라 "이 유니버스에 그냥 다 넣었을 때"를 재는 기준선이라, G2(net 초과) 판정의 상대가 된다. 구간 승률 비교의 3종 기준(KOSPI·KOSDAQ·이것) 중 하나이기도 하다. |

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

축 어디에도 안 들어간 태그가 **4개**. 화면에 안 뜨므로 만들어 두고 잊기 쉽다.

- `C_pbr_path_random`
- `F_pbr_no_r3r4_rimcut`
- `F_pbr_no_r3r4r5`
- `F_rimrank_no_r3r4`

## 왜 만들었는지 안 적힌 태그

축이 설명해 주지도 않고 개별 설명도 없는 태그가 **0개**. 조건표만으로는 "왜 이 조합을 굳이 만들었나"를 알 수 없는 자리다. 설명은 `dashboard/tags.py` 에 추가한다.

- (없음)
