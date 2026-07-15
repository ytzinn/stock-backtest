# AUDIT_04 — CLAUDE.md 에 추가할 AUDIT MODE 블록

> **★ Pass 0A 를 시작하기 전에 반드시 적용하라.**

---

## 왜 이게 먼저인가

현재 `CLAUDE.md` 의 「코드 배포 규칙」은 다음과 같다:

> **세션에서 파일을 수정했고 다른 주제로 넘어가거나 세션이 끝날 때, 사용자가 요청하지 않아도 아래 3단계를 자동 수행한다:**
> 1. `git commit` (로컬)
> 2. `git push origin master` (GitHub)
> 3. `ssh ... "cd /opt/stock-backtest && git pull"` (서버)

**이걸 끄지 않으면 Claude Code 가 감사 중간 상태를 성실하게 master 와 운영 서버에 밀어넣는다.**
Pass 3 를 "항목당 1 PR"로 설계해도, 이 규칙이 살아 있으면 무의미하다.

평상시엔 훌륭한 규칙이지만, 감사 기간에는 정확히 반대 방향으로 작동한다.

---

## CLAUDE.md 에 추가할 블록

`## 반드시 지킬 규칙` 섹션의 **맨 앞**에 넣어라 (배포 규칙보다 위에 와야 한다).

```markdown
### ⚠ AUDIT MODE — 2026-07 코드 정합성 감사 기간 한정

> 이 블록이 존재하는 동안 아래 규칙이 「코드 배포 규칙」보다 **우선한다.**
> 감사 종료 시 이 블록 전체를 삭제한다.

- **자동 master push · 서버 pull 규칙을 정지한다.**
  세션 종료 시 자동으로 `git push origin master` 하지 마라. 서버에서 `git pull` 하지 마라.
- 모든 작업은 `audit/{ITEM_ID}` 브랜치에서만 한다. `master` 에서 직접 작업하지 마라.
- push 는 `git push origin audit/{ITEM_ID}` 까지만 한다.
- master merge 와 서버 pull 은 **사용자가 수동 승인 후 직접 지시**한다.
- 배포 순서: PR 승인 → master merge → 서버 pull → 서버 shadow run
- `tests/baselines/` (characterization baseline) 갱신은 **별도 커밋**으로 분리하고,
  **사용자 승인 없이 갱신하지 않는다.** 테스트가 깨졌다고 baseline 을 맞춰 고치는 것은 금지다.
- Pass 0 · Pass 1 · Pass 2 세션에서는 **프로덕션 코드를 수정하지 않는다.**
  `tests/`, `scripts/audit/`, 감사 문서만 추가한다.
- 통합 테스트용 임시 PostgreSQL 은 **포트 5434 이상**에 띄운다.
  **포트 5433(운영 DB)에 절대 접속하지 마라.**

- 감사 문서 위치:
    AUDIT_00_MASTER.md    — 원칙 · Pass 순서 · 모델 배치 (먼저 읽어라)
    AUDIT_01_PASS0.md     — 인벤토리 · 특성화 · 오라클
    AUDIT_02_PASS1.md     — 읽기 전용 감사
    AUDIT_03_PASS2_3.md   — 재현 · 수정 · PR
    TECH_DEBT.md          — 부채 대장 (산출물)

- 핵심 원칙 (전 Pass 공통):
    **기존 결과는 "보존해야 할 동작"이지 "정답"이 아니다.**
    tests/characterization/ 은 기존 동작 기록 — 버그 수정 시 정당하게 깨진다.
    tests/oracle/ · tests/integration/ 은 옳음의 증명 — 깨지면 수정이 틀린 것이다.
    이 둘을 절대 혼동하지 마라.
```

---

## `.claude/settings.json` (프로젝트 루트)

```json
{
  "model": "sonnet"
}
```

기본을 Sonnet 5 로 두고, 무거운 Pass 만 세션 시작 시 상향한다.

```
/model fable          # Pass 0C, 1A, 1B, 2  — Fable 5는 기본 모델이 아니므로 명시 선택 필요
/model opus           # 대안
/effort xhigh         # Opus 4.8 / Fable 5 사용 시. 세션을 넘어 유지됨
/effort               # 현재 값 확인 (설정이 무시되는 사례 보고가 있으니 눈으로 확인할 것)
/status               # 현재 모델 확인
```

---

## 감사 종료 시 할 일

1. `CLAUDE.md` 에서 **AUDIT MODE 블록 전체를 삭제**한다.
2. 대신 `AUDIT_03_PASS2_3.md` 말미의 「코드 정합성 규칙 (영구)」 블록을 `CLAUDE.md` 에 남긴다.
3. `.claude/settings.json` 의 model 설정을 평상시 값으로 되돌린다.
4. `AUDIT_0*.md` 문서는 `experiments/runs/` 또는 `docs/audit/` 로 이관해 기록으로 보존한다.
5. `TECH_DEBT.md` 의 미해결 P1 이하 항목을 `SPEC_06_phases.md` 의 향후 작업으로 이관한다.
