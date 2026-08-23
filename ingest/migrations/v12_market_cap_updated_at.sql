-- Migration v12: market_cap_history.updated_at
-- 적용: python -m ingest.migrations.apply v12_market_cap_updated_at
--   (운영 5433 · 섀도우 5436 양쪽)
--
-- 배경: v11 이 price_history 에 답한 질문("어떤 행이 언제 재작성됐는가")을
-- market_cap_history 는 여전히 답하지 못한다. 그리고 시총 쪽이 더 급하다 —
-- 가격은 haircut 이 종목당 한 값만 읽지만, 시총은 매 구간 **전 유니버스가 동시에**
-- 랭킹을 결정한다(ablation `_PBRRankPipeline`: pbr = mktcap/equity).
-- 한 종목의 시총이 틀리면 그 종목만이 아니라 뒤따르는 종목의 순위가 전부 밀린다.
--
-- 2026-08-23 실측: 전체 7,431,097행 중 96.91% 가 source='krx_pit' 이다. 이 값은
-- `rebuild_from_snapshot()` 만 쓰므로 --rebuild-from-snapshot 이 과거에 최소 1회
-- 실행됐다는 뜻인데, **언제였는지 알 방법이 없다.** 이 컬럼의 부재가 그 이유다.
--
-- 기존 행 처리 = **NULL** (v11 과 동일한 이유):
--   마이그레이션 시각을 찍으면 "원래부터 있던 행"과 "그때 갱신된 행"의 구분이 사라진다.
--   NULL 은 "이 컬럼이 생기기 전부터 있던 행 — 갱신 시각 불명"을 정직하게 표현한다.
--   DEFAULT 는 컬럼 추가 **이후**에 걸어, 신규/갱신 행만 시각을 갖게 한다.
--
-- ADD COLUMN 에 DEFAULT 를 붙이지 않으므로 PG11+ 에서 메타데이터 전용 연산이다
-- (7.4M 행 재작성 없음).

ALTER TABLE market_cap_history
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

ALTER TABLE market_cap_history
    ALTER COLUMN updated_at SET DEFAULT now();

COMMENT ON COLUMN market_cap_history.updated_at IS
    'INSERT/UPDATE 시각. NULL = v12 마이그레이션 이전부터 존재한 행(갱신 시각 불명).';
