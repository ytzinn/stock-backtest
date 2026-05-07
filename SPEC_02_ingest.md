# SPEC_02 — 데이터 수집 & DB 스키마 & Data Integrity

> **관련 파일**: `ingest/schema.sql`, `ingest/dart_ingest.py`, `ingest/price_ingest.py`,
>   `ingest/market_cap_ingest.py`, `ingest/delisting_ingest.py`,
>   `ingest/universe_loader.py`, `ingest/pit_loader.py`,
>   `ingest/validator.py`, `ingest/dq_gate.py`
> **선행 조건**: SPEC_01 완료 (Docker PostgreSQL 가동 확인)
> **Claude Code 지시**:
>   1. schema.sql을 먼저 작성하고 `psql`로 적용 확인 후 수집 코드를 작성하라.
>   2. 각 수집 함수는 `ingest_status` 테이블에 상태를 기록하라.
>   3. DQ Gate는 Phase 0A 샘플 30종목 기준으로 먼저 검증하라.

---

# 3. Phase 0 — 전종목 데이터 수집

## 3-1. 수집 규모 및 스케줄

| 항목 | 수치 |
|------|------|
| 대상 종목 수 | ~2,500개 (현재 상장) + 상장폐지 종목 (~1,500개 추정) |
| 수집 시작 연도 | 2014년 (백테스트 2015년 시작 기준 FY 데이터 확보) |
| DART 호출 수 (현재 상장) | 2,500 × 48 ≈ 120,000회 |
| DART 일일 한도 | 10,000회 |
| 최소 수집 소요 | 14일 이상 (증분 수집) |

**수집 실행 환경 (Ubuntu 서버 cron):**

| 작업 | 스케줄 | 명령 |
|------|--------|------|
| DART 재무·공시 수집 | 평일 KST 18:00 | `/opt/stock-backtest/venv/bin/python -m ingest.dart_ingest` |
| pykrx OHLCV + 펀더멘털 | 평일 KST 19:00 | `/opt/stock-backtest/venv/bin/python -m ingest.price_ingest` |
| DB 백업 | 매주 일요일 KST 10:00 | `scripts/backup_db.sh` |

> **venv 절대경로 필수**: cron 환경에서 PATH가 제한되므로 `python3` 직접 호출 금지.
> 올바른 방식: `/opt/stock-backtest/venv/bin/python -m ingest.dart_ingest`
> 잘못된 방식: `python3 -m ingest.dart_ingest` (시스템 Python을 가리킴)

pykrx(KRX OHLCV/PER/PBR)는 Ubuntu 서버의 국내 IP에서 직접 실행한다.
stock-analysis에서 Railway(해외 IP) 차단으로 로컬 PC에서만 실행하던 제약이 해소된다.
(단, KRX IP 정책에 따라 첫 실행 시 차단 여부를 반드시 확인할 것)

## 3-2. 생존편향 해소 — 상장폐지 종목 수집

백테스트에서 생존편향(Survivorship Bias)을 제거하기 위해 현재 상장 종목뿐 아니라 상장폐지 종목의 이력 데이터도 수집한다. 상장폐지된 종목이 포트폴리오에 편입됐을 경우 마지막 거래일 종가로 청산 처리한다.

```python
# ingest/delisting_ingest.py

import FinanceDataReader as fdr

def collect_delisting_universe() -> list[dict]:
    """
    FDR KRX-DELISTING으로 상장폐지 종목 목록 수집.
    반환: [{ticker, corp_name, market, listed_date, delisted_date, delist_reason}]
    """
    df = fdr.StockListing('KRX-DELISTING')
    result = []
    for _, row in df.iterrows():
        result.append({
            'ticker':        row.get('Code', ''),
            'corp_name':     row.get('Name', ''),
            'market':        row.get('Market', ''),
            'listed_date':   row.get('ListingDate'),
            'delisted_date': row.get('DelistingDate'),
            'delist_reason': row.get('Reason', ''),
        })
    return result

def collect_delisting_price_history(ticker: str, listed_date, delisted_date):
    """상장폐지 종목 가격 이력 수집 (pykrx OHLCV)."""
    start = listed_date.strftime('%Y%m%d') if listed_date else '20140101'
    end   = delisted_date.strftime('%Y%m%d') if delisted_date else _today()
    # price_ingest.py의 collect_price_and_turnover() 재사용
    collect_price_and_turnover(ticker, start=start, end=end)
```

**상장폐지 종목 청산 처리 원칙:**
- 포트폴리오 보유 중 상장폐지 발생 시 → 아래 3개 시나리오를 병렬로 계산, 리포트에 범위로 표시
- 거래정지(`is_suspended=TRUE`) 발생 시 → 다음 리밸런싱까지 보유 유지, 청산 불가 포지션으로 별도 기록

**상장폐지 청산 시나리오 (3개 병렬):**

| 시나리오 | 청산 가격 | 근거 |
|---------|----------|------|
| 낙관 | 마지막 거래일 종가 | 실제 매도 가능한 최선 케이스 |
| **기준 (메인)** | 마지막 거래일 종가 × 70% | 정리매매 하한가 연속 현실 반영 |
| 보수 | 100% 손실 (0원 청산) | 최악 케이스 (유동성 완전 소멸) |

최종 리포트는 기준 시나리오를 메인 지표로 사용하고, 낙관/보수 범위를 괄호로 병기한다.
예: `CAGR 12.3% (낙관 12.8% / 보수 11.1%)`

## 3-3. 과거 시점 상장 종목 목록

리밸런싱 기준일(21개)마다 당시 실제 상장 종목만 유니버스에 포함하기 위해 과거 시점 종목 목록을 수집한다.

```python
# ingest/universe_loader.py

def collect_historical_universe(rebalance_dates: list) -> None:
    """
    리밸런싱 기준일마다 pykrx get_market_ticker_list(날짜)로
    당시 상장 종목 목록 수집 → stock_listing_events 테이블에 저장.
    """
    from pykrx import stock as krx
    for rd in rebalance_dates:
        date_str = rd.strftime('%Y%m%d')
        for market in ('KOSPI', 'KOSDAQ'):
            tickers = krx.get_market_ticker_list(date_str, market=market)
            for ticker in tickers:
                upsert_listing_event(ticker, market, rd)
```

## 3-3-1. pykrx 티커 목록 → stock_listing_events 저장 전략

> **⚠️ 2026-05 pykrx 제약**: `get_market_ticker_list()` 불작동 (KRX 2024 웹 API 변경으로 OTP 엔드포인트 404).
> `collect_historical_universe()` 및 `update_listing_events_daily()` 현재 실행 불가.
> 대안: FDR StockListing 스냅샷 비교 방식으로 대체 예정.

pykrx `get_market_ticker_list(날짜)`는 해당 날의 전체 상장 종목 스냅샷을 반환한다.
이벤트 이력 구조(stock_listing_events)로 변환하기 위해 **전일 대비 delta** 를 계산해 저장한다.

```python
# ingest/universe_loader.py

def update_listing_events_daily(market: str = 'KOSPI') -> None:
    """
    전일 대비 delta → listed / delisted 이벤트 저장.
    historical 수집은 collect_historical_universe() 사용.
    """
    from pykrx import stock as krx
    today     = _today_str()
    yesterday = _prev_trading_day_str()

    today_set     = set(krx.get_market_ticker_list(today,     market=market))
    yesterday_set = set(krx.get_market_ticker_list(yesterday, market=market))

    for ticker in today_set - yesterday_set:        # 신규 상장
        insert_listing_event(ticker, market, event_type='listed',
                             listed_date=today, source='pykrx')
    for ticker in yesterday_set - today_set:        # 상장폐지
        insert_listing_event(ticker, market, event_type='delisted',
                             delisted_date=today, source='pykrx')
```

**유니버스 복원 쿼리 설명**: `stock_listing_events`에서 리밸런싱 기준일 기준으로 상장 중인 종목을 단일 테이블 쿼리로 복원한다 (§5-3 리밸런싱 유니버스 복원 쿼리 참조). 별도 스냅샷 테이블 없이 event_type + listed_date/delisted_date 조건만으로 PIT 일관성 유지.

## 3-3-2. is_financial 판정 (KRX 섹터코드)

금융업(은행·보험·증권·자산운용) 종목은 부채비율 기준이 일반 제조업과 다르므로
재무안정성 필터 R1(부채비율 > 200%) 적용 전에 `stocks.is_financial=TRUE`로 분류해 필터를 건너뛴다.

```python
# ingest/universe_loader.py

def update_financial_flag() -> None:
    """
    pykrx get_market_sector_classifications()로 KRX 섹터코드 조회.
    섹터명에 '금융', '은행', '보험', '증권'이 포함되면 is_financial=TRUE.

    ⚠️ 2026-05: KRX 섹터 API 불작동으로 현재 실행 불가.
    최근 5 거래일 순차 시도하나 모두 빈 응답 반환.
    is_financial은 수동으로 직접 UPDATE 필요.
    """
    from pykrx import stock as krx
    from datetime import date, timedelta
    df = None
    for offset in range(5):
        d = (date.today() - timedelta(days=offset)).strftime('%Y%m%d')
        try:
            df = krx.get_market_sector_classifications(d, market='ALL')
            if df is not None and not df.empty:
                break
        except Exception:
            df = None
    if df is None or df.empty:
        log.warning('섹터 분류 조회 실패 — is_financial 수동 설정 필요')
        return
    for _, row in df.iterrows():
        ticker     = row.get('Code', '')
        sector_nm  = row.get('Sector', '')
        is_fin     = any(kw in sector_nm for kw in ('금융', '은행', '보험', '증권'))
        update_stock_is_financial(ticker, is_fin)
```

> **⚠️ 2026-05 현황**: KRX 섹터 API 불작동으로 `update_financial_flag()` 실행 불가.
> `is_financial`은 DB에서 직접 수동 업데이트: `UPDATE stocks SET is_financial=TRUE WHERE corp_name LIKE '%은행%' OR ...`

## 3-4. 수정주가 수집

액면분할·무상증자로 인한 주가 불연속성을 제거하기 위해 수정주가를 별도 수집한다.

```python
# ingest/price_ingest.py

def collect_price_and_turnover(ticker: str, start: str = '20140101', end: str = None) -> int:
    """
    pykrx get_market_ohlcv()로 일별 OHLCV + 수정주가 수집.

    ⚠️ 2026-05: get_market_ohlcv_by_date() 불작동 (KRX API 변경).
    get_market_ohlcv(start, end, ticker) 로 대체.

    adj_close: 액면분할·무상증자 반영 수정주가 (배당 미반영).
    turnover: 거래대금 컬럼 없음 → volume × close 근사값.
    수익률 계산에는 adj_close 사용. 모멘텀 MA 계산도 adj_close 기준.
    """
    from pykrx import stock as krx
    df_raw = krx.get_market_ohlcv(start, end or _today(), ticker, adjusted=False)
    df_adj = krx.get_market_ohlcv(start, end or _today(), ticker, adjusted=True)
    for idx in df_raw.index:
        raw = df_raw.loc[idx]
        close     = float(raw.get('종가', 0)) or None
        volume    = int(raw.get('거래량', 0)) or None
        adj_close = float(df_adj.loc[idx]['종가']) if idx in df_adj.index else close
        turnover  = (volume * close) if (volume and close) else None  # 근사
        is_suspended = volume is None or volume == 0
```

**배당 처리 확정**: 수익률 계산에서 배당 제외. 벤치마크(KOSPI)도 동일하게 배당 미반영으로 통일.
성과 보고 시 한계 명시: *"배당 미반영, 수정주가(액면분할·무상증자 조정) 기준 수익률"*

## 3-5. 시가총액·상장주식수 수집

```python
# ingest/market_cap_ingest.py

def _load_shares() -> dict[str, int]:
    """FDR StockListing으로 현재 상장주식수 로드."""
    import FinanceDataReader as fdr
    listing = fdr.StockListing('KRX')
    return {
        str(row['Code']).strip(): int(row['Stocks'])
        for _, row in listing.iterrows()
        if row.get('Stocks') and int(row['Stocks']) > 0
    }

def collect_market_cap(ticker: str, shares: int, start: str = '20140101', end: str = None) -> int:
    """
    pykrx 종가 × 상장주식수(FDR) → market_cap 추정 후 market_cap_history upsert.

    ⚠️ 2026-05: get_market_cap_by_date() 불작동 (KRX API 변경).
    대안: FDR StockListing의 현재 Stocks 컬럼 × 일별 종가로 근사.
    한계: 주식수 변경 이력(유상증자·감자) 미반영, 현재 주식수로 전 기간 적용.
    """
    from pykrx import stock as krx
    df = krx.get_market_ohlcv(start, end or _today(), ticker, adjusted=False)
    rows = [
        (ticker, idx.date(), float(row['종가']) * shares if row.get('종가') else None,
         shares, 'fdr_shares')
        for idx, row in df.iterrows() if row.get('종가')
    ]
    # market_cap_history 테이블에 upsert
```

## 3-6. 리츠·스팩 사전 제외

```python
EXCLUDE_NAME_PATTERNS = [
    '스팩', '기업인수목적', '리츠',
    '선박펀드', '인프라펀드', '해운펀드',
    'ETF', 'ETN', 'KODEX', 'TIGER', 'KBSTAR', 'ARIRANG', 'HANARO',
]

def _is_excluded(ticker: str, corp_name: str) -> tuple[bool, str]:
    for pattern in EXCLUDE_NAME_PATTERNS:
        if pattern in corp_name:
            return True, f'사전제외: {pattern!r} 포함'
    return False, ''
```

---

# 4. DB 스키마 (`ingest/schema.sql`)

```sql
-- 1. 종목 마스터 (현재 상장 + 상장폐지 포함)
CREATE TABLE IF NOT EXISTS stocks (
    ticker          TEXT        PRIMARY KEY,
    corp_name       TEXT        NOT NULL,
    corp_code       TEXT,
    market          TEXT,
    sector          TEXT,
    sector_name     TEXT,
    is_financial    BOOLEAN     DEFAULT FALSE,
    is_excluded     BOOLEAN     DEFAULT FALSE,
    exclude_reason  TEXT,
    listed_date     DATE,
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- 2. 상장 이벤트 이력 (생존편향 해소 + 시점별 유니버스 복원)  ← v4.7 재설계
-- stock_listing_history(ticker PK 단일행) 폐기 → 이벤트 이력 구조로 교체.
-- ticker PK 단일행은 시장이전·재상장·스팩합병·종목코드 재사용을 표현 불가.
CREATE TABLE IF NOT EXISTS stock_listing_events (
    id            SERIAL      PRIMARY KEY,
    ticker        TEXT        NOT NULL,
    corp_code     TEXT,
    corp_name     TEXT,
    market        TEXT,
    listed_date   DATE,
    delisted_date DATE,        -- NULL이면 해당 구간 현재 상장 중
    event_type    TEXT        NOT NULL,
    -- Phase 0 유니버스 필터 적용 대상: 'listed' | 'delisted' | 'market_transfer'
    -- 기록만, 유니버스 필터 미적용:   'spac_merge' | 'split' | 'merger'
    source        TEXT,        -- 'fdr' | 'pykrx' | 'dart_manual'
    source_note   TEXT         -- 수동 확인 메모 (스팩합병 등 복잡 케이스)
);

-- 3. 재무제표 원시 수치
CREATE TABLE IF NOT EXISTS financials (
    id          SERIAL  PRIMARY KEY,
    ticker      TEXT    NOT NULL REFERENCES stocks(ticker),
    corp_code   TEXT    NOT NULL,
    year        INTEGER NOT NULL,
    report_type TEXT    NOT NULL,   -- 'FY' | 'H1' | 'Q1' | 'Q3'
    fs_div      TEXT    NOT NULL,   -- 'CFS' | 'OFS'
    account_nm  TEXT    NOT NULL,
    amount      NUMERIC,
    UNIQUE (ticker, year, report_type, fs_div, account_nm)
);

-- 4. Point-in-Time 재무 데이터
CREATE TABLE IF NOT EXISTS financials_pit (
    id              SERIAL  PRIMARY KEY,
    ticker          TEXT    NOT NULL REFERENCES stocks(ticker),
    corp_code       TEXT    NOT NULL,
    year            INTEGER NOT NULL,
    report_type     TEXT    NOT NULL,
    fs_div          TEXT    NOT NULL,
    account_nm      TEXT    NOT NULL,
    amount          NUMERIC,
    available_from  DATE    NOT NULL,
    source_rcept_no TEXT,
    fallback_used   BOOLEAN NOT NULL DEFAULT FALSE,  -- TRUE: 법정마감+5일 fallback 사용 (실제 공시일 없음)
    UNIQUE (ticker, year, report_type, fs_div, account_nm, available_from)
);

-- 5. 공시 목록 (available_from 결정용 + DQ Gate R06~R08)
CREATE TABLE IF NOT EXISTS disclosures (
    rcept_no    TEXT    PRIMARY KEY,
    ticker      TEXT    NOT NULL REFERENCES stocks(ticker),
    rcept_dt    DATE,
    report_nm   TEXT,
    report_type TEXT,
    year        INTEGER
);

-- 6. 일별 주가 OHLCV + 수정주가  ← v4.3 컬럼 추가
CREATE TABLE IF NOT EXISTS price_history (
    ticker        TEXT    NOT NULL,
    date          DATE    NOT NULL,
    open          NUMERIC,
    high          NUMERIC,
    low           NUMERIC,
    close         NUMERIC,
    adj_close     NUMERIC,     -- 수정주가 (액면분할·무상증자 반영, 배당 미반영)
    volume        BIGINT,
    turnover      NUMERIC,     -- 거래대금 (원)
    is_suspended  BOOLEAN DEFAULT FALSE,   -- 거래정지 여부
    PRIMARY KEY (ticker, date)
);

-- 7. 일별 시가총액·상장주식수  ← v4.3 신규
CREATE TABLE IF NOT EXISTS market_cap_history (
    ticker      TEXT    NOT NULL,
    date        DATE    NOT NULL,
    market_cap  NUMERIC,   -- 원 단위
    shares      BIGINT,    -- 상장주식수
    source      TEXT DEFAULT 'pykrx',
    PRIMARY KEY (ticker, date)
);

-- 8. DQ Gate 판정 결과 — 시점별  ← v4.7 재설계
-- 기존 universe_gate(ticker PK 영구판정) 폐기.
-- 영구 구조 제외(ETF·리츠·스팩 등)는 stocks.is_excluded로 처리 (이미 존재).
-- 시점 의존 조건(자본잠식·계정누락 등)은 year+report_type 단위 시점별 판정으로 분리.
-- 한 종목이 과거 자본잠식이었다가 정상화됐을 경우 해당 연도만 REJECT, 이후 연도는 PASS 가능.
CREATE TABLE IF NOT EXISTS universe_gate_pit (
    ticker         TEXT    NOT NULL,
    year           INTEGER NOT NULL,
    report_type    TEXT    NOT NULL,  -- 'FY' | 'H1' | 'Q1' | 'Q3'
    status         TEXT    NOT NULL,  -- 'PASS' | 'REJECT'
    reject_reasons JSONB   DEFAULT '[]',
    flags          JSONB   DEFAULT '[]',
    evaluated_at   TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (ticker, year, report_type)
);

-- 9. 수집 상태 추적
CREATE TABLE IF NOT EXISTS ingest_status (
    ticker       TEXT        PRIMARY KEY,
    status       TEXT        NOT NULL DEFAULT 'pending',
    last_attempt TIMESTAMPTZ,
    error_msg    TEXT,
    call_count   INTEGER     DEFAULT 0
);

-- 10. 백테스트 실험 실행 로그
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id            SERIAL      PRIMARY KEY,
    run_name          TEXT,
    phase             TEXT,       -- 'phase2_rim_only' | 'phase3_classified' | 'phase5_multimodel'
    params            JSONB,
    fitness           NUMERIC,
    metrics           JSONB,
    ablation_tag      TEXT,       -- 'A_random' | 'B_hard_random' | 'C_stability_random' | 'D_rim_only' | 'E_screener_rim' | 'F_momentum_rim' | 'G_full'
    -- ── 재현성 컬럼 (v4.7 추가) ─────────────────────────────────────────────
    git_commit        TEXT,       -- git rev-parse HEAD (실험 당시 코드 상태)
    param_hash        TEXT,       -- md5(json.dumps(params, sort_keys=True)) — 중복 실험 탐지
    data_cutoff_date  DATE,       -- 이 실험에 사용된 데이터의 최신 날짜
    db_schema_version TEXT,       -- schema.sql 상단 주석과 수동 동기화 (예: 'v1.2')
    started_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ,
    status            TEXT,       -- 'running' | 'done' | 'failed'
    error_msg         TEXT,       -- 실패 시 traceback 저장
    created_at        TIMESTAMPTZ DEFAULT now()
);

-- 11. Reasoning Log (XAI)
CREATE TABLE IF NOT EXISTS reasoning_log (
    id          SERIAL  PRIMARY KEY,
    run_id      INTEGER REFERENCES backtest_runs(run_id),
    change_desc TEXT,
    reason      TEXT,
    confidence  TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- 12. 분류 이력 추적
CREATE TABLE IF NOT EXISTS classification_history (
    id             SERIAL  PRIMARY KEY,
    ticker         TEXT    NOT NULL REFERENCES stocks(ticker),
    rebalance_date DATE    NOT NULL,
    prev_type      TEXT,
    curr_type      TEXT    NOT NULL,
    changed        BOOLEAN NOT NULL DEFAULT FALSE,
    change_reason  TEXT,
    soft_label     JSONB,
    UNIQUE (ticker, rebalance_date)
);
```

---

# 5. Data Integrity Layer

## 5-1. available_from 결정 규칙

```python
# ingest/pit_loader.py

def resolve_available_from(ticker: str, year: int, report_type: str) -> date:
    """
    실제 DART 공시 접수일 우선 사용.
    없으면 법정 제출 마감일 + 5일 (항상 실제 공시일보다 늦게 잡음).
    """
    rcept_dt = fetch_rcept_dt(ticker, year, report_type)
    if rcept_dt:
        return rcept_dt
    FALLBACK = {
        'FY': date(year + 1, 4,  5),   # 3/31 마감 + 5일
        'H1': date(year,     8, 19),   # 8/14 마감 + 5일
        'Q1': date(year,     5, 20),   # 5/15 마감 + 5일
        'Q3': date(year,     11, 19),  # 11/14 마감 + 5일
    }
    return FALLBACK[report_type]
```

## 5-1-1. Cross-batch 일관성 보장

DART 수집이 14일 이상 분산되면 종목별 수집 시점이 다르다. 이로 인해 같은 리밸런싱 기준일에
종목 A(1일차 수집)와 종목 B(12일차 수집)의 FY 데이터가 서로 다른 DART 버전일 수 있다.

**이 문제가 PIT 설계에서 자동 해소되는 이유:**
- `financials_pit.available_from`은 DART **공시 접수일(rcept_dt)** 로 결정된다.
- 수집 순서(1일차 vs 12일차)와 관계없이, 각 종목의 `available_from`은 항상 실제 공시일을 가리킨다.
- 따라서 백테스트 엔진이 `available_from <= rebalance_date` 조건으로 PIT 데이터를 조회하면,
  수집 시점 차이가 아닌 **공시 시점 기준**으로 일관성이 유지된다.

**fallback_used=TRUE 케이스:**
- 실제 공시일을 찾지 못한 경우 법정 마감일+5일(fallback)을 사용하며 `fallback_used=TRUE`로 표시.
- fallback은 항상 실제 공시일보다 늦게 설정되므로 룩어헤드 오염이 발생하지 않는다.
- `fallback_used` 비율이 20% 초과 시 Phase 0A 게이팅 기준 위반 → 공시일 수집 로직 재검토.

## 5-2. 재무 이상치 검사 (`ingest/validator.py`)

모든 검사는 CFS 기준. CFS 미존재 시 OFS fallback, 결과에 fs_div 기록.

```python
CHECKS = [
    {'id': 'V01', 'desc': '자산 = 부채 + 자본 (CFS, 허용오차 1%)',
     'action': 'FLAG', 'escalate': 'REJECT'},    # 1~5% → FLAG, >5% → REJECT
    {'id': 'V02', 'desc': '영업이익 <= 매출액 (금융업 제외, CFS)', 'action': 'FLAG'},
    {'id': 'V03', 'desc': '|NI - CFO| > 자본총계 30% (CFS)',       'action': 'FLAG'},
    {'id': 'V04', 'desc': '매출액 전년비 ±500% 이상 (FY)',          'action': 'FLAG'},
    {'id': 'V05', 'desc': '자본총계 전년비 ±300% 이상 (FY)',        'action': 'FLAG'},
    {'id': 'V06', 'desc': '자본총계 < 0 (CFS)',                    'action': 'REJECT'},
    {'id': 'V07', 'desc': '자산총계 < 0 (CFS)',                    'action': 'REJECT'},
    {'id': 'V08', 'desc': '핵심 계정 2개 이상 누락 (FY)',           'action': 'REJECT'},
    {'id': 'V09', 'desc': 'FY 재무데이터 연속 2년 이상 누락',        'action': 'REJECT'},
]
```

## 5-3. Data Quality Gate (`ingest/dq_gate.py`)

### DQ Gate vs Hard Filter 역할 분리

| 구분 | 역할 | 실행 시점 | 결과 저장 |
|------|------|----------|----------|
| **영구 제외** | 구조적 종목 유형 제외 (ETF·리츠·스팩 등) | 수집 시 1회 | `stocks.is_excluded` |
| **DQ Gate (시점별)** | 연도·보고서 단위 재무 품질 결함 | Phase 1, 연도별 | `universe_gate_pit` |
| **Hard Filter** | 리밸런싱 시점 기준 동적 조건 | 리밸런싱마다 재계산 | 메모리 내 처리 |

**설계 원칙 (v4.7 변경)**: 기존 `universe_gate`(ticker PK 영구판정)를 폐기하고 두 레이어로 분리.
- 한 종목이 2016년 자본잠식 → 2018년 정상화된 경우, 2016년 FY만 REJECT되고 2018년 이후는 PASS 가능.
- 영구 제외(ETF·리츠·스팩)는 `stocks.is_excluded`로 처리 (이미 존재하는 컬럼).

### DQ Gate — 조건 재분류 (v4.7)

| ID | 조건 | 저장 위치 | 비고 |
|----|------|----------|------|
| R01 | 리츠·스팩·ETF 등 사전 제외 | `stocks.is_excluded = TRUE` | 영구 제외, 시점 무관 |
| R02 | 자본총계 < 0 (CFS) | `universe_gate_pit` | 시점별 — 정상화 후 복귀 가능 |
| R03 | 자산총계 < 0 (CFS) | `universe_gate_pit` | 시점별 |
| R04 | 핵심 계정 2개 이상 누락 (FY) | `universe_gate_pit` | 시점별 — 매핑 개선 후 복귀 가능 |
| R05 | FY 재무 연속 2년 이상 누락 | `universe_gate_pit` | 시점별 |
| R06 | 감사의견 비적정·한정 | **Hard Filter** | 공시일 기준 시점 처리 |
| R07 | 상장폐지 사유 이력 | **Hard Filter** | `stock_listing_events` 기준 |
| R08 | 관리종목 지정 이력 | **Hard Filter** | 지정 공시일 기준 처리 |
| R09 | 자산 = 부채 + 자본 오차 > 5% | `universe_gate_pit` | 시점별 |

### 리밸런싱 유니버스 복원 쿼리

```sql
-- rebalance_date 기준 투자 가능 종목 복원
-- 조건 1: 영구 제외 아님
-- 조건 2: 해당 시점 PIT 기준 DQ Gate PASS
-- 조건 3: 해당 리밸런싱일에 실제 상장 중 (stock_listing_events 기준)
SELECT DISTINCT g.ticker
FROM universe_gate_pit g
JOIN stocks s ON s.ticker = g.ticker
JOIN stock_listing_events e ON e.ticker = g.ticker
WHERE s.is_excluded = FALSE
  AND g.status = 'PASS'
  AND g.year = :pit_year
  AND g.report_type = :pit_report_type
  AND e.event_type IN ('listed', 'market_transfer')
  AND e.listed_date  <= :rebalance_date
  AND (e.delisted_date IS NULL OR e.delisted_date > :rebalance_date);
```

### DQ Gate — 자동 PASS + 플래그 (P01~P07)

| ID | 조건 | 플래그명 | 활용 |
|----|------|----------|------|
| P01 | 매출액 전년비 ±500% | `revenue_spike` | 스파이크 연도 스크리닝 제외 |
| P02 | 자본총계 전년비 ±300% | `equity_spike` | RIM 자본총계 3년 평균 스무딩 |
| P03 | \|NI - CFO\| > 자본총계 30% | `accrual_alert` | 재무안정성 감점 참고 |
| P04 | 자산 = 부채 + 자본 오차 1~5% | `balance_sheet_mismatch` | 경고 기록 |
| P05 | 최근 2년 CB/BW 3건 이상 | `dilution_risk` | RIM 희석 조정 참고 |
| P06 | 영업이익 변동성 > 30% | `high_op_volatility` | CYCLICAL 분류 가중치 참고 |
| P07 | 스팩 탐지 의심 | `spac_suspect` | 결과 해석 시 참고 |

---

