# stock-backtest — MASTER 설계서

> **설계서 버전**: v4.9
> **프로젝트 저장소**: `stock-backtest/`
> **기준 문서**: 멀티모델_백테스트_머신_설계서_v4.8.md

---

## 이 프로젝트의 목적

RIM(잔여이익모델) 기반 한국 주식 멀티팩터 백테스트 머신.
생존편향 없는 전종목(~2,500개 + 상장폐지 ~1,500개) 데이터를 수집하고,
4단계 필터(Hard → 재무안정성 → 팩터스크리닝 → 모멘텀) + RIM 적정가 기준으로
반기 리밸런싱 포트폴리오를 구성해 2015년부터 현재까지 백테스트한다.

---

## 핵심 설계 철학

> "RIM + 모멘텀으로 Baseline 먼저 → 결과 보고 확장"
> 멀티모델 산식 확정은 Phase 2 Ablation Test 결과 이후로 이동.

---

## 세부 설계서 파일 목록

| 파일 | 내용 | 관련 Phase |
|------|------|-----------|
| `SPEC_01_infra.md` | 인프라(Ubuntu/cron/Docker), 디렉토리 구조, 데이터 흐름 | 사전 준비 |
| `SPEC_02_ingest.md` | 데이터 수집(DART/FDR), DB 공통 스키마, PIT/DQ Gate | Phase 0~1 + 공통 DB |
| `SPEC_03_universe.md` | Universe 필터 4단계, interfaces, BacktestPipeline, configs | Phase 1~2 |
| `SPEC_04_models.md` | RIM 모델, 분류기(skeleton), 포트폴리오, 엔진 | Phase 2~3 |
| `SPEC_05_backtest.md` | Ablation Test, 성과측정, Fitness Function, 튜닝, 과최적화 방지 | Phase 2~4 |
| `SPEC_06_phases.md` | Phase별 로드맵 + 체크리스트, 산출물 포맷, 향후 확장 메모 | 전체 |

---

# 2. 전체 데이터 흐름

```
[Phase 0 — 전종목 수집 (생존편향 포함)]
KRX 현재 상장 종목 + 상장폐지 종목
    │ universe_loader.py + delisting_ingest.py
    ▼
stocks 테이블 + stock_listing_events 테이블        ← v4.7: 이벤트 이력 구조
    │
    ├─ dart_ingest.py        →  financials 테이블       (2014년~)
    │                            disclosures 테이블
    ├─ price_ingest.py       →  price_history 테이블    (adj_close, is_suspended 포함)
    └─ market_cap_ingest.py  →  market_cap_history 테이블

[Phase 1 — 데이터 정제]
    │ pit_loader.py  →  financials_pit 테이블           (fallback_used 컬럼 포함)
    │ validator.py   →  validation_log 테이블
    │ dq_gate.py     →  universe_gate_pit 테이블        ← v4.7: 시점별 판정 (ticker×year×report_type)
    │                    (영구 제외는 stocks.is_excluded 로 처리)

[Phase 2 — RIM 단일 모델 백테스트 (핵심 실행 단계)]
universe_gate_pit(PASS) + financials_pit + price_history
    │
    ├─ Hard Filter          (거래대금, 상장기간, PIT 존재)
    ├─ 재무안정성 필터       (부채비율, 차입금비율, 회전율, 영업CF)    ← v4.3 신규
    ├─ 팩터 스크리닝         (매출YoY + 영업이익YoY + GP/A + 1/PBR → 상위 20%)  ← v4.3 신규
    ├─ 모멘텀 필터           (MA20/MA60 이중 조건)
    └─ RIM 밸류에이션 필터   (현재가 > RIM적정가 × 1.05 제외)         ← v4.3 신규
    ▼
포트폴리오 구성 → 성과 측정 → Ablation Test (A~G, 랜덤 시나리오 500회)

[Phase 3 — 기업 분류기 + 팩터 가중치 튜닝]
    ← Phase 2 결과 기반으로 실행

[Phase 4 — Walk-forward 검증 + Fitness Sensitivity]
    ← Phase 3 결과 기반으로 실행

[Phase 5 — 멀티모델 확장]           ← v4.3에서 명시적으로 분리
    ← Phase 4 OOS Alpha 확인 후 결정
```

### pykrx API 제약 (2026-05 확인)

KRX 2024 웹사이트 리뉴얼로 OTP 엔드포인트(`/cgi-bin/service/otp.cmd`)가 404 반환.
아래 함수들은 **빈 응답** 또는 오류를 반환하므로 대체 로직 적용:

| 함수 | 상태 | 대체 |
|------|------|------|
| `get_market_ohlcv(start, end, ticker)` | ✅ 작동 | — |
| `get_market_ohlcv_by_date(start, end, ticker)` | ❌ 빈 응답 | `get_market_ohlcv` 사용 |
| `get_market_cap_by_date(start, end, ticker)` | ❌ 빈 응답 | FDR shares × 종가 근사 |
| `get_market_sector_classifications(date)` | ❌ 빈 응답 | `is_financial` 수동 설정 |
| `get_market_ticker_list(date)` | ❌ 빈 응답 | FDR StockListing 스냅샷 비교 예정 |
| `get_index_ohlcv_by_date(start, end, ticker)` | ❌ KeyError('지수명') | `price_history` DISTINCT date 조회 (영업일 캘린더 대용) |

한계: 시가총액은 FDR 현재 상장주식수 × 종가 근사 (주식수 변경 이력 미반영).

---

# 3. 핵심 파라미터 확정값

> 이 섹션의 수치는 코드 전체에서 단일 소스(constants)로 관리한다.
> Phase별 변경 시 이 테이블과 코드를 동시에 수정한다.

## 3-1. RIM 모델 상수

| 파라미터 | 값 | 근거 |
|---------|-----|------|
| RF (무위험수익률) | 2.63% | stock-analysis 기존값 유지 |
| RK (시장기대수익률 = RF + ERP) | 8.73% | ERP = 6.10%, stock-analysis 기존값 유지 |
| r (요구수익률, β=1.0 고정) | **8.73%** | r = RF + 1.0 × (RK − RF) = RK |
| β | 1.0 고정 (Phase 2~4) | get_beta() 미구현. Phase 3 이후 rolling β 도입 검토 |
| adjROE 방식 | Dechow(1994) Method C, λ=0.5 | adjROE = (0.5×NI + 0.5×CFO) / equity — **stock-analysis fair_value.py와 동일** |
| dividend_status missing 처리 | payout=0 가정 (낙관적 편향 허용) | KOSDAQ 소형주 누락 다수 → 제외 시 소형주 편향 |

코드 내 선언 위치: `backtest/models/rim.py`, `backtest/filters/stability_filter.py`
```python
RF, RK = 0.0263, 0.0873   # 두 파일에서 동일 값 유지
```

- **성장률 상한**: `g = max(0, min(adjROE × (1−payout), r × 0.9))`. 상한 `r × 0.9`는 분모 `(1+r−g)` 발산 방지 수학적 안전장치. 튜닝 제외, 고정값.
- **FV 음수 방어**: `fv_total ≤ 0`이면 `None` 반환. R6 필터로 대부분 선제 제거되지만, PIT 데이터와 재무안정성 필터 타이밍 불일치(리밸런싱 시점 vs FY 시점)로 FV 음수 발생 가능 → 방어적 처리. 실제 발생 확인으로 추가됨.

## 3-2. 리밸런싱 날짜

| 구분 | 법정 마감일 | 리밸런싱 기준일 |
|------|-----------|---------------|
| 상반기 (FY 사업보고서 활용) | 3월 31일 | 3/31 + **3 영업일** |
| 하반기 (H1 반기보고서 활용) | 8월 14일 | 8/14 + **3 영업일** |

- 백테스트 구간: 2015년 상반기 ~ 2026년 상반기 (**21개 구간**)
- 영업일 계산: `price_history` DISTINCT date (대표 KOSPI 종목 기준, 삼성전자 005930). pykrx `get_index_ohlcv_by_date('KOSPI')`는 KRX 리뉴얼 후 KeyError 반환으로 사용 불가.
- 21개 날짜는 사전 계산 후 `configs/rebalance_dates.py`에 하드코딩 (재현성 보장). 생성 스크립트: `scripts/generate_rebalance_dates.py`

## 3-3. 포트폴리오 구성

| 항목 | 확정값 |
|------|--------|
| 가중 방식 | 동일가중 (1/N) |
| 목표 종목 수 | **20개** (Bayesian 튜닝 범위: 10~30) |
| 종목당 최대 비중 | **5%** |
| 업종 최대 비중 | 25% |
| KOSDAQ 최대 비중 | 60% |
| AUM 가정 | 5억원 |
| 거래대금 하한 | 일평균 **1억원** 미만 제외 (Hard Filter) |

## 3-4. 성과 지표 정의

| 지표 | 정의 |
|------|------|
| **알파** | 전략 CAGR − KOSPI CAGR (단순 차이, 배당 미반영 동일 조건) |
| Robustness | 21개 리밸런싱 구간 중 KOSPI 대비 Alpha 양수 비율 |
| 벤치마크 3종 | KOSPI / KOSDAQ / 유니버스 랜덤 (C_stability_random 500회 중앙값) |
| 수익률 기준 | adj_close 수정주가. 배당 미반영. 벤치마크도 동일 조건. |

## 3-5. 팩터 스크리닝 초기 가중치

| 팩터 | 초기 가중치 | Phase 3+ 튜닝 |
|------|-----------|--------------|
| 매출액 YoY | 1/6 | 대상 |
| 영업이익 YoY | 1/6 | 대상 |
| GP/A (Novy-Marx 2013) | 1/3 | 대상 |
| 1/PBR | 1/3 (= 1 − 나머지 합) | 파생 |

**Phase 2 설계 원칙**: 팩터 간 상관관계 측정 및 PCA/직교화 필요성 검토는 **Phase 2 Ablation 결과 확인 후** 결정. 상위 20% 기준 매출YoY-영업이익YoY 상관계수 ≥ 0.5 시 가중 구조 재검토.

## 3-7. Phase 2 튜닝 파라미터 (4개)

| 파라미터 | 초기값 | 튜닝 범위 | 비고 |
|---------|--------|----------|------|
| `beta_adj` (r 오프셋) | 0.0 | [-0.02, +0.02] | r = RF + β×(RK-RF) + **beta_adj**. β=1.0 고정 유지, r 수준만 미세 조정 |
| `rim_threshold` | 0.05 | [-0.10, +0.20] | 밸류에이션 필터 임계값 (현재가 > FV×(1+rim_threshold) 제외) |
| `top_pct` | 0.20 | [0.10, 0.40] | 팩터 스크리닝 컷오프 비율 |
| `n_stocks` | 20 | [10, 30] | 포트폴리오 목표 종목 수 |

> `beta_adj`는 종목별 β 차이를 흡수하기 위한 전역 오프셋. β=1.0 고정은 유지.
> `beta_adj` < 0: r 낙관적(할인율 낮음) → 적정가 상승. `beta_adj` > 0: r 보수적 → 적정가 하락.

**Phase 2 고정값 (튜닝 제외):** 모멘텀 파라미터 4개 / 업종 집중 상한 25%\* / 거래대금 기준 1억원 / 팩터 가중치 (동일가중 고정)

> \* 업종 집중 상한 25%: `stocks.sector` 수동 업데이트 의존으로 데이터 신뢰도 불확실. Phase 2에서는 하드 룰로 유지. Phase 3 이후 sector 데이터 정비 완료 시 Bayesian 튜닝 대상 [15%, 40%] 검토.

## 3-6. 재무안정성 필터 기준 (R1~R6, 하드 룰)

Bayesian 튜닝 대상에서 제외. 조건 충족 시 즉시 탈락.

| 규칙 | 기준 | 예외 조건 |
|------|------|----------|
| R1 부채비율 | 총부채 / 자기자본 > 200% | 없음 |
| R2 차입금비율 | (단·장기차입금+사채) / 자기자본 > 150% | 최근 3FY 단조 감소 + 누적 10%p 이상 개선 시 통과 |
| R3 매출 역성장 | 최근 3FY 중 YoY < -5% 횟수 ≥ 2 | 없음 |
| R4 영업CF | 2년 연속 음수 | 없음 |
| R5 영업CF(-)+재무CF(+) | 차입으로 운영, 1회 발생 | 없음 |
| R6 adjROE < r | adjROE < 8.73% (β=1.0, RIM 가치 파괴 구간) | 없음 |

참고 플래그 (탈락 아님): 재고자산 회전율 전년비 -30% 이상 하락, 매출채권 회전율 전년비 -30% 이상 하락 → Phase 3+에서 감점 참고.

코드 위치: `backtest/filters/stability_filter.py`

---


---

# 1. 전체 시스템 구조

## 1-1. 저장소 분리 원칙

```
[기존 — 독립 유지, 일절 수정 없음]
stock-analysis/             ← 30개 종목 대시보드, 운영 파이프라인

[신규 — 완전 독립]
stock-backtest/      ← 백테스트 전용 저장소
    ingest/
    backtest/
    experiments/
```

두 저장소는 DB도 분리한다. `stock-backtest/`는 자체 PostgreSQL 인스턴스(포트 5433)를 사용한다.
기존 대시보드 DB(포트 5432)에 접속하거나 의존하는 코드를 작성하지 않는다.

**실행 환경:**
- **코드 개발**: Windows 11 PC (VS Code Remote SSH로 Ubuntu 서버에 직접 접속)
- **DB / 배치 수집 / 백테스트 연산**: Ubuntu 26.04 서버 (Beelink SER8, 32GB RAM, NVMe 1TB, LAN 172.30.1.96, Docker Engine + cron)
- **배포 대상 없음**: 백테스트는 서버 로컬 실행. 외부 공개 불필요.
- pykrx KRX 수집은 국내 IP인 Ubuntu 서버에서 직접 실행 (Railway 해외 IP 차단 문제 해소)

## 1-2. 디렉토리 구조

```text
korean-stock-backtest/
│
├─ .env
├─ .gitignore
├─ requirements.txt               # 주요 패키지 버전 고정 필수
│                                 # 예: pykrx==1.0.47, FinanceDataReader==0.9.50
│                                 #     optuna==3.6.1, pandas==2.2.2, psycopg2-binary==2.9.9
├─ docker-compose.yml             # Ubuntu 서버 PostgreSQL (포트 5433)
├─ CLAUDE.md
│
├─ scripts/
│   ├─ start.sh                   # Docker + 서비스 일괄 시작
│   ├─ stop.sh                    # 일괄 종료
│   ├─ backup_db.sh               # DB 백업 (cron에서 호출)
│   └─ run_batch.sh               # 전체 배치 수집 래퍼 (cron에서 호출)
│
├─ ingest/
│   ├─ schema.sql
│   ├─ connection.py
│   ├─ universe_loader.py         # KRX 현재 + 상장폐지 종목 목록
│   ├─ dart_ingest.py
│   ├─ price_ingest.py            # OHLCV + adj_close + is_suspended
│   ├─ market_cap_ingest.py       # 시가총액·상장주식수 (pykrx)
│   ├─ delisting_ingest.py        # FDR KRX-DELISTING 상장폐지 이력
│   ├─ pit_loader.py
│   ├─ validator.py
│   └─ dq_gate.py
│
├─ backtest/
│   ├─ interfaces.py              # [v4.8] Protocol 정의: UniverseFilter, ValuationModel
│   ├─ data_access.py             # [v4.8] DB 조회 헬퍼 (ingest/connection.py 재사용)
│   ├─ pipeline.py                # [v4.8] BacktestPipeline 조립 클래스
│   ├─ engine.py                  # 리밸런싱 루프, 수익률 계산
│   ├─ filters/                   # [v4.8] 유니버스 필터 구현체
│   │   ├─ hard_filter.py         # (기존 universe.py → 이동)
│   │   ├─ stability_filter.py    # (기존 universe.py → 이동)
│   │   ├─ factor_screener.py     # (기존 screener.py → 이동)
│   │   └─ momentum_filter.py     # (기존 universe.py → 이동)
│   ├─ models/                    # [v4.8] 적정가 모델 구현체
│   │   ├─ rim.py                 # RIMModel (기존 models.py → 이동)
│   │   └─ _skeleton.py           # Phase 5 멀티모델 skeleton (EV/Sales, FCFF 등)
│   ├─ configs/                   # [v4.8] Phase별 파이프라인 조립
│   │   ├─ rebalance_dates.py     # 21개 리밸런싱 날짜 하드코딩 (재현성 보장)
│   │   ├─ phase2_rim.py          # Phase 2 기본 파이프라인
│   │   └─ phase5_multimodel.py   # Phase 5 멀티모델 파이프라인 (미래)
│   ├─ classifier.py              # Phase 3 이후 활성화 (현재 skeleton)
│   ├─ scorer.py                  # 저평가 랭킹 + 밸류에이션 필터
│   ├─ portfolio.py
│   ├─ metrics.py
│   ├─ tuner.py
│   └─ reports.py
│
└─ experiments/
    ├─ runs/
    ├─ dq_gate_result.csv
    ├─ ablation/                  # Ablation Test 결과
    ├─ sensitivity/
    └─ reports/
```

---


---

## 버전 이력

# 멀티모델 적정가 엔진 + 기업 분류기 + 자동 튜닝 백테스트 머신 통합 설계서 v4.8

> **최초 작성**: v3.0
> **v4.2**: Rule-based 분류 로직 수정, 리밸런싱 날짜 확정, Fitness Function 재조정, 모멘텀 필터 추가, 백테스트 시작점 2015년 변경
> **v4.3**: RIM 단일 모델 우선 실행 전략 채택, 팩터 스크리닝 레이어 신규 추가, 재무안정성 Hard Filter 정량화, 멀티모델 확장 구조 명시적 분리, Rolling Walk-forward 도입, Ablation Test 추가, 생존편향 해소 데이터 추가
> **v4.4**: 데이터 신뢰성 검증 강화(adj_close 교차검증, DART 계정 매핑 게이팅, FDR 완결성 검증), 거래비용 모델 거래세 명시, Final Holdout 분리, β 편향 정량화 체크리스트 추가, 배당금지급 None 로깅, 대주주 지분율 향후 메모 추가
> **v4.5**: Ablation 구조 재설계(Hard/Stability 기여도 분리, 7개 시나리오), Random benchmark 500회 반복 + percentile 표시, 벤치마크 3개로 확장(KOSPI + KOSDAQ + 유니버스 랜덤)
> **v4.6**: 인프라 운영 환경 확정 (개발: Windows 11 / 실행: Ubuntu 서버). §0 신규 추가. §1-1·§1-2·§3-1 Ubuntu 환경 기준으로 수정
> **v4.7**: 설계 비판 검토 반영. ① `backtest_runs` 재현성 컬럼 6개 추가 ② `stock_listing_history` → `stock_listing_events` (상장 이벤트 이력 구조) 교체 ③ `universe_gate` → 영구제외(`stocks`) + 시점별(`universe_gate_pit`) 분리 ④ `financials_pit`에 `fallback_used` 추가 ⑤ `dividend_status` 3분류 도입 + RIM 코드 수정 ⑥ `requirements.txt` 버전 고정 명시
> **v4.8**: 모듈화 설계 도입. `backtest/` 하위 `filters/` · `models/` · `configs/` 디렉토리 분리. `interfaces.py` Protocol 정의(UniverseFilter, ValuationModel). `build_universe()` → `BacktestPipeline` 클래스로 교체. `RIMModel` 클래스화. Phase별 파이프라인 조립을 `configs/`에서 관리.
> **v4.9** (인터뷰 반영): ① `UniverseFilter.apply()` 시그니처 `pit_prev` 제거 → `pit_series: dict[str, list[dict]]`([0]=현재, [1]=t-1, [2]=t-2) 통일. ② `backtest/data_access.py` 신규 — DB 조회 헬퍼 집중, `ingest/connection.py` 재사용, `conn` 주입 패턴. ③ 필터 클래스: 생성자 파라미터 주입 + `apply()` 메서드 구조 확정. ④ FactorScreener 가중치 키 영어 통일(`rev_yoy, op_yoy, gpa, inv_pbr`). ⑤ `beta_adj` 파라미터 정의 명시 (r 오프셋, β=1.0 유지). ⑥ `configs/rebalance_dates.py` 생성 스크립트 추가. ⑦ Phase 2 튜닝 파라미터 테이블 §3-7 신규.
> **v4.9 추가** (심층 인터뷰 반영): ⑧ `g` 상한 `r×0.9` 수학적 안전장치 명시(§3-1). ⑨ `fv_total ≤ 0` 방어 처리 추가 — 실제 발생 확인, R6 이후 PIT 타이밍 불일치 케이스. ⑩ 리밸런싱 날짜 영업일 계산: pykrx 불가 → `price_history` DISTINCT date(삼성전자 기준) 대체. ⑪ `dividend_status` 로컬 변수 제거, `logging.debug` 유지 — Phase 4 민감도용 DB 원본 활용. ⑫ 업종 집중 상한 25%: `stocks.sector` 데이터 미정비로 Phase 2 고정, Phase 3 이후 검토.
>
> **핵심 변경 철학**: "모든 모델 산식 먼저 → 구현" 순서 대신 "RIM + 모멘텀으로 Baseline 먼저 → 결과 보고 확장" 순서로 전환.
> 멀티모델(EV/Sales, Peer PER, NAV, FCFF 등) 산식 확정은 Phase 2 Ablation Test 결과 이후로 이동.
>
> **저장소**: `stock-backtest/` (신규 독립 저장소)

---

