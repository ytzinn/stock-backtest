"""
H1 공시 누락 보정 스크립트.

financials에 H1 데이터가 있으나 disclosures에 H1 공시 기록이 없는
(ticker, year) 쌍을 찾아 DART list.json API로 재수집한다.

배경:
  dart_ingest --h1-only 실행 시 공시 수집이 스킵되었고,
  초기 전체 수집 때도 일부 연도 공시가 빠져 약 51%의 H1 공시 날짜가 누락됨.
  이 누락은 pit_loader에서 available_from = 8월 19일 fallback을 유발하며,
  Aug-18 리밸런싱 구간(2016/2017/2022/2023)에서 현재연도 H1 데이터가 통째로 제외됨.

실행:
  venv/bin/python -m scripts.fix_h1_disclosures

쿼터 초과(QuotaExceededError) 시 그 시점까지 commit 후 종료.
내일 다시 실행하면 이미 복구된 쌍은 건너뛰고 남은 것만 처리.
"""
from __future__ import annotations

import logging
import time

from ingest.connection import db_conn
from ingest.dart_ingest import DartAPI, QuotaExceededError, _upsert_disclosures
from ingest.logging_config import configure_logging

configure_logging('fix_h1_disclosures.log')
log = logging.getLogger(__name__)


def find_missing_pairs(cur) -> list[tuple[str, str, int]]:
    """
    H1 재무 데이터는 있지만 disclosures에 H1 공시가 없는
    (ticker, corp_code, year) 목록. ticker 순, year 순 정렬.
    """
    cur.execute("""
        SELECT DISTINCT f.ticker, s.corp_code, f.year
        FROM financials f
        JOIN stocks s ON s.ticker = f.ticker
        WHERE f.report_type = 'H1'
          AND s.corp_code IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM disclosures d
              WHERE d.ticker = f.ticker
                AND d.year   = f.year
                AND d.report_type = 'H1'
          )
        ORDER BY f.ticker, f.year
    """)
    return cur.fetchall()


def run() -> None:
    dart = DartAPI()
    call_count  = 0
    fixed_pairs = 0

    with db_conn() as conn:
        cur = conn.cursor()
        missing = find_missing_pairs(cur)
        total = len(missing)
        log.info(f'누락된 H1 공시 (ticker,year) 쌍: {total:,}개')

        if not missing:
            log.info('누락 없음. 완료.')
            return

        for idx, (ticker, corp_code, year) in enumerate(missing, 1):
            try:
                items = dart.get_disclosures(corp_code, year)
                call_count += 1
                _upsert_disclosures(cur, ticker, items)

                cur.execute(
                    "SELECT COUNT(*) FROM disclosures WHERE ticker=%s AND year=%s AND report_type='H1'",
                    (ticker, year),
                )
                if cur.fetchone()[0] > 0:
                    fixed_pairs += 1

                if idx % 500 == 0:
                    conn.commit()
                    log.info(f'진행: {idx}/{total} ({idx/total:.0%}) | 복구 {fixed_pairs}건')

            except QuotaExceededError:
                conn.commit()
                log.error(f'DART 일일 쿼터 초과 — {idx}/{total} 지점에서 중단. 내일 재실행.')
                return
            except Exception as e:
                log.warning(f'{ticker} {year}: {e}')

            time.sleep(0.05)

        conn.commit()

    log.info(f'완료: API 콜 {call_count:,}회 / H1 공시 복구 {fixed_pairs:,}/{total:,}쌍')


if __name__ == '__main__':
    run()
