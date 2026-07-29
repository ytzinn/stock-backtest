# 다음 세션 인계 — CORR-TTM-001 이후 전체 재발행 (P0)

> 이 파일은 다음 세션 착수용 프롬프트다. 아래 "프롬프트" 절을 그대로 복사해 새 세션에 붙여넣으면 된다.
> 작성: 2026-07-29 세션 종료 시점, HEAD `5ea4f74`.

---

## 프롬프트 (이 아래를 복사)

SPEC_13은 종료됐고, 이번엔 **CORR-TTM-001 이후 미재실행 태그 전체 재발행**을 진행한다.
근거와 배경은 `docs/설계/SPEC_13_rebalance_calendar_v0.7.md` **§9-8**에 기록돼 있다.

### 먼저 검증할 것 (회고 믿지 말고 직접 확인 — verify_prior_session_claims 원칙)

1. 메모리 `spec13_cost_model_m1.md` 맨 아래 "2026-07-29 — Q-H 완료 + P0 미해결" 절을 읽어라.
2. `git log --oneline -3` 로 HEAD가 `5ea4f74`인지 확인.
3. SPEC_13 §9-7(Q-H 판정 결과)·§9-8(미해결 P0)이 실제로 문서에 있는지 대조.
4. **동결 스냅샷이 살아 있는지 반드시 확인** (없으면 아래 "스냅샷 재생성" 절차 선행):
   `ssh ... "docker ps --filter name=qg-snapshot-5435; ls -d /tmp/qg_run"`

### 문제 (이미 측정으로 확정된 사실)

`CORR-TTM-001`(커밋 `f1d2935`)은 `load_pit_series`가 연도 라벨을 버리고 위치 리스트를 반환해
`_make_ttm`이 `FY_2021 − H1_2020 + H1_2022` 같은 **틀린 TTM 조합**을 조용히 만들던 버그를 고쳤다.

동일 동결 스냅샷에서 수정 직전 코드(`ed2856e`)로 인컴번트를 재실행해 측정한 결과:

| | gross CAGR | 비고 |
|---|---|---|
| `ed2856e` (수정 전) | **16.282%** | 07-18 공표값 16.28%를 **정확히 재현** |
| 현재 HEAD (수정 후) | **15.820%** | −0.462%p (CORR-TTM-001 −0.568 + DEBT-3 +0.105) |

→ **데이터 변경(Q1/Q3 수집·financials_pit 재빌드·dq_gate 재실행)은 무영향, 전부 코드 효과**임이 확정됐다.

그런데 `experiments/ablation/*.json` 의 `run_at` 을 확인하면 **재실행된 태그는 3개뿐이고
공표된 13개는 여전히 버그 TTM 기준**이다. 규칙상 요구되는 baseline 재고정을 프로즌 단계로
미뤘는데, 프로즌 단계(Q-C2)에서 **인컴번트 1개만** 재고정하고 범위를 명시하지 않은 것이 원인이다.

### 왜 P0인가 — 채택안 자체가 미검증

| 비교 | 버그 TTM 기준 | 마진 | 위험 |
|---|---|---|---|
| `F_pbr_no_r3r4` 16.28% vs `F_pbr_no_r2r3r4` 16.17% | 채택안 승 | **0.11%p** | 수정 효과(−0.57%p)가 **5배** → 역전 가능 |
| `F_pbr_r6` 14.806% vs `F_pbr_only` 14.793% | — | **0.013%p** | 사실상 동률, 기존 결론 무의미 |
| `F_pbr_no_r3r4` vs `F_no_r3r4` 13.91%(RIM) | PBR 승 | 2.37%p | 견딜 가능성 높으나 확인 필요 |

현재 채택안 `F_pbr_no_r3r4`는 39개 태그 비교에서 **0.11%p 차로 선택된 것**이다. 순위가 뒤집히면
SPEC_09~SPEC_13(방금 끝낸 Q-H 포함) 전부가 잘못된 "채택 후보" 위에 쌓인 게 된다.

### 재실행 대상 — **61개 전부** (2026-07-29 실측)

`ABLATION_CONFIGS` 기준 **전체 61개 = 결정론 56 + 랜덤 5**. 수정 후 재실행된 건 **`F_pbr_no_r3r4` 1개뿐**이다.

| 상태 | 개수 | 비고 |
|---|---|---|
| 수정 후 재실행됨 | **1** | `F_pbr_no_r3r4` (Q-C2에서 재고정) |
| 개별 JSON이 버그 TTM 기준 | **13** | 아래 목록 |
| **개별 JSON 자체가 없음** | **42** | 공표 수치가 `summary.json`에만 존재 |

**42개 미보유 그룹이 오히려 더 위험하다** — RIM 경로 태그(`D_rim_only`, `D_no_r1`~`D_no_r6`, `E_*`,
`F_momentum_rim`, `F_no_r*`, `G_full`, `G_no_r6`, `H_no_stability`)가 전부 여기 있고, RIM은 **TTM
순이익으로 adjROE를 계산**해 TTM 버그 노출이 가장 크다. SPEC_12 모멘텀 기준 17개
(`F_pbr_ma*`, `F_pbr_52w*`, `F_pbr_absret126`, `F_pbr_signcount126`, `F_pbr_mktresid126` 등)도 여기 포함.

**랜덤 5개**(`A_random`, `B_hard_random`, `C_stability_random`, `C_no_r6`, `C_pbr_path_random`)도
반드시 재실행한다 — 풀 구성 필터가 TTM을 쓰므로 **귀무분포 자체가 이동**하고, 그러면 G1·D>C_p95
같은 분포 기반 판정이 전부 바뀐다.

개별 JSON이 버그 TTM 기준인 13개:
`F_pbr_no_r2r3r4`, `F_pbr_no_r3r4_parent`, `F_no_stability_clean`, `F_pbr_r6`, `F_pbr_only`,
`F_pbr_r6only`, `F_no_r3r4`, `F_pbr_no_r3r4r6`, `F_momentum_rim`, `F_pbr_nostab`,
`D_pbr_no_r3r4`, `D_pbr_only`, `U_pbr_path_ew`

**소요 예상**: 결정론 56개 ≈ 2\~3시간, 랜덤 5개(500\~1,000회 × 5) ≈ 수 시간. 한 세션을 넘길 수 있으니
아래 순서대로 진행해 중간에 끊겨도 의사결정에 필요한 것부터 확보한다.

### 할 일 (사용자 결정: 전체 재발행)

**모든 실행은 기존 동결 스냅샷에서** — 그래야 이미 확정된 인컴번트(14.0799%)·Q-G 대조군과 정합한다.
```
컨테이너  qg-snapshot-5435   (2026-07-28 10:56 UTC dump, 포트 5435)
워크트리  /tmp/qg_run
접속      DB_HOST=localhost DB_PORT=5435 DB_PASSWORD=snapshot_local_only DB_NAME=backtest DB_USER=postgres
```

**우선순위 순서** (중간에 끊겨도 의사결정 가치가 남도록 앞에 배치):

1. **채택안 경쟁 그룹 먼저** — `F_pbr_no_r3r4`(재실행 완료)의 직접 경쟁자부터:
   `F_pbr_no_r2r3r4`(마진 0.11%p), `F_pbr_no_r3r4_parent`, `F_pbr_r6`, `F_pbr_only`,
   `F_pbr_no_r3r4r6`, `F_pbr_r6only`, `F_pbr_nostab`, `F_no_r3r4`, `F_no_r2r3r4`.
   → **여기서 순위가 뒤집히면 즉시 멈추고 보고**. SPEC_09~13(방금 끝낸 Q-H 포함)의 상위 전제
   재검토가 필요해진다.
2. **RIM vs PBR head-to-head** — `D_rim_only`, `D_no_r6`, `D_pbr_only`, `D_pbr_no_r3r4`,
   `F_momentum_rim`, `F_no_r6`, `G_full`, `G_no_r6`. "RIM 랭킹 근거 상실" 결론이 유지되는지.
3. **나머지 결정론 태그 전부** — `E_*`(스크리너), `D_no_r1`~`D_no_r5`(LOO), `F_no_r2`~`F_no_r2r4`,
   `H_no_stability`, `F_no_stability_clean`, `D_no_stability`, SPEC_12 모멘텀 17개
   (`F_pbr_ma*`·`F_pbr_52w*`·`F_pbr_absret126`·`F_pbr_signcount126`·`F_pbr_mktresid126`·
   `F_pbr_ma_double_adapter`), `U_pbr_path_ew`.
   `scripts/run_ablation.py --det-only`(태그 미지정 시 전체) 한 번으로 처리 가능.
4. **랜덤 5개 재실행** — `A_random`·`B_hard_random`·`C_stability_random`·`C_no_r6`(각 500회,
   `run_ablation --random-only`) + `C_pbr_path_random` 1,000회
   (`scripts/robustness/run_random_pool.py`, 등가성 게이트 3종 통과 필수).
   **귀무분포가 이동하므로 G1·`D>C_p95` 판정이 바뀔 수 있다.**
5. **일별 NAV 재생성** — 판정에 쓰이는 태그들(`F_pbr_no_r3r4`, `U_pbr_path_ew`, 최종 채택안)에
   `scripts/run_daily_nav.py`. summary는 이제 태그 단위 병합이라 나눠 돌려도 안전.
6. **SPEC_10 G1/G2/G5 재판정** — `scripts/robustness/gate_analysis.py`(반기 전용, 그대로 사용 가능).
   기존 판정(G1·G2 PASS, G5 FAIL −54.2%)이 유지되는지 확인.
7. **문서·메모리 갱신** — SPEC_13 §9-8 해소, SPEC_05/10/11/12 공표 수치 정정, MASTER.md 표.
   메모리 `pbr_momentum_ablation_results.md`·`backtest_reproducibility_drift.md`·
   `audit_pass3_status.md`에 "16.19%/16.28%/16.45% 등은 버그 TTM 기준"임을 명시.

### 주의 사항 (이번 세션에서 실제로 겪은 것들)

- **워크트리 실행 시 미커밋 변경분 반영 여부를 반드시 먼저 확인.** 워크트리를 커밋에서 만들면
  로컬 수정본이 안 들어간다. Q-G에서 이것 때문에 등가성 게이트가 실패했다.
  점검 스크립트: `/tmp/qh_precheck.sh` (서버에 있음).
- **검증 스크립트는 운영 코드의 필터링 로직을 그대로 복제할 것.** `financials_pit` 대조 시
  정정 PIT 규칙(`amendment_from`/`original_amount`, `data_access.py:378-388`)을 빠뜨리면
  가짜 불일치가 난다. 이번에도, Q-A M3에서도 같은 실수를 했다.
- **크론 시간대(UTC 10:00~10:45) 실행 금지** — 격리 스냅샷에서 돌더라도 스크립트의 시계 가드에 걸린다.
- **서버 pull이 untracked 파일로 막히면** `/tmp/verify_then_pull.sh` 사용(sha256 대조 후 제거·pull).
- `summary*.json` 은 이제 태그 단위로 **병합**된다(2026-07-29 수정). 예전처럼 통째로 덮어쓰지 않는다.

### 스냅샷이 사라졌을 경우 재생성 절차

1. 크론 시간대 피해서 `pg_dump -Fc` → 격리 컨테이너(포트 5435) `pg_restore`
2. 운영 DB와 8개 핵심 테이블 행수 대조
3. **인컴번트 `F_pbr_no_r3r4` 재실행해 net CAGR `0.14079850522450377` 비트 재현 확인** —
   이게 통과해야 새 스냅샷이 기존 Q-G/Q-H 수치와 정합한다. 불일치 시 중단하고 보고.
