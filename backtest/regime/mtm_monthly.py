"""
SPEC_07 §7-2 — 기존 반기 홀딩스를 월말마다 재평가(MTM). 전략 불변, 읽기 전용.

★ 반드시 '초기 수량 고정 NAV' 방식이다. 매월 목표비중으로 되돌리면(월별 리밸런싱)
  '전략 불변' 불변식이 깨진다. 리밸런싱일에 산 수량을 구간 끝까지 그대로 들고 간다.

★ 상폐 판정은 is_delisted_at()으로 명시한다(engine.py d2d619e와 동일 함정 회피).
  get_close_price()는 date<=as_of 최신값을 반환해 상폐 후에도 절대 None이 되지 않으므로
  "가격 없으면 상폐"식 분기는 도달 불가능한 코드가 된다. 최초 상폐 감지월에 1회만
  haircut을 적용하고 그 가격에 동결한다(반복 청산 금지, 기존 백테스트 청산 가정과 동일).

실행: venv/bin/python -m backtest.regime.mtm_monthly
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from backtest.configs.rebalance_dates import REBALANCE_DATES
from backtest.engine import DELISTING_HAIRCUT, _last_known_price
from backtest.data_access import is_delisted_at
from backtest.regime.config_regime import GATE_CUTOFF_DATE, REQUIRED_HOLDINGS_TAGS
from backtest.regime.data_access_regime import (
    kospi_return,
    latest_close_batch,
    list_universe_tickers,
    market_cap_batch,
    month_end_dates,
)
from ingest.connection import get_connection

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

HOLDINGS_DIR = Path('experiments/ablation')
VALID_START_DATE = date(2016, 4, 5)   # 첫 2개(2015-04/08)는 TTM 미충족으로 제외 (SPEC_07 §2)

DELISTING_SCENARIO = 'base_70pct'     # 기존 백테스트 기준 시나리오와 동일 청산 가정
LARGECAP_RULE = 'top_decile'
SIZE_DECILE_N = 10


# ── 구간 정의 (export_portfolios.py와 동일 관례) ─────────────────────────────

def _periods() -> list[tuple[date, date, bool]]:
    """(rebalance_date, next_date, is_closed). 마지막 구간(#23)은 is_closed=False."""
    dates = [d for d in REBALANCE_DATES if d >= VALID_START_DATE]
    out = []
    for rebal_date in dates:
        idx = REBALANCE_DATES.index(rebal_date)
        is_closed = idx + 1 < len(REBALANCE_DATES)
        next_date = REBALANCE_DATES[idx + 1] if is_closed else date.today()
        # GATE_CUTOFF_DATE는 §9 게이트 모집단 제외 기준(config_regime.py)과 이 위치 기반
        # is_closed 판정이 어긋나지 않는지 지키는 안전장치다. REBALANCE_DATES가 향후 재생성돼
        # 아직 도래하지 않은 미래 구간이 추가되면 이 둘이 어긋날 수 있으므로 여기서 명시 검증한다.
        expected_closed = rebal_date < GATE_CUTOFF_DATE
        if is_closed != expected_closed:
            raise AssertionError(
                f'{rebal_date}: 위치 기반 is_closed({is_closed})와 GATE_CUTOFF_DATE 기준'
                f'({expected_closed})이 불일치 — REBALANCE_DATES 갱신 시 GATE_CUTOFF_DATE도 '
                f'함께 갱신했는지 확인할 것'
            )
        out.append((rebal_date, next_date, is_closed))
    return out


def _obs_dates(conn, rebal_date: date, next_date: date) -> list[date]:
    """월말 관측일 + 구간 종료 stub(반드시 포함, 월말이 아니어도)."""
    dates = month_end_dates(conn, rebal_date, next_date)
    if not dates or dates[-1] != next_date:
        dates.append(next_date)
    return dates


# ── 홀딩스 로드 ──────────────────────────────────────────────────────────────

def _load_period_holdings(tag: str, rebal_date: date) -> dict:
    path = HOLDINGS_DIR / f'{tag}_holdings.json'
    if not path.exists():
        raise FileNotFoundError(
            f'{path} 없음 — STEP A-0(export_portfolios.py 상폐버그 수정) 완료 후 '
            f'`python -m scripts.export_portfolios --tags {" ".join(REQUIRED_HOLDINGS_TAGS)}` 먼저 실행할 것'
        )
    periods = json.loads(path.read_text(encoding='utf-8'))
    for p in periods:
        if p['rebalance_date'] == rebal_date.isoformat():
            return p
    raise ValueError(f'{tag}: {rebal_date.isoformat()} 구간이 홀딩스 JSON에 없음')


# ── 고정수량 NAV 경로 ────────────────────────────────────────────────────────

def _nav_path(conn, weights: dict[str, float], rebal_date: date, obs_dates: list[date]) -> list[float]:
    """
    weights(합계≈1.0)로 rebal_date에 매수 후 obs_dates마다 재평가한 NAV 리스트.
    상폐 종목은 최초 감지 시점 가격(직전 종가 × DELISTING_HAIRCUT)에 동결 —
    이후 관측일에도 반복 청산하지 않는다(기존 백테스트와 동일 1회성 청산 가정).

    ★ 진입가 없는 종목은 제외하되, 남은 종목들로 비중을 재정규화한다(engine.py
    _calc_period_return()의 생존종목 재정규화와 동일 관례) — 그냥 빼기만 하면
    그 비중만큼 자본이 증발해 NAV가 1.0 미만에서 출발하게 된다.
    """
    tickers = list(weights.keys())
    p0 = latest_close_batch(conn, tickers, rebal_date)
    valid_weights = {t: weights[t] for t in tickers if p0.get(t) and p0[t] > 0}
    dropped = [t for t in tickers if t not in valid_weights]
    if dropped:
        dropped_frac = 1.0 - sum(valid_weights.values()) / sum(weights.values())
        log.warning('rebal_date=%s: 진입가 없어 제외된 종목 %d개(비중 %.2f%%, 잔여 종목으로 재정규화) %s',
                     rebal_date, len(dropped), dropped_frac * 100, dropped[:5])

    total_valid_weight = sum(valid_weights.values())
    if total_valid_weight <= 0:
        return [0.0 for _ in obs_dates]
    shares = {t: (w / total_valid_weight) / p0[t] for t, w in valid_weights.items()}

    frozen: dict[str, float] = {}
    navs: list[float] = []
    for d in obs_dates:
        active = [t for t in shares if t not in frozen]
        px_now = latest_close_batch(conn, active, d) if active else {}
        nav = 0.0
        for t, sh in shares.items():
            if t in frozen:
                nav += sh * frozen[t]
                continue
            if is_delisted_at(conn, t, d):
                last = _last_known_price(conn, t, d)
                px = last * DELISTING_HAIRCUT
                frozen[t] = px
                nav += sh * px
            else:
                nav += sh * px_now.get(t, 0.0)
        navs.append(nav)
    return navs


def _build_largecap_sleeve(conn, rebal_date: date) -> tuple[dict[str, float], dict[str, float]]:
    """§5-1 대형주 sleeve. 공통 유니버스(§4-1, universe_gate_pit 미적용) 상위 decile."""
    universe = list_universe_tickers(conn, rebal_date)
    caps = market_cap_batch(conn, universe, rebal_date)
    ranked = sorted(caps.items(), key=lambda kv: kv[1], reverse=True)
    n_top = max(1, len(ranked) // SIZE_DECILE_N)
    sleeve = ranked[:n_top]
    total_cap = sum(c for _, c in sleeve)
    cw_weights = {t: c / total_cap for t, c in sleeve}
    ew_weights = {t: 1.0 / len(sleeve) for t, _ in sleeve}
    return cw_weights, ew_weights


# ── A-2 복제 게이트 ──────────────────────────────────────────────────────────

def _check_replication_gate(tag: str, rebal_date: date, period: dict, port_nav: list[float],
                             tol: float = 1e-3) -> None:
    """
    stub 포함 월수익 누적곱 == 기존 반기 수익률(홀딩스 JSON의 개별 종목 'ret' 동일가중 평균).
    export_portfolios.py의 'ret'는 반올림 전 원 정밀도 가격으로 계산되므로 tol은
    좁게 잡되, 저가주 등의 부동소수 누적오차를 감안해 0.1%로 둔다.
    """
    rets = [h['ret'] for h in period['holdings'] if h.get('ret') is not None]
    if not rets:
        raise RuntimeError(
            f'[STEP A-2 복제 게이트 검증 불가] {tag} {rebal_date.isoformat()}: '
            f'홀딩스 {len(period["holdings"])}건 전부 ret=None — 가격 데이터 결측/시스템 장애 가능성. '
            f'검증 없이 통과시키지 않고 중단.'
        )
    reference = sum(rets) / len(rets)
    mtm_total = port_nav[-1] - 1.0 if port_nav else 0.0
    diff = abs(mtm_total - reference)
    if diff > tol:
        raise RuntimeError(
            f'[STEP A-2 복제 게이트 실패] {tag} {rebal_date.isoformat()}: '
            f'MTM 누적수익={mtm_total:.6f} vs 기존 반기수익={reference:.6f} (오차 {diff:.6f} > {tol}). '
            f'MTM 로직 결함 가능성 — 진행 중단.'
        )
    log.info('[A-2 게이트 통과] %s %s: MTM=%.4f%% ref=%.4f%% (오차 %.5f)',
              tag, rebal_date, mtm_total * 100, reference * 100, diff)


# ── DB 적재 ──────────────────────────────────────────────────────────────────

def _upsert(conn, run_id: str, tag: str, rebal_date: date, next_date: date, is_closed: bool,
            return_start: date, return_end: date, port_ret: float, cw_ret: float, ew_ret: float,
            kospi_ret: float, n_holdings: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO strategy_returns_monthly (
                source_run_id, holdings_source, delisting_scenario, largecap_rule, scenario,
                period_start, period_end, is_closed_period, return_start, return_end, date,
                port_return, largecap_cw_return, largecap_ew_return, kospi_return,
                rel_vs_large, rel_vs_large_ew, rel_vs_kospi, n_holdings
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (source_run_id, scenario, date) DO UPDATE SET
                period_start = EXCLUDED.period_start,
                period_end = EXCLUDED.period_end,
                is_closed_period = EXCLUDED.is_closed_period,
                return_start = EXCLUDED.return_start,
                port_return = EXCLUDED.port_return,
                largecap_cw_return = EXCLUDED.largecap_cw_return,
                largecap_ew_return = EXCLUDED.largecap_ew_return,
                kospi_return = EXCLUDED.kospi_return,
                rel_vs_large = EXCLUDED.rel_vs_large,
                rel_vs_large_ew = EXCLUDED.rel_vs_large_ew,
                rel_vs_kospi = EXCLUDED.rel_vs_kospi,
                n_holdings = EXCLUDED.n_holdings
            """,
            (run_id, str(HOLDINGS_DIR), DELISTING_SCENARIO, LARGECAP_RULE, tag,
             rebal_date, next_date, is_closed, return_start, return_end, return_end,
             port_ret, cw_ret, ew_ret, kospi_ret,
             port_ret - cw_ret, port_ret - ew_ret, port_ret - kospi_ret, n_holdings),
        )


# ── 메인 ────────────────────────────────────────────────────────────────────

def run(run_id: str = 'mtm_v1') -> None:
    for tag in REQUIRED_HOLDINGS_TAGS:
        path = HOLDINGS_DIR / f'{tag}_holdings.json'
        if not path.exists():
            raise FileNotFoundError(
                f'{path} 없음 — STEP A-0 완료 후 `python -m scripts.export_portfolios '
                f'--tags {" ".join(REQUIRED_HOLDINGS_TAGS)}` 먼저 실행할 것'
            )

    conn = get_connection()
    try:
        for rebal_date, next_date, is_closed in _periods():
            obs_dates = _obs_dates(conn, rebal_date, next_date)
            cw_weights, ew_weights = _build_largecap_sleeve(conn, rebal_date)
            cw_nav = _nav_path(conn, cw_weights, rebal_date, obs_dates)
            ew_nav = _nav_path(conn, ew_weights, rebal_date, obs_dates)

            for tag in REQUIRED_HOLDINGS_TAGS:
                period = _load_period_holdings(tag, rebal_date)
                tickers = [h['ticker'] for h in period['holdings']]
                if not tickers:
                    log.warning('%s %s: 편입 종목 0개, 건너뜀', tag, rebal_date)
                    continue
                weights = {t: 1.0 / len(tickers) for t in tickers}
                port_nav = _nav_path(conn, weights, rebal_date, obs_dates)

                if is_closed:
                    _check_replication_gate(tag, rebal_date, period, port_nav)
                else:
                    log.info('%s %s: 진행 중인 구간(#23류) — 복제 게이트 판정 제외, 참고 표시만', tag, rebal_date)

                nav_prev = cw_prev = ew_prev = 1.0
                prev_d = rebal_date
                for i, d in enumerate(obs_dates):
                    port_ret = port_nav[i] / nav_prev - 1
                    cw_ret = cw_nav[i] / cw_prev - 1
                    ew_ret = ew_nav[i] / ew_prev - 1
                    kospi_ret = kospi_return(prev_d, d)
                    _upsert(conn, run_id, tag, rebal_date, next_date, is_closed,
                            prev_d, d, port_ret, cw_ret, ew_ret, kospi_ret, len(tickers))
                    nav_prev, cw_prev, ew_prev, prev_d = port_nav[i], cw_nav[i], ew_nav[i], d

            conn.commit()
            log.info('구간 완료: %s ~ %s (닫힘=%s)', rebal_date, next_date, is_closed)
    finally:
        conn.close()

    log.info('전체 MTM 완료 (run_id=%s)', run_id)


# SPEC_08(Phase B) 재사용을 위한 공개 별칭 — 동작 변경 없음, 이름만 공개(SPEC_08 §4-1).
# Phase A 자체 코드는 계속 프라이빗 이름(_nav_path 등)을 쓴다.
nav_path = _nav_path
build_largecap_sleeve = _build_largecap_sleeve
load_period_holdings = _load_period_holdings
periods = _periods


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', default='mtm_v1')
    args = parser.parse_args()
    run(run_id=args.run_id)
