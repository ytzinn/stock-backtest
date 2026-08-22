-- Migration v10: stock_listing_events 자연키 유니크 제약
-- 적용: python -m ingest.migrations.apply v10_listing_events_unique
--   (운영 5433 · 섀도우 5436 양쪽. DB 선택은 실행 디렉토리의 .env 가 정한다)
--
-- 배경 (2026-08-19~22 진단): 이 테이블의 PK 는 `id` 시리얼뿐이고 INSERT 에 ON CONFLICT 가
-- 없었다. delisting_ingest 는 crontab 에 **등록된 적이 없어** 4,124건 전부 수동 실행
-- 결과이고, 마지막 배치가 상폐일 2026-05-07 까지 덮은 뒤 끊겼다. 백필하려면 재실행이
-- 안전해야 하는데, 제약이 없으면 기존 행이 통째로 중복 삽입된다.
--
-- 키 설계 근거:
--   FDR KRX-DELISTING 은 **같은 상폐 건을 사유별로 여러 행**으로 준다. 실측 6건:
--     028855 동성판유리우 1976-05-14 → '종목별 상장폐지신청' / '해산 사유 발생'
--   그래서 (ticker, event_type, delisted_date) 만으로는 위반 6그룹이 나온다.
--   사유가 다른 행을 "갱신이 아니라 별개 행"으로 보는 이상(=ON CONFLICT DO NOTHING 의
--   근거) **사유는 키의 일부여야 한다**. source_note 를 포함하면 위반 0건이다.
--
--   source_note 는 NULL 이 0건이라(문자열 'nan' 1,393건 — 미해결 SOURCE-NOTE-NAN-CAST)
--   키에 넣어도 NULL 때문에 유니크가 무력화되지 않는다.
--   ⚠️ SOURCE-NOTE-NAN-CAST 를 고쳐 'nan' → NULL 로 바꾸면 **이 제약이 조용히 무력화된다.**
--   그 수정은 반드시 "NULL 전환 + COALESCE 기반 부분 인덱스 추가"를 한 세트로 해야 한다.
--
--   listed 이벤트 2,770행은 delisted_date 가 전부 NULL 이라 위 제약이 걸리지 않는다
--   (Postgres UNIQUE 는 NULL 을 서로 다르게 취급). 부분 유니크 인덱스로 따로 막는다.
--
-- 기존 12행(중복 6그룹 + 상폐일 상이 6그룹)은 **전부 보존**된다. 삭제 0건.
-- 백테스트 노출도 0이다 (price_history 0행 · universe_gate_pit 0행 · 풀·편입 0건).

ALTER TABLE stock_listing_events
    DROP CONSTRAINT IF EXISTS stock_listing_events_natural_key;

ALTER TABLE stock_listing_events
    ADD CONSTRAINT stock_listing_events_natural_key
    UNIQUE (ticker, event_type, delisted_date, source_note);

-- listed 는 delisted_date 가 NULL 이라 위 제약의 사정권 밖이다. 종목당 1건으로 막는다.
DROP INDEX IF EXISTS idx_listing_events_listed_unique;

CREATE UNIQUE INDEX idx_listing_events_listed_unique
    ON stock_listing_events (ticker)
    WHERE event_type = 'listed';
