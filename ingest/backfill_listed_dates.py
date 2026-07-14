"""
stocks.listed_date 백필 (CORR-HARD-001, 사용자 결정 2026-07-12).

운영 DB stocks의 92%(3,005/3,264)가 listed_date NULL이라 Hard Filter의 "상장 6개월"
요건이 사실상 미작동이었다. NULL 행만 채운다 (기존값 절대 덮어쓰지 않음).

소스 우선순위 (정확도 순):
  1. FDR StockListing('KRX-DESC')의 ListingDate — 현재 상장 종목의 공식 상장일
     ※ 'KRX'는 시세 스냅샷이라 ListingDate 컬럼이 없다 (2026-07 확인). 'KRX-DESC'를 써야 한다.
  2. stock_listing_events.listed_date — 상장폐지 종목 (FDR KRX-DELISTING 유래)
  3. (잔여) 백필하지 않음 — hard_filter가 가격 이력 최초일 프록시로 판정
     (backtest/data_access.get_first_price_date)

실행 (서버):
    venv/bin/python -m ingest.backfill_listed_dates --dry-run   # 건수만 확인
    venv/bin/python -m ingest.backfill_listed_dates             # 실제 UPDATE
"""
import argparse
import logging

import FinanceDataReader as fdr

from ingest.connection import db_conn
from ingest.logging_config import configure_logging

configure_logging('backfill_listed_dates.log')
log = logging.getLogger(__name__)


def load_fdr_listing_dates() -> dict[str, object]:
    """FDR KRX-DESC 전 종목의 {ticker: ListingDate}. 실패 시 예외 전파 (조용한 빈 dict 금지)."""
    df = fdr.StockListing('KRX-DESC')
    if 'ListingDate' not in df.columns:
        raise RuntimeError(
            f"FDR StockListing('KRX-DESC')에 ListingDate 컬럼 없음: {list(df.columns)}"
        )
    code_col = 'Code' if 'Code' in df.columns else 'Symbol'
    result = {}
    for _, row in df.iterrows():
        ticker = str(row[code_col]).strip().zfill(6)
        ld = row['ListingDate']
        if ld is not None and str(ld) not in ('NaT', 'nan', ''):
            result[ticker] = ld
    log.info(f'FDR KRX-DESC ListingDate: {len(result)}개 종목')
    return result


def backfill(dry_run: bool = False) -> None:
    fdr_dates = load_fdr_listing_dates()

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT ticker FROM stocks WHERE listed_date IS NULL")
        null_tickers = [r[0] for r in cur.fetchall()]
        log.info(f'listed_date NULL: {len(null_tickers)}개')

        # 1순위: FDR 공식 상장일
        from_fdr = {t: fdr_dates[t] for t in null_tickers if t in fdr_dates}

        # 2순위: 상폐 이벤트의 listed_date (FDR 미커버 = 주로 상폐 종목)
        remaining = [t for t in null_tickers if t not in from_fdr]
        from_events = {}
        if remaining:
            cur.execute(
                """
                SELECT DISTINCT ON (ticker) ticker, listed_date
                FROM stock_listing_events
                WHERE ticker = ANY(%s) AND listed_date IS NOT NULL
                ORDER BY ticker, listed_date ASC
                """,
                (remaining,),
            )
            from_events = {r[0]: r[1] for r in cur.fetchall()}

        leftover = [t for t in remaining if t not in from_events]
        log.info(
            f'백필 계획: FDR {len(from_fdr)}개 + listing_events {len(from_events)}개, '
            f'잔여 {len(leftover)}개 (가격 이력 프록시로 커버 — hard_filter)'
        )

        if dry_run:
            log.info('dry-run — UPDATE 생략')
            return

        for source, mapping in (('fdr_krx', from_fdr), ('listing_events', from_events)):
            for ticker, ld in mapping.items():
                cur.execute(
                    "UPDATE stocks SET listed_date = %s, updated_at = now() "
                    "WHERE ticker = %s AND listed_date IS NULL",
                    (ld, ticker),
                )
            log.info(f'{source}: {len(mapping)}개 UPDATE')

        cur.execute("SELECT COUNT(*) FROM stocks WHERE listed_date IS NULL")
        log.info(f'백필 후 잔여 NULL: {cur.fetchone()[0]}개')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    backfill(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
