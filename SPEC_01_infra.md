# SPEC_01 — 인프라 & 시스템 구조

> **관련 파일**: `docker-compose.yml`, `scripts/`, `.env`
> **선행 조건**: 없음 (최초 설정)
> **Claude Code 지시**: 이 파일의 §0 구성을 그대로 따라 환경을 설정하라.
>   Ubuntu 서버 기준. Windows 전용 파일(.bat) 작성 금지.

---

# 0. 인프라 운영 환경

## 0-1. 환경 구성 원칙

| 구분 | 환경 | 역할 |
|------|------|------|
| 개발 PC | Windows 11 | 코드 작성, Git push, 브라우저 확인 |
| 서버 | Ubuntu 26.04 LTS (Beelink SER8, LAN) | DB, 수집 배치, 백테스트 연산, 서비스 서빙 |
| 접속 방식 | VS Code Remote SSH | 서버 파일을 로컬처럼 편집·실행 |

**Railway 대비 Ubuntu 서버의 핵심 차이점:**
- pykrx(KRX OHLCV/PER/PBR 수집)가 해외 IP 차단을 받지 않아 서버에서 직접 실행 가능 (국내 IP 기준, 실제 차단 여부는 첫 실행 시 확인 필요 — Claude의 의견)
- Windows 작업 스케줄러 없이 cron으로 모든 배치를 서버에서 처리 → 개발 PC를 켜놓을 필요 없음
- Docker Engine 네이티브 실행 (Docker Desktop WSL2 레이어 없음) → DB I/O 성능 개선

## 0-2. Ubuntu 서버 구성

```
[Windows 11 개발 PC]
  VS Code (Remote SSH)
  Git push
  브라우저 (대시보드 확인)

        ↕ SSH / Git (LAN 172.30.1.96)

[Ubuntu 서버 (Beelink SER8, LAN)]
  ├─ PostgreSQL 16 (Docker Engine, 포트 5433)  ← 백테스트 전용 DB
  ├─ cron: DART 수집 (평일 KST 18:00 + 재시도 18:30)
  ├─ cron: pykrx 수집 (평일 KST 19:00 + 재시도 20:00)  ← stock-analysis와 동일 방식
  ├─ cron: healthcheck (평일 KST 21:00)
  ├─ cron: 백테스트 배치 연산 (야간 예약)
  └─ systemd: FastAPI (stock-analysis 대시보드 서빙, 포트 8000)
```

**서버 하드웨어 명세:**

```
모델        : Beelink SER8
RAM         : 32GB DDR5 (5600 MHz)
스토리지    : NVMe 931.5GB (LVM ubuntu-vg, 루트 파티션 914GB, ~864GB 여유)
OS          : Ubuntu 26.04 LTS (Linux 7.0.0-15-generic)
네트워크    : WiFi (wlp2s0), 고정 IP 172.30.1.96 (로컬 LAN) — 유선 불가
사용자      : milmelmul  /  호스트명: milmuelmulbacktest
```

> WiFi 전용 환경임. 네트워크 끊김 대응책은 §0-9 참조.

## 0-3. Docker Engine 설치 (Ubuntu)

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2
sudo systemctl enable --now docker
sudo usermod -aG docker $USER   # sudo 없이 docker 명령 사용
```

`docker-compose.yml` 파일 자체는 수정 없이 그대로 사용. 포트 5433 사용.

## 0-3-1. Docker Volume 전략

PostgreSQL 데이터는 **named volume(pgdata)** 으로 관리하고, 백업은 외부 NAS/디스크에 저장한다.

```yaml
# docker-compose.yml — 볼륨 및 포트 바인딩 핵심 부분
volumes:
  pgdata:
    driver: local

services:
  db:
    image: postgres:16
    ports:
      - "127.0.0.1:5433:5432"   # localhost만 바인딩 — 외부 노출 방지
    volumes:
      - pgdata:/var/lib/postgresql/data
    environment:
      POSTGRES_DB: backtest
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    restart: unless-stopped
```

```bash
# scripts/backup_db.sh
#!/bin/bash
set -e
BACKUP_DIR="/mnt/external/backtest_backups"   # 외부 디스크/NAS 마운트 포인트
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p "${BACKUP_DIR}"
docker exec backtest-db pg_dump -U postgres backtest \
  > "${BACKUP_DIR}/backtest_${DATE}.sql"
# 30일 이상 된 백업 자동 삭제
find "${BACKUP_DIR}" -name "*.sql" -mtime +30 -delete
echo "$(date): backup OK → ${BACKUP_DIR}/backtest_${DATE}.sql"
```

> 외부 디스크 마운트 경로(`/mnt/external`)는 실제 장치에 맞게 조정.

## 0-3-2. Python 가상환경 (venv)

```bash
# 최초 1회 설정 (Ubuntu 서버)
python3 -m venv /opt/stock-backtest/venv
source /opt/stock-backtest/venv/bin/activate
pip install -r requirements.txt
```

cron에서는 venv 활성화 없이 **절대경로** 직접 호출:

```bash
# 올바른 방식 ✓
/opt/stock-backtest/venv/bin/python -m ingest.dart_ingest

# 잘못된 방식 ✗ (cron에서는 PATH가 제한되어 python3가 시스템 Python을 가리킴)
python3 -m ingest.dart_ingest
```

## 0-4. cron 스케줄 (Ubuntu 서버)

```bash
# crontab -e 로 등록
# VENV, WORKDIR 변수는 crontab 상단에 선언
VENV=/opt/stock-backtest/venv/bin/python
WORKDIR=/opt/stock-backtest

# ── 백테스트 DB 전용 ────────────────────────────────────────────────────────
# DART 수집 (평일 KST 18:00 = UTC 09:00)
0 9 * * 1-5  cd $WORKDIR && $VENV -m ingest.dart_ingest >> /var/log/backtest/dart.log 2>&1
# WiFi 끊김 대비 재시도 (KST 18:30 = UTC 09:30)
30 9 * * 1-5  cd $WORKDIR && $VENV -m ingest.dart_ingest --skip-if-done >> /var/log/backtest/dart_retry.log 2>&1

# pykrx OHLCV + 펀더멘털 수집 (평일 KST 19:00 = UTC 10:00)
0 10 * * 1-5  cd $WORKDIR && $VENV -m ingest.price_ingest >> /var/log/backtest/price.log 2>&1
# WiFi 끊김 대비 재시도 (KST 20:00 = UTC 11:00)
0 11 * * 1-5  cd $WORKDIR && $VENV -m ingest.price_ingest --skip-if-done >> /var/log/backtest/price_retry.log 2>&1

# healthcheck — 수집 완료 여부 확인 (평일 KST 21:00 = UTC 12:00)
0 12 * * 1-5  cd $WORKDIR && $VENV -m ingest.healthcheck >> /var/log/backtest/healthcheck.log 2>&1

# DB 백업 (매주 일요일 KST 10:00 = UTC 01:00)
0 1 * * 0  $WORKDIR/scripts/backup_db.sh >> /var/log/backtest/backup.log 2>&1
```

`--skip-if-done`: 해당 날짜 데이터가 DB에 이미 존재하면 조기 종료. 중복 수집 방지.

## 0-4-1. healthcheck 스크립트

수집 완료 여부를 DB row count로 검증한다. 실패 시 로그에 ERROR를 기록한다.

```python
# ingest/healthcheck.py (골격)
# - 오늘 날짜 기준 price_history / financials 행 존재 여부 확인
# - 미존재 시: logging.error() → /var/log/backtest/healthcheck.log 에 ERROR 행 기록
# - 정상 시: logging.info() 로 row count 기록
# TODO: 향후 Telegram 봇 또는 이메일(mailx) 알림 추가
```

> 알림 체계(Telegram/이메일) 추가 전까지는 로그 파일로만 관리.

## 0-5. systemd 서비스 (stock-analysis 대시보드)

백테스트 프로젝트와 별개로, stock-analysis 대시보드를 Ubuntu에서 서비스로 관리하는 경우에만 작성.

```ini
# /etc/systemd/system/stock-api.service
[Unit]
Description=Stock Analysis API
After=docker.service

[Service]
WorkingDirectory=/opt/stock-analysis
ExecStart=/usr/bin/python3 -m uvicorn api.main:app --host 0.0.0.0 --port 8000
Restart=on-failure
EnvironmentFile=/opt/stock-analysis/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now stock-api
```

## 0-5-1. SSH Key 인증 설정

비밀번호 인증을 SSH Key 인증으로 전환한다. VS Code Remote SSH 자동 접속에도 필요하다.

```powershell
# Windows 11 PowerShell에서 실행 (최초 1회)
ssh-keygen -t ed25519 -C "stock-backtest"
# 생성된 공개키를 서버에 등록
ssh-copy-id milmelmul@172.30.1.96
```

```
# C:\Users\진윤태\.ssh\config 에 추가
Host backtest-server
    HostName 172.30.1.96
    User milmelmul
    IdentityFile ~/.ssh/id_ed25519
```

VS Code Remote SSH에서 `backtest-server` 호스트명으로 접속하면 비밀번호 없이 자동 연결된다.

## 0-6. 로그 디렉토리 초기화

```bash
sudo mkdir -p /var/log/backtest
sudo chown $USER /var/log/backtest
```

## 0-7. UFW 방화벽 설정

```bash
sudo ufw allow OpenSSH
sudo ufw enable
# 5433은 docker-compose에서 127.0.0.1:5433 바인딩으로 이미 외부 차단됨
# UFW 규칙 추가 없이도 외부 노출되지 않음
sudo ufw status  # 확인용
```

**DBeaver(또는 다른 DB GUI)에서 접속할 때:**

```powershell
# Windows PowerShell에서 SSH 터널 열기
ssh -L 5433:localhost:5433 milmelmul@172.30.1.96
# 터널이 열린 상태에서 DBeaver → localhost:5433 으로 접속
```

## 0-8. .env 파일 구성

```env
# .env — git 제외 필수 (.gitignore에 추가)
DART_API_KEY=your_dart_api_key_here

# PostgreSQL 접속 (docker-compose와 일치)
DB_HOST=localhost
DB_PORT=5433
DB_NAME=backtest
DB_USER=postgres
DB_PASSWORD=your_db_password_here

# 이후 필요 시 추가 (LOG_LEVEL, 알림 토큰 등)
```

## 0-9. WiFi 안정성 대응

서버가 WiFi 전용 환경(wlp2s0)이므로 네트워크 끊김 시 수집 실패를 대비한다.

**cron 레벨:** DART +30분, pykrx +60분 재시도 슬롯 등록 (§0-4 참조)

**코드 레벨:**
- `--skip-if-done` 플래그: 오늘 날짜 데이터가 DB에 이미 존재하면 조기 종료 (중복 방지)
- HTTP 요청 실패 시 exponential backoff retry → `tenacity` 라이브러리 권장

```python
# 예시 (ingest 공통 유틸)
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=30))
def fetch_with_retry(url):
    ...
```

**NetworkManager 자동 재연결:** Ubuntu 기본 동작으로 별도 설정 불필요.

---

