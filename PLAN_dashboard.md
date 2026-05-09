# Plan: Read-only Development Diagnostics Dashboard

## Context
개발 중 매번 Claude/Codex에게 서버 상태를 물어봐야 하는 불편함을 줄이기 위해, 브라우저와 에이전트가 동시에 읽을 수 있는 읽기 전용 진단 대시보드를 구축한다.

이 대시보드는 백테스트 성과 분석용이 아니다. 목적 우선순위는 다음과 같다.

1. 개발 디버깅: 로그, 파일 상태, 배포 상태, 실행 흔적, DB 상태를 빠르게 확인한다.
2. 데이터 품질 판정: DQ Gate, validation, PIT fallback, freshness 문제를 드러낸다.
3. 운영 이상 감지: ingest 지연, 에러, 데이터 누락, cron/systemd 이상을 감지한다.

Codex/Claude Code도 캡처 없이 같은 정보를 읽고 문제점을 파악할 수 있어야 한다. 따라서 Streamlit UI만 만들지 않고, 동일한 진단 결과를 Markdown/JSON 파일로도 저장한다. 저장할 때는 반쯤 쓰인 파일이 보이지 않도록 임시 파일에 먼저 쓴 뒤 완성된 파일로 교체한다.

## Access
- 개발 PC 브라우저: `http://172.30.1.96:8501`
- 서버에서 systemd 서비스로 상시 실행
- 외부 공개 없음. LAN 전용.
- 읽기 전용. 대시보드에서 재시작, 재시도, DB 변경, 파일 삭제 같은 조치 버튼은 제공하지 않는다.

## Project Context
- 서버: Ubuntu 26.04, `/opt/stock-backtest/`
- DB: PostgreSQL 16, port 5433, database `backtest`, user `postgres`
- Python venv: `/opt/stock-backtest/venv/bin/python`
- DB 접속: `ingest/connection.py`의 `get_connection()` 재사용
- 로그 디렉토리: `/var/log/backtest/`
- 개발 PC 브라우저 전용 UI. 모바일 최적화는 범위 밖.

---

## Design Decisions From Interview

### Dashboard Role
- 첫 화면은 관제용 큰 카드보다 개발 콘솔처럼 조밀한 표, 상태 배지, 로그 요약, JSON/Markdown 블록 중심으로 구성한다.
- 백테스트 성과 점검은 별도 대시보드/리포트로 분리한다.
- `backtest_runs`는 최근 실행 상태와 metadata만 표시한다. 성과 metric 심층 분석은 포함하지 않는다.

### Failure Scope
빨간불/노란불은 명시적 에러뿐 아니라 최신성 지연, 품질 저하, validation 증가, fallback 상승까지 포함한다.

상태 등급:
- `OK`: 정상
- `WARN`: 개발자가 확인해야 하지만 즉시 중단 수준은 아님
- `FAIL`: 현재 수집/검증/백테스트 결과를 신뢰하기 어려움
- `UNKNOWN`: 증거 부족, 파일/테이블/권한 없음

### Read-only Principle
서버 상태 확인을 위한 가벼운 subprocess 실행은 허용한다.

허용 예:
- `systemctl is-active`
- `journalctl -u ... -n ...`
- `crontab -l`
- `ps` 중 dashboard/backtest 관련 프로세스 조회
- `df -h`
- 제한된 `du -sh`
- `git rev-parse HEAD`
- `tail`

제약:
- 모든 subprocess는 timeout 3~5초를 기본으로 둔다.
- DB 쿼리는 statement timeout을 적용한다.
- `du`는 지정된 안전 디렉토리만 조회한다.
- `pip list`처럼 비용이 큰 명령은 캐시한다.
- 쓰기/재시작/재시도/삭제/마이그레이션 명령은 실행하지 않는다.

---

## Agent-readable Outputs

Streamlit UI와 별개로, 매 refresh마다 아래 파일을 원자적으로 갱신한다.

### Latest Snapshot
- `/opt/stock-backtest/dashboard/status/health.json`
- `/opt/stock-backtest/dashboard/status/summary.md`

### History
- `/var/log/backtest/dashboard_health.jsonl`

`health.json`은 에이전트용 진실 소스로 삼는다. `summary.md`는 사람이 빠르게 훑는 브리핑이다. JSONL 이력은 최근 상태 변화와 "방금 나빠졌는지 원래 나빴는지" 판단하는 데 사용한다.

파일 저장 규칙:
- 임시 파일에 먼저 쓰고 rename으로 교체한다.
- JSON은 UTF-8, `ensure_ascii=False`, `indent=2`로 저장한다.
- JSONL에는 한 줄에 한 snapshot summary를 append한다.
- 서버에서는 `/var/log/backtest`가 `milmelmul`에게 쓰기 가능해야 한다. 권한이 없으면 JSONL append와 로그 파일 생성이 실패한다.
- JSONL이 과도하게 커지지 않도록 최대 7일 또는 최대 10,000줄 유지 정책을 둔다.

### `health.json` Shape
```json
{
  "generated_at": "2026-05-07T21:30:00+09:00",
  "overall_status": "WARN",
  "summary": "price_history is fresh, DART ingest has recent errors, PIT fallback is above target.",
  "sections": {
    "db": {
      "status": "OK",
      "checks": [
        {
          "severity": "OK",
          "area": "db",
          "title": "DB connectivity/schema checks",
          "evidence": {
            "row_counts": 12
          }
        }
      ]
    },
    "ingest": {
      "status": "WARN",
      "checks": []
    },
    "data_integrity": {
      "status": "WARN",
      "checks": []
    },
    "logs": {
      "status": "FAIL",
      "checks": []
    },
    "system": {
      "status": "OK",
      "checks": []
    },
    "backtest_runs": {
      "status": "UNKNOWN",
      "checks": []
    }
  },
  "findings": [
    {
      "severity": "WARN",
      "area": "pit",
      "title": "PIT fallback rate above target",
      "evidence": {
        "fallback_pct": 23.4,
        "threshold_pct": 20.0
      },
      "suggested_next_check": "Inspect disclosure matching and financials_pit.available_from coverage."
    }
  ],
  "raw": {}
}
```

### `summary.md` Shape
```markdown
# Backtest Dev Health

Generated: 2026-05-07 21:30:00 KST
Overall: WARN

## Red Flags
- ...

## Yellow Flags
- ...

## Evidence
- ...

## Suggested Next Checks
- ...
```

---

## Initial Thresholds

개발 중 조정 가능하도록 상수로 분리한다. 초기값은 보수적으로 시작한다.

| Area | OK | WARN | FAIL |
|---|---:|---:|---:|
| DART ingest error ratio | `< 2%` | `>= 2%` | `>= 10%` |
| DART pending ratio after full run | `< 5%` | `>= 5%` | `>= 20%` |
| price freshness lag | `<= 1 trading day` | `2-3 trading days` | `>= 4 trading days` |
| market cap freshness lag | `<= 1 trading day` | `2-3 trading days` | `>= 4 trading days` |
| `financials_pit.fallback_used` rate | `<= 20%` | `> 20%` | `> 35%` |
| DQ Gate reject ratio | `<= 40%` | `> 40%` | `> 60%` |
| validation REJECT count | `0` | `1-99` | `>= 100` |
| disk usage `/` | `< 80%` | `>= 80%` | `>= 90%` |
| log recent hard failures | `0` | `1-2` | `>= 3` |

거래일 기준 freshness는 주말/휴일을 고려해야 한다. 초기 구현에서는 `price_history`의 최신 날짜와 현재 날짜 차이를 함께 보여주고, 가능하면 DB에 존재하는 최근 거래일 분포를 근거로 판단한다.

---

## New Files

### `dashboard/__init__.py`
빈 파일. 패키지 마커.

### `dashboard/config.py`
경로, 로그 파일, 임계값, timeout을 중앙 관리한다.

주요 상수:
```python
PROJECT_ROOT = Path("/opt/stock-backtest")
STATUS_DIR = PROJECT_ROOT / "dashboard" / "status"
HEALTH_JSON = STATUS_DIR / "health.json"
SUMMARY_MD = STATUS_DIR / "summary.md"
HEALTH_JSONL = Path("/var/log/backtest/dashboard_health.jsonl")
LOG_DIR = Path("/var/log/backtest")
COMMAND_TIMEOUT_SEC = 5
DB_STATEMENT_TIMEOUT_MS = 3000
```

### `dashboard/sanitize.py`
로그와 명령 출력에서 민감정보를 마스킹한다.

마스킹 대상:
- `.env` 값처럼 보이는 `KEY=value`
- DART API key
- DB password
- URL credential
- 긴 토큰처럼 보이는 문자열

로컬 사용자명과 서버 경로는 기본적으로 유지한다. 내부 개발 진단에서는 경로가 디버깅에 중요하기 때문이다.

### `dashboard/queries.py`
모든 SQL 쿼리를 분리한다. `ingest/connection.py`의 `get_connection()`을 재사용한다.

공통 규칙:
- 각 함수는 새 connection을 열고 닫는다.
- `SET LOCAL statement_timeout = '3000ms'` 또는 equivalent를 적용한다.
- `@st.cache_data(ttl=30)` 캐싱을 사용한다.
- 실패 시 exception을 UI/health snapshot에 `UNKNOWN` 또는 `FAIL` evidence로 전달할 수 있게 한다.

기본 패턴:
```python
from ingest.connection import get_connection
import streamlit as st

@st.cache_data(ttl=30)
def get_xxx() -> list[tuple]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '3000ms'")
            cur.execute(SQL)
            return cur.fetchall()
    finally:
        conn.close()
```

주요 함수:
- `get_row_counts()` - 주요 테이블 행 수
- `get_ingest_progress()` - done/error/pending/total 집계
- `get_ingest_status_summary()` - status별 상세
- `get_ingest_errors()` - 최근 에러 20건
- `get_price_freshness()` - 최신 날짜와 해당 날짜 종목 수
- `get_market_cap_freshness()` - 최신 날짜와 해당 날짜 종목 수
- `get_recent_price_coverage()` - 최근 거래일별 price 종목 수
- `get_recent_market_cap_coverage()` - 최근 거래일별 시총 종목 수
- `get_dq_gate_summary()` - PASS/REJECT by report_type
- `get_dq_gate_top_rejects()` - 상위 reject 사유 10개
- `get_validation_summary()` - check_id별 V01-V09 집계
- `get_validation_top_tickers()` - 문제 많은 ticker Top 20
- `get_pit_fallback_rate()` - fallback_cnt, total_cnt, fallback_pct
- `get_pit_available_from_anomalies()` - 미래 available_from 등 이상치
- `get_stocks_stats()` - 시장별 상장/제외 통계
- `get_orphan_checks()` - FK 밖 데이터 후보
- `get_backtest_runs()` - 최근 실행 상태/metadata/traceback

핵심 SQL 예:
```sql
SELECT 'stocks' AS tbl, COUNT(*) FROM stocks UNION ALL
SELECT 'stock_listing_events', COUNT(*) FROM stock_listing_events UNION ALL
SELECT 'financials', COUNT(*) FROM financials UNION ALL
SELECT 'financials_pit', COUNT(*) FROM financials_pit UNION ALL
SELECT 'disclosures', COUNT(*) FROM disclosures UNION ALL
SELECT 'price_history', COUNT(*) FROM price_history UNION ALL
SELECT 'market_cap_history', COUNT(*) FROM market_cap_history UNION ALL
SELECT 'universe_gate_pit', COUNT(*) FROM universe_gate_pit UNION ALL
SELECT 'ingest_status', COUNT(*) FROM ingest_status UNION ALL
SELECT 'validation_log', COUNT(*) FROM validation_log UNION ALL
SELECT 'rim_input_status', COUNT(*) FROM rim_input_status UNION ALL
SELECT 'backtest_runs', COUNT(*) FROM backtest_runs
ORDER BY tbl;
```

```sql
SELECT reason, COUNT(*) AS cnt
FROM universe_gate_pit,
     jsonb_array_elements_text(reject_reasons) AS reason
GROUP BY reason
ORDER BY cnt DESC
LIMIT 10;
```

### `dashboard/system_checks.py`
서버에 영향을 주지 않는 읽기 전용 시스템 진단을 담당한다.

수집 항목:
- `systemctl is-active backtest-dashboard`
- `journalctl -u backtest-dashboard -n 100 --no-pager`
- `crontab -l`
- 각 로그 파일 mtime
- `df -h /`
- `du -sh /opt/stock-backtest`, 단 timeout 적용
- `git rev-parse HEAD`
- `git status --short`, 단 읽기 전용
- 관련 프로세스 목록
- venv package 목록, 캐시 적용

모든 subprocess는 allowlist 기반으로 작성하고 shell string이 아니라 argv list를 사용한다.

### `dashboard/logs.py`
로그 파일 읽기, 마스킹, 요약을 담당한다.

대상 로그:
```python
LOG_FILES = {
    "dart.log": "/var/log/backtest/dart.log",
    "dart_retry.log": "/var/log/backtest/dart_retry.log",
    "price.log": "/var/log/backtest/price.log",
    "market_cap.log": "/var/log/backtest/market_cap.log",
    "pit.log": "/var/log/backtest/pit.log",
    "dq_gate.log": "/var/log/backtest/dq_gate.log",
    "healthcheck.log": "/var/log/backtest/healthcheck.log",
}
```

요약 규칙:
- `ERROR`
- `WARN`
- `FAIL`
- `Traceback`
- `Exception`
- `[FAIL]`

에러 후보는 파일별 최근 3개를 뽑고, 각 후보는 앞뒤 2줄 context를 포함한다. 출력 전 반드시 sanitize를 통과한다.

빈 로그 파일은 정상 상태로 본다. 즉, 파일이 존재하고 최근 tail에 에러 키워드가 없으면 `OK`다. 파일 자체가 없거나 읽을 수 없으면 `UNKNOWN`이다.

### `dashboard/health.py`
DB 진단, 시스템 진단, 로그 요약을 모아 `health.json`, `summary.md`, JSONL을 생성한다.

책임:
- 각 check의 severity 산정
- section별 status 집계
- overall status 산정
- agent-readable JSON 생성
- 사람용 Markdown 생성
- 파일 원자적 저장
- DB 연결과 기본 schema/row count 조회가 성공하면 `db` section에 명시적인 `OK` check를 추가한다. check 배열이 비어 있으면 section 집계가 `UNKNOWN`이 되므로 정상 케이스도 evidence를 남긴다.

overall status 규칙:
1. 하나라도 `FAIL`이면 overall `FAIL`
2. 아니면 하나라도 `WARN`이면 overall `WARN`
3. 아니면 모두 `OK`이면 overall `OK`
4. 핵심 section이 읽히지 않으면 `UNKNOWN`

### `dashboard/app.py`
Streamlit 메인 앱. `sys.path` 설정 후 탭을 구성한다.

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

자동갱신 및 수동 갱신:
```python
st.markdown('<meta http-equiv="refresh" content="30">', unsafe_allow_html=True)

if st.button("Refresh Now"):
    st.cache_data.clear()
    st.rerun()
```

탭 구성:

| Tab | Purpose |
|---|---|
| Agent Summary | `summary.md` 스타일 요약, red/yellow flags, evidence, suggested next checks, `health.json` 표시 |
| Health Matrix | DB/ingest/data integrity/logs/system/cron/backtest 상태표 |
| Data Integrity | PIT fallback, DQ Gate, validation, freshness, orphan/anomaly checks |
| Logs | 마스킹된 원문 tail, 에러/워닝 추출 요약, 파일별 최근 원인 후보 |
| System | systemd, journal, cron, disk, git, process, filesystem, package cache |
| Backtest Runs | 최근 실행 status, run metadata, git commit, param_hash, started/finished, traceback |

---

## Existing File Changes

### `ingest/logging_config.py`
수집 스크립트가 대시보드 로그 디렉토리에 직접 파일 로그를 남기도록 공통 로깅 설정을 제공한다.

동작:
- 기본 로그 디렉토리는 서버에서 `/var/log/backtest`, Windows 로컬에서는 `PROJECT_ROOT/logs`
- `BACKTEST_LOG_DIR` 환경변수로 override 가능
- stderr stream handler와 UTF-8 file handler를 함께 붙인다.
- 로그 디렉토리 생성/쓰기 권한이 없으면 warning을 남기고 stderr 로깅은 유지한다.

### Ingest scripts
다음 스크립트는 cron redirect가 없어도 대시보드가 읽는 로그 파일에 직접 기록한다.

| Script | Log file |
|---|---|
| `ingest/dart_ingest.py` | `dart.log`, `--skip-if-done` 실행 시 `dart_retry.log`도 추가 |
| `ingest/price_ingest.py` | `price.log` |
| `ingest/market_cap_ingest.py` | `market_cap.log` |
| `ingest/pit_loader.py` | `pit.log` |
| `ingest/dq_gate.py` | `dq_gate.log` |
| `ingest/healthcheck.py` | `healthcheck.log` |

### `requirements.txt`
추가:
```text
streamlit>=1.35.0
watchdog>=4.0.0
```

---

## Server Deployment Steps

### 1. Install Packages
```bash
/opt/stock-backtest/venv/bin/pip install "streamlit>=1.35.0" "watchdog>=4.0.0"
```

### 2. Create Status and Log Directories
```bash
mkdir -p /opt/stock-backtest/dashboard/status
sudo mkdir -p /var/log/backtest
sudo chown -R milmelmul:milmelmul /var/log/backtest
touch /var/log/backtest/dart.log \
      /var/log/backtest/dart_retry.log \
      /var/log/backtest/price.log \
      /var/log/backtest/market_cap.log \
      /var/log/backtest/pit.log \
      /var/log/backtest/dq_gate.log \
      /var/log/backtest/healthcheck.log \
      /var/log/backtest/dashboard_health.jsonl
```

### 3. Add Missing Cron Log Redirects
수집 스크립트는 파일 핸들러로 직접 로그를 남긴다. 그래도 cron 자체의 stdout/stderr 보존을 위해 redirect를 함께 유지하는 것을 권장한다.

market_cap, pit_loader, dq_gate cron lines:
```bash
>> /var/log/backtest/market_cap.log 2>&1
>> /var/log/backtest/pit.log 2>&1
>> /var/log/backtest/dq_gate.log 2>&1
```

### 4. Register systemd Service
Create `/etc/systemd/system/backtest-dashboard.service`:
```ini
[Unit]
Description=Stock Backtest Development Diagnostics Dashboard (Streamlit)
After=network.target docker.service

[Service]
Type=simple
User=milmelmul
WorkingDirectory=/opt/stock-backtest
EnvironmentFile=/opt/stock-backtest/.env
ExecStart=/opt/stock-backtest/venv/bin/streamlit run dashboard/app.py \
    --server.port=8501 \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --browser.gatherUsageStats=false
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now backtest-dashboard
```

### 5. Open UFW Port for LAN Only
```bash
sudo ufw allow from 172.30.1.0/24 to any port 8501 proto tcp
sudo ufw reload
```

---

## Verification

1. `sudo systemctl status backtest-dashboard` -> `active (running)`
2. 서버에서 `curl -s http://localhost:8501` -> HTML 응답 확인
3. Windows 브라우저에서 `http://172.30.1.96:8501` 접속
4. `Agent Summary` 탭에서 overall status와 red/yellow flags 확인
5. `/opt/stock-backtest/dashboard/status/health.json` 생성 확인
6. `/opt/stock-backtest/dashboard/status/summary.md` 생성 확인
7. `/var/log/backtest/dashboard_health.jsonl` append 확인
8. 로그 탭에서 민감정보가 마스킹되는지 확인
9. 시스템 탭의 subprocess 항목이 timeout 내 반환되는지 확인
10. DB 장애 또는 로그 파일 누락 시 `UNKNOWN`/`FAIL`로 표시되는지 확인
11. DB 정상 연결 시 Health Matrix의 DB section이 `OK`이며 `DB connectivity/schema checks` evidence를 갖는지 확인
12. 빈 로그 파일만 있는 초기 상태에서 Logs section이 `OK`로 표시되는지 확인

---

## Implementation Notes

- UI는 개발 PC 브라우저 전용이므로 모바일 레이아웃보다 정보 밀도를 우선한다.
- 첫 화면은 `Agent Summary`로 시작한다.
- `health.json`은 에이전트가 읽는 primary interface다.
- `summary.md`는 사람이 읽는 브리핑이다.
- 대시보드가 시스템에 영향을 주면 안 된다. 모든 진단은 읽기 전용이어야 한다.
- 실패한 check는 숨기지 말고 `UNKNOWN` 또는 `FAIL` evidence로 표시한다.
- 로그 원문 표시 전 반드시 sanitize한다.
- 임계값은 초기값으로 시작하고 개발 중 실제 데이터 분포를 보며 조정한다.
