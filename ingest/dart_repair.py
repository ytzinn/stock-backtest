"""
DART 재무 데이터 버그 복구 도구.

수집 로직 버그 수정 후, 기존 잘못 저장된 데이터를 재수집·정정한다.
일반 수집(dart_ingest.py)과 독립적으로 실행하거나, --repair-ni / --repair-equity 옵션으로 호출된다.
"""
import logging

from ingest.connection import db_conn
from ingest.dart_ingest import (
    DartAPI,
    QuotaExceededError,
    REPRT_CODE,
    _upsert_financials,
)

log = logging.getLogger(__name__)


def repair_ni() -> None:
    """
    당기순이익 0 버그 복구.

    자본변동표의 0값이 손익계산서 정답을 upsert로 덮어쓴 종목만 대상.
    조건: 당기순이익=0 AND 매출>0. 영업손실 기업도 포함(이전 영업이익>0 조건보다 넓음).
    수정된 _upsert_financials(sj_nm 필터)로 재수집하므로 손익계산서 값만 저장된다.
    """
    dart = DartAPI()

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT ni.ticker, s.corp_code, ni.year, ni.report_type, ni.fs_div
            FROM financials ni
            JOIN financials rev ON rev.ticker = ni.ticker
                               AND rev.year = ni.year
                               AND rev.report_type = ni.report_type
                               AND rev.fs_div = ni.fs_div
                               AND rev.account_nm = '매출액'
            JOIN stocks s ON s.ticker = ni.ticker
            WHERE ni.account_nm = '당기순이익'
              AND (ni.amount IS NULL OR ni.amount = 0)
              AND rev.amount > 0
              AND s.corp_code IS NOT NULL
            ORDER BY ni.ticker, ni.year, ni.report_type
        """)
        targets = cur.fetchall()

    log.info(f'당기순이익 복구 대상: {len(targets)}개 (ticker×year×report_type×fs_div)')
    ok = 0
    for ticker, corp_code, year, report_type, fs_div in targets:
        reprt_code = REPRT_CODE.get(report_type)
        if not reprt_code:
            continue
        try:
            items = dart.get_financial_statement(corp_code, year, reprt_code, fs_div)
            if not items:
                log.warning(f'{ticker} {year} {report_type} {fs_div} — API 빈 응답')
                continue
            with db_conn() as conn:
                cur = conn.cursor()
                _upsert_financials(cur, ticker, corp_code, year, report_type, fs_div, items)
            ok += 1
            log.info(f'{ticker} {year} {report_type} {fs_div} 복구 완료')
        except QuotaExceededError:
            log.error('DART 쿼터 초과 — 복구 중단, 내일 재실행')
            break
        except Exception as e:
            log.error(f'{ticker} {year} {report_type} 복구 실패: {e}')

    log.info(f'당기순이익 복구 완료: {ok}/{len(targets)}개')


def repair_equity() -> None:
    """
    자산 ≠ 부채+자본 버그 복구.

    원인: '자본및부채총계'(=자산총계)나 '지배기업소유주지분'이 자본총계 alias로
    잘못 매핑돼 upsert로 덮어쓰여진 케이스.
    조건: |자산 - 부채 - 자본| / 자산 > 1% (1% 초과 오차).
    수정된 _upsert_financials + ACCOUNT_ALIASES로 재수집하면
    자본총계와 지배기업소유주지분이 분리 저장된다.
    """
    dart = DartAPI()

    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT a.ticker, s.corp_code, a.year, a.report_type, a.fs_div
            FROM financials a
            JOIN financials l ON l.ticker=a.ticker AND l.year=a.year
                             AND l.report_type=a.report_type AND l.fs_div=a.fs_div
                             AND l.account_nm='부채총계'
            JOIN financials e ON e.ticker=a.ticker AND e.year=a.year
                             AND e.report_type=a.report_type AND e.fs_div=a.fs_div
                             AND e.account_nm='자본총계'
            JOIN stocks s ON s.ticker=a.ticker
            WHERE a.account_nm='자산총계'
              AND a.amount > 0
              AND l.amount IS NOT NULL AND e.amount IS NOT NULL
              AND ABS(a.amount - l.amount - e.amount) / a.amount > 0.01
              AND s.corp_code IS NOT NULL
            ORDER BY a.ticker, a.year, a.report_type
        """)
        targets = cur.fetchall()

    log.info(f'자산=부채+자본 복구 대상: {len(targets)}개 (ticker×year×report_type×fs_div)')
    ok = 0
    for ticker, corp_code, year, report_type, fs_div in targets:
        reprt_code = REPRT_CODE.get(report_type)
        if not reprt_code:
            continue
        try:
            items = dart.get_financial_statement(corp_code, year, reprt_code, fs_div)
            if not items:
                log.warning(f'{ticker} {year} {report_type} {fs_div} — API 빈 응답')
                continue
            with db_conn() as conn:
                cur = conn.cursor()
                _upsert_financials(cur, ticker, corp_code, year, report_type, fs_div, items)
            ok += 1
            log.info(f'{ticker} {year} {report_type} {fs_div} 복구 완료')
        except QuotaExceededError:
            log.error('DART 쿼터 초과 — 복구 중단, 내일 재실행')
            break
        except Exception as e:
            log.error(f'{ticker} {year} {report_type} 복구 실패: {e}')

    log.info(f'자산=부채+자본 복구 완료: {ok}/{len(targets)}개')
