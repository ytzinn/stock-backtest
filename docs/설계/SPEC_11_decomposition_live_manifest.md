# SPEC_11 — 분해 완결 3종 + #24 라이브 포워드 동결 manifest

> **작성일**: 2026-07-19
> **세션 스코프**: §2(멤버십 분석·우선주 점검 — 저비용, DB 조회 중심) → §3(PBR 정의 비교) → §4(D_pbr_no_r3r4) → §5(manifest). §5는 **기한 있음** — #24 리밸런싱(반기보고서 마감 8/14 + 3영업일 ≈ **2026-08-19 전후**) 이전 완성 필수. SPEC_10과 병행 가능하나 §3·§4 실행은 크론 동결 스냅샷 조건 공유.
> **모델 권장**: §2는 Sonnet 가능, §3~§5는 Fable 5 권장
> **표기 규칙**: `[검증된 사실]` / `[Claude 의견]` / `[확실하지 않은 사실]`

---

## 1. 목적

채택 후보 F_pbr_no_r3r4의 내부 구성을 분해로 확정하고(모멘텀 기여·PBR 정의·룰 중복), 데이터 정정이나 코드 변경 후에도 **당시 실제 의사결정을 보존**하는 라이브 기록 체계를 #24 전에 가동한다.

---

## 2. 멤버십 분석 — 성과 실험보다 먼저 (결정론적 근거 우선)

### 2-1. R1/R2/R5 룰별 탈락 분해

**[검증된 사실]** RIM 경로에서 R2는 R1과 완전 중복(D_rim_only == D_no_r2, Pass 0B에서 독립 재확인). PBR 경로에서는 미검증. CAGR 재실행 전에 **집합 수준에서 결정론적으로** 판정한다.

각 리밸런싱일(완결 20구간)에 대해, HARD + 모멘텀 통과 풀을 기준으로 집계:

- R1 단독 탈락 / R2 단독 탈락 / R1·R2 동시 탈락 종목 수·종목명
- R5 단독 탈락 / R6 단독 탈락
- 각 룰 제거 시 새로 유입되는 종목 수·종목명
- **핵심 판정**: R2 제거 전후 최종 편입 후보 집합(1/PBR 상위 20 진입권)이 전 리밸런싱일에서 동일한가?
  - 동일 → R2 결정론적 삭제 가능 (CAGR 실험 불필요). 룰셋 {R1,R5,R6} 단순화 제안
  - 상이 → 차이 종목 목록 기록 후, 해당 조합만 CAGR 재실행

**부수 확인** — 문서 정합: MASTER §3-6 활성 룰은 {R1,R4,R5,R6}(R2·R3 폐기, v5.4)인데 채택 후보의 룰셋은 {R1,R2,R5,R6}(R4 제외·R2 포함)이다. 이 불일치의 유래(no_r3r4 태그 명명이 R1~R6 기준이라 R2가 재포함됨)를 기록하고, R4에 대해서도 같은 멤버십 분석을 수행해 최종 룰셋 표기를 하나로 확정할 재료를 만든다.

### 2-2. 우선주 유니버스 점검

**[확실하지 않은 사실 → 확정 대상]** 우선주 티커가 필터를 통과해 편입된 적이 있는가. 우선주 시가총액을 회사 전체 지배주주지분(또는 자본총계)으로 나누면 PBR이 왜곡·중복 계상된다.

```sql
-- 예시: 편입 이력 전체에서 우선주 의심 티커 스캔
-- (KRX 우선주 티커는 통상 끝자리 5/7/9/K 등 — stocks 테이블의 종목명
--  '우'/'우B'/'2우B' 포함 여부와 교차 확인)
```

- 편입 이력 0건 + 통과 풀에도 0건 → "DART 재무 매핑 부재로 HARD의 PIT 존재 조건에서 자연 탈락" 가설 확인, 리포트에 한 줄 기록으로 종료
- 존재 → HARD 필터에 보통주 한정 규칙 추가 여부를 정책 결정 항목(CONTRACT-UNIV-001)으로 상신

---

## 3. PBR 정의 비교 — `PBR_total` vs `PBR_parent`

**[검증된 사실]** 현행 `rank_mode='pbr'`는 자본총계 기준, R6·RIM은 지배기업소유주지분 우선. BASIS-RIM-001과 같은 성격의 기저 불일치가 채택안 내부에 남아 있다.

- 전 필터의 equity를 일괄 통일하지 **않는다**: R1(부채비율)은 연결 전체 부채에 대응하는 자본총계가 자연스럽고, R6·PBR은 주주 귀속 기준이 맞다 — 필터별 적정 기준 유지
- 신규 태그 `F_pbr_no_r3r4_parent`: PBR = market_cap ÷ 지배기업소유주지분(우선순위·fallback은 RIM equity 규칙 재사용, SSOT)
- 비교: CAGR·net·Sharpe·MDD + 편입 종목 Jaccard. **판정 목적이 아니라 안정성 확인** — 두 정의 간 결과가 크게 갈리면 그 자체가 경고(어느 한쪽 선택이 또 하나의 in-sample 선택이 되므로, 갈릴 경우 처리 방침은 사용자 결정으로 상신)
- 장기 계약(코드 주석으로 명시만, 구현은 보류): "보통주 시가총액 ÷ 지배기업 보통주 귀속 장부가" — §2-2 결과와 결합해 Phase 3+에서

---

## 4. `D_pbr_no_r3r4` — 채택안에서 모멘텀 독립 기여 격리

- 신규 태그: HARD + {R1,R2,R5,R6} + 1/PBR (모멘텀 없음) — F_pbr_no_r3r4에서 모멘텀만 제거한 정확한 대조군
- 단순 CAGR 차이 외 필수 진단:
  1. 구간별 paired return 차이 (부호 승률 포함)
  2. 일별 MDD (SPEC_09 엔진), turnover, 구간별 포트폴리오 종목 수
  3. **모멘텀에 의해 탈락한 종목의 이후 1구간 수익률** — 거부권이 실제 가치를 더했는지, 후보 폭만 줄였는지
  4. **모멘텀 통과 vs 탈락 종목의 평균 PBR 차이** — 모멘텀이 가장 싼 종목을 체계적으로 쳐내는지(가치-모멘텀 상충). 상충이 크면 모멘텀 다변화(M1/M2 랭크 블렌드) 논의의 실증 근거가 됨
- **[Claude 의견]** 이 결과는 M0~M3 사전등록 실험의 기준선(M0 vs 모멘텀 부재)을 겸한다. 즉 모멘텀 다변화는 여기서 멈추고, M1~M3 실행은 walk-forward/shadow 프레임 확정 후.

---

## 5. #24 라이브 포워드 동결 manifest — 기한: 2026-08-19 전후 리밸런싱 이전

### 5-1. 사전 manifest (리밸런싱 실행 **전** git 커밋)

저장: `experiments/live/2026-08-XX/manifest.yaml`

```yaml
strategy_version:            # 예: F_pbr_no_r3r4 v1.0 (SPEC_10 관문 결과 반영)
git_commit_sha:
config_hash:                 # constants + 활성 룰 + n_stocks 직렬화 해시
database_snapshot_date:
price_max_date:
market_cap_max_date:
financial_pit_build_id:
signal_date:
execution_date:
execution_rule:              # 예: 종가 체결 가정, 실제는 당일 분할 주문
selected_tickers:
target_weights:
pbr_scores:                  # 전 후보 순위 포함 (top-20만이 아니라 통과 풀 전체)
filter_rejection_reasons:    # 단계별 탈락 사유 집계
expected_turnover:
expected_cost:
random_seed:                 # 해당되는 경우
test_suite_status:           # 실행 시점 fast/integration 통과 여부
```

구현 노트 (2026-07-19): `financial_pit_build_id`로 쓸 빌드 식별자가 DB에 없으면
`financials_pit`의 `MAX(available_from)` + 행 수 해시로 대체 정의한다 (구현 시 확인).

### 5-2. 사후 실행 기록 (거래 완료 후 별도 파일)

```yaml
actual_execution_price:      # 종목별
actual_commission:
actual_tax:
actual_slippage:
fill_ratio:
order_start_time:
order_end_time:
tracking_error_vs_model:     # 모델 가정 체결 vs 실제 체결 수익률 차이
```

### 5-3. 운영 규칙

- manifest 커밋 후에는 해당 리밸런싱의 신호를 **어떤 이유로도 소급 수정하지 않는다.** 이후 데이터 정정·코드 변경으로 재계산 신호가 달라지면 별도 파일(`recomputed_signal.yaml`)로 병기하고 원본은 불변
- 이 기록이 이 프로젝트 유일의 진짜 OOS 관측 축적 수단이다 — 완결 20구간 전체를 보며 룰을 고른 이상, 과거 홀드아웃(W6/W7)은 시나리오 선택 관점에서 이미 소진됐다
- SPEC_10 관문이 FAIL이어도 shadow portfolio로서 manifest는 동일하게 기록한다 (실제 자금 집행 여부만 분리)

---

## 6. Claude Code 지시

```
[M-1] §2-1 멤버십 분석 스크립트 (scripts/analysis/rule_membership.py, 읽기 전용)
      + §2-2 우선주 스캔 SQL. 결과는 experiments/runs/에 보고서로.
[M-2] §3 F_pbr_no_r3r4_parent 태그 추가 (equity 규칙은 RIM SSOT 재사용) + 비교표.
[M-3] §4 D_pbr_no_r3r4 태그 + 진단 4종. 탈락 종목 이후 수익률은
      momentum_filter의 rejected dict를 tape에 보존하도록 최소 확장
      (기존 시나리오 결과 불변 확인 필수).
[M-4] §5 manifest 스키마 구현 (scripts/live/freeze_rebalance.py) —
      dry-run으로 현재 스냅샷 기준 가상 manifest 1회 생성해 스키마 검증.
[M-5] 전 작업 후 fast/integration suite 무손상 + 결정론 태그 기존값 불변 확인.

[실행 조건] §3·§4는 크론 동결 스냅샷 + valuation_date 명시 (SPEC_10과 동일 스냅샷
권장 — 상호 비교 가능성 확보). 운영 DB(5433)는 읽기 전용으로만.
```

## 7. 완료 체크리스트

- [ ] R2(·R4) 멤버십 판정 완료 — 결정론 삭제 가능 여부 확정, 룰셋 표기 단일화 재료 확보
- [ ] 우선주 편입 이력 확인 완료 (0건 확인 또는 CONTRACT-UNIV-001 상신)
- [ ] PBR_total vs PBR_parent 비교표 발행 (크게 갈리면 사용자 결정 상신)
- [ ] D_pbr_no_r3r4 + 진단 4종 발행
- [ ] freeze_rebalance.py dry-run 성공 — **8월 리밸런싱 전**
- [ ] MASTER/SPEC 일괄 개정은 SPEC_09~11 결과 취합 후 별도 세션 (본 SPEC 범위 외)
