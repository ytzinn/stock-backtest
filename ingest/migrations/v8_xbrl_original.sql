-- Migration v8: XBRL 원본값 컬럼 추가
-- 적용: psycopg2 스크립트로 실행 (서버 PATH에 psql 없음)
--   python -m ingest.migrations.apply v8_xbrl_original
-- 또는: xbrl_historical_ingest.py --migrate 시 자동 적용

-- 1. financials: XBRL에서 추출한 원본(정정 전) 값
--    amendment_checker.py v7이 이미 사용 중 (서버 ALTER 완료 상태 가능성 있음)
ALTER TABLE financials
    ADD COLUMN IF NOT EXISTS original_amount NUMERIC;

-- 2. financials_pit: original_amount + 정정공시 최초 등록일
--    pit_loader.py가 financials.original_amount를 복사
--    amendment_from: 정정공시 rcept_dt 중 최솟값 (이 날짜부터 amended값 사용)
ALTER TABLE financials_pit
    ADD COLUMN IF NOT EXISTS original_amount NUMERIC,
    ADD COLUMN IF NOT EXISTS amendment_from  DATE;

-- 3. disclosures: 정정공시 여부 플래그
ALTER TABLE disclosures
    ADD COLUMN IF NOT EXISTS is_amendment BOOLEAN DEFAULT FALSE;

-- 기존 rows 역산: report_nm에 '정정' 포함 여부
UPDATE disclosures
SET is_amendment = (report_nm LIKE '%정정%')
WHERE is_amendment IS NULL;

-- 인덱스: amendment_from 기반 필터링 성능
CREATE INDEX IF NOT EXISTS idx_financials_pit_amendment_from
    ON financials_pit (amendment_from)
    WHERE amendment_from IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_disclosures_is_amendment
    ON disclosures (ticker, year, report_type, is_amendment)
    WHERE is_amendment = TRUE;
