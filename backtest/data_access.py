"""
백테스트 DB 조회 헬퍼. 모든 필터/파이프라인에서 이 모듈만 import한다.
커넥션은 엔진에서 열어 conn 인자로 주입한다 (ingest/connection.py 팩토리 재사용).

단위: 모든 금액은 KRW(원), 주식수는 실제 주식 수.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta

import pandas as pd

log = logging.getLogger(__name__)


class PriceDataUnavailable(RuntimeError):
    """price_history에 해당 종목의 행이 아예 없음 (미수집 또는 미상장).

    '데이터 없음'과 '거래 없음(정지·무거래)'은 다른 상태다 (CORR-DA-001) —
    조용한 0/False 반환 대신 이 예외로 구분한다. 호출자가 '제외'로 처리하려면
    명시적으로 잡아서 사유를 남겨라 (hard_filter가 그렇게 한다).
    """


def _has_any_price_row(cur, ticker: str, as_of: date) -> bool:
    cur.execute(
        "SELECT 1 FROM price_history WHERE ticker = %s AND date <= %s LIMIT 1",
        (ticker, as_of),
    )
    return cur.fetchone() is not None


# TTM 계산 대상 계정 (IS + CF: 기간 누적값)
_IS_CF_ACCOUNTS = frozenset({
    '매출액', '매출총이익', '영업이익', '당기순이익',
    '영업활동현금흐름', '투자활동현금흐름', '재무활동현금흐름', '배당금지급',
})


# ── 가격 / 거래대금 ─────────────────────────────────────────────────────────────

def get_avg_turnover(conn, ticker: str, as_of: date, window: int = 20,
                     max_lookback_days: int = 90) -> float:
    """최근 window 영업일 평균 거래대금(KRW).

    계약 (CORR-DA-001):
      - price_history에 이 종목의 행이 **아예 없으면** PriceDataUnavailable을 던진다
        (미수집/미상장 — '무거래'와 다른 상태. 조용한 0 반환 금지).
      - 행은 있으나 거래정지·turnover NULL 등으로 유효 거래가 없으면 0.0
        (실제 '거래 없음' — 유동성 부족 제외 사유로 정당).

    max_lookback_days: 이 기간(캘린더 일) 밖의 데이터는 사용하지 않는다.
    거래정지 후 오래된 거래량이 현재 유동성인 것처럼 계산되는 것을 방지.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT AVG(turnover), COUNT(*)
            FROM (
                SELECT turnover
                FROM price_history
                WHERE ticker = %s AND date <= %s
                  AND date >= %s - INTERVAL '1 day' * %s
                  AND is_suspended = FALSE
                  AND turnover IS NOT NULL
                ORDER BY date DESC
                LIMIT %s
            ) sub
            """,
            (ticker, as_of, as_of, max_lookback_days, window),
        )
        avg, cnt = cur.fetchone()
        if cnt == 0 and not _has_any_price_row(cur, ticker, as_of):
            raise PriceDataUnavailable(f'{ticker}: price_history에 {as_of} 이전 행 없음')
    return float(avg) if avg is not None else 0.0


# window 거래일을 확보하기 위해 넉넉히 잡는 캘린더 창 (연휴 포함). 판정에는 쓰이지 않고
# trading_dates 조회 범위만 정한다.
_TRADING_DAY_LOOKBACK = 40


def has_recent_trade(conn, ticker: str, as_of: date, window: int = 5) -> bool:
    """as_of 이하 **시장 거래일** 최근 window 일 중 실제 거래가 하루라도 있으면 True.

    창 규약: window=5 는 `[T-4, T]` — as_of 를 포함한 시장 거래일 5일. (종전과 동일.)

    `[수정 2026-08-22 — TRADE-HALT]` 종전 구현은 **그 종목 자신의 최근 5 '행'** 을 봤다
    (`ORDER BY date DESC LIMIT 5`). docstring 은 "영업일 기준"이라 적혀 있었으나 코드는
    아니었고, 그 불일치가 결함의 본질이었다. 거래정지로 행이 더 이상 쌓이지 않으면
    몇 달 전 행 5개를 보고 통과시킨다. 이제 `daily_nav.trading_dates`(price_history
    DISTINCT date — CLAUDE.md 관례)로 **시장 거래일**을 잡아 그 날짜에 실거래가 있는지 본다.

    **이 함수의 역할은 좁다.** 상폐 방어선은 게이트 로더(`load_gate_passed_tickers`
    조건 3)에 있고, #24 후보 오염의 원인도 상폐 이벤트 피드 정지였지 이 결함이 아니었다.
    또한 정지 기간에 `is_suspended=TRUE` 행이 쌓이는 종목은 **종전 구현도 올바로 탈락**
    시켰다 (2014~2025 매년 300~470종목이 그 형태). 이 함수가 담당하는 것은
    **"상폐는 아닌데 행 생성이 완전히 끊긴"** 좁은 틈뿐이다.
    근거는 주석이 아니라 tests/oracle/test_trade_halt_contract.py 에 있다.

    계약 (CORR-DA-001): price_history에 이 종목의 행이 아예 없으면
    PriceDataUnavailable을 던진다 — '거래정지'(False)와 '데이터 미수집'은 다른 상태다.
    """
    # 지연 import — data_access ← engine ← daily_nav 순환을 피한다.
    from backtest.daily_nav import trading_dates

    days = trading_dates(conn, as_of - timedelta(days=_TRADING_DAY_LOOKBACK), as_of)[-window:]
    with conn.cursor() as cur:
        traded = False
        if days:
            cur.execute(
                """
                SELECT 1 FROM price_history
                WHERE ticker = %s AND date = ANY(%s)
                  AND adj_close IS NOT NULL AND is_suspended = FALSE
                LIMIT 1
                """,
                (ticker, days),
            )
            traded = cur.fetchone() is not None
        if not traded and not _has_any_price_row(cur, ticker, as_of):
            raise PriceDataUnavailable(f'{ticker}: price_history에 {as_of} 이전 행 없음')
    return traded


def get_adj_close_range(conn, ticker: str, as_of: date, lookback: int) -> pd.Series:
    """as_of 이전 lookback 영업일 adj_close 시계열 (오름차순 날짜 인덱스). 없으면 빈 Series."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT date, adj_close
            FROM price_history
            WHERE ticker = %s AND date <= %s AND adj_close IS NOT NULL
            ORDER BY date DESC
            LIMIT %s
            """,
            (ticker, as_of, lookback),
        )
        rows = cur.fetchall()
    if not rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(rows, columns=['date', 'adj_close'])
    return df.set_index('date')['adj_close'].sort_index()


def get_max_price_date(conn) -> date | None:
    """price_history 전체의 최신 거래일. 데이터 신선도 검증용 (CORR-FRESH-001). 없으면 None."""
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(date) FROM price_history")
        row = cur.fetchone()
    return row[0] if row else None


def get_close_price(conn, ticker: str, as_of: date) -> float | None:
    """as_of 기준 가장 가까운 adj_close. 없으면 None."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT adj_close FROM price_history
            WHERE ticker = %s AND date <= %s AND adj_close IS NOT NULL
            ORDER BY date DESC LIMIT 1
            """,
            (ticker, as_of),
        )
        row = cur.fetchone()
    return float(row[0]) if row else None


# ── 시가총액 / 주식수 ────────────────────────────────────────────────────────────

def get_market_cap(conn, ticker: str, as_of: date) -> float | None:
    """as_of 기준 가장 가까운 시가총액(KRW). 없으면 None."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT market_cap FROM market_cap_history
            WHERE ticker = %s AND date <= %s AND market_cap IS NOT NULL
            ORDER BY date DESC LIMIT 1
            """,
            (ticker, as_of),
        )
        row = cur.fetchone()
    return float(row[0]) if row else None


def get_shares_outstanding(conn, ticker: str, as_of: date) -> int | None:
    """as_of 기준 가장 가까운 상장주식수. 없으면 None."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT shares FROM market_cap_history
            WHERE ticker = %s AND date <= %s AND shares IS NOT NULL
            ORDER BY date DESC LIMIT 1
            """,
            (ticker, as_of),
        )
        row = cur.fetchone()
    return int(row[0]) if row else None


# ── 종목 메타 ───────────────────────────────────────────────────────────────────

def get_markets(conn, tickers, as_of: date) -> dict[str, str]:
    """as_of 이하 최신 거래일 기준 종목별 시장 구분 ('KOSPI'|'KOSDAQ'). PIT.

    1순위 `krx_daily_snapshot`(일별 PIT — 시장 이동을 실제 반영, 미래 앵커 포함),
    누락분은 `stocks.market`(현재값) fallback. 둘 다 없으면 반환 dict에서 키 누락 →
    호출자가 시장 미상으로 보고 sell_cost() 기본(KOSPI 상한)을 적용한다.
    거래비용 산출(CORR-COST-001)의 시장별 매도요율 결정에만 쓴다 — 상폐 판정 금지.
    """
    tickers = list(dict.fromkeys(tickers))  # 중복 제거, 순서 보존
    if not tickers:
        return {}
    out: dict[str, str] = {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (ticker) ticker, market
            FROM krx_daily_snapshot
            WHERE ticker = ANY(%s) AND date <= %s AND market IS NOT NULL
            ORDER BY ticker, date DESC
            """,
            (tickers, as_of),
        )
        for tk, mk in cur.fetchall():
            out[tk] = mk
        missing = [t for t in tickers if t not in out]
        if missing:
            cur.execute(
                "SELECT ticker, market FROM stocks WHERE ticker = ANY(%s) AND market IS NOT NULL",
                (missing,),
            )
            for tk, mk in cur.fetchall():
                out[tk] = mk
    return out


def get_listed_date(conn, ticker: str) -> date | None:
    """stocks.listed_date 반환. 없으면 None (운영 DB의 92%가 NULL — CORR-HARD-001,
    백필 전까지 호출자는 get_first_price_date 프록시로 보완해야 한다)."""
    with conn.cursor() as cur:
        cur.execute("SELECT listed_date FROM stocks WHERE ticker = %s", (ticker,))
        row = cur.fetchone()
    return row[0] if row else None


def is_financial_company(conn, ticker: str) -> bool | None:
    """stocks.is_financial 반환. **None = 판정 불가**(stocks 미등재 또는 NULL)이지 '아니다'가
    아니다 — 호출자가 None 을 False 로 접어 읽으면 금융업이 조용히 통과한다 (GATE-FINANCIAL).

    DQ Gate 는 이 플래그를 읽지 않는다. 2026-08-17 기준 게이트 통과 종목의 1.5%(37종목)가
    is_financial=TRUE 다. 금융업은 차입 계정 이름이 달라 R2 가 0으로 무조건 통과하고
    제조업 기준 부채비율 상한도 무의미해 R1 도 못 잡으므로, 배제는 HardFilter 몫이다."""
    with conn.cursor() as cur:
        cur.execute("SELECT is_financial FROM stocks WHERE TRIM(ticker) = %s",
                    (ticker.strip(),))
        row = cur.fetchone()
    return row[0] if row else None


def get_first_price_date(conn, ticker: str) -> date | None:
    """price_history 최초 거래일. 상장일 프록시 (실제 상장일보다 늦을 수 없는 하한이 아니라
    수집 시작일(2014-01)로 절단된 값 — 2014년 이전 상장 종목은 2014년으로 나온다.
    '최근 상장' 판정(상장 N개월 미만)에는 보수적으로 안전: 프록시가 실제보다 늦으면
    더 오래 제외될 뿐 조기 편입은 없다). 없으면 None."""
    with conn.cursor() as cur:
        cur.execute("SELECT MIN(date) FROM price_history WHERE ticker = %s", (ticker,))
        row = cur.fetchone()
    return row[0] if row else None


def is_delisted_at(conn, ticker: str, as_of: date) -> bool:
    """as_of 시점에 상장폐지 여부. stock_listing_events 기준."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM stock_listing_events
            WHERE ticker = %s
              AND delisted_date IS NOT NULL
              AND delisted_date <= %s
            LIMIT 1
            """,
            (ticker, as_of),
        )
        return cur.fetchone() is not None


# ── PIT 데이터 ──────────────────────────────────────────────────────────────────

def load_gate_passed_tickers(
    conn,
    rebalance_date: date,
    report_type: str = 'FY',
    fiscal_year: int | None = None,
) -> list[str]:
    """
    리밸런싱 기준일에 투자 가능한 종목 목록.

    조건:
      1. stocks.is_excluded = FALSE
      2. universe_gate_pit 시점별 판정 = 'PASS'.
         **fiscal_year 미지정(기본, 기존 동작)** — rebalance_date 기준 **최신 가용
         연도**(DISTINCT ON ... ORDER BY year DESC)를 쓴다.
         **fiscal_year 지정 (DEBT-3, SPEC_13 §0-A)** — 정확히 그 사업연도만 매칭한다.
         원하는 연도 보고서가 아직 available_from 이전(미공개)이면 그 종목은 조용히
         다른 연도로 대체되지 않고 결과에서 그냥 빠진다 — late/missing 집계는 이
         함수의 책임이 아니라 호출부(Q-H 판정 보고서)가 신청 유니버스와의 차집합으로
         계산한다.
         **시점별 판정 (CORR-GATE-003)**: 게이트 계정 정정 공시일(amendment_from)이
         rebalance_date 이하면 status_amended(정정값 판정), 아니면 status(최초 공시값
         판정)를 쓴다 — 정정 이전엔 최초값(룩어헤드 방지), 이후엔 정정값(stale 방지).
      3. rebalance_date 이전에 상장폐지된 종목 제외 (stock_listing_events 기준)

    report_type: 'FY'|'H1'|'Q1'|'Q3'
    fiscal_year: 지정 시 (ticker, fiscal_year, report_type) 정확 매칭.
    """
    if fiscal_year is None:
        latest_report_sql = """
            SELECT DISTINCT ON (ticker) ticker, year, report_type
            FROM financials_pit
            WHERE available_from <= %s AND report_type = %s
            ORDER BY ticker, year DESC
        """
        latest_report_params: tuple = (rebalance_date, report_type)
    else:
        latest_report_sql = """
            SELECT DISTINCT ON (ticker) ticker, year, report_type
            FROM financials_pit
            WHERE available_from <= %s AND report_type = %s AND year = %s
            ORDER BY ticker
        """
        latest_report_params = (rebalance_date, report_type, fiscal_year)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH latest_report AS ({latest_report_sql}),
            gate_pass AS (
                SELECT lr.ticker
                FROM latest_report lr
                JOIN universe_gate_pit ugp
                  ON lr.ticker = ugp.ticker
                 AND lr.year = ugp.year
                 AND lr.report_type = ugp.report_type
                WHERE CASE
                          WHEN ugp.amendment_from IS NOT NULL
                               AND ugp.amendment_from <= %s
                          THEN ugp.status_amended
                          ELSE ugp.status
                      END = 'PASS'
            )
            SELECT s.ticker
            FROM stocks s
            JOIN gate_pass gp ON s.ticker = gp.ticker
            WHERE s.is_excluded = FALSE
              AND NOT EXISTS (
                SELECT 1 FROM stock_listing_events sle
                WHERE sle.ticker = s.ticker
                  AND sle.delisted_date IS NOT NULL
                  AND sle.delisted_date <= %s
              )
            ORDER BY s.ticker
            """,
            (*latest_report_params, rebalance_date, rebalance_date),
        )
        return [row[0] for row in cur.fetchall()]


def load_pit_by_year(
    conn,
    rebalance_date: date,
    n_years: int = 3,
    report_type: str = 'FY',
) -> dict[str, dict[int, dict]]:
    """
    universe_gate_pit PASS 종목 전체에 대해 rebalance_date 기준 최신 n_years 개
    PIT 데이터를 **연도 키 dict**로 로드 (DEBT-2 — 위치 정렬 버그 방지).

    반환: {ticker: {year: {account_nm: amount}}}
      - available_from <= rebalance_date 조건 (룩어헤드 방지)
      - report_type: 'FY'|'H1'|'Q1'|'Q3'
      - CFS(연결) 우선, OFS(별도) fallback
      - 정정 PIT(amendment_from/original_amount) 규칙은 SQL에서 적용

    load_pit_series_ttm 이 위치가 아니라 명시적 연도로 조회하기 위한 내부 SSOT.
    외부 계약(위치 리스트)은 load_pit_series 가 이 dict를 collapse해 유지한다.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH avail AS (
                SELECT DISTINCT ON (ticker, year)
                    ticker, year, report_type
                FROM financials_pit
                WHERE available_from <= %s AND report_type = %s
                ORDER BY ticker, year DESC, available_from ASC
            ),
            top_n AS (
                SELECT ticker, year, report_type,
                       ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY year DESC) AS rn
                FROM avail
            ),
            selected AS (
                SELECT ticker, year, report_type FROM top_n WHERE rn <= %s
            ),
            -- CFS 우선, OFS fallback (동일 account_nm에서 CFS 선택)
            prioritized AS (
                SELECT f.ticker, f.year, f.account_nm,
                       CASE
                           WHEN f.amendment_from IS NOT NULL AND f.amendment_from <= %s
                           THEN f.amount           -- 정정 공개됨 → 정정값
                           WHEN f.amendment_from IS NOT NULL AND f.original_amount IS NULL
                           THEN NULL               -- 정정 미공개 + 원본 미상 → 사용 불가
                                                   -- (정정값을 쓰면 룩어헤드 — PIT-AMEND-001.
                                                   --  NULL은 파이썬 쪽에서 계정 제외로 처리)
                           WHEN f.original_amount IS NOT NULL
                           THEN f.original_amount  -- 정정 미공개 → 원본값
                           ELSE f.amount           -- 정정 없음
                       END AS effective_amount,
                       ROW_NUMBER() OVER (
                           PARTITION BY f.ticker, f.year, f.account_nm
                           ORDER BY CASE f.fs_div WHEN 'CFS' THEN 1 ELSE 2 END
                       ) AS div_rank
                FROM financials_pit f
                JOIN selected s
                  ON f.ticker = s.ticker AND f.year = s.year AND f.report_type = s.report_type
                WHERE f.available_from <= %s
            )
            SELECT ticker, year, account_nm, effective_amount
            FROM prioritized
            WHERE div_rank = 1
            ORDER BY ticker, year DESC, account_nm
            """,
            (rebalance_date, report_type, n_years, rebalance_date, rebalance_date),
        )
        rows = cur.fetchall()

    # {ticker: {year: {account_nm: amount}}}
    raw: dict[str, dict[int, dict]] = defaultdict(lambda: defaultdict(dict))
    for ticker, year, account_nm, amount in rows:
        if amount is not None:
            raw[ticker][year][account_nm] = float(amount)
    return {ticker: dict(year_dict) for ticker, year_dict in raw.items()}


def load_pit_series(
    conn,
    rebalance_date: date,
    n_years: int = 3,
    report_type: str = 'FY',
) -> dict[str, list[dict]]:
    """
    최신 n_years 개 PIT 데이터를 **위치 리스트**로 로드 (외부 계약 유지).

    반환: {ticker: [현재dict, t-1dict, t-2dict]}  (연도 내림차순, 연도가 부족하면 짧아짐)
    load_pit_by_year 를 collapse한 얇은 뷰다. 계약·PIT 규칙은 load_pit_by_year 참조.
    """
    by_year = load_pit_by_year(conn, rebalance_date, n_years=n_years, report_type=report_type)
    result: dict[str, list[dict]] = {}
    for ticker, year_dict in by_year.items():
        sorted_years = sorted(year_dict.keys(), reverse=True)[:n_years]
        result[ticker] = [year_dict[yr] for yr in sorted_years]
    return result


def load_pit_series_ttm(
    conn,
    rebalance_date: date,
    report_type: str = 'FY',
    fiscal_year: int | None = None,
) -> dict[str, list[dict]]:
    """
    TTM(Trailing Twelve Months) 적용 PIT 시계열 — 외부 계약 [current, prev, prev2].

    **DEBT-2 (§4-2): 위치가 아니라 명시적 사업연도로 조회한다.** 완전 데이터 종목은
    종전과 동일값(QG0 오라클 대상). 중간 연도 결측 종목은 종전의 조용한 오조합
    (예: FY_2021 − H1_2020 + H1_2022) 대신 해당 연도의 TTM 계정을 비운다.
    중간보고서 연율화(H1×2 등)는 전면 폐지 — _make_ttm 참조.

    - FY 리밸런싱(4월): 최신 FY 연도 y_f 앵커. [FY_{y_f}, FY_{y_f-1}, FY_{y_f-2}].
    - H1/Q1/Q3 리밸런싱(§4-1 표): 최신 interim 연도 y_c 앵커.
      [TTM_{y_c}, TTM_{y_c-1}, TTM_{y_c-2}], TTM_y = FY_{y-1} − interim_{y-1} + interim_y
      (IS/CF), BS는 interim_y 스냅샷. Q1/Q3 확장(§6-2, Q-D) — H1과 동일 산식, interim
      report_type만 다르다.
    각 원소는 항상 그 연도에 대응 — 결측 연도는 빈 dict(소비처가 .get으로 흡수).

    fiscal_year (DEBT-3, SPEC_13 §0-A): 지정하면 y_f/y_c를 "가용한 것 중 최신"이
    아니라 **정확히 이 값**으로 고정한다. 목표 연도 보고서가 아직 없는 종목은 그
    자리가 조용히 다른 연도로 대체되지 않고 빈 dict가 된다(late/missing report가
    다른 연도로 둔갑하는 걸 막는다) — 미지정(기본)이면 기존처럼 `max(...)`(가용
    최신 연도)를 쓴다.
    """
    if report_type == 'FY':
        # 갭이 있어도 y_f-2 까지 닿도록 여유분(n_years=4) 확보
        fy_by_year = load_pit_by_year(conn, rebalance_date, n_years=4, report_type='FY')
        result: dict[str, list[dict]] = {}
        for ticker, year_dict in fy_by_year.items():
            y_f = fiscal_year if fiscal_year is not None else max(year_dict)
            result[ticker] = [year_dict.get(y_f - k, {}) for k in range(3)]
        return result

    if report_type not in ('H1', 'Q1', 'Q3'):
        raise ValueError(f'지원하지 않는 report_type: {report_type!r}')

    # H1/Q1/Q3: TTM_{y_c..y_c-2} 를 만들려면 interim 은 y_c..y_c-3, FY 는 y_c-1..y_c-3 필요
    fy_by_year = load_pit_by_year(conn, rebalance_date, n_years=4, report_type='FY')
    interim_by_year = load_pit_by_year(conn, rebalance_date, n_years=4, report_type=report_type)

    result = {}
    for ticker, interim_years in interim_by_year.items():
        y_c = fiscal_year if fiscal_year is not None else max(interim_years)
        fy_years = fy_by_year.get(ticker, {})
        result[ticker] = [
            _make_ttm(fy_years.get(y - 1, {}), interim_years.get(y, {}), interim_years.get(y - 1, {}), ticker)
            for y in (y_c, y_c - 1, y_c - 2)
        ]
    return result


def _make_ttm(fy_prev: dict, interim_curr: dict, interim_prev: dict, ticker: str) -> dict:
    """
    TTM_y = FY_{y-1} − interim_{y-1} + interim_y (IS/CF 계정만). BS 계정은 interim_y
    스냅샷 그대로. interim은 H1/Q1/Q3 중 하나(§4-1, §6-2 Q1/Q3 확장) — 산식은 동일.

    **DEBT-2: 연율화 fallback 전면 폐지.** FY_{y-1}·interim_{y-1}·interim_y 세 값 중
    하나라도 없으면 그 TTM 계정을 만들지 않는다(계정 없음). 종전의 'FY 없으면 H1×2'는
    회계 계절성을 왜곡하는 오류였다(§4-2b). 소비처(필터)가 계정 부재를 흡수한다.
    인자명 fy_prev/interim_curr/interim_prev = 각각 FY_{y-1}/interim_y/interim_{y-1}.
    """
    result = dict(interim_curr)
    for acct in _IS_CF_ACCOUNTS:
        fy_val   = fy_prev.get(acct)
        curr_val = interim_curr.get(acct)
        prev_val = interim_prev.get(acct)
        if fy_val is not None and curr_val is not None and prev_val is not None:
            result[acct] = fy_val - prev_val + curr_val
        else:
            result.pop(acct, None)
    return result
