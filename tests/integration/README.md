# tests/integration/

**합성 PostgreSQL 기반 SQL 경계 계약 테스트 (AUDIT_01 Pass 0C, I-1~I-6).**

DB 없는 단위 테스트로는 잡을 수 없는 것들을 검증한다: `available_from` 경계,
`amendment_from` 처리, `DISTINCT ON` 정렬 방향, CFS↔OFS fallback, 상장폐지일 경계,
`fallback_used` 지연 규칙. 오라클과 동일한 계약: **깨지면 수정이 틀린 것이다**
(단, "현행 동작 문서화"라고 명시된 테스트는 예외 — 해당 docstring 참조).

## 안전 가드

- **운영 DB(포트 5433)와 stock-analysis DB(5432)에는 절대 접속하지 않는다.**
  conftest.py가 두 포트를 하드 차단한다.
- 실데이터 복사 금지 — 전부 손으로 만든 합성 데이터다.
- 임시 컨테이너는 세션 종료 시 파기한다.

## 실행 방법

```bash
# 1. 임시 PostgreSQL 기동 (포트 5434)
docker run -d --name audit-pg-5434 -p 5434:5432 \
  -e POSTGRES_PASSWORD=audit postgres:16-alpine

# 2. 실행 (기본 DSN이 5434를 가리킴; 필요 시 AUDIT_PG_DSN으로 오버라이드)
pytest -m integration -v

# 3. 파기
docker rm -f audit-pg-5434
```

DB가 없으면 테스트는 실패가 아니라 **skip**된다 (fast suite `-m "not integration"`에는
어차피 포함되지 않는다).
