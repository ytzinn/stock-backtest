"""
종목 마스터 + 상장 이벤트 이력 관리.

실행:
    python -m ingest.universe_loader --init          # 최초 1회: FDR로 전종목 등록
    python -m ingest.universe_loader --update-daily  # cron 일일 delta 업데이트
    python -m ingest.universe_loader --financial-flag # is_financial 갱신
"""
import argparse
import logging
from datetime import date, datetime

import FinanceDataReader as fdr
from pykrx import stock as krx

from ingest.connection import db_conn

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

EXCLUDE_NAME_PATTERNS = [
    '스팩', '기업인수목적', '리츠',
    '선박펀드', '인프라펀드', '해운펀드',
    'ETF', 'ETN', 'KODEX', 'TIGER', 'KBSTAR', 'ARIRANG', 'HANARO',
]

FINANCIAL_KEYWORDS = ('금융', '은행', '보험', '증권')


def _today_str() -> str:
    return date.today().strftime('%Y%m%d')


def _is_excluded(corp_name: str) -> tuple[bool, str]:
    for p in EXCLUDE_NAME_PATTERNS:
        if p in corp_name:
            return True, f'사전제외: {p!r} 포함'
    return False, ''


# ── 최초 초기화 ────────────────────────────────────────────────────────────────

def init_universe() -> None:
    """FDR KRX 전종목 + 상장폐지 목록으로 stocks 테이블 초기화."""
    log.info('stocks 테이블 초기화 시작')
    with db_conn() as conn:
        cur = conn.cursor()

        for market_code in ('KOSPI', 'KOSDAQ'):
            df = fdr.StockListing(market_code)
            for _, row in df.iterrows():
                ticker    = str(row.get('Code', '')).strip()
                corp_name = str(row.get('Name', '')).strip()
                sector    = str(row.get('Industry', '')).strip()
                if not ticker:
                    continue
                excl, reason = _is_excluded(corp_name)
                cur.execute(
                    """
                    INSERT INTO stocks (ticker, corp_name, market, sector,
                                        is_excluded, exclude_reason, listed_date)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticker) DO UPDATE SET
                        corp_name    = EXCLUDED.corp_name,
                        market       = EXCLUDED.market,
                        sector       = EXCLUDED.sector,
                        is_excluded  = EXCLUDED.is_excluded,
                        exclude_reason = EXCLUDED.exclude_reason,
                        updated_at   = now()
                    """,
                    (ticker, corp_name, market_code, sector,
                     excl, reason if excl else None,
                     row.get('ListingDate')),
                )
                _upsert_listing_event(cur, ticker, market_code, corp_name,
                                      listed_date=row.get('ListingDate'),
                                      event_type='listed', source='fdr')
        log.info('stocks 초기화 완료')


def _upsert_listing_event(cur, ticker: str, market: str, corp_name: str,
                           listed_date=None, delisted_date=None,
                           event_type: str = 'listed', source: str = 'pykrx',
                           source_note: str = None) -> None:
    cur.execute(
        """
        INSERT INTO stock_listing_events
            (ticker, corp_name, market, listed_date, delisted_date,
             event_type, source, source_note)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (ticker, corp_name, market,
         listed_date, delisted_date,
         event_type, source, source_note),
    )


# ── 과거 시점 유니버스 수집 ────────────────────────────────────────────────────

def collect_historical_universe(rebalance_dates: list) -> None:
    """
    리밸런싱 기준일마다 pykrx get_market_ticker_list(날짜)로
    당시 상장 종목 목록 수집 → stock_listing_events 테이블에 저장.
    """
    log.info(f'과거 유니버스 수집: {len(rebalance_dates)}개 기준일')
    with db_conn() as conn:
        cur = conn.cursor()
        for rd in rebalance_dates:
            date_str = rd.strftime('%Y%m%d') if hasattr(rd, 'strftime') else rd
            for market in ('KOSPI', 'KOSDAQ'):
                try:
                    tickers = krx.get_market_ticker_list(date_str, market=market)
                except Exception as e:
                    log.warning(f'{date_str} {market} 조회 실패: {e}')
                    continue
                for ticker in tickers:
                    corp_name = krx.get_market_ticker_name(ticker)
                    _upsert_listing_event(cur, ticker, market, corp_name,
                                          listed_date=rd, event_type='listed',
                                          source='pykrx')
            log.info(f'{date_str} 완료')


# ── 일일 delta 업데이트 ────────────────────────────────────────────────────────

def _prev_trading_day_str() -> str:
    df = krx.get_index_ohlcv_by_date('20000101', _today_str(), 'KOSPI')
    dates = df.index.strftime('%Y%m%d').tolist()
    today = _today_str()
    idx = dates.index(today) if today in dates else -1
    return dates[idx - 1] if idx > 0 else dates[-2]


def update_listing_events_daily() -> None:
    """전일 대비 delta → listed / delisted 이벤트 저장."""
    today     = _today_str()
    yesterday = _prev_trading_day_str()
    log.info(f'delta 업데이트: {yesterday} → {today}')

    with db_conn() as conn:
        cur = conn.cursor()
        for market in ('KOSPI', 'KOSDAQ'):
            today_set     = set(krx.get_market_ticker_list(today,     market=market))
            yesterday_set = set(krx.get_market_ticker_list(yesterday, market=market))

            for ticker in today_set - yesterday_set:
                corp_name = krx.get_market_ticker_name(ticker)
                _upsert_listing_event(cur, ticker, market, corp_name,
                                      listed_date=date.today(),
                                      event_type='listed', source='pykrx')
                log.info(f'신규 상장: {ticker} {corp_name}')

            for ticker in yesterday_set - today_set:
                corp_name = krx.get_market_ticker_name(ticker)
                _upsert_listing_event(cur, ticker, market, corp_name,
                                      delisted_date=date.today(),
                                      event_type='delisted', source='pykrx')
                log.info(f'상장폐지: {ticker} {corp_name}')


# ── is_financial 갱신 ──────────────────────────────────────────────────────────

def update_financial_flag() -> None:
    """
    pykrx 섹터 분류로 is_financial 갱신.
    섹터명에 금융·은행·보험·증권이 포함되면 TRUE.
    오늘 데이터 없으면 최근 5 거래일을 순차 시도.
    """
    from datetime import timedelta
    log.info('is_financial 갱신 시작')
    with db_conn() as conn:
        cur = conn.cursor()
        for market in ('KOSPI', 'KOSDAQ'):
            df = None
            for offset in range(5):
                d = (date.today() - timedelta(days=offset)).strftime('%Y%m%d')
                try:
                    df = krx.get_market_sector_classifications(d, market=market)
                    if not df.empty:
                        log.info(f'{market} 섹터 조회 성공: {d}')
                        break
                except Exception as e:
                    log.warning(f'{market} 섹터 조회 실패({d}): {e}')
                    df = None
            if df is None or df.empty:
                log.warning(f'{market} 섹터 데이터 없음, 건너뜀')
                continue
            for _, row in df.iterrows():
                ticker    = str(row.get('Code',   '')).strip()
                sector_nm = str(row.get('Sector', '')).strip()
                is_fin = any(kw in sector_nm for kw in FINANCIAL_KEYWORDS)
                cur.execute(
                    "UPDATE stocks SET is_financial = %s, updated_at = now() WHERE ticker = %s",
                    (is_fin, ticker),
                )
    log.info('is_financial 갱신 완료')


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--init',           action='store_true')
    parser.add_argument('--update-daily',   action='store_true')
    parser.add_argument('--financial-flag', action='store_true')
    args = parser.parse_args()

    if args.init:
        init_universe()
    if args.update_daily:
        update_listing_events_daily()
    if args.financial_flag:
        update_financial_flag()


if __name__ == '__main__':
    main()
