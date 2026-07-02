"""
krx_daily_snapshot 대사 스크립트.

검증 항목:
  V1. close_price vs price_history.close  — KRX 원천 동일 여부
  V2. market_cap vs market_cap_history    — 기존 FDR 추정치와 비교
  V3. shares 급변 탐지                    — 감자·증자 이벤트 후보

실행:
    python -m ingest.krx_daily_validate
    python -m ingest.krx_daily_validate --ticker 002070
"""
import argparse
import logging

import pandas as pd

from ingest.connection import db_conn
from ingest.logging_config import configure_logging

configure_logging('krx_daily_validate.log')
log = logging.getLogger(__name__)


def _query(sql: str, params=None) -> pd.DataFrame:
    with db_conn() as conn:
        return pd.read_sql(sql, conn, params=params)


def validate_close(ticker: str | None = None, sample: int = 0) -> pd.DataFrame:
    """V1: krx_daily_snapshot.close_price vs price_history.close."""
    where = "WHERE k.ticker = %(t)s" if ticker else ""
    limit = f"LIMIT {sample}" if sample else ""
    sql = f"""
        SELECT k.ticker, k.date,
               k.close_price          AS krx_close,
               p.close                AS ph_close,
               k.close_price - p.close AS diff
        FROM krx_daily_snapshot k
        JOIN price_history p USING (ticker, date)
        {where}
        HAVING k.close_price IS DISTINCT FROM p.close::int
        {limit}
    """
    # HAVING without GROUP BY doesn't work — use WHERE
    sql = f"""
        SELECT k.ticker, k.date,
               k.close_price          AS krx_close,
               p.close                AS ph_close,
               k.close_price - p.close AS diff
        FROM krx_daily_snapshot k
        JOIN price_history p USING (ticker, date)
        {where}
        WHERE k.close_price IS DISTINCT FROM p.close::integer
        ORDER BY ABS(k.close_price - p.close::integer) DESC NULLS LAST
        {limit}
    """
    params = {'t': ticker} if ticker else None
    return _query(sql, params)


def validate_market_cap(ticker: str | None = None, threshold_pct: float = 20.0) -> pd.DataFrame:
    """V2: krx_daily_snapshot.market_cap vs market_cap_history (괴리율 threshold% 초과)."""
    where = "AND k.ticker = %(t)s" if ticker else ""
    sql = f"""
        SELECT k.ticker, k.date,
               k.market_cap                                         AS krx_mktcap,
               m.market_cap                                         AS old_mktcap,
               ROUND((k.market_cap - m.market_cap)
                     / NULLIF(m.market_cap, 0) * 100, 1)            AS diff_pct
        FROM krx_daily_snapshot k
        JOIN market_cap_history m USING (ticker, date)
        WHERE k.market_cap IS NOT NULL
          AND m.market_cap IS NOT NULL
          AND ABS(k.market_cap - m.market_cap)
              / NULLIF(m.market_cap, 0) > %(thresh)s / 100.0
          {where}
        ORDER BY 5 DESC NULLS LAST
        LIMIT 200
    """
    params = {'thresh': threshold_pct}
    if ticker:
        params['t'] = ticker
    return _query(sql, params)


def detect_share_changes(threshold_pct: float = 20.0) -> pd.DataFrame:
    """V3: 전일 대비 주식수 변동 threshold% 초과 종목 (감자·증자 이벤트 후보)."""
    sql = """
        SELECT ticker, date,
               shares                                                   AS shares_today,
               LAG(shares) OVER (PARTITION BY ticker ORDER BY date)     AS shares_prev,
               ROUND((shares::numeric / NULLIF(
                   LAG(shares) OVER (PARTITION BY ticker ORDER BY date), 0) - 1) * 100, 1
               )                                                         AS change_pct
        FROM krx_daily_snapshot
        WHERE shares IS NOT NULL
    """
    df = _query(f"""
        SELECT * FROM ({sql}) sub
        WHERE ABS(change_pct) >= %(thresh)s
        ORDER BY date DESC, ABS(change_pct) DESC
    """, {'thresh': threshold_pct})
    return df


def summary() -> None:
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT ticker), MIN(date), MAX(date) FROM krx_daily_snapshot")
        r = cur.fetchone()
    print(f"\n[krx_daily_snapshot 현황]")
    print(f"  총 행수:  {r[0]:>12,}")
    print(f"  종목 수:  {r[1]:>12,}")
    print(f"  기간:     {r[2]} ~ {r[3]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--ticker', default=None, help='특정 종목만 대사')
    parser.add_argument('--threshold', type=float, default=20.0,
                        help='시총/주식수 괴리율 기준 (%%, 기본 20)')
    args = parser.parse_args()

    summary()

    print("\n[V1] close_price vs price_history.close (상위 30건)")
    v1 = validate_close(ticker=args.ticker, sample=30)
    if v1.empty:
        print("  ✅ 차이 없음")
    else:
        print(v1.to_string(index=False))

    print(f"\n[V2] 시총 괴리 {args.threshold:.0f}% 초과 (상위 30건)")
    v2 = validate_market_cap(ticker=args.ticker, threshold_pct=args.threshold)
    if v2.empty:
        print("  ✅ 차이 없음")
    else:
        print(v2.head(30).to_string(index=False))

    print(f"\n[V3] 주식수 급변 {args.threshold:.0f}% 초과 (감자·증자 후보)")
    v3 = detect_share_changes(threshold_pct=args.threshold)
    if v3.empty:
        print("  해당 없음")
    else:
        print(v3.head(50).to_string(index=False))
        print(f"\n  → 총 {len(v3)}건  (이 종목들의 price_history adj_close 점검 필요)")


if __name__ == '__main__':
    main()
