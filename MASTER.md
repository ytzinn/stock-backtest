# stock-backtest — MASTER 설계서

> **설계서 버전**: v5.1
> **프로젝트 저장소**: `stock-backtest/`
> **기준 문서**: 멀티모델_백테스트_머신_설계서_v4.8.md
> **Phase 2 완료**: 2026-06-21 (13개 시나리오 Ablation Test 완료) → **가격보정 재실행**: 2026-07-02

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
    ├─ dart_ingest.py        →  financials 테이블       (2015년~, 2014년 미수집 확인 — SPEC_06 Phase 0C)
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
포트폴리오 구성 → 성과 측정 → Ablation Test (13개 시나리오, 랜덤 500회)

[Phase 3 — 기업 분류기 + 팩터 가중치 튜닝]
    ← Phase 2 결과 기반으로 실행

[Phase 4 — Walk-forward 검증 + Fitness Sensitivity]
    ← Phase 3 결과 기반으로 실행

[Phase 5 — 멀티모델 확장]           ← v4.3에서 명시적으로 분리
    ← Phase 4 OOS Alpha 확인 후 결정
```

### 현재 사용 중인 데이터 소스 (2026-06 확인)

| 데이터 종류 | 소스 | 모듈 |
|------------|------|------|
| 일별 OHLCV (수정주가·거래량) | pykrx `get_market_ohlcv_by_date(adjusted=True)` | `ingest/price_ingest.py` |
| 시가총액 | pykrx `get_market_ohlcv()` × 상장주식수 | `ingest/market_cap_ingest.py` |
| 연도별 상장 종목 스냅샷 | KRX Open API `stk_bydd_trd`/`ksq_bydd_trd` | `ingest/krx_listing_ingest.py` |
| 재무 데이터 (FY·H1) | DART `fnlttSinglAcntAll.json` | `ingest/dart_ingest.py` |
| 영업일 캘린더 | `price_history` DISTINCT date (삼성전자 기준) | `backtest/configs/rebalance_dates.py` |
| 금융업 섹터 분류 | 수동 DB UPDATE | `stocks.is_financial` |

**pykrx 불작동 함수 (사용 금지, KRX 2024 리뉴얼 이후):**
- `get_market_cap_by_date()` → 빈 DataFrame
- `get_market_ticker_list()` → 빈 응답 (KRX Open API로 대체)
- `get_market_sector_classifications()` → 빈 응답
- `get_index_ohlcv_by_date()` → KeyError('지수명')

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
| adjROE 방식 | Dechow(1994) Method C, λ=0.5 | adjROE = (0.5×NI + 0.5×CFO) / equity. equity = **지배기업소유주지분** 우선, 없으면 자본총계 fallback. CFS에서 비지배지분 제외하여 지배주주 기준 적정가 산출. |
| ω (초과이익 지속성) | **0.62** | Dechow(1994) 실증값. 구 산식의 g·payout 대체. V/B = 1 + (adjROE − r) / (1 + r − ω) |
| VB_CAP | **5.0** | V/B 상한 새니티 캡. FV = equity × clamp(V/B, 0, 5.0). 극단적 고ROE 종목 FV 폭발 방지 |

코드 내 선언 위치: `backtest/models/rim.py`, `backtest/filters/stability_filter.py`
```python
RF, RK = 0.0263, 0.0873   # 두 파일에서 동일 값 유지
```

- **FV 산출**: `FV = equity × clamp(V/B, 0, 5.0)`. ω=0.62 고정(Dechow 1994 실증값), VB_CAP=5.0(새니티 캡). 구 산식(g·payout 기반)은 분자에 ×g가 붙어 ROE 민감도가 PBR 대비 ~20배 낮은 병리가 있었음 → 2026-06-21 산식 교체.
- **FV 음수 방어**: clamp 하한 0으로 처리. equity 자체가 음수인 경우(자본잠식) R6 필터에서 선제 제거.

## 3-2. 리밸런싱 날짜

| 구분 | 법정 마감일 | 리밸런싱 기준일 |
|------|-----------|---------------|
| 상반기 (FY 사업보고서 활용) | 3월 31일 | 3/31 + **3 영업일** |
| 하반기 (H1 반기보고서 활용) | 8월 14일 | 8/14 + **3 영업일** |

- 백테스트 구간: 2015년 상반기 ~ 2026년 상반기 (**23개 리밸런싱 날짜**)
  - **TTM 제약**: 2015-04-03·2015-08-19 두 날짜는 FY2014/H1_2014 PIT 데이터 미충족으로 유니버스 0개 (빈 구간, 0% 수익).
  - **유효 포트폴리오 구간: 21개** (2016-04-05 ~ 2026-04-03). CAGR·Robustness 등 성과지표 산출 기준.
- 영업일 계산: `price_history` DISTINCT date (대표 KOSPI 종목 기준, 삼성전자 005930). pykrx `get_index_ohlcv_by_date('KOSPI')`는 KRX 리뉴얼 후 KeyError 반환으로 사용 불가.
- 23개 날짜는 사전 계산 후 `configs/rebalance_dates.py`에 하드코딩 (재현성 보장). 생성 스크립트: `scripts/generate_rebalance_dates.py`

## 3-3. 포트폴리오 구성

| 항목 | 확정값 |
|------|--------|
| 가중 방식 | 동일가중 (1/N) |
| 목표 종목 수 | **20개** (Bayesian 튜닝 범위: 10~30) |
| 종목당 최대 비중 | 없음 — 동일가중 1/N (2026-07-05 5% 캡 폐지, SPEC_06 참조) |
| 업종 최대 비중 | 25% |
| KOSDAQ 최대 비중 | 60% |
| AUM 가정 | 5억원 |
| 거래대금 하한 | 일평균 **1억원** 미만 제외 (Hard Filter) |

## 3-4. 성과 지표 정의

| 지표 | 정의 |
|------|------|
| **알파** | 전략 CAGR − KOSPI CAGR (단순 차이, 배당 미반영 동일 조건) |
| Robustness | **21개 유효 구간** (TTM 미충족 2015-04·08 2구간 제외) 중 KOSPI 대비 Alpha 양수 비율 |
| 벤치마크 3종 | KOSPI / KOSDAQ / 유니버스 랜덤 (C_stability_random 500회 중앙값) |
| 수익률 기준 | adj_close 수정주가. 배당 미반영. 벤치마크도 동일 조건. |

> **미결 항목 (2026-07-02)**: KOSPI를 1차 벤치마크로 두는 현재 순서 대신 "Hard+Stability 통과
> 동일가중 유니버스"를 1차 KPI로 재배치하자는 제안이 검토됨 (2026년 KOSPI가 시총 상위 소수 종목
> 쏠림으로 급등해 동일가중 소형 가치주 전략과 스타일이 안 맞는다는 문제 제기). 아직 확정 아님 —
> 상세: SPEC_06 Phase 3 미결 항목, `2026.06.21. 백테스트_검토_및_모델개선_워크플로우.md` STEP 5.

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
stock-backtest/                   # 실제 서버 경로: /opt/stock-backtest/
│
├─ .env
├─ .gitignore
├─ requirements.txt
├─ docker-compose.yml             # Ubuntu 서버 PostgreSQL (포트 5433)
├─ CLAUDE.md
│
├─ scripts/
│   ├─ generate_rebalance_dates.py  # 리밸런싱 날짜 1회 생성 후 하드코딩
│   ├─ run_ablation.py              # 13개 시나리오 전체 실행
│   ├─ export_portfolios.py         # 기간별 편입 종목·가격 추출
│   ├─ fix_h1_disclosures.py        # H1 공시 누락 보정 (1회성)
│   ├─ estimate_omega.py            # Ohlson ω 파라미터 추정
│   ├─ run_omega_sensitivity.py     # ω 민감도 분석
│   └─ rebuild_stocks_from_krx.py   # stocks 테이블 재구성
│
├─ ingest/
│   ├─ schema.sql
│   ├─ connection.py
│   ├─ logging_config.py
│   ├─ universe_loader.py
│   ├─ krx_listing_ingest.py        # KRX Open API 상장 스냅샷 (연도별)
│   ├─ dart_ingest.py
│   ├─ price_ingest.py              # FDR DataReader → adj_close + is_suspended
│   ├─ market_cap_ingest.py
│   ├─ delisting_ingest.py
│   ├─ pit_loader.py                # financials_pit 생성 (XBRL 정정 반영)
│   ├─ validator.py
│   ├─ dq_gate.py
│   ├─ amendment_checker.py         # DART 정정 공시 → financials_pit.amendment_from
│   ├─ xbrl_historical_ingest.py    # XBRL 과거 재무 수집 (정정 이력 포함)
│   ├─ xbrl_mapper.py               # XBRL 계정명 ↔ 표준명 매핑
│   ├─ xbrl_poc.py                  # XBRL 개발용 POC
│   ├─ healthcheck.py
│   ├─ check_status.py
│   ├─ quick_status.py
│   └─ migrations/
│       └─ apply.py                 # DB 마이그레이션 순차 적용
│
├─ backtest/
│   ├─ interfaces.py                # UniverseFilter, ValuationModel Protocol
│   ├─ data_access.py               # DB 조회 헬퍼 (conn 주입, has_recent_trade 포함)
│   ├─ pipeline.py                  # BacktestPipeline 조립 클래스
│   ├─ engine.py                    # 리밸런싱 루프, 수익률 계산
│   ├─ metrics.py
│   ├─ portfolio.py
│   ├─ ablation.py                  # 13개 시나리오 정의 + _RandomSelectPipeline
│   ├─ filters/
│   │   ├─ hard_filter.py           # 5일 거래정지 검사 + 90일 거래대금 lookback
│   │   ├─ stability_filter.py      # R1~R6 하드 룰 (use_r6 플래그)
│   │   ├─ factor_screener.py       # 4팩터 상위 20%
│   │   └─ momentum_filter.py       # MA20/MA60 이중 조건
│   ├─ models/
│   │   └─ rim.py                   # RIMModel (Dechow 1994 adjROE, Gordon growth)
│   └─ configs/
│       ├─ constants.py             # RF=0.0263, RK=0.0873, OMEGA=0.62
│       ├─ rebalance_dates.py       # 23개 날짜 하드코딩 (2015~2026; 2015-04·08 TTM 미충족 빈 구간, 유효 21개)
│       └─ phase2_rim.py            # Phase 2 파이프라인 조립
│
├─ dashboard/
│   ├─ app.py                       # Streamlit 대시보드 (포트 8502)
│   ├─ server.py
│   ├─ health.py
│   ├─ queries.py
│   ├─ logs.py
│   ├─ config.py
│   ├─ system_checks.py
│   ├─ sanitize.py
│   └─ pages/
│       └─ ablation.py
│
├─ validate/
│   └─ factor_comparison.py
│
└─ experiments/
    ├─ ablation/                    # 13개 시나리오 결과 (JSON/CSV)
    └─ (기타 실험 결과)
```

---


---

## Ablation Test 결과 요약 (2026-07-02 가격보정 재실행 기준)

> 2026-06-21 최초 실행(13개 시나리오) → 같은 날 RIM 산식 교체(g·payout 기반 → Ohlson 지속성형,
> ω=0.62) → 2026-06-25 재실행 → 감자·분할 미반영 4종목(001290/002380/005950/043590) adj_close
> 소급보정 후 2026-07-02 전체 재실행. 아래는 최신(07-02) 수치. 상세: `experiments/runs/2026.07.02. BACKTEST_RESULTS.md`

| 시나리오 | CAGR (순) | Alpha vs KOSPI | Sharpe | MDD | 비고 |
|---------|---------|-------------|--------|-----|------|
| **A_random** | — | — | — | — | 랜덤 500회 분포만 |
| **B_hard_random** | 4.68% (중앙) | — | — | — | p95=12.13% |
| **C_stability_random** | 6.80% (중앙) | — | — | — | p95=11.94% |
| **D_rim_only** | 11.99% (10.99%) | -1.84% | 0.434 | -33.9% | RIM 단독 |
| **E_screener_rim** | 6.29% (5.31%) | -7.54% | 0.251 | -35.2% | 팩터 스크리닝 효과 없음 |
| **F_momentum_rim** | **14.63%** (13.45%) | +0.80% | **0.508** | **-32.6%** | **최적 조합** |
| **G_full** | 9.23% (8.08%) | -4.60% | 0.347 | -25.3% | 팩터 스크리닝이 성과 저해 |
| **H_no_stability** | 11.81% (10.62%) | -2.02% | 0.405 | -37.7% | 재무안정성 필터 제거 시 MDD 급등 |
| KOSPI 벤치마크 | 13.83% | — | — | — | 배당 미반영 |
| KOSDAQ 벤치마크 | 2.12% | — | — | — | 배당 미반영 |

**판정 결과:**
- ✅ D ≥ C_p95 (RIM 유효성): D(11.99%) ≥ C_p95(11.94%) → RIM 통계적 유효 **(근소 우위 +0.05%p —
  06-25 시점엔 미달이었다가 가격보정 후 역전. 경계값 근방이라 과신 금지, Phase 3 신호분리 ablation 필요)**
- ✅ F > D (모멘텀 기여): F(14.63%) > D(11.99%) → 모멘텀 필터 유지
- ❌ C > B_p95 (재무안정성 기여): C_median(6.80%) < B_p95(12.13%) → 재무안정성 자체 Alpha는 미미
- ❌ E > D (팩터 스크리닝 기여): E(6.29%) < D(11.99%) → 팩터 스크리닝 제거 검토 필요

**Phase 3 방향**: Hard + Stability + Momentum + RIM 구조(F 기반) 유지. 팩터 스크리닝은 Phase 3에서 가중치 재조정 후 재검증. 재무안정성 필터(R6 포함)는 MDD 관리에 기여 확인(H vs F MDD 비교).

> ✅ (해소) `D_no_r6`·`F_no_r6` 이상 수치는 감자 대상 4종목의 adj_close 미보정이 만든 인위적 고수익이었음이
> 확인되어 2026-07-02 소급보정 후 재실행으로 해소됨. 상세: SPEC_06 Phase 2 결과, 워크플로우 문서 STEP 9.

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
> **v5.0** (Phase 2 완료, 2026-06-21): ① Ablation Test 13개 시나리오 완료 (no_r6 변형 6개 + H_no_stability 추가). ② `has_recent_trade(window=5)` Hard Filter에 추가 — 거래정지 5일 이상 종목 선제 제외 (제일바이오 감자 아티팩트 근본 차단). ③ `get_avg_turnover(max_lookback_days=90)` — 90일 초과 과거 거래량 사용 방지. ④ XBRL 파이프라인 추가: `xbrl_historical_ingest.py`, `xbrl_mapper.py`, `amendment_checker.py` — `financials_pit` 정정 공시 추적(`original_amount`, `amendment_from`). ⑤ `load_pit_series_ttm()` H1 TTM 계산 추가. ⑥ `dashboard/` 추가 (Streamlit, 포트 8502). ⑦ 디렉토리 구조 실제 서버 파일 기준으로 업데이트. ⑧ Phase 2 결과: F_momentum_rim 최적(CAGR 14.09%, Sharpe 0.518, MDD -28.06%). 팩터 스크리닝 성과 저해 확인 — Phase 3 재검증 예정.
> **v5.1** (설계서 정합성 복구, 2026-07-04): ① `SPEC_04_models.md` §7-1이 v5.0에서 MASTER §3-1에만 반영됐던 RIM 산식 교체(Ohlson 지속성형, ω=0.62, VB_CAP=5.0)를 그동안 반영하지 못하고 옛 g·payout 산식을 그대로 담고 있던 것을 확인·동기화. ② 2026-07-02 가격 소급보정(감자·분할 미반영 4종목) 후 Ablation 전체 재실행 결과 반영 — RIM 유효성 판정 역전(❌→✅, D≥C_p95 근소 우위 +0.05%p), R6 착시 효과 해소. ③ 결과 문서(`BACKTEST_RESULTS.md` 4개)·포트폴리오 홀딩스(xlsx 3개)를 `experiments/runs/`로 정리. ④ 미결 항목 2건을 SPEC_05/06·MASTER에 명시: 포트폴리오 최소 편입 종목 수 규칙, 벤치마크 우선순위 재배치(KOSPI vs Hard+Stability 동일가중) — 둘 다 검토만 됐을 뿐 확정 아님.
>
> **핵심 변경 철학**: "모든 모델 산식 먼저 → 구현" 순서 대신 "RIM + 모멘텀으로 Baseline 먼저 → 결과 보고 확장" 순서로 전환.
> 멀티모델(EV/Sales, Peer PER, NAV, FCFF 등) 산식 확정은 Phase 2 Ablation Test 결과 이후로 이동.
>
> **저장소**: `stock-backtest/` (신규 독립 저장소)

---

