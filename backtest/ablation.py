"""
Ablation Test — 7개 시나리오 (A_random ~ G_full).
Phase 2 필수 실행. 레이어별 Alpha 기여도 분해.

판정 기준:
  C > B  : 재무안정성 필터가 Alpha에 기여 (단순 종목 수 축소 이상의 효과)
  D > C  : RIM이 랜덤 대비 Alpha를 냄 → RIM 유효성 확인 (핵심 관문)
  E > D  : 팩터 스크리닝이 추가 Alpha를 냄
  F > D  : 모멘텀이 추가 Alpha를 냄
  G ≈ E 또는 G ≈ F : 팩터 스크리닝·모멘텀 중 하나가 중복 → 제거 검토
"""
from __future__ import annotations

import random

from backtest.configs.constants        import OMEGA
from backtest.filters.factor_screener  import FactorScreener
from backtest.filters.hard_filter      import HardFilter
from backtest.filters.momentum_filter    import MomentumFilter
from backtest.filters.momentum_criteria  import build_momentum_criterion_filter
from backtest.filters.stability_filter import StabilityFilter
from backtest.models.rim               import RIMModel
from backtest.pipeline                 import BacktestPipeline

ABLATION_CONFIGS: dict[str, dict] = {
    'A_random':            {'use_hard': False, 'use_stability': False, 'use_screener': False,
                            'use_momentum': False, 'use_rim_filter': False, 'random_n': 20},
    'B_hard_random':       {'use_hard': True,  'use_stability': False, 'use_screener': False,
                            'use_momentum': False, 'use_rim_filter': False, 'random_n': 20},
    'C_stability_random':  {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': False, 'use_rim_filter': False, 'random_n': 20},
    'C_no_r6':             {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': False, 'use_rim_filter': False, 'random_n': 20,
                            'stability_r6': False},
    'D_rim_only':          {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': False, 'use_rim_filter': True},
    'D_no_r6':             {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': False, 'use_rim_filter': True,  'stability_r6': False},
    'D_pbr_only':          {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': False, 'use_rim_filter': False, 'stability_r6': False,
                            'rank_mode': 'pbr'},
    'D_factor_only':       {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': False, 'use_rim_filter': False, 'stability_r6': False,
                            'rank_mode': 'factor_composite'},
    'E_screener_rim':      {'use_hard': True,  'use_stability': True,  'use_screener': True,
                            'use_momentum': False, 'use_rim_filter': True},
    'E_no_r6':             {'use_hard': True,  'use_stability': True,  'use_screener': True,
                            'use_momentum': False, 'use_rim_filter': True,  'stability_r6': False},
    'E_rev_only':          {'use_hard': True,  'use_stability': True,  'use_screener': True,
                            'use_momentum': False, 'use_rim_filter': True,
                            'screener_weights': {'rev_yoy': 1.0, 'op_yoy': 0.0, 'gpa': 0.0, 'inv_pbr': 0.0}},
    'E_op_only':           {'use_hard': True,  'use_stability': True,  'use_screener': True,
                            'use_momentum': False, 'use_rim_filter': True,
                            'screener_weights': {'rev_yoy': 0.0, 'op_yoy': 1.0, 'gpa': 0.0, 'inv_pbr': 0.0}},
    'E_gpa_only':          {'use_hard': True,  'use_stability': True,  'use_screener': True,
                            'use_momentum': False, 'use_rim_filter': True,
                            'screener_weights': {'rev_yoy': 0.0, 'op_yoy': 0.0, 'gpa': 1.0, 'inv_pbr': 0.0}},
    'E_pbr_only':          {'use_hard': True,  'use_stability': True,  'use_screener': True,
                            'use_momentum': False, 'use_rim_filter': True,
                            'screener_weights': {'rev_yoy': 0.0, 'op_yoy': 0.0, 'gpa': 0.0, 'inv_pbr': 1.0}},
    'F_momentum_rim':      {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': True,  'use_rim_filter': True},
    'F_no_r6':             {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': True,  'use_rim_filter': True,  'stability_r6': False},
    # D_no_r6 vs D_pbr_only 쌍의 모멘텀 결합판 — 동일 필터(R1~R5+모멘텀)에서 랭킹만
    # RIM(F_no_r6) vs 순수 1/PBR로 바꿔, 2026-07-15 재실행에서 뒤집힌 RIM 고유신호가
    # 모멘텀 결합 후에도 열위인지(= 1/PBR+모멘텀이 채택안을 대체 가능한지) 확인.
    'F_pbr_only':          {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': True,  'use_rim_filter': False, 'stability_r6': False,
                            'rank_mode': 'pbr'},
    # F_pbr_only + R6 — R6(adjROE<r)는 StabilityFilter 내부 계산이라 PBR 경로에도 적용 가능.
    # F_momentum_rim(R6 포함)과의 정확한 head-to-head: 이기면 "RIM의 가치는 랭킹 신호가
    # 아니라 R6 스크린"으로 분해됨.
    'F_pbr_r6':            {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': True,  'use_rim_filter': False,
                            'rank_mode': 'pbr'},
    # F_no_r3r4(현재 최고치)의 PBR 대응판 — Stability {R1,R2,R5,R6} + 모멘텀 + PBR 랭킹.
    'F_pbr_no_r3r4':       {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': True,  'use_rim_filter': False,
                            'stability_rules': {'R1', 'R2', 'R5', 'R6'},
                            'rank_mode': 'pbr'},
    # SPEC_12 §4-5 배관 양성 대조군 — F_pbr_no_r3r4와 나머지 스택 완전 동일, 모멘텀만
    # 신규 MomentumCriterionFilter(ma_double_adapter)로 교체. criterion은 기존
    # _momentum_filter()를 그대로 호출하므로(§0 규칙 3), 완결 20구간에서 F_pbr_no_r3r4와
    # 100% 일치해야 한다 — 불일치 시 신규 배관(prepare→evaluate→stats_key→tape) 결함.
    'F_pbr_ma_double_adapter': {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_rim_filter': False,
                            'stability_rules': {'R1', 'R2', 'R5', 'R6'},
                            'rank_mode': 'pbr',
                            'momentum_criterion': {'type': 'ma_double_adapter',
                                                   'tag': 'F_pbr_ma_double_adapter'}},
    # SPEC_12 §6-1 사전등록 primary 4개 — F_pbr_no_r3r4와 나머지 스택 동일, 모멘텀만
    # 신규 판정기준으로 교체. MC-0 manifest(experiments/momentum_criteria/MC0_manifest.json)에
    # 문턱값·robustness 밴드 동결. 결과 열람 전 committed.
    'F_pbr_absret126':     {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_rim_filter': False,
                            'stability_rules': {'R1', 'R2', 'R5', 'R6'},
                            'rank_mode': 'pbr',
                            'momentum_criterion': {'type': 'abs_return', 'tag': 'F_pbr_absret126',
                                                   'formation_days': 126, 'skip_days': 21,
                                                   'threshold': 0.0}},
    'F_pbr_signcount126':  {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_rim_filter': False,
                            'stability_rules': {'R1', 'R2', 'R5', 'R6'},
                            'rank_mode': 'pbr',
                            'momentum_criterion': {'type': 'sign_count', 'tag': 'F_pbr_signcount126',
                                                   'formation_days': 126, 'skip_days': 21}},
    'F_pbr_ma200':         {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_rim_filter': False,
                            'stability_rules': {'R1', 'R2', 'R5', 'R6'},
                            'rank_mode': 'pbr',
                            'momentum_criterion': {'type': 'ma200', 'tag': 'F_pbr_ma200',
                                                   'ma_window': 200}},
    'F_pbr_52w75':         {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_rim_filter': False,
                            'stability_rules': {'R1', 'R2', 'R5', 'R6'},
                            'rank_mode': 'pbr',
                            'momentum_criterion': {'type': '52w_high', 'tag': 'F_pbr_52w75',
                                                   'window': 252, 'threshold': 0.75}},
    # SPEC_12 §6-2 OAT 국소 밴드 — 52w75가 §5-3 1차 문턱 통과 후 robust 검증용 이웃 설정.
    'F_pbr_52w70':         {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_rim_filter': False,
                            'stability_rules': {'R1', 'R2', 'R5', 'R6'},
                            'rank_mode': 'pbr',
                            'momentum_criterion': {'type': '52w_high', 'tag': 'F_pbr_52w70',
                                                   'window': 252, 'threshold': 0.70}},
    'F_pbr_52w80':         {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_rim_filter': False,
                            'stability_rules': {'R1', 'R2', 'R5', 'R6'},
                            'rank_mode': 'pbr',
                            'momentum_criterion': {'type': '52w_high', 'tag': 'F_pbr_52w80',
                                                   'window': 252, 'threshold': 0.80}},
    # F_pbr_no_r3r4에서 R6까지 제외 — R6은 PBR 경로에서 음의 기여(F_pbr_r6 14.70 <
    # F_pbr_only 14.96)였으므로, 신기록 구성 {R1,R2,R5,R6}에서도 빼면 개선되는지 확인.
    'F_pbr_no_r3r4r6':     {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': True,  'use_rim_filter': False,
                            'stability_rules': {'R1', 'R2', 'R5'},
                            'rank_mode': 'pbr'},
    # PIT 재구축(2026-07-18) 후 stability 레이어 순감 반전(F_no_stability_clean > F)
    # 후속 — PBR 경로에서 stability 완전 제거 / R6 단독의 두 미검증 셀.
    'F_pbr_nostab':        {'use_hard': True,  'use_stability': False, 'use_screener': False,
                            'use_momentum': True,  'use_rim_filter': False,
                            'rank_mode': 'pbr'},
    'F_pbr_r6only':        {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': True,  'use_rim_filter': False,
                            'stability_rules': {'R6'},
                            'rank_mode': 'pbr'},
    # ── SPEC_10 §3 정합 대조군 (2026-07-19 사전 등록) ──────────────────────
    # 채택 후보 F_pbr_no_r3r4와 **동일 필터 스택**(HARD + Stability{R1,R2,R5,R6} +
    # 모멘텀) 통과 풀에서 무작위 20종목 — "필터 유니버스 축소 효과"와 "1/PBR 랭킹
    # 고유 기여"를 분리하는 귀무 분포. 1,000회는 scripts/robustness/run_random_pool.py
    # fast-path로 실행 (풀은 리밸런싱일당 1회 구축, 등가성 게이트 필수).
    'C_pbr_path_random':   {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': True,  'use_rim_filter': False, 'random_n': 20,
                            'stability_rules': {'R1', 'R2', 'R5', 'R6'}},
    # 동일 필터 통과 **전 종목** 동일가중 (랭킹 없음) — 1차 KPI 벤치마크 확정안
    # (기존 Option 2). "같은 필터 유니버스를 다 사는 것 대비 1/PBR 상위 20 선별이
    # 무엇을 더하는가"의 기준선 (SPEC_10 §5-1 G2).
    'U_pbr_path_ew':       {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': True,  'use_rim_filter': False,
                            'stability_rules': {'R1', 'R2', 'R5', 'R6'},
                            'rank_mode': 'ew_all'},
    # ── SPEC_11 분해 (2026-07-19 설계) ─────────────────────────────────────
    # §4: 채택 후보에서 모멘텀만 제거한 정확한 대조군 — 모멘텀 독립 기여 격리.
    # M0~M3 사전등록 실험의 기준선(M0 vs 모멘텀 부재) 겸용.
    'D_pbr_no_r3r4':       {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': False, 'use_rim_filter': False,
                            'stability_rules': {'R1', 'R2', 'R5', 'R6'},
                            'rank_mode': 'pbr'},
    # §3: PBR 분모를 지배기업소유주지분(RIM equity SSOT 우선순위)으로 — 정의 간
    # 안정성 확인 목적 (판정 목적 아님. 크게 갈리면 그 자체가 경고 → 사용자 상신).
    'F_pbr_no_r3r4_parent': {'use_hard': True, 'use_stability': True,  'use_screener': False,
                            'use_momentum': True,  'use_rim_filter': False,
                            'stability_rules': {'R1', 'R2', 'R5', 'R6'},
                            'rank_mode': 'pbr_parent'},
    # §2-1 후속 (2026-07-20): 멤버십 분석에서 R2 제거가 2025-08-20 1개 구간의
    # top20만 2종목 바꿈(021050↔092230) → 사전 등록 분기 "해당 조합만 CAGR 재실행".
    # 채택 후보에서 R2까지 제거한 {R1,R5,R6} — F_no_r2r3r4(RIM 랭킹)의 PBR 대응판.
    'F_pbr_no_r2r3r4':     {'use_hard': True,  'use_stability': True,  'use_screener': False,
                            'use_momentum': True,  'use_rim_filter': False,
                            'stability_rules': {'R1', 'R5', 'R6'},
                            'rank_mode': 'pbr'},
    'G_full':              {'use_hard': True,  'use_stability': True,  'use_screener': True,
                            'use_momentum': True,  'use_rim_filter': True},
    'G_no_r6':             {'use_hard': True,  'use_stability': True,  'use_screener': True,
                            'use_momentum': True,  'use_rim_filter': True,  'stability_r6': False},
    'H_no_stability':      {'use_hard': True,  'use_stability': False, 'use_screener': True,
                            'use_momentum': True,  'use_rim_filter': True},

    # ── StabilityFilter 검증 (SPEC_05 부록 A) ──────────────────────────────
    # H_no_stability는 use_screener=True까지 같이 꺼져 교란됨(F 대비 stability·screener 두 축이
    # 동시에 다름). F_no_stability_clean/D_no_stability는 채택 파이프라인(screener 없음)에서
    # stability만 깨끗이 제거한 대조군.
    'F_no_stability_clean': {'use_hard': True,  'use_stability': False, 'use_screener': False,
                            'use_momentum': True,  'use_rim_filter': True},
    'D_no_stability':       {'use_hard': True,  'use_stability': False, 'use_screener': False,
                            'use_momentum': False, 'use_rim_filter': True},

    # R1~R5 leave-one-out (R6은 켠 채로 유지 = D_rim_only와 동일 기준선, 하나씩만 제외)
    'D_no_r1': {'use_hard': True, 'use_stability': True, 'use_screener': False,
                'use_momentum': False, 'use_rim_filter': True,
                'stability_rules': {'R2', 'R3', 'R4', 'R5', 'R6'}},
    'D_no_r2': {'use_hard': True, 'use_stability': True, 'use_screener': False,
                'use_momentum': False, 'use_rim_filter': True,
                'stability_rules': {'R1', 'R3', 'R4', 'R5', 'R6'}},
    'D_no_r3': {'use_hard': True, 'use_stability': True, 'use_screener': False,
                'use_momentum': False, 'use_rim_filter': True,
                'stability_rules': {'R1', 'R2', 'R4', 'R5', 'R6'}},
    'D_no_r4': {'use_hard': True, 'use_stability': True, 'use_screener': False,
                'use_momentum': False, 'use_rim_filter': True,
                'stability_rules': {'R1', 'R2', 'R3', 'R5', 'R6'}},
    'D_no_r5': {'use_hard': True, 'use_stability': True, 'use_screener': False,
                'use_momentum': False, 'use_rim_filter': True,
                'stability_rules': {'R1', 'R2', 'R3', 'R4', 'R6'}},

    # R2/R3/R4 단일·조합 제외 — 채택 파이프라인(F) 기준. R1·R2가 둘 다 부채/차입금 관련이라
    # R2가 D_no_r2에서 "완전히 무력"으로 나온 게 R1과의 중복(R1이 먼저 걸러냄) 때문인지,
    # R3(매출역성장, D_no_r3에서 역효과로 나옴)·R4(영업CF 2년연속음수, 거의 무력)와의
    # 조합에서도 같은 패턴이 유지되는지 확인. R1·R5·R6는 항상 유지.
    'F_no_r2':     {'use_hard': True, 'use_stability': True, 'use_screener': False,
                    'use_momentum': True, 'use_rim_filter': True,
                    'stability_rules': {'R1', 'R3', 'R4', 'R5', 'R6'}},
    'F_no_r3':     {'use_hard': True, 'use_stability': True, 'use_screener': False,
                    'use_momentum': True, 'use_rim_filter': True,
                    'stability_rules': {'R1', 'R2', 'R4', 'R5', 'R6'}},
    'F_no_r4':     {'use_hard': True, 'use_stability': True, 'use_screener': False,
                    'use_momentum': True, 'use_rim_filter': True,
                    'stability_rules': {'R1', 'R2', 'R3', 'R5', 'R6'}},
    'F_no_r2r3':   {'use_hard': True, 'use_stability': True, 'use_screener': False,
                    'use_momentum': True, 'use_rim_filter': True,
                    'stability_rules': {'R1', 'R4', 'R5', 'R6'}},
    'F_no_r2r4':   {'use_hard': True, 'use_stability': True, 'use_screener': False,
                    'use_momentum': True, 'use_rim_filter': True,
                    'stability_rules': {'R1', 'R3', 'R5', 'R6'}},
    'F_no_r3r4':   {'use_hard': True, 'use_stability': True, 'use_screener': False,
                    'use_momentum': True, 'use_rim_filter': True,
                    'stability_rules': {'R1', 'R2', 'R5', 'R6'}},
    'F_no_r2r3r4': {'use_hard': True, 'use_stability': True, 'use_screener': False,
                    'use_momentum': True, 'use_rim_filter': True,
                    'stability_rules': {'R1', 'R5', 'R6'}},
}

RANDOM_TAGS    = frozenset({'A_random', 'B_hard_random', 'C_stability_random', 'C_no_r6',
                            'C_pbr_path_random'})
RANDOM_REPEATS = 500  # C_pbr_path_random은 1,000회 — fast-path 러너에서 별도 지정 (SPEC_10 §3-1)


class _RandomSelectPipeline(BacktestPipeline):
    """
    필터 통과 종목 중 무작위 N개 선택. 랜덤 시나리오(A/B/C) 전용.

    seed × rebalance_date 복합 시드 → 구간마다 다른 무작위 선택.
    500회 반복 실행 시 각 run_seed로 독립적인 분포 생성.
    """

    def __init__(self, filters: list, n_stocks: int = 20, seed: int | None = None):
        super().__init__(filters=filters, valuation_model=RIMModel(), n_stocks=n_stocks)
        self._seed = seed

    def score_and_rank(self, universe, rebalance_date, pit_series, conn) -> list[dict]:
        rng = random.Random(f"{self._seed}:{rebalance_date.isoformat()}")
        shuffled = list(universe)
        rng.shuffle(shuffled)
        return [
            {'ticker': t, 'upside_pct': 0.0, 'model': 'RANDOM',
             'fair_value': 0.0, 'price': 0.0}
            for t in shuffled
        ]


class _PBRRankPipeline(BacktestPipeline):
    """
    필터 통과 종목을 1/PBR(inv_pbr) 내림차순으로 랭킹해 상위 N개 선택.

    STEP 3 신호분리용 대조군 — D_no_r6(RIM 업사이드 랭킹)와 필터 구성을 동일하게 두고
    랭킹 기준만 "RIM V/B" → "순수 1/PBR"로 바꿔, RIM 알파가 사실상 저PBR 재포장인지
    확인한다. equity 정의(기본 'total')는 factor_screener._compute_factors의 inv_pbr과
    동일하게 자본총계 기준(비교 가능성 우선, RIM의 지배주주지분 우선순위와는 다름).

    equity_mode='parent' (SPEC_11 §3, F_pbr_no_r3r4_parent): 분모를 RIM SSOT
    우선순위(RIMModel.parent_equity — 지배기업소유주지분 > _1 > 자본총계)로 교체.
    필터별 적정 기준은 유지한다 — 전 필터 일괄 통일 아님 (SPEC_11 §3 확정).
    """

    def __init__(self, filters: list, n_stocks: int = 20, equity_mode: str = 'total'):
        super().__init__(filters=filters, valuation_model=RIMModel(), n_stocks=n_stocks)
        if equity_mode not in ('total', 'parent'):
            raise ValueError(f'equity_mode는 total|parent — {equity_mode!r}')
        self.equity_mode = equity_mode

    def score_and_rank(self, universe, rebalance_date, pit_series, conn) -> list[dict]:
        from backtest.data_access import get_market_cap, get_close_price

        scored = []
        for ticker in universe:
            pit0   = pit_series.get(ticker, [{}])[0]
            equity = (RIMModel.parent_equity(pit0) if self.equity_mode == 'parent'
                      else pit0.get('자본총계'))
            mktcap = get_market_cap(conn, ticker, rebalance_date)
            price  = get_close_price(conn, ticker, rebalance_date)

            if not equity or equity <= 0 or not mktcap or mktcap <= 0 or price is None:
                continue

            pbr = mktcap / equity
            if pbr <= 0:
                continue

            scored.append({
                'ticker':     ticker,
                'upside_pct': 1.0 / pbr,   # inv_pbr 스코어(랭킹용, 업사이드 % 아님)
                'model':      'PBR_ONLY',
                'fair_value': None,
                'price':      price,
            })

        return sorted(scored, key=lambda x: x['upside_pct'], reverse=True)


class _FactorCompositeRankPipeline(BacktestPipeline):
    """
    필터 통과 종목을 FactorScreener 4팩터 합산 점수(기본 가중치)로 직접 랭킹해 상위 N개 선택.
    RIM 없이 팩터 컴포지트 자체가 독립 알파 신호로 작동하는지 확인하는 대조군
    (STEP 3B 후속 — 단일팩터 프리필터+RIM 진단과 달리, 여기서는 RIM을 완전히 배제하고
    합성 점수만으로 선정해 "위치(프리필터) 문제 vs 구성 자체 문제"를 분리한다).
    `factor_screener._factor_screening()`을 top_pct=1.0으로 호출해 전체 유니버스를
    점수 내림차순으로 받은 뒤 그대로 반환한다 (build_portfolio가 상위 n_stocks만 사용).
    """

    def __init__(self, filters: list, n_stocks: int = 20, weights: dict | None = None):
        super().__init__(filters=filters, valuation_model=RIMModel(), n_stocks=n_stocks)
        self.weights = weights or {'rev_yoy': 1 / 6, 'op_yoy': 1 / 6, 'gpa': 1 / 3, 'inv_pbr': 1 / 3}

    def score_and_rank(self, universe, rebalance_date, pit_series, conn) -> list[dict]:
        from backtest.data_access import get_close_price
        from backtest.filters.factor_screener import _factor_screening

        ranked_all = _factor_screening(
            universe, rebalance_date, pit_series, conn, self.weights, top_pct=1.0
        )

        result = []
        for ticker in ranked_all:
            price = get_close_price(conn, ticker, rebalance_date)
            if price is None or price <= 0:
                continue
            result.append({
                'ticker':     ticker,
                'upside_pct': 0.0,   # 점수는 _factor_screening 내부 정렬에만 사용, 순서 보존
                'model':      'FACTOR_COMPOSITE',
                'fair_value': None,
                'price':      price,
            })
        return result


class _AllEqualWeightPipeline(BacktestPipeline):
    """
    필터 통과 **전 종목**을 동일가중 편입 (랭킹 없음) — U_pbr_path_ew 전용
    (SPEC_10 §3-2, 적격 유니버스 동일가중 벤치마크).

    n_stocks=None(상한 없음)으로 build_portfolio가 전 종목을 1/n 편입한다.
    반환 순서는 ticker 오름차순 고정 — 결과는 집합에만 의존하지만(engine
    _calc_period_return 순서 독립 계약) 산출물 재현성·diff 가독성을 위해 명시.
    """

    def __init__(self, filters: list):
        super().__init__(filters=filters, valuation_model=RIMModel(), n_stocks=None)

    def score_and_rank(self, universe, rebalance_date, pit_series, conn) -> list[dict]:
        return [
            {'ticker': t, 'upside_pct': 0.0, 'model': 'EW_ALL',
             'fair_value': None, 'price': 0.0}
            for t in sorted(universe)
        ]


def build_ablation_pipeline(
    tag:           str,
    config:        dict,
    seed:          int | None = None,
    beta_adj:      float = 0.0,
    omega:         float = OMEGA,
    rim_threshold: float = 0.05,
    top_pct:       float = 0.20,
    n_stocks:      int   = 20,
) -> BacktestPipeline:
    """config 플래그에 따라 파이프라인 조립. 랜덤 시나리오는 _RandomSelectPipeline 반환."""
    filters: list = []

    if config.get('use_hard', False):
        filters.append(HardFilter(min_turnover=100_000_000, min_listed_months=6))
    if config.get('use_stability', False):
        rules = config.get('stability_rules')
        if rules is not None:
            filters.append(StabilityFilter(r2_exception=True, active_rules=rules))
        else:
            use_r6 = config.get('stability_r6', True)
            filters.append(StabilityFilter(r2_exception=True, use_r6=use_r6))
    if config.get('use_screener', False):
        filters.append(FactorScreener(
            weights=config.get(
                'screener_weights',
                {'rev_yoy': 1/6, 'op_yoy': 1/6, 'gpa': 1/3, 'inv_pbr': 1/3},
            ),
            top_pct=top_pct,
        ))
    # SPEC_12 §4-2: momentum_criterion이 있으면 신규 배관, 없으면 기존 레거시 경로.
    # 레거시 MomentumFilter/_momentum_filter()는 이 분기 신설로도 전혀 수정되지 않는다.
    momentum_config = config.get('momentum_criterion')
    if momentum_config is not None:
        filters.append(build_momentum_criterion_filter(momentum_config))
    elif config.get('use_momentum', False):
        filters.append(MomentumFilter(
            ma_short=20, ma_long=60, confirm_days=5, slope_lookback=20,
        ))

    if config.get('rank_mode') == 'ew_all':
        return _AllEqualWeightPipeline(filters=filters)

    if config.get('rank_mode') == 'pbr':
        return _PBRRankPipeline(filters=filters, n_stocks=n_stocks)

    if config.get('rank_mode') == 'pbr_parent':
        return _PBRRankPipeline(filters=filters, n_stocks=n_stocks, equity_mode='parent')

    if config.get('rank_mode') == 'factor_composite':
        return _FactorCompositeRankPipeline(filters=filters, n_stocks=n_stocks)

    if not config.get('use_rim_filter', True):
        return _RandomSelectPipeline(
            filters=filters,
            n_stocks=config.get('random_n', n_stocks),
            seed=seed,
        )

    return BacktestPipeline(
        filters=filters,
        valuation_model=RIMModel(beta_adj=beta_adj, omega=omega),
        rim_threshold=rim_threshold,
        n_stocks=n_stocks,
    )
