# experiments/ — 산출물 취급 규칙

2026-07-20, 대용량 산출물 6개 파일군(9.38MB)을 **git 추적에서 해제**했다.
파일을 지운 것이 아니라 추적만 끊었다 — 원본은 서버, 사본은 각 개발 PC working tree에 그대로 있다.

## 왜

`experiments/`가 저장소 추적 용량 11.56MB 중 9.73MB(84%)를 차지해 외부 도구
(Claude 프로젝트 지식베이스 등)의 인덱싱 한도를 초과시켰다. 정작 읽을 가치가 있는
`runs/*.md` 공식 보고서와 요약 JSON은 전부 합쳐 1MB 미만이다.

## 추적 / 미추적 구분

| 미추적 (`.gitignore`) | 내용 | 재생성 |
|---|---|---|
| `robustness/*.csv.gz` | seed × 구간 × 종목 귀무분포 (5.21MB) | `run_random_pool` |
| `robustness/*_draws.csv`, `robustness/pools.json` | seed별 CAGR, 리밸일별 풀 | `run_random_pool` |
| `ablation/*_holdings.json` | 시나리오별 구간 편입 tape (~2.2MB) | `export_portfolios` |
| `daily_nav/*_daily_nav.csv` | 태그별 일별 NAV 시계열 (~1.0MB) | `run_daily_nav` |
| `runs/*.xlsx` | 편입 종목 Excel 배포본 (0.77MB) | `make_excel` |

**계속 추적**: `runs/*.md`(공식 보고서 전부), `ablation/{tag}.json` 요약,
`robustness/gate_results.json`·`random_summary.json`, `daily_nav/summary.json`·
`*_reconciliation.csv`·`benchmarks_daily.csv`, `analysis/*.json`, `live/dryrun/manifest.yaml`,
그리고 `ARTIFACTS_MANIFEST.json`.

## 사본은 3중이다 (유실 대비)

1. **서버** `/opt/stock-backtest/experiments/` — 원본. 추가로 2026-07-20 시점 전체 스냅샷이
   `~/backtest-artifacts/experiments_20260720/` (레포 밖, pull·clean 영향 없음)에 있다.
2. **로컬 개발 PC** working tree — untracked 상태로 잔류. 레포가 OneDrive 아래라 클라우드 백업됨.
3. **git 히스토리** — 히스토리를 재작성하지 **않았다.** 추적 해제 이전 모든 버전은 영구히 복구 가능:
   ```bash
   git show 841bd8c:experiments/ablation/F_pbr_no_r3r4_holdings.json > /tmp/restored.json
   ```
   각 파일의 마지막 커밋 해시는 `ARTIFACTS_MANIFEST.json`에 기록돼 있다.

> ⚠️ 이 파일들은 untracked다. **`git clean -fdx` 를 실행하면 로컬 사본이 전부 날아간다.**

## 무결성 검증

`ARTIFACTS_MANIFEST.json`에 26개 파일의 `path / bytes / sha256 / last_commit`이 동결돼 있다.
sha256은 **git blob 기준(LF 정규화)** 이다. 서버 worktree 파일 중 생성 스크립트가 CRLF로 쓴 것이
있어(`C_pbr_path_random_draws.csv`) raw 비교가 어긋날 수 있다 — 그때는 정규화 후 비교한다.

```bash
sha256sum experiments/ablation/F_pbr_no_r3r4_holdings.json      # 우선 raw 비교
tr -d '\r' < experiments/robustness/C_pbr_path_random_draws.csv | sha256sum   # 어긋나면 정규화 후
```

## 재생성 (전부 서버에서, 크론 동결 스냅샷 원칙 준수)

```bash
cd /opt/stock-backtest
venv/bin/python -m scripts.export_portfolios                              # ablation/*_holdings.json
venv/bin/python -m scripts.run_daily_nav                                  # daily_nav/*
venv/bin/python -m scripts.make_excel                                     # runs/*.xlsx
venv/bin/python -m scripts.robustness.run_random_pool --valuation-date YYYY-MM-DD  # robustness/* (장시간)
```

재생성 결과를 공식 수치로 인용하려면 CLAUDE.md "데이터 재현성 규칙"을 따른다 —
크론 동결(UTC 10:00~10:45 = KST 19:00~19:45 실행 금지) 후 실행, 서로 다른 날짜의 실행 결과 혼용 금지.

## 서버 → 로컬 사본 회수

개발 PC에서 (PowerShell, SSH 키 경로 필수):

```powershell
scp -i "$env:USERPROFILE\.ssh\id_ed25519" `
  "milmelmul@172.30.1.96:/opt/stock-backtest/experiments/ablation/*_holdings.json" `
  .\experiments\ablation\

scp -i "$env:USERPROFILE\.ssh\id_ed25519" `
  "milmelmul@172.30.1.96:/opt/stock-backtest/experiments/daily_nav/*_daily_nav.csv" `
  .\experiments\daily_nav\
```

새 산출물을 서버에서 만들었으면 **위 명령으로 로컬 사본을 갱신하고**, 필요하면
`ARTIFACTS_MANIFEST.json`도 함께 갱신한다 (git이 더 이상 자동으로 날라주지 않는다).
