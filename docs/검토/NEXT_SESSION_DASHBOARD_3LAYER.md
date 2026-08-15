# 다음 세션 인수인계 — Ablation 대시보드 3층 재구성

> 1차 세션 `3fd3844`~`4a59ef6` · **2차 세션 `6e6fcb9`~`cc864d4` (2026-08-15, 13커밋)**
> 설계 정본: `설계메모_v3_ablation_대시보드_3층_재구성.md` (저장소 루트)

## 0. 한 줄 요약

설계메모 v3 의 **MVP·선택 항목이 전부 끝났다.** sub2 용어사전, B형 전용 뷰 4종,
왜-지도 3축(sub1 착수)까지 붙었고 **태그 조건 매트릭스**가 신설됐다.
남은 것은 **왜-지도 13축**과 **미배정 태그 4개 축 배정**이다.

**검사 380 → 456 passed** (서버 무결성 47 → 123).

## 1. 2차 세션에 만든 것

| 항목 | 파일 | 커밋 |
|---|---|---|
| sub2 용어사전 8항목 + 전용 페이지 | `dashboard/glossary.py`, `pages/glossary.py` | `6e6fcb9`, `d27d062` |
| B형 전용 뷰 **4종 전부** + renderer 레지스트리 | `dashboard/b_views.py` | `6e6fcb9`, `4eabfe8` |
| 왜-지도(sub1) 구조 + **3축** 작성 | `series.py` `WhyMap`·`Delta` | `ecbd7c8`, `2b4590b` |
| 화면 헤더 재구성 (유형·상태 필터 → 설명) | `pages/series_explorer.py` | `da125c3` |
| 구간별 누적수익률·알파 그래프 | `series_view.py` `compound_curve`·`excess_curve` | `3af4b4f` |
| 세트 묶기 (on/off 쌍을 붙여 그림) | `SeriesSpec.groups` | `d6bd6d9`, `ecbd7c8` |
| **설정 매트릭스** (화면) + **태그 조건 매트릭스** (문서) | `pipeline_facts`, `scripts/make_tag_matrix.py` | `d46111f`, `75dfa42` |
| 태그 설명을 코드로 통합 | `dashboard/tags.py` (43항목) | `cc864d4` |

## 2. 이번 세션에 드러난 결함 (전부 조용한 종류였다)

1. **취소선.** 물결표 두 개가 한 줄에 있으면 Streamlit 이 그 사이를 그어 버린다.
   `A~C ... D~H` 가 통째로 그어진 채 배포돼 있었다. `\~` 로 이스케이프하고 검사를
   만들었는데 — **그 검사가 왜-지도를 안 훑어서 같은 사고가 한 번 더 났다.**
2. **분포 CSV 와 비교표가 다른 실행이었다.** 분포 CSV 는 2026-07-18 배치, `summary.json`
   중앙값은 07-30 재발행(TTM 수정 후)이라 최대 0.48%p 차이. 분포 탭은 p95 선으로
   "무작위로도 나올 성적인가"를 재게 하므로 **폐기된 실행의 합격선**을 쓰고 있었다.
3. **철회된 실행이 정본으로 잡힐 뻔했다.** `regime_overlay` 에 같은 그리드가 두 날짜로
   있고 옛것은 버그로 `68/144 통과` 를 냈다. glob 순서에 기대면 그걸 집는다.
4. **`st.page_link` 는 AppTest 로 검증 불가.** 페이지 컨텍스트가 없어 16개 축이 전부
   죽었다. 검증 못 하는 위젯은 안 쓴다.
5. **밸류에이션 컷 표시가 거짓이었다.** 무작위·동일가중·팩터 경로는 `score_and_rank` 를
   오버라이드해 `passes_rim_cut` 을 호출하지 않는데 `rim_threshold` 값은 남아 있다.
6. **배정 규칙 복제.** 매트릭스가 명시 태그만 세어 패턴으로 붙는 축 23개를 "미배정"
   이라 보고했다. `series.claimed_keys` 로 단일 정의화 (미배정 42 → 4).

## 3. 내가 세 번 낸 같은 실수 — 다음 세션이 반드시 알아야 할 것

**짝이 맞지 않는 둘을 빼서 "X 의 효과"라고 불렀다.** 세 번 다 사용자가 잡았다.

| 언제 | 무엇을 뺐나 | 실제로 몇 개가 달랐나 |
|---|---|---|
| 랭킹 증분표 | `D_rim_only` − `D_pbr_only` | **3개** (랭킹·R6·밸류에이션 컷) |
| 관문 표 | `D_pbr_no_r3r4` vs `C_stability_random` p95 | 유니버스가 다름 (짝 대조군 없음) |
| 왜-지도 | 채택안 G1 vs RIM 관문 | **6개** + 귀무분포 자체가 다름 |

**대책이 코드에 들어갔다:**
- `docs/TAG_MATRIX.md` 의 **짝 대조군** 열 — `없음` 이면 관문을 물으면 안 된다
- 화면 **설정 비교** 패널 — 이 축에서 달라지는 조건 개수를 세어 보여준다
- `Delta.crosses_sets` — 세트를 넘는 뺄셈은 기본 금지, 명시할 때만 허용

> **다음 세션 규칙: 두 태그를 빼기 전에 `docs/TAG_MATRIX.md` 에서 두 행을 나란히 놓고
> 다른 열을 세라.** 2개 이상이면 그 차이는 어느 한 조건의 효과가 아니다.

## 4. 남은 작업 (권장 순서)

### ① 미배정 태그 4개 축 배정 — 가장 짧고 중요
`docs/TAG_MATRIX.md` §등록 대장에 없는 태그. 넷 다 **오늘 논의의 핵심 근거**를 담고 있다.

| 태그 | 무엇 | 어디로 |
|---|---|---|
| `C_pbr_path_random` | **채택안 G1 의 귀무분포** (SPEC_10 §3-1) | `benchmarks` |
| `F_rimrank_no_r3r4` | SPEC_14 랭킹×컷 2×2 — RIM·컷없음 | `ranking_signal` |
| `F_pbr_no_r3r4_rimcut` | 같은 2×2 — 1/PBR·컷있음 | `ranking_signal` |
| `F_pbr_no_r3r4r5` | SPEC_14 `C_R5` contrast 용 | `pbr_rules` |

**주의**: 앞 둘의 ablation 산출물이 개발 PC 에 없다(서버 확인 필요). 값은
`stage_b.json` 의 랭킹×컷 블록에 있다 — 기간이 달라(2017-05-18~) 룰 contrast 와
직접 견주면 안 된다.

### ② 왜-지도 나머지 13축
3축 완료(`layers`·`r6_loo`·`ranking_signal`). 포맷은 `WhyMap` — 결과 해석(맨 앞) /
바꾸는 것 / 답하려는 질문 / 막으려는 오해 / 증분표 / 결정 이력 / 주의할 점 /
어디까지 확인됐나 / 다음 질문.

**다음 후보는 `pbr_rules`** — 현행 채택안 `{R1,R2,R5,R6}` 의 근거라 가장 무겁다.
그다음 `momentum_grid`(MA200 채택), `benchmarks`(G1·G2·G5 판정).

> ⚠️ **혼자 채우지 마라.** 다만 이번 세션에서 확인된 것: 계보는 대부분 문서에 있다.
> 3축 전부 SPEC·검토 문서에서 초안을 뽑았고, 사용자가 기억하는 결론(R6 backfire)도
> `2026.06.22. BACKTEST_RESULTS.md` §6-5 에 원문이 있었다. **먼저 찾고, 못 찾은 것만
> 물어라.**

### ③ 그 밖
- **분포 CSV 재생성** — 07-18 배치라 비교표와 다른 실행. 재실행 = 기준선 재산출이라
  사용자 승인 필요. 지금은 화면 경고 + 검사 warning 으로 관리 중.
- 레거시 산출물 68개 `n_stocks` 미기록 / 캘린더 메타 76개 미기록 — 위와 같은 이유로 보류.
- 카탈로그 스캔 범위는 아직 `experiments/ablation/` 뿐.

## 5. 반드시 알아야 할 함정

- **두 태그를 빼기 전에 조건 열을 세라** (§3). 이번 세션 최대 교훈.
- **관문은 짝 대조군이 있을 때만 물을 수 있다.** `TAG_MATRIX` 의 그 열이 `없음` 이면
  어떤 p95 에도 대보면 안 된다.
- **구간 수를 화면이 세지 마라.** 산출물 `n_periods` 를 읽는다 (23/21/20 세 층).
- **MDD 는 축을 둘 다 밝혀라.** 구간/일별 **그리고** gross/net —
  `mdd` −34.14% / `daily_mdd_gross` −57.08% / `net.daily_mdd` −58.12%(판정 SSOT).
- **물결표 두 개 = 취소선.** 화면에 뜨는 마크다운은 `\~` 로 이스케이프. raw 문자열로.
- **`st.page_link` 금지** (AppTest 검증 불가).
- **holdings tape 은 서버가 원본.** 개발 PC 사본은 낡을 수 있다.
- **PowerShell 로 서버 명령**: 인라인 파이썬은 따옴표 충돌로 깨진다. 스크립트 파일 →
  `scp` 패턴을 쓴다.

## 6. 운영 상태

- 대시보드: systemd `backtest-dashboard.service`. `sudo -n systemctl restart` 로 재시작.
- 페이지 둘: `series_explorer.py`(main) · `glossary.py`(sub2).
- 배포: 커밋 → `git push origin master` → 서버 `git pull` → restart → `/health` 200.

## 7. 검증 명령

```bash
pytest -m "not integration" -q            # 로컬 456 passed
python -m scripts.make_canonical --check  # 0
python -m scripts.make_tag_matrix --check # 0  ← 신설
ssh milmelmul@100.120.62.97 "cd /opt/stock-backtest && venv/bin/python -m pytest tests/integrity -q"   # 123 passed
```

**생성물 둘 다 `--check` 가 무결성 검사에 물려 있다.** 조건을 고치면 fast suite 가
빨간불이 되고, `python -m scripts.make_tag_matrix` 한 번으로 고쳐진다. `git diff` 로
무엇이 바뀌었는지 눈으로 보게 하려고 자동 재생성은 일부러 안 한다.

화면 렌더 검사는 `tests/integrity/test_dashboard_renders.py` (대표 축 6개 + 용어사전).
축을 지정해야 그 축의 렌더 경로가 돈다 — `at.session_state['series_pick']`.
