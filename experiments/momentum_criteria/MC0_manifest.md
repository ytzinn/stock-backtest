# SPEC_12 MC-0 사전등록 manifest (2026-07-23)

기계판독본: [`MC0_manifest.json`](./MC0_manifest.json). 결과(Family A/B/C/D 실제 성과) 열람 전 커밋.
이후 primary 교체 금지 (SPEC_12 §6-2).

## 확정된 것 (MC-1~MC-3, 이미 실행·검증됨)

- **MC-1 VERIFY 4건**: 전부 코드/DB 실측으로 확인 완료 — config 미배선, stats key 하드코딩,
  compute_daily_metrics() CAGR 부재, is_suspended 컬럼 정상 작동. 상세: SPEC_12 §7 MC-1.
- **MC-3 배관 게이트**: `F_pbr_ma_double_adapter`가 `F_pbr_no_r3r4`와 완결 20구간 전부
  완전 일치(포트폴리오·수익률·turnover·모멘텀 통과/탈락 집합, metrics). 신규 배관
  (prepare→evaluate→stats_key→tape) 신뢰 확보. 이 결과는 "성과 비교"가 아니라 "배관 정합성
  증명"이므로 사전등록 취지(결과 미열람)를 해치지 않는다.
- **MC-5 compute_nav_cagr()**: 신설 완료.

## 아직 안 본 것 (MC-6+ 결과)

Family A(absret126/signcount126)·B(ma200)·C(52w75)·D(mktresid126)의 실제 성과 비교는
**아직 실행하지 않았다.** 이 manifest는 그 실행 전에 동결한다.

## 문턱값 확정 경위

사용자에게 §5-3/§5-4/§4-4/conflict_rate 문턱값 표를 제시하고 논의를 제안했으나
"이대로 해보자"로 **스펙 v0.3.1 기본값을 그대로 채택**하기로 확정 (2026-07-23).
표는 JSON의 `decision_thresholds`에 기계판독 형태로 고정.

## 실행 조건 (MC-6+ 착수 시 확정 필요)

- `snapshot_id` / `valuation_date` / `benchmark_file_sha256`: 크론 동결 스냅샷 절차
  (CLAUDE.md — crontab 주석 처리 → 전체 재실행 → 원복) 진행 시 채운다. 사용자가 이
  단계를 "별도 확인 후 진행"하기로 결정(2026-07-23) — 지금은 미실행.
- Family D(`mktresid126`)는 coverage gate(§3-D3, HardFilter 6개월 vs 요구 13개월) 사전
  확인 전까지 미구현.
