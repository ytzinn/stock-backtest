# 다음 세션 인수인계 — Ablation 대시보드 3층 재구성

> 1차 세션 `3fd3844`~`4a59ef6` · 2차 세션 `6e6fcb9`~`cc864d4` · **3차 세션 (2026-08-15)**
> 설계 정본: `설계메모_v3_ablation_대시보드_3층_재구성.md` (저장소 루트)

## 0. 한 줄 요약

설계메모 v3 의 MVP·선택 항목은 2차 세션에 끝났다. 3차 세션은 **미배정 태그 4개를 축에
배정**하고 **`pbr_rules` 왜-지도**를 썼다. 남은 것은 **왜-지도 12축**이다.

**검사 456 → 471 passed.** 미배정 태그 **4 → 0**.

## 1. 3차 세션에 만든 것

| 항목 | 파일 |
|---|---|
| `Elsewhere` — 산출물이 다른 데 있는 태그를 축에 배정하는 기제 | `series.py`, `series_view.elsewhere_rows` |
| 미배정 4개 배정 (`benchmarks` 1 · `ranking_signal` 2 · `pbr_rules` 1) | `series.py` |
| **`pbr_rules` 왜-지도** + 세트 3덩어리 + 룰 조합 라벨 7개 | `series.py` `_WHY_PBR_RULES` |
| A형 축의 `paths` 원본 목록 렌더 (B형 전용이라 안 뜨고 있었다) | `pages/series_explorer.py` |
| 검사 15개 — elsewhere 4 · pbr_rules 사실 6 · 렌더 3 · 취소선 범위 2 | `tests/integrity/` |

### `Elsewhere` 가 왜 필요했나

넷 다 **`experiments/ablation/{키}.json` 이 없다.** 개발 PC 에도 서버에도 없다 —
유실이 아니라 `run_ablation` 을 한 번도 태운 적이 없어서다.

- `C_pbr_path_random` → `scripts/robustness/run_random_pool.py` fast-path 가
  `experiments/robustness/` 로 뽑는다
- 나머지 셋 → 캘린더 민감도 B단계 하네스가 `calendar_sens/stage_b.json` 안에서만 굴린다

그래서 `tags=` 로 배정하면 화면에 **빨간 "산출물이 없는 키" 오류가 영구히** 뜬다.
정상 상태를 오류로 띄우면 진짜 유실이 났을 때 아무도 그 줄을 안 읽는다. `Elsewhere` 는
카탈로그가 `source='file'` 과 `'summary'` 를 나눈 것과 같은 구별이다 — **"없다" 와
"애초에 그 형태로 존재하지 않는다"** 는 다른 사실이다.

배정으로는 세지만(`claimed_keys` → 태그 매트릭스 미배정 0) 비교표 멤버로도 `missing`
으로도 잡지 않고, 화면은 **"이 축에 속하지만 비교표에 없는 전략"** 표로 값의 위치·
개발 PC 존재 여부·읽을 때의 주의를 띄운다.

## 2. 이번 세션에 드러난 결함

1. **A형 축의 `paths` 가 화면 어디에도 안 떴다.** 원본 파일 목록을 `if kind == 'B'`
   안에서만 그리고 있었다. `benchmarks` 축은 관문 산출물 3개를 등록해 뒀는데 **판정
   근거가 통째로 안 보이는** 상태였다.
2. **`C_pbr_path_random` 이 두 벌인데 이름이 같다.** `_n13` 붙은 것(p95 15.61%,
   현행 채택안의 관문)과 안 붙은 것(p95 14.15%, 조상 `F_pbr_no_r3r4` 의 관문). 축의
   `paths` 는 **n=20 벌**을 n=13 게이트 결과 옆에 나란히 걸어 두고 있었다 —
   2026-08-12 회전율 사고와 같은 종류. `elsewhere` 의 `읽을 때` 에 명시했다.
3. **취소선 검사가 또 좁았다.** 왜-지도는 2차 세션에 넣었는데 `next_step` 과 새
   `Elsewhere` 칸이 빠져 있었다. 화면에 마크다운으로 뜨는 칸을 늘릴 때마다 여기 더할 것.

## 3. 세 번 낸 같은 실수 — 여전히 유효한 경고

**짝이 맞지 않는 둘을 빼서 "X 의 효과"라고 불렀다.** (2차 세션에 세 번, 전부 사용자가 잡음)

| 언제 | 무엇을 뺐나 | 실제로 몇 개가 달랐나 |
|---|---|---|
| 랭킹 증분표 | `D_rim_only` − `D_pbr_only` | **3개** (랭킹·R6·밸류에이션 컷) |
| 관문 표 | `D_pbr_no_r3r4` vs `C_stability_random` p95 | 유니버스가 다름 (짝 대조군 없음) |
| 왜-지도 | 채택안 G1 vs RIM 관문 | **6개** + 귀무분포 자체가 다름 |

**대책이 코드에 들어가 있다:**
- `docs/TAG_MATRIX.md` 의 **짝 대조군** 열 — `없음` 이면 관문을 물으면 안 된다
- 화면 **설정 비교** 패널 — 이 축에서 달라지는 조건 개수를 세어 보여준다
- `Delta.crosses_sets` — 세트를 넘는 뺄셈은 기본 금지, 명시할 때만 허용

> **규칙: 두 태그를 빼기 전에 `docs/TAG_MATRIX.md` 에서 두 행을 나란히 놓고 다른 열을
> 세라.** 2개 이상이면 그 차이는 어느 한 조건의 효과가 아니다.

3차 세션은 이 규칙대로 `pbr_rules` 를 썼다 — 달라지는 조건이 **`안정성 룰` 하나뿐**임을
`varying_columns` 로 확인하고, 2축 이상인 증분 3개(`R3·R4 복원`·`안정성 전체 제거`·
`R1 이 없을 때의 R2`)는 `crosses_sets=True` 로 표를 냈다.

## 4. 남은 작업

### ① 왜-지도 나머지 12축
4축 완료(`layers`·`r6_loo`·`ranking_signal`·`pbr_rules`).

**다음 후보는 `momentum_grid`** (MA200 채택 — SPEC_14 §8-4.3 이 §9 금지를 명시적으로
무효화한 결정이라 찬반 논거가 문서에 둘 다 보존돼 있다), 그다음 `benchmarks`
(G1·G2 PASS · G5 FAIL), `n_stocks`(n=13, 낙폭 미해결).

> ⚠️ **혼자 채우지 마라.** 다만 4축 전부 SPEC·검토 문서에서 초안이 나왔다.
> `pbr_rules` 의 계보는 `2026.07.18._PIT_OFFICIAL.md` §2-1 + `2026.07.30._TTM_REISSUE_
> OFFICIAL.md` §7 + SPEC_14 §6-3 에 전부 있었다. **먼저 찾고, 못 찾은 것만 물어라.**

### ② 그 밖
- **분포 CSV 재생성** — 07-18 배치라 비교표와 다른 실행. 재실행 = 기준선 재산출이라
  사용자 승인 필요. 지금은 화면 경고 + 검사 warning 으로 관리 중.
- 레거시 산출물 68개 `n_stocks` 미기록 / 캘린더 메타 76개 미기록 — 같은 이유로 보류.
- 카탈로그 스캔 범위는 아직 `experiments/ablation/` 뿐.
- **`elsewhere` 넷을 `run_ablation` 으로 돌릴지**는 미결이다. 돌리면 비교표 행이 생겨
  R5 칸의 빈자리가 메워지지만, `F_rimrank_*` 두 개는 기간이 달라(2017-05-18\~) 룰
  contrast 와 같은 표에 놓으면 안 된다. 돌리는 순간 `test_elsewhere_really_has_no_
  artifact` 가 깨져서 등록 대장을 고치게 돼 있다.

## 5. 반드시 알아야 할 함정

- **두 태그를 빼기 전에 조건 열을 세라** (§3). 최대 교훈.
- **관문은 짝 대조군이 있을 때만 물을 수 있다.** `TAG_MATRIX` 의 그 열이 `없음` 이면
  어떤 p95 에도 대보면 안 된다. `pbr_rules` 는 **전 태그가 `없음`** 이다.
- **`C_pbr_path_random` 은 두 벌이다** (n=20 / n=13). 판정에 쓸 값은
  `gate_results_*.json` 의 `draws_tag` 가 가리키는 쪽 하나뿐.
- **구간 수를 화면이 세지 마라.** 산출물 `n_periods` 를 읽는다 (23/21/20 세 층).
- **MDD 는 축을 둘 다 밝혀라.** 구간/일별 **그리고** gross/net —
  `mdd` −34.14% / `daily_mdd_gross` −57.08% / `net.daily_mdd` −58.12%(판정 SSOT).
- **물결표 두 개 = 취소선.** 화면에 뜨는 마크다운은 `\~` 로 이스케이프. raw 문자열로.
- **`st.page_link` 금지** (AppTest 검증 불가).
- **holdings tape 은 서버가 원본.** 개발 PC 사본은 낡을 수 있다.
- **PowerShell 로 서버 명령**: 인라인 파이썬은 따옴표 충돌로 깨진다. 스크립트 파일 →
  `scp` 패턴을 쓴다. (`python -c` 도 f-string 이 들어가면 PowerShell 파서가 먹는다 —
  스크래치패드에 `.py` 로 써서 돌려라.)

## 6. 운영 상태

- 대시보드: systemd `backtest-dashboard.service`. `sudo -n systemctl restart` 로 재시작.
- 페이지 둘: `series_explorer.py`(main) · `glossary.py`(sub2).
- 배포: 커밋 → `git push origin master` → 서버 `git pull` → restart → `/health` 200.

## 7. 검증 명령

```bash
pytest -m "not integration" -q            # 로컬 471 passed
python -m scripts.make_canonical --check  # 0
python -m scripts.make_tag_matrix --check # 0
ssh milmelmul@100.120.62.97 "cd /opt/stock-backtest && venv/bin/python -m pytest tests/integrity -q"
```

**생성물 둘 다 `--check` 가 무결성 검사에 물려 있다.** 조건을 고치면 fast suite 가
빨간불이 되고, `python -m scripts.make_tag_matrix` 한 번으로 고쳐진다. `git diff` 로
무엇이 바뀌었는지 눈으로 보게 하려고 자동 재생성은 일부러 안 한다.

화면 렌더 검사는 `tests/integrity/test_dashboard_renders.py` (대표 축 8개 + 용어사전).
축을 지정해야 그 축의 렌더 경로가 돈다 — `at.session_state['series_pick']`.
