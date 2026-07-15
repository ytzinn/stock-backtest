# docs/audit/ — 2026-07 코드 정합성 감사 아카이브

2026-07 코드 정합성 감사의 계획·산출물 전체를 기록 보존용으로 이관한 디렉토리다.
감사는 **2026-07-14 종료**됐다 (CLAUDE.md의 AUDIT MODE 블록 제거, 영구 규칙 삽입).

`tests/`·`backtest/`·`scripts/audit/` 의 docstring·주석에서 언급하는 `AUDIT_0N`,
`TECH_DEBT.md`, `IMPACT_MATRIX.md`, `GAPS.md`, `AUDIT_PROGRESS.md` 는 전부 이 디렉토리를
가리킨다 (감사 당시엔 저장소 루트에 있었다).

## 계획 문서 (감사 절차 정의)
| 파일 | 내용 |
|------|------|
| `AUDIT_00_MASTER.md` | 원칙 · Pass 순서 · 모델 배치 |
| `AUDIT_01_PASS0.md` | Pass 0A 인벤토리 / 0B 특성화 / 0C 오라클·통합테스트 |
| `AUDIT_02_PASS1.md` | Pass 1 읽기 전용 감사 |
| `AUDIT_03_PASS2_3.md` | Pass 2 재현 / Pass 3 수정·PR |
| `AUDIT_04_CLAUDE_MD.md` | AUDIT MODE 블록 (적용·종료 절차) |

## 산출물
| 파일 | 내용 |
|------|------|
| `TECH_DEBT.md` | 부채 대장 — 최종 P0-A 2건 · P0-B 10건 + P1↓. 미해결 P1↓은 SPEC_06 §24로 이관 |
| `IMPACT_MATRIX.md` | Pass 2 재현·영향 행렬·수정 후보 차이표 (PIT-AMEND 정정 포함) |
| `GAPS.md` | Pass 0A/0B/0C 발견 기록 |
| `AUDIT_PROGRESS.md` | Pass별 게이트 체크리스트·PR 체인 |

## 결과 요약
- **수정된 P0**: CORR-ENGINE-001/002/003, CORR-METRIC-001/002, CORR-FRESH-001,
  CORR-BENCH-001, CORR-DA-001, CORR-HARD-001, CORR-GATE-001/002, PIT-AMEND-001/002 (PR #1~#14)
- **공표 수치 변경**: CANONICAL CAGR 15.27% → 16.45% (완결 구간 공식 기준 전환 주도).
  전후 비교: `experiments/runs/2026.07.14._AUDIT_BEFORE_AFTER.md`
- **자기 반증 기록**: PIT-AMEND-001의 P0-A 승격 근거가 amendment_from 오탐에 오염됐음을
  배포 검증 중 발견 → P0-B 환원 + PIT-AMEND-002 신규 수정 (TECH_DEBT.md 해당 항목 참조)
- **영구 규칙**: CLAUDE.md "코드 정합성 규칙 (영구)" 블록
- **회귀 안전망**: `tests/oracle/`(옳음 증명) + `tests/integration/`(SQL 경계) +
  `tests/characterization/`(동작 기록)
