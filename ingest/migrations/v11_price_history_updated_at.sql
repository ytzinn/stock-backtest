-- Migration v11: price_history.updated_at
-- 적용: python -m ingest.migrations.apply v11_price_history_updated_at
--   (운영 5433 · 섀도우 5436 양쪽)
--
-- 배경: 2026-08-22 진단에서 "상폐종목 가격 이력이 과거에 재작성됐는가"에 **답하지 못했다.**
-- 재작성 경로는 코드로 확정됐고(price_ingest 의 ON CONFLICT DO UPDATE 가 adj_close 를
-- 덮어씀), 호출 구조도 확정됐고(delisting_ingest.main 이 조건 없이 호출), 최소 1회
-- 수동 실행된 것도 이벤트 데이터로 증명됐다. 그런데 **이 테이블에 시각 컬럼이 없어**
-- 실제로 언제 어떤 행이 갱신됐는지 알 방법이 없었다. 백업 테이블은 4종목뿐이었다.
--
-- 다음에 같은 질문이 오면 5분에 끝나야 한다.
--
-- 기존 행 처리 = **NULL**:
--   마이그레이션 시각을 찍으면 "원래부터 있던 행"과 "그때 갱신된 행"의 구분이 사라진다.
--   NULL 은 "이 컬럼이 생기기 전부터 있던 행 — 갱신 시각 불명"을 정직하게 표현한다.
--   DEFAULT 는 컬럼 추가 **이후**에 걸어, 신규/갱신 행만 시각을 갖게 한다.
--
-- ADD COLUMN 에 DEFAULT 를 붙이지 않으므로 PG11+ 에서 메타데이터 전용 연산이다
-- (7.3M 행 재작성 없음).

ALTER TABLE price_history
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

ALTER TABLE price_history
    ALTER COLUMN updated_at SET DEFAULT now();

COMMENT ON COLUMN price_history.updated_at IS
    'INSERT/UPDATE 시각. NULL = v11 마이그레이션 이전부터 존재한 행(갱신 시각 불명).';
