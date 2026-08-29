# 2026.08.29 배관 세션 산출물

**화면 대상이 아니다.** 왜 승격하지 않았는지·왜 전환을 중단했는지의 물증이다.

| 파일 | 무엇 |
|---|---|
| `pre_session_sha.json` | 1차 세션(`773bb0f`) 착수 전 14개 파일 sha |
| `session2_pre_sha.json` | 2차 세션(성적표 전환) 착수 전 9개 파일 sha |
| `scratch_F_pbr_ma200_n13_periods.csv` | 워크트리 밖 재현본. **등재본과 비트 동일하지 않다**는 사실의 물증 |
| `scratch_F_pbr_ma200_n13.json` | 같은 재현 실행의 요약 JSON (승격하지 않음) |
| **`coverage_20260829_driftref_F_pbr_ma200_n13.csv`** | **드리프트 대조 상대 — 삭제 시 종목 단위 규명 불가** |

## 드리프트 대조 상대에 대해

`[검증된 사실]` 2026-08-29 재현 대조에서 `hard_passed` 가 2024 이후 구간에서만, 그리고
감소 방향으로 등재본과 달랐다. **어느 종목인지 특정하지 못했다** — 등재 세대(2026-08-16)의
`coverage.csv` 가 남아 있지 않아 대조할 상대가 없었기 때문이다.

그래서 **현 세대(2026-08-29) coverage 를 보존한다.** 다음에 같은 드리프트가 관측되면
이 파일이 대조 상대가 되어 종목 단위 특정이 가능해진다.

- 출처: `scripts.run_ablation --tags F_pbr_ma200 --n-stocks 13`
  (cwd=scratch, 섀도우 5436, 워크트리 무접촉)
- raw sha256: `6d6893f7f6c079e0d320f370b3e830da34191b1e7da9736d945a1ba90fc65179`
- 서버 사본: `~/drift_reference_20260829/` (README.txt 동봉)

`[Claude 의견]` `price_history_recheck` 가 DOTENV 가드의 판별자를 겸하는 것과 같은 성격이다.
**용도를 적어두지 않으면 다음 사람이 정리 대상으로 본다.**
