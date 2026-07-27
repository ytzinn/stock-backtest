-- Migration v9: report_type 단위 수집 진행 상태 테이블
-- 적용: psycopg2 스크립트로 실행 (서버 PATH에 psql 없음)
--   python -m ingest.migrations.apply v9_ingest_status_reports
--
-- 배경 (SPEC_13 §5-7 Q-A1 파일럿, 2026-07-27): only_reports 모드(예: --only-reports Q1 Q3)는
-- 기존 ingest_status(ticker 단위, FY+H1 기본 수집 전용)를 쓰면 이미 FY/H1이 'done'인
-- 종목이 전부 걸러져버려 skip_if_done을 아예 안 쓴다. 그런데 그러면 여러 날에 걸친 분산
-- 실행마다 이미 끝난 종목을 처음부터 재수집해 쿼터를 낭비한다. financials 테이블 내용만
-- 보고 "이 종목의 대상 (연도,report_type) 쌍이 다 있으면 완료"로 추론하는 방식은
-- 오래된 연도(DART에 원래 데이터 없음)·아직 마감 안 된 최신 분기 때문에 영원히
-- "미완료"로 잡히는 결함이 있다 — report_type 단위로 "한 번 다 훑었는지"를 직접
-- 기록해야 한다.

CREATE TABLE IF NOT EXISTS ingest_status_reports (
    ticker       TEXT        NOT NULL,
    report_type  TEXT        NOT NULL,
    status       TEXT        NOT NULL DEFAULT 'done',
    last_attempt TIMESTAMPTZ,
    PRIMARY KEY (ticker, report_type)
);
