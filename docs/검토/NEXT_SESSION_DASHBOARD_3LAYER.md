# 다음 세션 인수인계 — Ablation 대시보드 3층 재구성

> 작성 2026-08-15 · 선행 세션 커밋 `3fd3844` ~ `4a59ef6`
> **갱신 2026-08-15 (2차 세션)** — sub2 용어사전 + B형 첫 전용 뷰 완료
> 설계 정본: `설계메모_v3_ablation_대시보드_3층_재구성.md` (저장소 루트)

## 0. 한 줄 요약

설계메모 v3 의 **MVP 필수 항목은 전부 끝났다.** 2차 세션에서 **sub2 용어사전**과
**B형 전용 뷰 4종 전부**가 붙었다. 남은 것은 **sub1 왜-지도** 하나이고, 이건 코드가
아니라 내용 작업이라 **사용자와 같이 채워야 한다.**

## 0-1. 2차 세션에 끝낸 것 (2026-08-15)

| 항목 | 파일 |
|---|---|
| sub2 용어사전 — 7항목, 등록 대장 소유 | `dashboard/glossary.py` |
| 용어사전 페이지 (검색·범위 필터) | `dashboard/pages/glossary.py` |
| 축별 용어 패널 (숫자 **옆에** 뜬다) | `series_explorer.py` |
| **B형 전용 뷰 4종** + renderer 레지스트리 | `dashboard/b_views.py` |
| 전용 뷰의 순수 데이터층 (16함수) | `series_view.py` |
| 무결성 검사 46개 추가 (380 → **426 passed**) | `tests/integrity/test_{glossary,b_views,dashboard_renders}.py` |

**새로 생긴 검사 종류 둘:**

- **값 대조 검사** — 용어사전 본문에 박힌 숫자(23·21·20, −34.14%·−57.08%·−58.12%)를
  산출물에서 다시 계산해 대조한다. 재발행으로 값이 바뀌면 **검사가 깨져서** 설명이
  낡은 채로 남지 않는다. 돌연변이 주입으로 검사가 실제로 무는 것을 확인했다.
- **화면 렌더 검사** (`AppTest`) — 대표 축 3개 + 용어사전 페이지. 1.7초.
  **즉시 값을 했다**: `st.page_link` 가 페이지 컨텍스트 없이 `url_pathname` 으로
  죽는 것을 붙이자마자 잡았다 (아래 §2-6).

## 1. 이번 세션에 끝낸 것

| 항목 | 커밋 |
|---|---|
| 대시보드 오염 수리 (CSV 재계산 삭제 → 산출물 직접 읽기) | `3fd3844` |
| 무결성 검사 신설 (`tests/integrity/`) | `3fd3844` |
| `collect()` 공용화 (`backtest/canonical_state.py`) | `3fd3844` |
| ArtifactCatalog + ScenarioRef 다대다 membership, 16축 등록 대장 | `8823ca5` |
| 캘린더 메타데이터 기록 + 레거시 `ablation.py` 통합·삭제 | `b3561e1` |
| 종목 수 곡선 산출물화 (`scripts/analysis/n_stocks_curve.py`) | `10d961e`, `611a651` |
| 검사 7·8번, 산출물 계보 UI, systemd 유닛 편입 | `4a59ef6` |

**현재 테스트**: 로컬 `pytest -m "not integration"` 380 passed · 서버 `tests/integrity` 47 passed.

## 2. 이번 세션에 드러난 결함 (전부 조용한 종류였다)

1. **판정 배지가 뒤집혀 있었다.** 대시보드가 구간 CSV 에서 지표를 재계산해
   `summary.json` 을 덮어써, `G > D (전체 기여)` 가 ✅ 로 떠 있었다(실제 ❌).
   E_no_r6 는 20.42% 로 표시됐으나 실제 9.76% (−10.66%p).
2. **`n_stocks` 곡선의 근거가 산문뿐이었다.** CANONICAL 미해결 과제 `G5-MDD` 가
   인용하는 18.78%/21.15% 가 코드 주석과 검토 문서에만 있었고 재현 스크립트가 없었다.
   산출물화하니 n=20 값이 **21.06%** 로 정정됐다(결론은 불변).
3. **개발 PC 의 holdings tape 이 서버와 달랐다.** git 미추적이라 독립 사본인데
   개발 PC 것이 낡아 있었다. 서버 원본으로 재생성하니 교차검증 오차가 0.0000%p 가 됐다.
4. **`ScenarioRef` 가 해시 불가였다** (frozen dataclass + dict 필드). Streamlit 이 위젯
   옵션을 해싱하므로 셀렉트 옵션으로 쓰는 순간 화면이 죽는다. 데이터 계층에선 멀쩡해
   보이다 화면에서만 터지는 종류.
5. **systemd 유닛에 오타 2개** (`-- server.address` 의 공백, `var/log/backtest` 의 빠진
   슬래시). 유닛이 repo 에 없어 아무도 못 봤다.

6. **`st.page_link` 는 `AppTest` 로 검증할 수 없다** (2차 세션). 페이지 컨텍스트가 없어
   `url_pathname` KeyError 로 **16개 축 전부가 죽었다.** 운영에서는 아마 돌겠지만
   *아마* 로 배포할 수 없어 안 쓴다 — 사이드바 이동 안내 caption 으로 대체했다.
   `ScenarioRef` 해시 사고와 같은 종류다(데이터 계층은 멀쩡, 화면에서만 죽음).

## 3. 남은 작업 (권장 순서)

### ① sub2 — 용어사전 ✅ **완료 (2026-08-15)**
`dashboard/glossary.py` 가 7항목을 소유한다. 후보 목록 그대로 들어갔다.
검색은 **코드 식별자**로도 된다(`artifact_key`, `median_cagr`) — 사람은 화면에서 본
이름으로 찾지 한글 설명으로 안 찾기 때문이다.

> **인수인계 문서의 오류 1건 정정**: "재실행 vs 절단 … `method` 필드로 구분"은 틀렸다.
> `n_stocks_curve.json` 의 `points[]` 는 **전부 `method='truncation'`** 이고, 재실행값은
> 곡선에 섞이지 않고 `cross_check_vs_rerun[]` 배열에 따로 있다. 용어사전은 산출물
> 쪽을 적었고 `test_glossary.py` 가 이를 못 박는다.

> **용어사전 작성 중 발견**: "구간 −34.14% vs 일별 −58.12%" 라는 대비는 **두 축을
> 뭉갠 것**이다. 전자는 구간·**gross**, 후자는 일별·**net** 이다. 실제 값은 셋이고
> (`mdd` −34.14% / `daily_mdd_gross` −57.08% / `net.daily_mdd` −58.12%), 24%p 차이의
> 대부분은 **측정 빈도**에서 나온다(23%p). 거래비용 몫은 1%p 다. "비용 때문에 낙폭이
> 크다"고 읽으면 틀린다. 이 문서 §4 와 `series_view.py` 주석에도 뭉갠 표현이 남아 있다.

### ② B형 전용 뷰 ✅ **4종 전부 완료 (2026-08-15)**
`dashboard/b_views.py` 의 `B_RENDERERS` 에 키를 등록하면 페이지가 디스패치한다.
없으면 raw fallback (검사 8번이 지킨다). **전용 뷰가 있어도 원본 파일 목록은 접힌 채로
남긴다** — 뷰는 산출물의 일부만 그리므로 나머지가 화면에서 사라지면 "없는 것"이 된다.

| 시리즈 | 만든 뷰 | 그 뷰가 실제로 막는 오독 |
|---|---|---|
| `time_overfit` | 순위 역전 기울기 + 사전등록 규칙 | "ρ 가 유의해서 과적합" (**CI 가 0을 배제 못 한다**) |
| `calendar_bootstrap` | contrast forest plot + 판정 3종 | "방향 일치율 0%" (**분모가 0이라 정의 불가**) |
| `live_decomposition` | 희생자·룰 멤버십·Jaccard | "사전등록된 검정" (**이 축엔 문턱이 없다**) |
| `regime_overlay` | Signal×Tilt 히트맵 + 게이트 | **철회된 68/144 인용** · "total_alpha 절반이 양수" |

> **`pre_registered`·`disclaimer` 가 "이 산출물들"에 다 있다는 §3 옛 서술은 틀렸다.**
> `calendar_sens/` 것(time_split·stage_b)에만 있다. **분해 산출물 3종은 `pre_registered`·
> `disclaimer`·`spec` 이 전부 없고**, `preferred_scan.json` 은 `generated_at` 조차 없어
> 신선도를 판정할 수 없다. 그래서 그 뷰는 **"사전등록이 없다"는 사실 자체를 경고로**
> 띄운다 (`missing_provenance()`, 검사가 필드 구성 변화를 감시한다).

#### 4종을 만들며 확정된 규칙

**판정을 요약하는 화면은 근거를 함께 띄우지 않으면 raw 목록보다 나쁘다.** 결론만 주고
확인은 막기 때문이다. 네 뷰 모두 판정 옆에 ① 그 판정을 만든 문턱(또는 문턱이 없다는
사실) ② 그 결론이 기대는 가정을 함께 놓는다.

**가장 값이 나가는 것은 언제나 경고 한 줄이었다.** 네 축에서 각각:

- `time_overfit` — bootstrap CI `[−0.783, +0.005]` 는 **0을 배제하지 못한다.** 판정은
  ρ 의 유의성이 아니라 초점 태그의 사전등록 문턱에서 나왔다.
- `calendar_bootstrap` — `J1_direction_hold_rate` 가 `null` 이고 **분모가 0**이다.
  0% 로 그리면 "캘린더가 룰 결론을 전부 뒤집었다"가 되는데 실제는 정반대에 가깝다
  (뒤집힐 만큼 뚜렷한 방향이 애초에 없었다).
- `regime_overlay` — `total_alpha > 0` 이 **72/144** 인데 `ex22_alpha > 0` 은 **6/144**.
  알파가 에피소드 #22 하나에 몰려 있다.
- `live_decomposition` — 희생자 평균은 F 에 뒤지지만 **6개 구간에서는 희생자가 이겼다**
  (14/20 이 진 것이지 20/20 이 아니다).

**판정 규칙·상수는 산출 스크립트에서 import 한다.** `time_split.judge`·`FRONT_TOP`·
`BACK_FLOOR`, `stage_b._action` 을 테스트가 직접 불러 산출물과 대조한다. 복제하면
복제본끼리 어긋나는 걸 아무도 못 잡는다 (CLAUDE.md 상수 재선언 금지와 같은 이유).

#### 이번에 잡은 결함 3건

1. **철회된 실행이 정본으로 잡힐 수 있었다.** `regime_overlay` 에는 같은 그리드가
   `2026-07-10`(**68/144 통과 — 철회됨**)과 `2026-07-11`(0/144, 정본) 두 벌 있다.
   glob 순서에 기대면 옛 파일을 집는다. `phase_b_runs()` 가 날짜로 묶어 최신을 정본으로
   표시하고, **철회된 수치와 그 이유(always-on 비교군 구간 불일치 버그)를 화면 맨 위에
   띄운다.** 숨기지 않는 이유는, 숨기면 원본 목록에서 그 CSV 를 열어 인용하기 때문이다.
2. **B형 축들이 같은 파일을 소유하고 있었다.** `decomposition` 의
   `experiments/analysis/*.json` 이 종목 수 축의 `n_stocks_curve.json` 까지 삼켰다.
   경로를 열거로 바꾸고 검사를 추가했다(돌연변이로 무는 것 확인).
3. **Arrow 직렬화 함정을 또 밟았다.** 사전등록 문턱 표에서 한 열에 숫자와 문자열을
   섞어 Streamlit 이 조용히 타입을 고치고 있었다. 이제 모든 행 생성 함수가
   `pa.Table.from_pandas` 를 통과하는지 검사한다.

#### `time_overfit` 을 만들고 배운 것 (나머지 3종에 그대로 적용할 것)

**판정을 요약하는 화면은 근거를 함께 띄우지 않으면 raw 목록보다 나쁘다.** 결론만 주고
확인은 막기 때문이다. 이 뷰는 판정 배지 바로 아래에 사전등록 규칙 2줄을 실제 값과 나란히
놓고(`앞 ≤3위` / `뒤 >10위` vs 실제 2위 / 13위), 그 규칙이 **수치 산출 전에 커밋됐다**는
사실(`pre_registered.note`)을 캡션에 박는다.

**가장 값이 나간 한 줄은 경고문이다.** bootstrap CI 가 `[−0.783, +0.005]` 로 **0 을
배제하지 못한다.** 즉 이 판정은 순위상관이 유의해서가 아니라 초점 태그가 사전등록
문턱을 넘어서 나온 것이다. ρ 와 판정을 그냥 나란히 띄우면 "상관이 유의하므로 과적합"
이라는 **없는 주장**이 읽힌다. `bootstrap_excludes_zero()` 로 계산해 화면에 명시했고,
재발행으로 CI 가 0 을 배제하게 되면 `test_b_views.py` 가 깨져 문구를 고치게 만든다.

**판정 규칙은 import 한다.** `scripts/calendar_sens/time_split.py` 의 `judge`·`FRONT_TOP`·
`BACK_FLOOR` 를 테스트가 직접 불러 산출물 판정과 대조한다. 복제하면 복제본끼리 어긋나는
걸 아무도 못 잡는다 (CLAUDE.md 상수 재선언 금지와 같은 이유).

### ③ sub1 — 왜-지도
16축 각각에 decision history. **코드가 아니라 내용 문제이고, 사용자와 같이 채워야 한다.**
현재 각 축이 가진 건 `Status` 4필드뿐이다. 포맷은 설계메모 §7 참조(변수/답하는 질문/
막는 실패 모드/판정/**먹여준 결정**/탐색 경고/내 이해).

> ⚠️ 혼자 채우지 마라. v1 이 `{R1,R4,R5,R6}` 를 현행으로 잘못 적은 사고가 그것이다.
> 계보는 SPEC 과 검토 문서에 흩어져 있고 일부는 사용자 머릿속에만 있다.

### ④ 그 밖
- **레거시 산출물 68개에 `n_stocks` 미기록** — 재실행하면 채워지지만 드리프트 규칙상
  신중해야 한다(재실행 = 기준선 재산출). warning 으로 관리하는 것도 정당한 선택.
- **캘린더 메타 미기록 76개** — 위와 같음. `run_ablation` 은 이제 기록한다.
- **46/23 ↔ 41/20** — 설계표의 계획 슬롯과 실제 유효 구간의 어긋남. 화면은 동적 읽기로
  해소됐으나 머릿속 모델과의 정합은 미확인.
- **카탈로그 스캔 범위** — 지금은 `experiments/ablation/` 만 훑는다.
  `momentum_criteria/`·`robustness/` 등은 시야 밖이라 고아 파일이 화면에 안 드러난다.

## 4. 반드시 알아야 할 함정

- **구간 수를 화면이 세지 마라.** 산출물의 `n_periods` 를 읽는다. 세 층(23/21/20)이 다르다.
- **MDD·Sharpe 는 열 단위로 기준 고정.** 일별 NAV 보유 태그가 76개 중 14개뿐이라 행별
  폴백은 한 열에 두 정의를 섞는다. 검사 7번이 지킨다.
- **MDD 를 말할 땐 축을 둘 다 밝혀라** (2차 세션 정정). 구간/일별 **그리고** gross/net 이다.
  `mdd`(구간·gross) −34.14% / `daily_mdd_gross` −57.08% / `net.daily_mdd`(판정 SSOT)
  −58.12%. "구간 vs 일별 24%p" 라고만 쓰면 차이의 원인을 비용으로 오해한다 — 빈도가
  23%p, 비용은 1%p 다.
- **`st.page_link` 쓰지 마라.** `AppTest` 로 검증 불가(§2-6). 검증 못 하는 위젯은
  화면에서만 터진다.
- **holdings tape 은 서버가 원본.** 개발 PC 사본은 낡을 수 있다. 곡선 재생성은 서버에서.
- **`pkill -f 'streamlit ... 8501'` 금지.** 그 문자열이 ssh 의 `bash -c` 명령줄에도 있어
  자기 세션까지 죽인다. 이제 `sudo -n systemctl restart backtest-dashboard.service` 를 쓴다.
- **PowerShell 로 서버 명령**: 인라인 파이썬은 따옴표 충돌로 깨진다. 스크립트 파일을
  `[System.IO.File]::WriteAllText(..., ASCII)` 로 쓰고 scp 하는 패턴이 안정적이다
  (`Out-File -Encoding utf8` 은 BOM 이 붙어 bash 첫 줄이 깨진다).

## 5. 운영 상태

- 대시보드는 **systemd 서비스**(`backtest-dashboard.service`, `Restart=always`, enabled).
- `/etc/sudoers.d/backtest-dashboard` 로 `restart|start|stop|status` 만 비밀번호 면제.
  **sudo 비밀번호는 어디에도 저장돼 있지 않다** (DB_PASSWORD 는 시스템 비밀번호가 아님을
  실측 확인).
- 배포: 로컬 커밋 → `git push origin master` → 서버 `git pull` →
  `sudo -n systemctl restart backtest-dashboard.service` → `curl .../health` 200 확인.
- 페이지: `series_explorer.py`(main) · `glossary.py`(sub2) 둘. 레거시 `ablation.py` 는 삭제됐다.

## 6. 검증 명령

```bash
pytest -m "not integration" -q          # 로컬 426 passed
python -m scripts.make_canonical --check # 종료코드 0
ssh milmelmul@100.120.62.97 "cd /opt/stock-backtest && venv/bin/python -m pytest tests/integrity -q"
```

화면 검증은 이제 **테스트에 들어 있다** (`tests/integrity/test_dashboard_renders.py`,
대표 축 6개 + 용어사전 페이지, 2초). 전 축(16개)을 돌리려면 그 파일의
`REPRESENTATIVE` 를 `[s.id for s in SERIES]` 로 바꿔 한 번 돌려보면 된다.

수동으로 쓸 때도 **축을 지정해야 한다** — 기본값은 레이어 축이라
다른 축의 렌더 경로가 실행되지 않는다(실제로 그래서 곡선 코드가 검증 없이 배포됐다):

```python
at.session_state['series_pick'] = SERIES_BY_ID['n_stocks']
at.run()
```
