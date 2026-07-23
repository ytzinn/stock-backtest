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

## 문턱값 확정 경위

사용자에게 §5-3/§5-4/§4-4/conflict_rate 문턱값 표를 제시하고 논의를 제안했으나
"이대로 해보자"로 **스펙 v0.3.1 기본값을 그대로 채택**하기로 확정 (2026-07-23).
표는 JSON의 `decision_thresholds`에 기계판독 형태로 고정.

## 실행 결과 (2026-07-23 완료 — 격리 스냅샷, 운영 DB/크론 무영향)

manifest 동결 이후 Family A/B/C/D 전체(absret126/signcount126/ma200/52w75/mktresid126) +
52w75 OAT 밴드까지 실행 완료. **F_pbr_52w75만 §5-3 1차 문턱 통과했으나 robust(§5-4) 미충족으로
INCONCLUSIVE, 나머지 4개 전부 FAIL.** 상세 수치·판정 근거는 SPEC_12 §9. 크론 동결 대신 운영
DB(5433)와 완전 분리된 격리 스냅샷(포트 5435, pg_dump/restore)에서 실행 — snapshot_id·
benchmark_file_sha256 필드는 이 방식 특성상 미기록(§9 "실행 방식" 참조), 대신 스냅샷 뜬 시각
(2026-07-23 05:55:54 UTC)과 행수 대조 결과로 재현성 보증. 통과 primary가 없어 permutation
귀무분포(MC-9)·전일신호 검증(MC-8)은 실행하지 않음(§6-3 연산범위 제한과 정합).

## 부가 탐색 (2026-07-23/24, 비-사전등록 — SPEC_12 §10)

사용자 요청으로 기존 `MomentumFilter`(MA20/60/confirm5/slope20) 파라미터 그리드(9개
변형) + 구간단위 paired 부트스트랩(10,000회)까지 진행. 전 변형이 인컴번트보다 열위였으나,
`ma60_120`·`slope_lookback=30`은 부트스트랩상 노이즈 범위(통계적 무승부)로 확인 — 원안이
날카로운 최적점이 아니라 넓은 안정 구간(plateau) 위에 있다는 게 결론. §6-2에 따라
사전등록 primary로 승격하지 않음. 상세: SPEC_12 §10.
