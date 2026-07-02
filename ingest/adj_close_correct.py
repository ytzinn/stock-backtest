"""
adj_close 소급 보정 — 감자·분할·무상증자 미반영 종목 수정.

pykrx get_market_ohlcv_by_date(adjusted=True)가 일부 감자·분할·무상증자를
반영하지 못해 adj_close에 불연속(단절)이 남는 경우가 있다. krx_daily_snapshot의
상장주식수(shares) 급변 이벤트를 corporate action 후보로 탐지하고,
이벤트 전후 adj_close 연속성을 검사해 미반영 종목만 소급 보정한다.

원리:
  shares_ratio = shares_after / shares_before   (이벤트 전후 주식수 비율)
  이미 보정됨:   continuity_ratio = adj_close_after / adj_close_before ≈ 1
  미반영:        continuity_ratio ≈ raw_ratio ≈ 1 / shares_ratio (연속성 깨짐)

  미반영 판정 시: UPDATE price_history SET adj_close = adj_close / shares_ratio
                  WHERE ticker = X AND date < event_date
  (최신 이벤트부터 역순 적용 — 과거로 갈수록 보정이 누적되어야 하므로)

실행:
    python -m ingest.adj_close_correct                       # 탐지만 (dry-run)
    python -m ingest.adj_close_correct --threshold 10         # 임계값 조정 (기본 15%)
    python -m ingest.adj_close_correct --apply                # 실제 UPDATE 적용 (백업 후)
    python -m ingest.adj_close_correct --apply --ticker 002070
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime

import pandas as pd

from ingest.connection import db_conn
from ingest.krx_daily_validate import detect_share_changes
from ingest.logging_config import configure_logging

configure_logging('adj_close_correct.log')
log = logging.getLogger(__name__)

CONTINUITY_OK_TOL    = 0.15   # continuity_ratio가 1 근방(±15%)이면 이미 정상으로 판정
MIN_SHARES_DEVIATION = 0.10   # 주식수 비율이 최소 이 정도는 벗어나야 후보로 간주 (작은 무상증자 포함)
CORRECTED_BAND       = 0.05   # 보정 적용 후 연속성이 1 ± 이 값 이내여야 확정 (상한가 우연일치 등 오탐 배제)

# 2026-07-02 세션: threshold=10%/±5% 통계 후보 6건을 DART list.json 공시로
# 개별 교차검증한 결과. 통계만으로는 오탐(122800 CB전환, 314130 CB전환+유상증자)이
# 섞여 있어 DART 공시 확인된 4건만 화이트리스트로 확정.
#   001290 상상인증권 2018-02-20  주요사항보고서(감자결정) (rcept 20180131)
#   002380 KCC        2020-01-21  증권발행실적보고서(합병등) — 케이씨씨글라스 인적분할 (rcept 20200102)
#   005950 이수화학    2023-05-31  증권신고서(분할) (rcept 20230320)
#   043590 웰킵스하이텍 2024-01-19  주요사항보고서(감자결정)+감자완료 (rcept 20231219~20240116)
CONFIRMED_TICKERS = {'001290', '002380', '005950', '043590'}


def _surrounding_prices(ticker: str, event_date) -> tuple | None:
    """이벤트 직전 마지막 거래일 / 직후 첫 거래일의 close·adj_close 조회."""
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT date, close, adj_close FROM price_history
            WHERE ticker = %s AND date < %s
            ORDER BY date DESC LIMIT 1
            """,
            (ticker, event_date),
        )
        prev = cur.fetchone()
        cur.execute(
            """
            SELECT date, close, adj_close FROM price_history
            WHERE ticker = %s AND date >= %s
            ORDER BY date ASC LIMIT 1
            """,
            (ticker, event_date),
        )
        curr = cur.fetchone()
    if not prev or not curr:
        return None
    return prev, curr


def detect_corrections(threshold_pct: float = 15.0, ticker: str | None = None) -> pd.DataFrame:
    """주식수 급변 이벤트 중 adj_close 미반영 종목만 필터링."""
    candidates = detect_share_changes(threshold_pct=threshold_pct)
    if ticker:
        candidates = candidates[candidates['ticker'] == ticker]

    rows = []
    for _, c in candidates.iterrows():
        sp = _surrounding_prices(c['ticker'], c['date'])
        if sp is None:
            continue
        (prev_date, prev_close, prev_adj), (curr_date, curr_close, curr_adj) = sp
        if not prev_adj or not curr_adj or float(prev_adj) == 0:
            continue

        shares_ratio     = float(c['shares_today']) / float(c['shares_prev'])
        continuity_ratio = float(curr_adj) / float(prev_adj)
        continuity_after = continuity_ratio * shares_ratio

        # 실제 기업행위(감자·분할·무상증자)로 볼만큼 주식수 변동이 크고,
        # 현재 연속성은 깨져 있는데(>tol), shares_ratio로 나누면 1 근방으로
        # 회복되는 경우만 "미반영"으로 확정 — 우연한 주가 변동과 구분.
        needs_correction = (
            abs(shares_ratio - 1.0) >= MIN_SHARES_DEVIATION
            and abs(continuity_ratio - 1.0) > CONTINUITY_OK_TOL
            and abs(continuity_after - 1.0) <= CORRECTED_BAND
        )

        rows.append({
            'ticker':           c['ticker'],
            'event_date':       c['date'],
            'shares_prev':      c['shares_prev'],
            'shares_today':     c['shares_today'],
            'shares_ratio':     round(shares_ratio, 4),
            'prev_date':        prev_date,
            'curr_date':        curr_date,
            'prev_adj_close':   float(prev_adj),
            'curr_adj_close':   float(curr_adj),
            'continuity_before': round(continuity_ratio, 4),
            'continuity_after':  round(continuity_ratio * shares_ratio, 4),
            'needs_correction': needs_correction,
        })

    return pd.DataFrame(rows).sort_values(['ticker', 'event_date']) if rows else pd.DataFrame()


def apply_corrections(events: pd.DataFrame, confirmed_only: bool = True) -> None:
    """needs_correction=True 인 이벤트 중, DART로 확정된 종목만 적용.

    confirmed_only=True(기본)면 CONFIRMED_TICKERS 화이트리스트 밖의 이벤트는
    통계상 needs_correction=True여도 스킵한다 — DART 미확인 상태로 프로덕션
    가격 데이터를 건드리지 않기 위한 안전장치.
    """
    targets = events[events['needs_correction']].copy()
    if confirmed_only:
        unconfirmed = targets[~targets['ticker'].isin(CONFIRMED_TICKERS)]
        if not unconfirmed.empty:
            log.warning(
                f'DART 미확인으로 스킵: {len(unconfirmed)}건 '
                f'({sorted(unconfirmed["ticker"].unique())})'
            )
        targets = targets[targets['ticker'].isin(CONFIRMED_TICKERS)]
    if targets.empty:
        log.info('보정 대상 없음')
        return

    backup_name = f"price_history_adj_backup_{datetime.now():%Y%m%d_%H%M%S}"
    tickers = sorted(targets['ticker'].unique())

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            CREATE TABLE {backup_name} AS
            SELECT ticker, date, adj_close FROM price_history
            WHERE ticker = ANY(%s)
            """,
            (tickers,),
        )
        log.info(f'백업 테이블 생성: {backup_name} ({len(tickers)}종목)')

        # 종목별로 event_date 내림차순(최신 먼저) 적용 — 과거일수록 보정 누적
        for ticker, grp in targets.groupby('ticker'):
            grp = grp.sort_values('event_date', ascending=False)
            for _, ev in grp.iterrows():
                cur.execute(
                    """
                    UPDATE price_history
                    SET adj_close = adj_close / %s
                    WHERE ticker = %s AND date < %s
                    """,
                    (ev['shares_ratio'], ticker, ev['event_date']),
                )
                log.info(
                    f"  [{ticker}] {ev['event_date']} 보정 적용 "
                    f"(shares_ratio={ev['shares_ratio']:.4f}, "
                    f"연속성 {ev['continuity_before']:.3f} → {ev['continuity_after']:.3f}) "
                    f"{cur.rowcount}행"
                )

    log.info(f'보정 완료: {len(targets)}건 ({len(tickers)}종목). 백업: {backup_name}')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--threshold', type=float, default=15.0, help='주식수 급변 임계값 (%%, 기본 15)')
    parser.add_argument('--ticker',    default=None, help='특정 종목만 처리')
    parser.add_argument('--apply',     action='store_true', help='실제 UPDATE 적용 (기본은 dry-run)')
    args = parser.parse_args()

    events = detect_corrections(threshold_pct=args.threshold, ticker=args.ticker)
    if events.empty:
        log.info('탐지된 주식수 급변 이벤트 없음')
        return

    n_need = int(events['needs_correction'].sum())
    n_skip = len(events) - n_need
    log.info(f'탐지: {len(events)}건 (보정 필요 {n_need}건 / 이미 정상 {n_skip}건)')
    print(events.to_string(index=False))

    if args.apply:
        apply_corrections(events)
    else:
        log.info('dry-run 모드 — 실제 반영하려면 --apply 추가')


if __name__ == '__main__':
    main()
