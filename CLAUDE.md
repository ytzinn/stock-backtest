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

### 코드 정합성 규칙 (영구 — 2026-07 감사 산출물)

> 2026-07 코드 정합성 감사(AUDIT_0*.md, `docs/audit/`로 이관)에서 확정된 영구 규칙.
> 백테스트 결과에 영향을 주는 코드를 수정할 때 반드시 지킨다.

- `backtest/data_access.py` 의 모든 조회 함수는 docstring 첫 줄에 **정확한 반환 계약**을
  명시한다. (예: `get_close_price`는 "종가"가 아니라 "as_of 이하 최신 거래일의 종가,
  상폐로 끊겨도 None 이 아님 — 상폐 판정에 쓰지 마라, is_delisted_at() 사용".)
- 조회·네트워크 실패 시 조용한 기본값(0, None, 빈 리스트)을 반환하지 않는다. 예외를 던진다.
  (`BenchmarkDataUnavailable`, `PriceDataUnavailable`.) 기본값이 필요하면 호출자가
  명시적으로 `allow_missing=True` 를 넘기게 한다.
- 계산했지만 소비되지 않는 파라미터를 남기지 않는다 (weight 미소비 사고 = CORR-ENGINE-001).
- 지표 산식(CAGR/Sharpe/MDD/turnover)은 `backtest/metrics.py` + `engine._calc_turnover`
  단일 정의. 복제 금지. CAGR 연수는 실제 캘린더 경과일수 기준. turnover = 0.5×Σ|Δw|.
- 상수(RF/RK/OMEGA/VB_CAP/DELISTING_HAIRCUT/거래비용)는 재선언 금지, `configs/constants.py`·
  `engine` 에서 **import 만**. 테스트도 마찬가지.
- 순회 순서에 의존하는 계산 금지. 불가피하면 정렬 키와 tie-break 를 명시한다
  (`pipeline._rank_key` = `(-upside_pct, ticker)`).
- 엔진은 `date.today()` 를 내부에서 호출하지 않는다. `run(rebalance_dates, valuation_date=...)`
  로 주입받는다. 공식 성과 지표는 **완결 구간(closed)만**으로 계산한다 (열린 구간은 실행일·
  가격 신선도에 종속되므로 참고 지표로만).
- 게이트/PIT 판정은 **최초 공시값(COALESCE(original_amount, amount)) 기준**이다. 정정
  반영값으로 판정하면 룩어헤드다. `amendment_from` 은 `is_amendment=TRUE` 공시에서만 산출한다.
- 테스트 2종을 절대 혼동하지 않는다:
    `tests/characterization/` 은 **기존 동작 기록** — 버그 수정 시 정당하게 깨진다.
      깨졌다고 자동 갱신 금지. baseline 재캡처는 사용자 승인 후 **별도 커밋**.
    `tests/oracle/` · `tests/integration/` 은 **옳음의 증명** — 깨지면 수정이 틀린 것이다.
- 백테스트 결과에 영향을 주는 코드를 수정하면 `pytest -m "not integration"` +
  `pytest -m integration` 전부 통과해야 한다. 통합 테스트 임시 PostgreSQL 은 포트 5434+
  (운영 5433 접속 금지).

### 코드
- Ubuntu 서버에서 실행되는 코드만 작성한다. Windows 전용(.bat, PowerShell 스크립트) 금지.
- cron 명령에 `python3` 직접 호출 금지 → `/opt/stock-backtest/venv/bin/python` 사용.
- DB 포트는 5433 (5432는 stock-analysis 전용, 혼용 금지).
- `stock-analysis/` 저장소와 코드·DB를 공유하지 않는다. import 금지.

### 코드 배포 규칙 (개발PC → 서버 동기화)
- **scp 직접 배포 절대 금지.** 코드는 항상 git을 통해서만 서버에 반영한다.
- **세션에서 파일을 수정했고 다른 주제로 넘어가거나 세션이 끝날 때, 사용자가 요청하지 않아도 아래 3단계를 자동 수행한다:**
  1. `git commit` (로컬)
  2. `git push origin master` (GitHub)
  3. `ssh -i "$env:USERPROFILE\.ssh\id_ed25519" milmelmul@172.30.1.96 "cd /opt/stock-backtest && git pull"` (서버)
- 긴급 핫픽스도 동일 순서. scp 우회 시 세 곳 상태가 갈라져 다음 세션에서 충돌 발생.

### 데이터 정합성
- 백테스트 엔진의 모든 데이터 조회는 `available_from <= rebalance_date` 조건 필수 (룩어헤드 방지).
- `financials_pit` 기준: `fallback_used=TRUE`는 법정마감+5일, 항상 실제 공시일보다 늦음.
- **정정 PIT (2026-07 감사)**: `amendment_from > rebalance_date`면 원본값 사용. 원본이 소실된
  (`original_amount IS NULL`) 정정 계정은 노출 창에서 제외. `amendment_from`은 `is_amendment=TRUE`
  공시에서만 산출한다 (`MAX(rcept_dt) FILTER (WHERE is_amendment)`) — 재공시·중복접수를 정정으로
  오탐하지 마라 (PIT-AMEND-002).
- `universe_gate_pit` PASS 종목만 백테스트에 사용한다. 게이트 판정 입력은 **최초 공시값**
  (`COALESCE(original_amount, amount)`) · **CFS 우선** 이다 (`dq_gate._load_accounts`).
  단, `universe_gate_pit` PK에 시점 차원이 없어 정정 이후 시점에는 판정이 stale하다 —
  미해결 항목 CORR-GATE-003 (SPEC_06 §24 참조).
- `stock_listing_events` 기준으로 리밸런싱 기준일 상장 여부 판단 (stock_listing_history 사용 금지).
- `stocks.listed_date` 백필됨(FDR **KRX-DESC** + listing_events, `ingest/backfill_listed_dates.py`).
  NULL 잔여분은 hard_filter가 가격 이력 최초일(`get_first_price_date`) 프록시로 상장기간 판정.

### Phase 순서
SPEC_06_phases.md의 Phase 순서를 반드시 준수한다.
Phase 0A 게이팅 통과 전 Phase 1 코드 작성 금지.

## 주요 상수
```python
RF, RK = 0.0263, 0.0873  # backtest/configs/constants.py (rim.py, stability_filter.py가 import)
OMEGA  = 0.62             # 초과이익 지속성. V/B = 1 + (adjROE-r)/(1+r-OMEGA)
VB_CAP = 5.0              # V/B 상한 새니티 캡. FV = equity × clamp(V/B, 0, VB_CAP)
```

## 현재 데이터 소스 및 API 한계

**현재 사용 중인 pykrx 함수 (작동 확인, 2026-06):**
- `get_market_ohlcv_by_date(start, end, ticker, adjusted=True)` → 일별 OHLCV (`price_ingest`)
- `get_market_ohlcv(start, end, ticker)` → 시가총액 계산용 (`market_cap_ingest`)

**pykrx 불작동 함수 (사용 금지, KRX 2024 리뉴얼 이후):**

| 불작동 함수 | 증상 | 현재 대체 수단 |
|------------|------|--------------|
| `get_market_cap_by_date()` | 빈 DataFrame | pykrx `get_market_ohlcv()` × 주식수 근사 |
| `get_market_ticker_list()` | 빈 응답 | **KRX Open API** `stk_bydd_trd`/`ksq_bydd_trd` (연도별 정확한 스냅샷) |
| `get_market_sector_classifications()` | 빈 응답 | DB 수동 UPDATE |
| `get_index_ohlcv_by_date()` | KeyError('지수명') | `price_history` DISTINCT date |

**FDR 사용처 (일별 OHLCV는 pykrx 사용, FDR 아님)**
- 상폐 종목 주식수: `fdr.StockListing('KRX-DELISTING')` `ListingShares` 컬럼 (`supplement_delisted()`)
- KOSPI 벤치마크 지수: `fdr.DataReader('KS11')` (Naver Finance 라우트). `'KRX/INDEX/KOSPI'`는 Yahoo fallback → 500 에러

**KRX Open API** (`data-dbg.krx.co.kr`)
- **엔드포인트**: `https://data-dbg.krx.co.kr/svc/apis/sto/{api_id}`
- **인증**: HTTP 헤더 `AUTH_KEY: {키값}` — 서버 `.env`에 `KRX_API_KEY` 저장
- **구독된 API ID**:
  - `stk_bydd_trd` — KOSPI(유가증권) 일별 시세 (종목코드·종목명·시장구분·종가·상장주식수 등)
  - `ksq_bydd_trd` — KOSDAQ 일별 시세 (동일 필드)
  - KONEX(`knx_bydd_trd`)는 미구독 — 사용 금지
- **파라미터**: `basDd=YYYYMMDD` (거래일 기준)
- **응답**: `OutBlock_1` 배열, 주요 필드: `BAS_DD`, `ISU_CD`(6자리), `ISU_NM`, `MKT_NM`, `SECT_TP_NM`, `TDD_CLSPRC`, `LIST_SHRS`
- **용도**: 연말 영업일(`basDd`) 기준 상장 종목 스냅샷 → 연도별 유니버스 구성, `listed_date=NULL` 문제 해결
- **주의**: `ISU_CD`는 6자리 숫자 코드. 우선주·스팩·리츠 포함 → 필터링 필요 시 `ISU_NM`으로 구분

**DART API**
- 일일 한도: 10,000콜, stock-analysis `dart-watcher`와 API 키 공유 중
- 에러 status `020` = 쿼터 초과 → `QuotaExceededError` 즉시 발생, retry 없이 배치 중단
- `fnlttSinglAcnt.json`(주요계정) 사용 금지 → CF 계정 제외됨. 반드시 `fnlttSinglAcntAll.json` 사용

## DB 스키마

### financials 테이블 컬럼
```
id, ticker, corp_code, year, report_type, fs_div, account_nm, amount, frmtrm_amount
```
- `year`: 회계연도 (int), `report_type`: 'FY'/'H1', `fs_div`: 'CFS'/'OFS'
- `bsns_year`, `reprt_code`, `period_div` 같은 컬럼은 존재하지 않음 — 헷갈리지 말 것

### account_nm suffix 규칙 (`_N`)
- `지배기업소유주지분_1`, `비지배지분_1` 등 `_숫자` suffix는 **당기/전기 구분이 아님**
- DART 응답에서 동일 account_nm이 중복 출현할 때 붙이는 **이름 충돌 fallback** (첫 번째=no suffix, 두 번째=`_1`, ...)
- `자본총계` 없이 `지배기업소유주지분_1`만 있다면 → DART API가 해당 연도에 `자본총계` 행을 반환하지 않은 것 (DART 데이터 한계, 코드 버그 아님)

## 서버 명령 실행 패턴

- **서버 SSH/SCP 명령은 반드시 PowerShell 툴로만 실행.** Bash 툴은 `$env:USERPROFILE` 구문을 인식 못 해 SSH 키 경로가 깨지고 `Host key verification failed`로 항상 실패한다.
- **SSH**: 항상 `-i "$env:USERPROFILE\.ssh\id_ed25519"` 포함. 생략 시 인증 실패.
- **psql 금지**: 서버 호스트 PATH에 psql 없음(Docker 내부 전용). DB 조회는 psycopg2 스크립트로.
- **멀티라인 Python**: PowerShell→SSH 직접 전달 시 따옴표 3중 충돌로 항상 실패.
  패턴: `$script=@'...'@ | Out-File "$env:TEMP\t.py"` → `scp -i ... t.py :/tmp/t.py` → `ssh ... "venv/bin/python /tmp/t.py"`
- **백그라운드 모듈**: `nohup python -m X` 단독 실행 시 ModuleNotFoundError.
  패턴: `ssh -i "..." user@host "cd /opt/stock-backtest && nohup venv/bin/python -m ingest.X >> /opt/stock-backtest/logs/X.log 2>&1 &"` (double quotes, 절대경로 필수)
- **현황 확인 순서**: ① `GET http://172.30.1.96:8502/health` (JSON) → ② SSH `dashboard/status/health.json` → ③ psycopg2 직접 쿼리. 신규 스크립트 작성은 마지막 수단.


