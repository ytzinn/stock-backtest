> **진단 전용 — 본 실행만으로 채택 후보 없음** (SPEC_14 §9)

단위 %p, net 연율 로그수익률. 공통 기간 2016-05-18~2026-04-03 (2424관측일).

## ① 룰 단일축 (판정용)

| contrast | variant | baseline | 축 | e(반기) | e(안C) | δ | δ 95% CI | 분류 |
|---|---|---|---|---|---|---|---|---|
| `C_R1` | `F_pbr_no_r1r3r4` | `F_pbr_no_r3r4` | R1 제거 | -1.0096 | +2.2142 | **+3.2237** | [-0.2603, +6.7598] | neutral_or_inconclusive |
| `C_R2` | `F_pbr_no_r2r3r4` | `F_pbr_no_r3r4` | R2 제거 | +0.0996 | +0.0000 | **-0.0996** | [-0.3684, +0.1174] | neutral_or_inconclusive |
| `C_R5` | `F_pbr_no_r3r4r5` | `F_pbr_no_r3r4` | R5 제거 | -0.9638 | +0.1900 | **+1.1538** | [-0.0340, +2.3400] | neutral_or_inconclusive |
| `C_R6` | `F_pbr_no_r3r4r6` | `F_pbr_no_r3r4` | R6 제거 | -1.1228 | +2.7327 | **+3.8555** | [-2.1793, +9.9044] | neutral_or_inconclusive |
| `C_MOM` | `D_pbr_no_r3r4` | `F_pbr_no_r3r4` | 모멘텀만 | -3.9568 | -1.3431 | **+2.6137** | [-5.0913, +10.1994] | neutral_or_inconclusive |

## ② 룰 다축 (보조 — J1·J3 분모 제외)

| contrast | variant | baseline | 축 | e(반기) | e(안C) | δ | δ 95% CI | 분류 |
|---|---|---|---|---|---|---|---|---|
| `C_R3R4` | `F_pbr_r6` | `F_pbr_no_r3r4` | R3+R4 복원 | -1.1207 | -0.3882 | **+0.7325** | [-1.1603, +2.7229] | neutral_or_inconclusive |
| `C_STAB` | `F_pbr_nostab` | `F_pbr_no_r3r4` | 안정성 전체 제거 | -1.3536 | +6.4133 | **+7.7669** | [+0.1180, +15.9346] | neutral_or_inconclusive |

## ③ 랭킹 × 밸류에이션컷 2×2 (J 분모 제외)

```
          컷 없음                 컷 있음
1/PBR     F_pbr_no_r3r4(현행안)   F_pbr_no_r3r4_rimcut
RIM       F_rimrank_no_r3r4        F_no_r3r4
```

| contrast | variant | baseline | 축 | e(반기) | e(안C) | δ | δ 95% CI | 분류 |
|---|---|---|---|---|---|---|---|---|
| `C_RANK` | `F_no_r3r4` | `F_pbr_no_r3r4` | 랭킹 + 밸류에이션컷 동시 | -0.8347 | +0.2559 | **+1.0907** | [-6.6062, +8.8742] | neutral_or_inconclusive |
| `C_RANK_NOCUT` | `F_rimrank_no_r3r4` | `F_pbr_no_r3r4` | 랭킹만 (컷 끈 상태) | -2.3056 | +0.2559 | **+2.5615** | [-4.4106, +9.5893] | neutral_or_inconclusive |
| `C_RIMCUT` | `F_pbr_no_r3r4_rimcut` | `F_pbr_no_r3r4` | 밸류에이션컷만 | -1.7872 | +0.5710 | **+2.3582** | [-4.3823, +9.0975] | neutral_or_inconclusive |
| `C_RANK_CUT` | `F_no_r3r4` | `F_pbr_no_r3r4_rimcut` | 랭킹만 (컷 켠 상태) | +0.9525 | -0.3151 | **-1.2675** | [-6.4925, +3.8221] | neutral_or_inconclusive |

## ④ 탐색 셀

(없음 — 추가 시 `exploratory=true`, 추가 시점·사유 병기, §7-4)

## Q1 — Δ_EW

| 지표 | 값 |
|---|---|
| Δ_EW (net) | **-0.5177%p** |
| 95% CI | [-4.2341, +2.9211] |
| 90% CI (equivalence 판정용) | [-3.7539, +2.4174] |
| EW 반기 / 안C net CAGR | 7.1399% / 6.5867% |

## 판정

| 축 | 결과 |
|---|---|
| Q1 (캘린더·유니버스 수준효과) | **Q1_INCONCLUSIVE** |
| Q2-D (방향 견고성) | **Q2D_INCONCLUSIVE** — 반전 0개 / 중립·불확정 5개 (단일축 5) |
| Q2-M (크기 민감성) | **Q2M_INCONCLUSIVE** |
| J1 방향 유지율 | None (분모 0) |
| J2 순위상관 (참고) | spearman -0.036 |

**조치**: 자동 조치 없음 — 수치 병기 후 사용자 결정. 라이브 OOS 대기
