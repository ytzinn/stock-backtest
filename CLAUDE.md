# stock-backtest — Claude Code 지침

## 프로젝트 개요
RIM(잔여이익모델) 기반 한국 주식 멀티팩터 백테스트 머신.
전체 설계는 MASTER.md → 각 SPEC_0N_*.md 순서로 읽어라.

## 실행 환경
- **코드 작성**: Windows 11 개발 PC (현재 디렉토리)
- **실행**: Ubuntu 26.04 서버 `/opt/stock-backtest/` (SSH: milmelmul@172.30.1.96)
- **DB**: PostgreSQL 16, 포트 5433 (docker-compose.yml)
- **Python**: `/opt/stock-backtest/venv/bin/python` (cron에서 절대경로 사용)

## 반드시 지킬 규칙

### 코드
- Ubuntu 서버에서 실행되는 코드만 작성한다. Windows 전용(.bat, PowerShell 스크립트) 금지.
- cron 명령에 `python3` 직접 호출 금지 → `/opt/stock-backtest/venv/bin/python` 사용.
- DB 포트는 5433 (5432는 stock-analysis 전용, 혼용 금지).
- `stock-analysis/` 저장소와 코드·DB를 공유하지 않는다. import 금지.

### 코드 배포 규칙 (개발PC → 서버 동기화)
- **scp 직접 배포 절대 금지.** 코드는 항상 git을 통해서만 서버에 반영한다.
- 코드 수정 후 서버 반영 순서:
  1. `git commit` (로컬)
  2. `git push origin master` (GitHub)
  3. `ssh ... "cd /opt/stock-backtest && git pull"` (서버)
- 긴급 핫픽스도 동일 순서. scp 우회 시 세 곳 상태가 갈라져 다음 세션에서 충돌 발생.

### 데이터 정합성
- 백테스트 엔진의 모든 데이터 조회는 `available_from <= rebalance_date` 조건 필수 (룩어헤드 방지).
- `financials_pit` 기준: `fallback_used=TRUE`는 법정마감+5일, 항상 실제 공시일보다 늦음.
- `universe_gate_pit` PASS 종목만 백테스트에 사용한다.
- `stock_listing_events` 기준으로 리밸런싱 기준일 상장 여부 판단 (stock_listing_history 사용 금지).

### Phase 순서
SPEC_06_phases.md의 Phase 순서를 반드시 준수한다.
Phase 0A 게이팅 통과 전 Phase 1 코드 작성 금지.

## 주요 상수 (변경 시 두 파일 동시 수정)
```python
RF, RK = 0.0263, 0.0873  # backtest/models/rim.py, backtest/filters/stability_filter.py
```

## 알려진 API 한계 및 대체 수단

pykrx는 KRX 2024 웹 리뉴얼 이후 다수 함수가 불작동한다. 아래 목록 외 pykrx 함수를 새로 사용할 경우 반드시 빈 응답 여부를 확인한다.

| 불작동 함수 | 증상 | 현재 대체 수단 |
|------------|------|--------------|
| `get_market_ohlcv_by_date()` | 빈 DataFrame | `fdr.DataReader(ticker, start, end)` |
| `get_market_cap_by_date()` | 빈 DataFrame | `fdr.StockListing('KRX')` 현재 주식수 × 종가 근사 (상폐 종목 미적용) |
| `get_market_ticker_list()` | 빈 응답 | `fdr.StockListing('KRX')` 스냅샷 |
| `get_market_sector_classifications()` | 빈 응답 | 최근 5거래일 retry → 실패 시 DB 수동 UPDATE |

**FDR 한계**
- `adj_close`: FDR은 단일 종가만 제공 → `adj_close = close`로 처리 (Naver 기준 수정주가 포함됨)
- `turnover`: `volume × close` 근사값 (실제 거래대금 아님)
- `market_cap`: 현재 주식수 기준 추정 (유상증자·감자 이력 미반영)
- 상폐 종목 주식수: `fdr.StockListing('KRX')` 현재 상장 목록만 제공 → `fdr.StockListing('KRX-DELISTING')`의 `ListingShares` 컬럼으로 보완 (`supplement_delisted()`)
- KOSPI 지수: `fdr.DataReader('KS11')` 사용 (Naver Finance 라우트). `'KRX/INDEX/KOSPI'`는 Yahoo fallback → 500 에러

**DART API**
- 일일 한도: 10,000콜, stock-analysis `dart-watcher`와 API 키 공유 중
- 새 키 발급 전까지 cron을 KST 00:05(쿼터 리셋 직후)에 실행 (`5 15 * * *` UTC)
- 에러 status `020` = 쿼터 초과 → `QuotaExceededError` 즉시 발생, retry 없이 배치 중단
- `fnlttSinglAcnt.json`(주요계정) 사용 금지 → CF 계정 제외됨. 반드시 `fnlttSinglAcntAll.json` 사용

## 서버 명령 실행 패턴

- **SSH**: 항상 `-i "$env:USERPROFILE\.ssh\id_ed25519"` 포함. 생략 시 인증 실패.
- **psql 금지**: 서버 호스트 PATH에 psql 없음(Docker 내부 전용). DB 조회는 psycopg2 스크립트로.
- **멀티라인 Python**: PowerShell→SSH 직접 전달 시 따옴표 3중 충돌로 항상 실패.
  패턴: `$script=@'...'@ | Out-File "$env:TEMP\t.py"` → `scp -i ... t.py :/tmp/t.py` → `ssh ... "venv/bin/python /tmp/t.py"`
- **백그라운드 모듈**: `nohup python -m X` 단독 실행 시 ModuleNotFoundError.
  패턴: `ssh -i "..." user@host "cd /opt/stock-backtest && nohup venv/bin/python -m ingest.X >> /opt/stock-backtest/logs/X.log 2>&1 &"` (double quotes, 절대경로 필수)
- **현황 확인 순서**: ① 로컬 `dashboard_health_server.json` → ② SSH `dashboard/status/health.json` → ③ psycopg2 직접 쿼리. 신규 스크립트 작성은 마지막 수단.

## 실행 순서 (Phase 0)
```bash
# 1. DB 스키마 적용
psql -h localhost -p 5433 -U postgres -d backtest -f ingest/schema.sql

# 2. 전종목 초기화
python -m ingest.universe_loader --init
python -m ingest.universe_loader --financial-flag

# 3. 상장폐지 종목
python -m ingest.delisting_ingest

# 4. DART 재무 (14일+ 분산)
python -m ingest.dart_ingest --skip-if-done

# 5. 가격 + 시가총액
python -m ingest.price_ingest --skip-if-done
python -m ingest.market_cap_ingest --skip-if-done

# 6. PIT 변환 + DQ Gate
python -m ingest.pit_loader
python -m ingest.dq_gate

# 7. Phase 0A 게이팅 확인
python -m ingest.pit_loader --check-fallback
python -m ingest.dq_gate --report
```

