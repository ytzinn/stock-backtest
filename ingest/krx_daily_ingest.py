"""
KRX Open API 날짜별 전종목 스냅샷 수집.

stk_bydd_trd (KOSPI) + ksq_bydd_trd (KOSDAQ) → krx_daily_snapshot 저장.
LIST_SHRS(상장주식수)·MKTCAP(시총)·TDD_CLSPRC(종가)를 날짜별로 보존.

용도:
  1. market_cap_history 대체 (정확한 날짜별 주식수)
  2. 감자·증자 이벤트 탐지 (주식수 급변 감지)
  3. price_history.close 교차검증

실행:
    python -m ingest.krx_daily_ingest                 # price_history 거래일 전체
    python -m ingest.krx_daily_ingest --from 20260101 # 특정 날짜부터
    python -m ingest.krx_daily_ingest --date 20260403 # 단일 날짜
"""
import argparse
import logging
import os
import time
from datetime import date, datetime

import requests

from ingest.connection import db_conn
from ingest.logging_config import configure_logging

configure_logging('krx_daily_ingest.log')
log = logging.getLogger(__name__)

KRX_BASE   = 'https://data-dbg.krx.co.kr/svc/apis/sto'
CALL_DELAY = 0.3   # 초 (API 과부하 방지)

_API_KEY: str | None = None


def _get_api_key() -> str:
    global _API_KEY
    if _API_KEY:
        return _API_KEY
    env_file = os.path.join(os.path.dirname(__file__), '..', '.env')
    with open(env_file) as f:
        for line in f:
            if line.startswith('KRX_API_KEY'):
                _API_KEY = line.split('=', 1)[1].strip()
                return _API_KEY
    raise RuntimeError('.env에 KRX_API_KEY 없음')


def _fetch_one(api_id: str, bas_dd: str) -> list[dict]:
    """KRX API 단일 호출. 실패 시 빈 리스트."""
    url = f'{KRX_BASE}/{api_id}'
    headers = {'AUTH_KEY': _get_api_key()}
    try:
        r = requests.get(url, headers=headers, params={'basDd': bas_dd}, timeout=15)
        r.raise_for_status()
        return r.json().get('OutBlock_1', [])
    except Exception as e:
        log.warning(f'{api_id} {bas_dd} 조회 실패: {e}')
        return []


def collect_date(bas_dd: str) -> int:
    """
    KOSPI + KOSDAQ 하루치 수집 → krx_daily_snapshot upsert.
    반환: 저장된 행 수.
    """
    rows = []
    for api_id, market in [('stk_bydd_trd', 'KOSPI'), ('ksq_bydd_trd', 'KOSDAQ')]:
        items = _fetch_one(api_id, bas_dd)
        time.sleep(CALL_DELAY)
        for item in items:
            ticker = str(item.get('ISU_CD', '')).strip().zfill(6)
            if not ticker or len(ticker) != 6:
                continue
            try:
                shares     = int(str(item.get('LIST_SHRS', '') or '').replace(',', '')) or None
                mktcap_str = str(item.get('MKTCAP', '') or '').replace(',', '')
                market_cap = int(mktcap_str) if mktcap_str else None
                close_str  = str(item.get('TDD_CLSPRC', '') or '').replace(',', '')
                close      = int(close_str) if close_str else None
            except (ValueError, TypeError):
                continue
            rows.append((ticker, bas_dd, market, shares, market_cap, close))

    if not rows:
        return 0

    with db_conn() as conn:
        cur = conn.cursor()
        cur.executemany(
            """
            INSERT INTO krx_daily_snapshot (ticker, date, market, shares, market_cap, close_price)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker, date) DO UPDATE SET
                market     = EXCLUDED.market,
                shares     = EXCLUDED.shares,
                market_cap = EXCLUDED.market_cap,
                close_price = EXCLUDED.close_price
            """,
            rows,
        )
    return len(rows)


def _trading_days(from_date: str) -> list[str]:
    """price_history에 있는 거래일 목록 (from_date 이후, 오름차순)."""
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT date FROM price_history
            WHERE date >= %s
            ORDER BY date
            """,
            (from_date,),
        )
        return [r[0].strftime('%Y%m%d') for r in cur.fetchall()]


def _already_collected(bas_dd: str) -> bool:
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM krx_daily_snapshot WHERE date = %s LIMIT 1",
            (datetime.strptime(bas_dd, '%Y%m%d').date(),),
        )
        return cur.fetchone() is not None


def ingest_range(from_date: str = '20140101', skip_existing: bool = True) -> None:
    days = _trading_days(from_date)
    log.info(f'KRX 일별 스냅샷 수집: {len(days)}개 거래일, {from_date}~')
    for i, bas_dd in enumerate(days, 1):
        if skip_existing and _already_collected(bas_dd):
            continue
        n = collect_date(bas_dd)
        if i % 100 == 0 or n == 0:
            log.info(f'  [{i}/{len(days)}] {bas_dd}: {n}행')
    log.info('수집 완료')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--from',   dest='from_date', default='20140101')
    parser.add_argument('--date',   dest='single_date', default=None,
                        help='단일 날짜 수집 (YYYYMMDD)')
    parser.add_argument('--no-skip', action='store_true',
                        help='이미 수집된 날짜도 재수집')
    args = parser.parse_args()

    if args.single_date:
        n = collect_date(args.single_date)
        log.info(f'{args.single_date}: {n}행 저장')
    else:
        ingest_range(from_date=args.from_date, skip_existing=not args.no_skip)


if __name__ == '__main__':
    main()
