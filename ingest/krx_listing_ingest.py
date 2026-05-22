"""
KRX Open API 기반 리밸런싱 시점 상장 종목 스냅샷 수집.

각 리밸런싱 날짜(backtest.configs.rebalance_dates.REBALANCE_DATES) 기준으로
KOSPI + KOSDAQ 상장 종목을 krx_listing_snapshots 테이블에 저장한다.

실행:
    python -m ingest.krx_listing_ingest            # 전체 수집
    python -m ingest.krx_listing_ingest --force    # 이미 수집된 날짜도 재수집
"""
import argparse
import logging
import os
import time
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

from backtest.configs.rebalance_dates import REBALANCE_DATES
from ingest.connection import db_conn
from ingest.logging_config import configure_logging

load_dotenv()
configure_logging('krx_listing.log')
log = logging.getLogger(__name__)

KRX_BASE = 'https://data-dbg.krx.co.kr/svc/apis/sto'
API_IDS = {
    'KOSPI':  'stk_bydd_trd',
    'KOSDAQ': 'ksq_bydd_trd',
}


def _create_table() -> None:
    with db_conn() as conn:
        conn.cursor().execute("""
            CREATE TABLE IF NOT EXISTS krx_listing_snapshots (
                snapshot_date DATE    NOT NULL,
                ticker        CHAR(6) NOT NULL,
                company_name  VARCHAR(200),
                market        VARCHAR(20),
                shares        BIGINT,
                close_price   INTEGER,
                PRIMARY KEY (snapshot_date, ticker)
            )
        """)


def _fetch_market(api_id: str, bas_dd: str) -> list[dict]:
    """KRX API 단일 호출. 빈 응답이면 []."""
    key = os.environ.get('KRX_API_KEY', '')
    if not key:
        raise EnvironmentError('KRX_API_KEY가 .env에 없음')
    resp = requests.get(
        f'{KRX_BASE}/{api_id}',
        params={'basDd': bas_dd},
        headers={'AUTH_KEY': key},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get('OutBlock_1', [])


def fetch_snapshot(snapshot_date: date) -> list[dict]:
    """
    snapshot_date 당일 KRX 데이터를 가져온다.
    비거래일이면 최대 5일 앞으로 이동해 데이터가 있는 날을 찾는다.
    반환: {'ticker', 'company_name', 'market', 'shares', 'close_price'} 리스트
    """
    for offset in range(6):
        target = snapshot_date + timedelta(days=offset)
        bas_dd = target.strftime('%Y%m%d')
        rows = []
        for market, api_id in API_IDS.items():
            raw = _fetch_market(api_id, bas_dd)
            for r in raw:
                ticker = str(r.get('ISU_CD', '')).zfill(6)
                try:
                    shares = int(str(r.get('LIST_SHRS', '0')).replace(',', ''))
                    price  = int(str(r.get('TDD_CLSPRC', '0')).replace(',', ''))
                except ValueError:
                    shares, price = 0, 0
                rows.append({
                    'ticker':       ticker,
                    'company_name': r.get('ISU_NM', ''),
                    'market':       market,
                    'shares':       shares,
                    'close_price':  price,
                })
        if rows:
            if offset > 0:
                log.debug(f'{snapshot_date} 비거래일 → {target} 데이터 사용')
            return rows
        time.sleep(0.3)

    log.warning(f'{snapshot_date} 기준 ±5일 내 KRX 데이터 없음 — 건너뜀')
    return []


def _already_collected(snapshot_date: date) -> bool:
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM krx_listing_snapshots WHERE snapshot_date = %s LIMIT 1",
            (snapshot_date,),
        )
        return cur.fetchone() is not None


def collect_date(snapshot_date: date, force: bool = False) -> int:
    """
    단일 날짜 수집. 반환: 저장된 행 수.
    force=False이면 이미 수집된 날짜는 건너뜀.
    """
    if not force and _already_collected(snapshot_date):
        log.info(f'{snapshot_date} 이미 수집됨 — 건너뜀')
        return 0

    rows = fetch_snapshot(snapshot_date)
    if not rows:
        return 0

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM krx_listing_snapshots WHERE snapshot_date = %s",
            (snapshot_date,),
        )
        cur.executemany(
            """
            INSERT INTO krx_listing_snapshots
                (snapshot_date, ticker, company_name, market, shares, close_price)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            [
                (snapshot_date, r['ticker'], r['company_name'],
                 r['market'], r['shares'], r['close_price'])
                for r in rows
            ],
        )
    log.info(f'{snapshot_date}: {len(rows)}개 종목 저장')
    return len(rows)


def ingest_all(force: bool = False) -> None:
    _create_table()
    total = 0
    for i, d in enumerate(REBALANCE_DATES, 1):
        log.info(f'[{i}/{len(REBALANCE_DATES)}] {d} 수집 중...')
        n = collect_date(d, force=force)
        total += n
        time.sleep(0.5)
    log.info(f'KRX 상장 스냅샷 수집 완료: 총 {total}행')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true', help='이미 수집된 날짜도 재수집')
    parser.add_argument('--date', dest='single_date', help='단일 날짜만 수집 (YYYY-MM-DD)')
    args = parser.parse_args()

    _create_table()
    if args.single_date:
        d = date.fromisoformat(args.single_date)
        collect_date(d, force=args.force)
    else:
        ingest_all(force=args.force)


if __name__ == '__main__':
    main()
