# deploy — 서버 서비스 정의

## 왜 repo 에 있나

`backtest-dashboard.service` 는 서버 `/etc/systemd/system/` 에만 있었고 repo 에 없었다.
그래서 두 가지가 벌어졌다:

1. **유닛의 오타를 아무도 못 봤다.** 서버 사본에 두 군데 결함이 있었다 —
   `--server.port=8501 -- server.address=0.0.0.0` 의 **`-- ` 공백**(streamlit 에서 `--` 는
   인자 분리자라 `server.address=...` 가 앱의 위치 인자로 넘어간다)과
   `BACKTEST_LOG_DIR=var/log/backtest` 의 **빠진 앞 슬래시**(상대 경로라
   `/opt/stock-backtest/var/log/backtest` 로 해석된다).
2. **유닛이 죽어 있는데 대시보드는 떠 있었다.** 실제로는 손으로 띄운 nohup 프로세스가
   8501 을 물고 있었다(2026-08-14 확인 시점 50일 가동). `systemctl restart` 로는 배포가
   반영되지 않고, 배포가 안 먹는 이유를 엉뚱한 데서 찾게 된다.

여기 있는 파일이 **정본**이다. 서버 사본이 이것과 다르면 서버 쪽이 틀린 것이다.

## 설치 — 인터랙티브 SSH 로 **한 번만** (콘솔에 갈 필요 없다)

Tailscale 로 어디서든 붙어서 아래를 한 번 실행하면 끝난다. 이후 배포는 비밀번호 없이
`sudo systemctl restart backtest-dashboard.service` 로 처리된다.

```bash
ssh milmelmul@100.120.62.97          # 사무실 밖에서도 붙는다 (CLAUDE.md)

# ① 유닛 교체 (repo 가 정본)
sudo cp /opt/stock-backtest/deploy/backtest-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload

# ② 서비스 제어만 비밀번호 면제 — 이 서버의 stock-api 와 같은 패턴
sudo cp /opt/stock-backtest/deploy/sudoers.d-backtest-dashboard /etc/sudoers.d/backtest-dashboard
sudo chmod 440 /etc/sudoers.d/backtest-dashboard
sudo visudo -c                        # 문법 검증 — 반드시 통과 확인

# ③ 손으로 띄운 nohup 을 내리고 서비스로 전환 (8501 을 이미 물고 있으면 기동 실패)
ps -eo pid,cmd | grep '[s]treamlit run dashboard/app.py' | grep 8501
kill <PID>
sudo systemctl enable --now backtest-dashboard.service
systemctl status backtest-dashboard.service --no-pager
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8501/health   # 200 이어야 한다
```

### 왜 sudo 비밀번호를 파일에 저장하지 않는가

평문으로 두면 `milmelmul` 로 도는 **모든 프로세스**(크론·대시보드·배포 스크립트)가
root 가 될 수 있다 — sudo 를 두는 의미가 사라진다. 위 NOPASSWD 규칙은 **명시된 네
명령만** root 로 돌린다. 필요한 인터랙티브 작업은 어느 쪽이든 설치 1회로 같으므로
편의는 동일하고 위험만 다르다.

repo 안에 비밀번호 파일을 두는 것은 특히 위험하다. `.gitignore` 의 `.env` 는 **정확히
그 이름만** 잡아서 `sudo.env` 같은 이름은 안 걸리고, 이 저장소는 GitHub 로 푸시된다.

## (참고) 수동 설치 — sudo 필요

```bash
sudo cp /opt/stock-backtest/deploy/backtest-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now backtest-dashboard.service
systemctl status backtest-dashboard.service --no-pager
```

**손으로 띄운 nohup 프로세스가 떠 있으면 먼저 내려야 한다** — 8501 을 이미 물고 있어
서비스가 기동에 실패한다.

```bash
ps -eo pid,cmd | grep '[s]treamlit run dashboard/app.py' | grep 8501   # PID 확인
kill <PID>                                                              # PID 로 정지
```

> ⚠️ **`pkill -f 'streamlit run dashboard/app.py --server.port 8501'` 를 쓰지 마라.**
> 그 문자열이 ssh 가 실행하는 `bash -c ...` 명령줄에도 들어 있어 **pkill 이 자기 세션까지
> 죽인다.** 2026-08-14 에 실제로 이렇게 대시보드를 2~3분 내렸다. 반드시 `ps` 로 PID 를
> 얻어 그 PID 만 kill 한다.

## 배포 후 반영

코드를 pull 한 뒤:

```bash
sudo systemctl restart backtest-dashboard.service
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8501/health   # 200 이어야 한다
```

페이지 코드만 고쳤다면 Streamlit 이 매 실행 때 스크립트를 다시 읽으므로 재시작이 필수는
아니다. **새 import 모듈을 추가했으면 재시작해야 확실하다.**

## 정합성

`tests/integrity/test_deploy_unit.py` 가 이 파일의 형태를 검사한다 — 위 두 결함(인자
분리자 오타·상대 로그 경로)을 실제로 잡는다. 서버 사본과의 대조는 자동화하지 않았다
(`systemctl cat` 은 sudo 없이 읽히지만 CI 가 서버에 붙지 않는다). 유닛을 고치면
**repo 를 먼저 고치고 서버로 복사**하는 순서를 지킨다.
