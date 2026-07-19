# DART XBRL 원본 공시 수집 — 역사적 룩어헤드 바이어스 소급 보정

## Context

`dart_ingest`가 2026-05에 전체 과거 재무데이터를 수집할 때, DART API(`fnlttSinglAcntAll.json`)는
항상 최신(정정 후) 값을 반환한다. 결과적으로 DB에 저장된 `amount`는 수집 시점 이전에 발생한
모든 정정을 이미 반영한 "미래 정보"를 담고 있어 역사적 백테스트(2015~2025) 전 기간에
룩어헤드 바이어스가 내재한다.

DART `fnlttXbrl.xml` API(DS003)는 특정 `rcept_no`의 재무제표 XBRL을 ZIP으로 제공한다.
이를 파싱해 `financials.original_amount`에 채워넣으면, 기존 `data_access.py`의 CASE 로직이
수정 없이 날짜 인식(date-aware) 방식으로 원본/정정값을 올바르게 선택하게 된다.

**PIT 시점 축**(공시일 기준 가용성)은 이미 `financials_pit.available_from` + `data_access.py`의
`available_from <= rebalance_date` 조건으로 처리됨. **이 작업의 범위는 "값 정정 축만 소급 보정".**

**date-aware 전략 (`data_access.py` CASE 로직)**:
```sql
CASE
  WHEN amendment_from IS NOT NULL AND amendment_from <= rebalance_date
       THEN amount           -- 정정이 리밸런싱 기준일 이전에 공개됨 → 정정값 사용
  WHEN original_amount IS NOT NULL
       THEN original_amount  -- 원본값 확보됨 (정정 미공개 구간) → 원본값 사용
  ELSE amount
END
```
- `amendment_from` = `pit_loader.py`가 `MAX(rcept_dt)`로 채움 (별도 작업 없음)
- XBRL vs frmtrm 충돌 시: **XBRL 직접 신고값 우선** (최초 신고 시점 투자자가 본 숫자)

**Branch**: `feature/xbrl-pit` (master 코드에 영향 없음; DB는 공유됨 — 주의사항 참고)

---

## 현재 상태 확인 (실행 전 체크)

- `financials` 테이블: `original_amount` 컬럼 유무 확인 (schema.sql에 없음, 서버 ALTER 여부 미확인)
- `financials_pit` 테이블: `original_amount`, `amendment_from` 컬럼 유무 확인 (v7 코드가 사용하는데 schema.sql 미반영)
- `disclosures` 테이블: `is_amendment` 컬럼 없음 → 마이그레이션 필요
- **⚠️ 핵심 전제 확인**: `disclosures`에 **정정 전 원본 공시의 rcept_no가 실제로 저장돼 있는지** 확인.
  `dart_ingest.get_disclosures()`는 전체 목록을 수집하므로 있어야 하지만, 아래 쿼리로 실측:
  ```sql
  -- 정정공시 있는 종목 중 원본(비정정) rcept_no도 함께 있는지
  SELECT ticker, year, report_type,
         COUNT(*) FILTER (WHERE report_nm NOT LIKE '%정정%') AS original_cnt,
         COUNT(*) FILTER (WHERE report_nm LIKE '%정정%')     AS amendment_cnt
  FROM disclosures
  GROUP BY ticker, year, report_type
  HAVING COUNT(*) > 1
  LIMIT 20;
  ```
  `original_cnt = 0`인 행이 많으면 → 원본 공시를 별도 재수집하는 선행 작업 필요.

---

## 구현 단계

### Step 1: Branch 생성 + PoC

```bash
git checkout -b feature/xbrl-pit
```

**신규 파일**: `ingest/xbrl_poc.py`

목적: 2가지 시대에서 커버리지 검증:
- **최근 건** (예: 039230 FY2022, 정정이 확실히 있었던 케이스)
  → PoC 필수 확인: `XBRL original값 ≠ 현재 DB amount`. 같으면 DART가 원본 rcept_no에도 정정본을 내줄 가능성 → 접근법 전제 붕괴
- **구형 건** (예: 임의 종목 FY2015~2016)
  → XBRL 인스턴스 파일이 아예 없는 케이스 비율 측정

**사용 엔드포인트**: `fnlttXbrl.xml` (DS003) — 재무제표 원본 XBRL 전용.
`document.xml`(DS001)은 사업보고서 원문 텍스트 ZIP으로 XBRL 파싱에 부적합.

PoC 로직 스케치:
```python
# 1. disclosures에서 해당 종목의 원본 rcept_no 조회
#    (report_nm LIKE '%정정%' 아닌 것 중 가장 이른 rcept_no)
#    + report_type → reprt_code 변환 (dart_ingest.REPRT_CODE 재사용)

# 2. fnlttXbrl.xml 다운로드 (rcept_no + reprt_code 모두 필수)
resp = session.get(f'{DART_BASE}/fnlttXbrl.xml',
                   params={'rcept_no': rcept_no,
                           'reprt_code': reprt_code,
                           'crtfc_key': api_key}, timeout=60)

# 3. ZIP에서 .xbrl 파일 추출 (dart_ingest.download_corp_codes()와 동일 zipfile 패턴)
with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
    print(zf.namelist())  # 파일 목록 먼저 출력
    for name in zf.namelist():
        if name.endswith('.xbrl'):
            root = ET.fromstring(zf.read(name))
            # 모든 element tag/text 출력해서 실제 네임스페이스·태그명 파악

# 4. XBRL 추출값 vs DB amount 비교 출력 (같으면 위험 신호)
# 5. 구형 건도 동일 시도 — XBRL 파일 존재 여부 + 파일 없으면 HTTP status 확인
```

**참고**: `dart-fss` 라이브러리의 XBRL 파싱 코드를 레퍼런스로 참고 (처음부터 파서 짜기 전).

**PoC 성공 기준**:
1. 핵심 계정(매출액, 영업이익, 당기순이익, 자산총계, 자본총계) 5개 이상 XBRL에서 확인
2. 정정 케이스에서 `XBRL값 ≠ 현재 DB amount` 확인 (같으면 중단 후 접근법 재검토)
3. 2015~2016 구형 건 커버리지 비율 측정 → **30% 미만이면 백테스트 시작연도를 2018년 이후로 올리는 방안 검토**

---

### Step 2: xbrl_mapper.py 구현

**신규 파일**: `ingest/xbrl_mapper.py`

PoC에서 확인한 실제 XBRL 태그명 기반으로 작성.
예상 구조 (PoC 후 실제 태그명으로 수정):

```python
# DART XBRL namespace prefix → fs_div 구분
# 실제 prefix는 PoC에서 확인 필요
NAMESPACE_FS_DIV = {
    'dart-ci': ('IS', 'CFS'),   # consolidated income
    'dart-oi': ('IS', 'OFS'),   # OFS income
    'dart-bs': ('BS', 'CFS'),
    # ...
}

# XBRL local name → 표준 account_nm
# dart_ingest.py의 ACCOUNT_ALIASES와 동일 계정명 집합 사용
XBRL_TO_ACCOUNT: dict[str, str] = {
    'Revenue': '매출액',
    'OperatingIncome': '영업이익',
    'NetIncome': '당기순이익',
    'Assets': '자산총계',
    'Liabilities': '부채총계',
    'Equity': '자본총계',
    # ... PoC 결과 기반으로 채움
}

def parse_xbrl_zip(zip_bytes: bytes) -> dict[tuple[str, str], float]:
    """Returns: {(account_nm, fs_div): amount}"""
```

---

### Step 3: Schema 마이그레이션

**신규 파일**: `ingest/migrations/v8_xbrl_original.sql`

```sql
-- financials: original_amount 추가 (없는 경우에만)
ALTER TABLE financials
  ADD COLUMN IF NOT EXISTS original_amount NUMERIC;

-- financials_pit: original_amount + amendment_from 추가 (없는 경우에만)
-- amendment_from은 data_access.py CASE 로직에서 실제로 사용됨 (날짜 인식 전략)
ALTER TABLE financials_pit
  ADD COLUMN IF NOT EXISTS original_amount NUMERIC,
  ADD COLUMN IF NOT EXISTS amendment_from  DATE;

-- disclosures: is_amendment 플래그 추가
ALTER TABLE disclosures
  ADD COLUMN IF NOT EXISTS is_amendment BOOLEAN DEFAULT FALSE;

UPDATE disclosures
  SET is_amendment = (report_nm LIKE '%정정%')
  WHERE is_amendment IS NULL;
```

서버 적용: psycopg2 스크립트로 실행 (psql 직접 사용 금지).

---

### Step 4: xbrl_historical_ingest.py 구현

**신규 파일**: `ingest/xbrl_historical_ingest.py`

```python
"""
XBRL 기반 원본 재무값 소급 수집.

실행:
    python -m ingest.xbrl_historical_ingest             # FRMTRM 차이 있는 종목부터
    python -m ingest.xbrl_historical_ingest --ticker 039230
    python -m ingest.xbrl_historical_ingest --limit 1000
"""

def find_targets(cur) -> list[tuple]:
    """
    반환: (ticker, corp_code, year, report_type, fs_div, rcept_no, reprt_code)
    우선순위:
    1. financials.original_amount IS NULL + disclosures에 is_amendment=TRUE 있는 그룹
    2. FRMTRM 불일치 그룹
    """

def fetch_original_xbrl(dart: DartAPI, rcept_no: str, reprt_code: str) -> dict:
    """fnlttXbrl.xml 다운로드 + xbrl_mapper.parse_xbrl_zip() 적용."""

def update_original_amounts(cur, ticker, year, report_type, fs_div, values: dict):
    """
    financials 테이블 UPDATE:
      SET original_amount = %s
      WHERE ... AND original_amount IS NULL  -- amendment_checker 보전값 덮어쓰지 않음
    """
```

**API 콜 예산**: DART 한도 20,000콜/일. dart-watcher 실제 일일 소비량 확인 후 여유분 배정.
1,000~2,000콜/일 안전하므로 FRMTRM 차이 1,460건은 **1~2일 완료**.
`--limit` 기본값 1,000. 첫 실행일 dart-watcher 사용량 보고 수동 조정.

**재사용**: `dart_ingest.DartAPI`, `dart_ingest.QuotaExceededError`, `dart_ingest.REPRT_CODE`.

---

### Step 5: pit_loader.py 수정 확인

`build_financials_pit()`가 `financials.original_amount` → `financials_pit.original_amount`로 복사하는지 확인.
v7에서 이미 처리 중이면 수정 불필요. 누락 시 INSERT 쿼리에 컬럼 추가.

---

### Step 6: financials_pit 재빌드 + 검증

**⚠️ DB 격리 주의**: Step 4에서 `original_amount` 적재 시, master 브랜치 코드도 즉시 해당 값 읽기 시작.
→ **Step 4 실행 전 반드시 현재 백테스트 결과 스냅샷 저장**.

```bash
python -m ingest.pit_loader  # 전 종목 재빌드
```

**검증 쿼리**:
```sql
-- original_amount 채워진 비율
SELECT
  COUNT(*) FILTER (WHERE original_amount IS NOT NULL) AS has_original,
  COUNT(*) AS total
FROM financials_pit;

-- XBRL원본 vs 다음해 보고서가 본 전기값(self-join) 비교
-- frmtrm_amount는 동일 행의 N-1년 값이므로 직접 비교 불가 → N+1년 보고서의 frmtrm으로 비교
SELECT f.ticker, f.year, f.account_nm,
       f.amount            AS amended_current,
       f.original_amount   AS xbrl_original,
       nxt.frmtrm_amount   AS next_yr_view_of_this_yr,
       round(abs(f.original_amount - nxt.frmtrm_amount)
             / nullif(nxt.frmtrm_amount, 0) * 100, 1) AS diff_pct
FROM financials f
JOIN financials nxt
  ON  nxt.ticker      = f.ticker
  AND nxt.year        = f.year + 1
  AND nxt.report_type = f.report_type
  AND nxt.account_nm  = f.account_nm
  AND nxt.fs_div      = f.fs_div
WHERE f.original_amount IS NOT NULL
ORDER BY diff_pct DESC NULLS LAST
LIMIT 30;
```

**백테스트 회귀 확인**: 적용 전/후 리밸런싱 날짜별 포트폴리오 구성 비교.

---

## 변경 파일 목록

| 파일 | 상태 |
|------|------|
| `ingest/xbrl_poc.py` | 신규 (PoC, 비프로덕션) |
| `ingest/xbrl_mapper.py` | 신규 |
| `ingest/xbrl_historical_ingest.py` | 신규 |
| `ingest/migrations/v8_xbrl_original.sql` | 신규 |
| `ingest/pit_loader.py` | 확인 후 필요시 소폭 수정 |
| `ingest/schema.sql` | 신규 컬럼 추가 반영 |

**변경 없는 파일**: `data_access.py` (CASE 로직이 이미 date-aware로 `original_amount`+`amendment_from` 처리),
`amendment_checker.py` (2026-05 이후 신규 정정 보호 역할 유지).

---

## 리스크 & 완화

| 리스크 | 완화 |
|--------|------|
| DART가 원본 rcept_no에도 정정본 XBRL을 반환 | PoC에서 `XBRL값 ≠ DB amount` 반드시 확인. 같으면 접근법 전면 재검토 |
| XBRL 파싱 복잡도 (태그명 불일치) | PoC 후 `dart-fss` 레퍼런스 참고해 매퍼 작성 |
| 구형 공시(2015~2016) XBRL 미제공 비율 높음 | PoC 실측 후 커버리지 < 30%이면 백테스트 시작연도 상향 검토 |
| `disclosures`에 원본 rcept_no 없음 | 실행 전 체크 쿼리로 확인. 없으면 list.json 재수집 선행 |
| DB는 브랜치 격리 불가 | Step 4 전 백테스트 스냅샷 저장 |
| dart-watcher API 콜 충돌 | 실행 전 dart-watcher 일일 사용량 확인 후 `--limit` 조정 |
| 서버 DB 마이그레이션 위험 | `ADD COLUMN IF NOT EXISTS`만 사용 → 기존 데이터 무손실 |
| XBRL 없어 `original_amount` NULL인 잔여 케이스 | CASE가 `amount`(정정값)으로 폴백 → 잔여 룩어헤드 존재. PoC 커버리지 결과로 영향 규모 정량화 |
