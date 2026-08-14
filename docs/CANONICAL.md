# CANONICAL — 현행 채택 설정과 성적

> ⚠️ **이 파일은 `scripts/make_canonical.py` 가 산출물에서 생성한다. 손으로 고치지 마라.**
> 고쳐야 할 값이 있으면 그 값을 만든 산출물이나 `docs/open_issues.yaml` 을 고쳐라.
> 판정 **논리·근거**는 여기 없다 — SPEC 이 SSOT 다.

## 채택 설정

| 항목 | 값 |
|---|---|
| 태그 | `F_pbr_ma200` |
| 종목 수 | **13** |
| 산출물 키 | `F_pbr_ma200_n13` |
| 랭킹 | `pbr` |
| 안정성 규칙 | R1, R2, R5, R6 |
| 모멘텀 기준 | `ma200` (ma_window=200, on_insufficient=reject) |
| HardFilter | 사용 |
| 팩터 스크리너 | 미사용 |
| RIM 밸류에이션 컷 | 미사용 |

필터 스택·모멘텀 기준은 `backtest/ablation.py` 의 `ABLATION_CONFIGS` 에서 읽는다 (산문이 아니라 파생물).

## 성적

| 지표 | 값 | 출처 | 산출 일자 |
|---|---|---|---|
| 구간 CAGR (gross) | 20.3329% | `ablation/F_pbr_ma200_n13.json` | 2026-08-12T13:49:09 |
| 구간 CAGR (net) | 18.6891% | `ablation/F_pbr_ma200_n13.json` | 2026-08-12T13:49:09 |
| 완결 구간 수 | 20 | `ablation/F_pbr_ma200_n13.json` | 2026-08-12T13:49:09 |
| **일별 net CAGR** | **18.5525%** | `daily_nav/summary.json` | 2026-08-11T23:35:40 |
| 일별 net MDD | -58.12% | `daily_nav/summary.json` | 2026-08-11T23:35:40 |
| 일별 net Sharpe | 0.725 | `daily_nav/summary.json` | 2026-08-11T23:35:40 |

Sharpe·MDD 의 SSOT 는 일별 NAV 다 (SPEC_13 §9-1). 구간 지표는 엔진 산술값이다.

## SPEC_10 하드 게이트

대상 `F_pbr_ma200_n13` · 귀무분포 `C_pbr_path_random_n13` (13종목) · 산출 2026-08-12T13:43:29

| 게이트 | 판정 | 근거 |
|---|---|---|
| G1 | PASS | CAGR 20.33% vs 귀무 p95 15.61% |
| G2 | PASS | net 18.69% vs U 6.93% |
| G5 | **FAIL** | 일별 net MDD -58.12% vs 한계 -45.00% |

## 라이브 신호 (dry-run)

| 항목 | 값 |
|---|---|
| 신호일 | 2026-08-10 |
| config_hash | `4fa366d612d1050f` |
| git_commit_sha | `0dfa0b17ca05` |
| 편입 종목 수 | 13 |
| 예상 회전율 | 92.31% |

## 상수

| 이름 | 값 |
|---|---|
| `RF` | 0.0263 |
| `RK` | 0.0873 |
| `OMEGA` | 0.62 |
| `VB_CAP` | 5.0 |

`backtest/configs/constants.py` 에서 import — 재선언 금지.

## 미해결 과제

| id | 심각도 | 내용 | 근거 |
|---|---|---|---|
| `G5-MDD` | high | 일별 net MDD 가 SPEC_10 G5 한계선(−45%)을 위반한다. 종목 수 축으로는 풀리지 않는다 — 구간간 표준편차가 n=1 18.78% 에서 n=20 21.15% 로 거의 줄지 않아 전 종목이 같은 저PBR 팩터에 물려 있다 (n=1..20 곡선은 tape 절단 산출). | `experiments/analysis/n_stocks_curve.json` |
| `SECTOR-DATA` | high | 섹터 분류 데이터가 전무해 업종 집중도를 측정할 수 없다. 낙폭 원인 규명의 최대 병목이다 (pykrx 섹터 API 불작동 → DB 수동 입력 외 수단 없음). | `docs/검토/f_pbr_ma200_median_split.md` |
| `TAPE-ASYNC` | medium | run_ablation 이 지표를 갱신해도 대응 holdings tape 은 그대로다. tape 에 생성 시각·코드 SHA·소스 지표 해시가 없어 소비처가 stale 을 감지할 수단이 없다. tape 자체가 없는 태그도 있다. | `docs/설계/[이슈] 모멘텀필터_coverage_gate_미구현.md` |
| `CORR-GATE-003` | medium | universe_gate_pit 의 PK 에 시점 차원이 없어 정정 공시 이후 시점에는 게이트 판정이 stale 하다. | `docs/설계/SPEC_06_phases.md` |
| `RF-ERP-SENS` | low | 할인율 r 이 고정값이라 출처가 불명확하고, 저금리 구간에서 기업가치를 과대 추정할 위험이 있다. 민감도 분석 미실시. | `docs/설계/SPEC_04_models.md` |

이 표의 원본은 `docs/open_issues.yaml` 이다. 거기를 고쳐라.

## 소스 지문

재생성 시 이 해시가 그대로면 내용도 그대로다. 생성 시각·mtime 은 일부러 찍지 않는다 — 매번 달라져 멱등성 검사를 무력화한다.

| 파일 | sha256(앞 16) |
|---|---|
| `experiments/ablation/summary.json` | f2839c3620434bda |
| `experiments/ablation/F_pbr_ma200_n13.json` | 2dd881b92484c088 |
| `experiments/daily_nav/summary.json` | d9313004882d1bde |
| `experiments/robustness/gate_results_F_pbr_ma200_n13.json` | 49db2dace297b722 |
| `experiments/live/dryrun/manifest.yaml` | 7f3064fff5799ce0 |
| `docs/open_issues.yaml` | 317ff6aee7dbdac4 |
