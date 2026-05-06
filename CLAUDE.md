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
