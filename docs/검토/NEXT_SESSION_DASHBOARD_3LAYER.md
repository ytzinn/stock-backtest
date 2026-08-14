# 다음 세션 인수인계 — Ablation 대시보드 3층 재구성

> 작성 2026-08-15 · 선행 세션 커밋 `3fd3844` ~ `4a59ef6`
> 설계 정본: `설계메모_v3_ablation_대시보드_3층_재구성.md` (저장소 루트)

## 0. 한 줄 요약

설계메모 v3 의 **MVP 필수 항목은 전부 끝났다.** 남은 것은 선택 항목과 3층 중 2층
(왜-지도·용어사전)이다. 대시보드는 systemd 서비스로 전환됐고 배포 시 재시작을
Claude 가 비밀번호 없이 처리할 수 있다.

## 1. 이번 세션에 끝낸 것

| 항목 | 커밋 |
|---|---|
| 대시보드 오염 수리 (CSV 재계산 삭제 → 산출물 직접 읽기) | `3fd3844` |
| 무결성 검사 신설 (`tests/integrity/`) | `3fd3844` |
| `collect()` 공용화 (`backtest/canonical_state.py`) | `3fd3844` |
| ArtifactCatalog + ScenarioRef 다대다 membership, 16축 매니페스트 | `8823ca5` |
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

## 3. 남은 작업 (권장 순서)

### ① sub2 — 용어사전 (가장 싸고 효과 큼)
이번 세션 사고 2건이 전부 용어 혼동에서 나왔다. 항목 후보는 이미 확정적이다:

- **23 → 21 → 20 세 층** (구간 CSV 전체 / `n_gate>0` / 완결 구간) ← 1.86%p 오염의 원인
- **`tag` vs `artifact_key`** ← 2026-08-12 회전율 사고의 원인
- **구간 기준 vs 일별 NAV 기준** (같은 태그에서 −34.14% vs −58.12%)
- **`source='file'` vs `'summary'`** (단일 실행 vs 500회 분포 집계)
- **`_parent`** = PBR 분모가 지배기업소유주지분 (`rank_mode='pbr_parent'`, SPEC_11 §3)
- **재실행 vs 절단** — 종목 수 축의 두 방법. 이름은 둘 다 `n` 이고 `method` 필드로 구분
- 상태 코드 5종 (`ADOPTED`/`CLOSED_FAIL`/`CLOSED_PASS`/`EXPLORING`/`ARCHIVED`)

`SeriesSpec.notes` 와 같은 방식으로 매니페스트가 소유하게 하면 된다.

### ② B형 전용 뷰 4종
현재는 원본 파일 목록만 뜬다(raw fallback, 검사 8번이 지킨다). 산출물 구조는 확인됨:

| 시리즈 | 산출물이 가진 것 | 만들 뷰 |
|---|---|---|
| `time_overfit` | `split`, `spearman_front_back`, `bootstrap`, `focal`, `pre_registered` | 전/후반 순위 역전 화살표 + Spearman |
| `calendar_bootstrap` | `contrasts_single_axis`, `contrasts_multi_axis`, `bootstrap_provenance` | contrast 별 신뢰구간 forest plot |
| `regime_overlay` | phaseB grid CSV 4개 + PNG 6장 | Signal×Tilt 히트맵 |
| `live_decomposition` | `momentum_victims`, `jaccard`, `rule_membership.verdict` | 희생자 표 + 룰 멤버십 |

**중요**: 이 산출물들은 `pre_registered`·`disclaimer` 필드를 갖고 있다. 전용 뷰는
**결과와 사전등록 조건을 반드시 함께** 띄워야 한다 — 사후 해석을 막는 것이 raw 목록보다
전용 뷰가 나은 진짜 이유다. `time_overfit` 하나만 먼저 만들고 판단하는 것을 권한다.

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
- 페이지: `dashboard/pages/series_explorer.py` 하나. 레거시 `ablation.py` 는 삭제됐다.

## 6. 검증 명령

```bash
pytest -m "not integration" -q          # 로컬 380 passed
python -m scripts.make_canonical --check # 종료코드 0
ssh milmelmul@100.120.62.97 "cd /opt/stock-backtest && venv/bin/python -m pytest tests/integrity -q"
```

화면 검증은 `AppTest` 로 하되 **축을 지정해야 한다** — 기본값은 레이어 축이라
다른 축의 렌더 경로가 실행되지 않는다(실제로 그래서 곡선 코드가 검증 없이 배포됐다):

```python
at.session_state['series_pick'] = SERIES_BY_ID['n_stocks']
at.run()
```
