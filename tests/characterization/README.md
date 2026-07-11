# tests/characterization/

**characterization baseline — 승인된 정답이 아니라 기존 구현의 동작 기록.**
**버그 수정 시 정당하게 깨진다. 깨졌다고 자동으로 되돌리지 마라.**

이 디렉토리의 테스트는 `tests/baselines/selection/{tag}.json`(원시 float, 반올림 없는
종목별 entry/exit 가격)을 입력으로 `backtest/engine.py`의 현재 산술 경로(gross return
평균, turnover 산식 등)를 재현해 `tests/baselines/aggregate/{tag}.json`과 대조한다.

- **DB에 접속하지 않는다.** 순수 파일 입력 → 순수 계산 → 비교.
- `pytest -m "not integration"` fast suite에 포함된다 (마커 없음 = 기본 fast suite).
- 이 테스트가 실패하면: (a) 산술 로직이 바뀌었거나(의도된 버그 수정이면 baseline을
  사용자 승인 하에 재캡처), (b) 진짜 회귀다. 어느 쪽인지는 `git diff backtest/engine.py`로
  판단하지, 테스트를 고쳐서 통과시키지 않는다.
- `tests/oracle/`(수학적으로 옳은지 검증)과는 완전히 다른 목적이다. 혼동 금지
  (AUDIT_00_MASTER.md §1 원칙 1 참조).

baseline 재캡처: `scripts/audit/characterize_baseline.py --tags {tag}` (서버 실행 필요,
운영 DB 읽기전용 조회 — AUDIT MODE 하에서는 재캡처도 사용자 승인 후 별도 커밋).
