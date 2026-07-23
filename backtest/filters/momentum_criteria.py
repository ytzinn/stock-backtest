"""
SPEC_12 v0.3.1 — 모멘텀 판정 기준 고도화 (신규 배관).

기존 MomentumFilter / _momentum_filter()는 절대 수정하지 않는다 (SPEC_12 §0 규칙 3,
가산형 구현). MADoubleAdapterCriterion만 예외적으로 _momentum_filter()를 그대로
호출한다 (§4-5 배관 양성 대조군 — 산식 재작성 금지).

용어: formation_days=점수 구간, skip_days=형성 구간과 신호일 사이 공백(Family A만
적용, §3-0b). anchor는 KRX 공통 거래일 달력 기준 signal_date (§3-0c) — 종목별
마지막 N개 non-null 행이 아니다 (get_adj_close_range()의 알려진 한계, 회피).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

from backtest.daily_nav import trading_dates
from backtest.filters.momentum_filter import _momentum_filter

log = logging.getLogger(__name__)

DIAG_DIR = Path('experiments/momentum_criteria')


@dataclass(frozen=True)
class CriterionResult:
    passed:          bool
    score:            float | None
    reason_code:      str    # passed_by_signal | passed_insufficient_data |
                              # rejected_by_signal | invalid_data
    n_obs:            int
    data_status:      str    # ok | insufficient | invalid
    zero_ratio:       float | None = None   # sign_count 유동성 교란 진단 (§3-A)
    cutoff_distance:  float | None = None   # 임계값 근접도


@dataclass
class CriterionContext:
    """prepare()가 일괄 조회로 만들어 evaluate()에 넘기는 공유 상태."""
    calendar_anchor: list           # KRX 공통 거래일 달력, 오름차순, signal_date 이하
    prices:           dict          # ticker -> pd.Series(adj_close, index=date)
    suspended:        dict          # ticker -> pd.Series(is_suspended bool, index=date)
    signal_date:      date
    extra:            dict = field(default_factory=dict)


class MomentumCriterion(Protocol):
    name: str

    def prepare(self, tickers: list, signal_date: date, conn) -> CriterionContext: ...
    def evaluate(self, ticker: str, ctx: CriterionContext) -> CriterionResult: ...


# ── 공통 헬퍼 ────────────────────────────────────────────────────────────────

def _calendar_window(conn, signal_date: date, n_back: int, margin_days: int = 30) -> list:
    """signal_date 이하 KRX 공통 거래일을 오름차순으로 최대 n_back개 반환 (§3-0c).

    trading_dates()는 (start, end] 구간 — 룩어헤드 없음(§0 규칙 2).
    margin_days: 공휴일 보정 여유 캘린더일 (n_back의 ~1.6배 + margin이 기본 lookback).
    """
    lookback_calendar_days = int(n_back * 1.6) + margin_days
    start = signal_date - timedelta(days=lookback_calendar_days)
    all_dates = trading_dates(conn, start, signal_date)
    return all_dates[-n_back:] if len(all_dates) >= n_back else all_dates


def _fetch_price_panel(conn, tickers: list, start: date, end: date) -> dict:
    """[start, end] 구간 tickers의 adj_close/is_suspended 일괄 조회 (§4-1 성능).

    반환: ticker -> {'adj_close': Series, 'is_suspended': Series} (index=date).
    is_suspended가 채워져 있으면(2026-07-23 MC-1 확인: ingest에서 실제로 채워짐,
    거래정지일도 행 누락 없이 기록) 정지일을 명시적으로 구분할 수 있다.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ticker, date, adj_close, is_suspended
            FROM price_history
            WHERE ticker = ANY(%s) AND date > %s AND date <= %s
            ORDER BY ticker, date
            """,
            (tickers, start, end),
        )
        rows = cur.fetchall()
    grouped: dict = {}
    for ticker, d, adj_close, is_susp in rows:
        grouped.setdefault(ticker, []).append((d, adj_close, is_susp))
    panels = {}
    for ticker, recs in grouped.items():
        idx = [r[0] for r in recs]
        panels[ticker] = {
            'adj_close':    pd.Series([r[1] for r in recs], index=idx, dtype=float),
            'is_suspended': pd.Series([bool(r[2]) for r in recs], index=idx, dtype=bool),
        }
    return panels


def _classify_gap(px: 'pd.Series | None', window_dates: list, max_invalid_frac: float = 0.1):
    """window_dates 구간의 가격 결손을 분류. 결손 없으면 None(정상 진행).

    신규상장 등으로 종목 이력 자체가 창 시작일보다 늦게 시작하면 §4-4의
    `insufficient`(정상적 이력 부족 — 통과)로, 이력이 창 전체를 덮는데도 중간에
    행이 빠지면 `invalid`(§4-4 — 조용히 통과시키지 않는다)로 구분한다.
    (2026-07-23 서버 실측: 이 구분 없이는 신규상장 종목이 전부 invalid로
    오분류됨 — 41~150일치 이력뿐인 종목이 200일 MA 창에서 결손 다수로 잡힘.)
    """
    if px is None or len(px.index) == 0:
        return CriterionResult(True, None, 'passed_insufficient_data', 0, 'insufficient')
    window = px.reindex(window_dates)
    n_missing = int(window.isna().sum())
    if n_missing == 0:
        return None
    n_obs = len(window_dates) - n_missing
    if px.index.min() > window_dates[0]:
        return CriterionResult(True, None, 'passed_insufficient_data', n_obs, 'insufficient')
    if n_missing > len(window_dates) * max_invalid_frac:
        return CriterionResult(True, None, 'invalid_data', n_obs, 'invalid')
    return None


def _prepare_price_context(tickers: list, signal_date: date, conn, n_back: int) -> CriterionContext:
    calendar = _calendar_window(conn, signal_date, n_back)
    if not calendar:
        return CriterionContext(calendar_anchor=[], prices={}, suspended={}, signal_date=signal_date)
    lookback_calendar_days = int(n_back * 1.6) + 30
    panels = _fetch_price_panel(
        conn, tickers, signal_date - timedelta(days=lookback_calendar_days), signal_date,
    )
    return CriterionContext(
        calendar_anchor=calendar,
        prices={t: p['adj_close'] for t, p in panels.items()},
        suspended={t: p['is_suspended'] for t, p in panels.items()},
        signal_date=signal_date,
    )


# ── Family A — 절대 수익률 계열 (§3-0a, skip 적용) ───────────────────────────

class AbsReturnCriterion:
    """A-1 abs_return(크기). formation_return >= threshold 통과."""
    name = 'abs_return'

    def __init__(self, formation_days: int = 126, skip_days: int = 21, threshold: float = 0.0):
        self.formation_days = formation_days
        self.skip_days = skip_days
        self.threshold = threshold

    def prepare(self, tickers, signal_date, conn) -> CriterionContext:
        need = self.formation_days + self.skip_days + 1
        return _prepare_price_context(tickers, signal_date, conn, need)

    def evaluate(self, ticker, ctx: CriterionContext) -> CriterionResult:
        need = self.formation_days + self.skip_days + 1
        cal = ctx.calendar_anchor
        if len(cal) < need:
            return CriterionResult(True, None, 'passed_insufficient_data', len(cal), 'insufficient')
        pos_end = len(cal) - 1 - self.skip_days
        pos_start = pos_end - self.formation_days
        if pos_start < 0:
            return CriterionResult(True, None, 'passed_insufficient_data', len(cal), 'insufficient')
        end_date, start_date = cal[pos_end], cal[pos_start]

        px = ctx.prices.get(ticker)
        gap = _classify_gap(px, [start_date, end_date])
        if gap is not None:
            return gap
        p_end, p_start = px.loc[end_date], px.loc[start_date]
        if pd.isna(p_end) or pd.isna(p_start) or p_start <= 0:
            return CriterionResult(True, None, 'invalid_data', len(px), 'invalid')

        formation_return = float(p_end / p_start - 1)
        passed = formation_return >= self.threshold
        reason = 'passed_by_signal' if passed else 'rejected_by_signal'
        return CriterionResult(
            passed, formation_return, reason, need, 'ok',
            cutoff_distance=formation_return - self.threshold,
        )


class SignCountCriterion:
    """A-2 sign_count(비모수 부호). 0%는 0.5 가중, 거래정지일 제외 (§3-A, v0.3 확정)."""
    name = 'sign_count'

    def __init__(self, formation_days: int = 126, skip_days: int = 21):
        self.formation_days = formation_days
        self.skip_days = skip_days

    def prepare(self, tickers, signal_date, conn) -> CriterionContext:
        need = self.formation_days + self.skip_days + 1
        return _prepare_price_context(tickers, signal_date, conn, need)

    def evaluate(self, ticker, ctx: CriterionContext) -> CriterionResult:
        need = self.formation_days + self.skip_days + 1
        cal = ctx.calendar_anchor
        if len(cal) < need:
            return CriterionResult(True, None, 'passed_insufficient_data', len(cal), 'insufficient')
        pos_end = len(cal) - 1 - self.skip_days
        pos_start = pos_end - self.formation_days
        if pos_start < 0:
            return CriterionResult(True, None, 'passed_insufficient_data', len(cal), 'insufficient')
        window_dates = cal[pos_start:pos_end + 1]   # formation_days+1개 날짜(수익률 formation_days개)

        px = ctx.prices.get(ticker)
        susp = ctx.suspended.get(ticker)
        gap = _classify_gap(px, window_dates, max_invalid_frac=0.5)
        if gap is not None:
            return gap
        closes = px.reindex(window_dates)

        susp_flags = (susp.reindex(window_dates).fillna(False).astype(bool)
                      if susp is not None else pd.Series(False, index=window_dates))
        daily_ret = closes.pct_change().dropna()
        susp_on_ret_day = susp_flags.reindex(daily_ret.index).fillna(False).astype(bool)
        valid_ret = daily_ret[~susp_on_ret_day]   # 거래정지일 → 점수 계산에서 제외

        n_valid = len(valid_ret)
        if n_valid < self.formation_days * 0.9:
            return CriterionResult(True, None, 'passed_insufficient_data', n_valid, 'insufficient')

        n_pos = int((valid_ret > 0).sum())
        n_zero = int((valid_ret == 0).sum())
        score = (n_pos + 0.5 * n_zero) / n_valid
        zero_ratio = n_zero / n_valid
        passed = score >= 0.5
        reason = 'passed_by_signal' if passed else 'rejected_by_signal'
        return CriterionResult(
            passed, score, reason, n_valid, 'ok',
            zero_ratio=zero_ratio, cutoff_distance=score - 0.5,
        )


# ── Family B — 이동평균 추세 (skip 미적용, §3-0b) ────────────────────────────

class MA200Criterion:
    """B1 price < MA200 제외 (primary)."""
    name = 'ma200'

    def __init__(self, ma_window: int = 200):
        self.ma_window = ma_window

    def prepare(self, tickers, signal_date, conn) -> CriterionContext:
        return _prepare_price_context(tickers, signal_date, conn, self.ma_window)

    def evaluate(self, ticker, ctx: CriterionContext) -> CriterionResult:
        cal = ctx.calendar_anchor
        if len(cal) < self.ma_window:
            return CriterionResult(True, None, 'passed_insufficient_data', len(cal), 'insufficient')
        window_dates = cal[-self.ma_window:]

        px = ctx.prices.get(ticker)
        gap = _classify_gap(px, window_dates)
        if gap is not None:
            return gap
        window = px.reindex(window_dates)
        n_obs = int(window.notna().sum())

        ma = float(window.dropna().mean())
        last_price = window.iloc[-1]
        if pd.isna(last_price) or ma <= 0:
            return CriterionResult(True, None, 'invalid_data', n_obs, 'invalid')

        score = float(last_price / ma - 1)
        passed = score >= 0.0
        reason = 'passed_by_signal' if passed else 'rejected_by_signal'
        return CriterionResult(passed, score, reason, n_obs, 'ok', cutoff_distance=score)


class Week52HighCriterion:
    """C 52주 신고가 근접도. pth = P[t]/max(최근 252거래일 종가) < threshold 제외."""
    name = '52w_high'

    def __init__(self, window: int = 252, threshold: float = 0.75):
        self.window = window
        self.threshold = threshold

    def prepare(self, tickers, signal_date, conn) -> CriterionContext:
        return _prepare_price_context(tickers, signal_date, conn, self.window)

    def evaluate(self, ticker, ctx: CriterionContext) -> CriterionResult:
        cal = ctx.calendar_anchor
        if len(cal) < self.window:
            return CriterionResult(True, None, 'passed_insufficient_data', len(cal), 'insufficient')
        window_dates = cal[-self.window:]

        px = ctx.prices.get(ticker)
        gap = _classify_gap(px, window_dates)
        if gap is not None:
            return gap
        window = px.reindex(window_dates)
        n_obs = int(window.notna().sum())

        high = float(window.dropna().max())
        last_price = window.iloc[-1]
        if pd.isna(last_price) or high <= 0:
            return CriterionResult(True, None, 'invalid_data', n_obs, 'invalid')

        pth = float(last_price / high)
        passed = pth >= self.threshold
        reason = 'passed_by_signal' if passed else 'rejected_by_signal'
        return CriterionResult(
            passed, pth, reason, n_obs, 'ok', cutoff_distance=pth - self.threshold,
        )


# ── Family D — 시장잔차 추세, Blitz식 부분합 (§3-D1) ─────────────────────────

class MarketResidualCriterion:
    """`market_residual_trend_126` — 절편 포함 OLS를 beta_window(기본 252일)에 적합하고,
    점수는 그 창의 최근 formation_days(기본 126일) 부분합(전체 합은 항상 0이므로
    부분집합이어야 0이 아니다, §3-D0). coverage gate 사전 확인(2026-07-23, 평균
    97.1%) 후 구현.

    벤치마크는 동결 CSV(run_daily_nav.py가 생성하는 benchmarks_daily.csv, §7 MC-5) —
    매 실행 FDR 재조회 금지. 시장구분은 stocks.market(현재값) 사용 — market_transfer
    이벤트의 PIT 정합성은 미해결 `[VERIFY]`이나, 2026-07-23 확인 시 DB에 해당 이벤트가
    0건이라 현재 데이터셋에서는 실무 영향이 낮다.
    """
    name = 'market_residual_blitz_subset'

    def __init__(
        self,
        beta_window:     int = 252,
        formation_days:  int = 126,
        skip_days:       int = 21,
        benchmarks_csv:  str = 'experiments/daily_nav/benchmarks_daily.csv',
        standardize:     bool = False,
    ):
        self.beta_window = beta_window
        self.formation_days = formation_days
        self.skip_days = skip_days
        self.standardize = standardize
        self._benchmarks_csv = benchmarks_csv
        self._benchmarks = None

    def _load_benchmarks(self) -> pd.DataFrame:
        if self._benchmarks is None:
            path = Path(self._benchmarks_csv)
            if not path.exists():
                raise FileNotFoundError(
                    f'{path} 없음 — scripts.run_daily_nav 먼저 실행해 동결 벤치마크'
                    f' 생성 필요 (SPEC_12 §7 MC-5). 매 실행 FDR 재조회는 금지.'
                )
            self._benchmarks = pd.read_csv(path, index_col=0, parse_dates=True)
        return self._benchmarks

    def prepare(self, tickers, signal_date, conn) -> CriterionContext:
        need = self.beta_window + self.skip_days
        ctx = _prepare_price_context(tickers, signal_date, conn, need)
        if not ctx.calendar_anchor:
            return ctx

        with conn.cursor() as cur:
            cur.execute("SELECT ticker, market FROM stocks WHERE ticker = ANY(%s)", (tickers,))
            market_map = dict(cur.fetchall())

        bench = self._load_benchmarks()
        cal_idx = pd.to_datetime(ctx.calendar_anchor)
        bench_ret = {}
        for col, mkt in [('kospi', 'KOSPI'), ('kosdaq', 'KOSDAQ')]:
            if col not in bench.columns:
                raise ValueError(f'{self._benchmarks_csv}에 {col!r} 컬럼 없음 — 스키마 확인 필요')
            s = bench[col].reindex(cal_idx).astype(float)
            bench_ret[mkt] = s.pct_change()

        ctx.extra['market_map'] = market_map
        ctx.extra['bench_ret'] = bench_ret
        return ctx

    def evaluate(self, ticker, ctx: CriterionContext) -> CriterionResult:
        need = self.beta_window + self.skip_days
        cal = ctx.calendar_anchor
        if len(cal) < need:
            return CriterionResult(True, None, 'passed_insufficient_data', len(cal), 'insufficient')
        pos_end = len(cal) - 1 - self.skip_days
        pos_start = pos_end - self.beta_window
        if pos_start < 0:
            return CriterionResult(True, None, 'passed_insufficient_data', len(cal), 'insufficient')
        window_dates = cal[pos_start:pos_end + 1]   # beta_window+1개 날짜(수익률 beta_window개)

        px = ctx.prices.get(ticker)
        gap = _classify_gap(px, window_dates)
        if gap is not None:
            return gap

        market = ctx.extra['market_map'].get(ticker)
        if market not in ('KOSPI', 'KOSDAQ'):
            return CriterionResult(True, None, 'invalid_data', 0, 'invalid')
        mkt_ret_full = ctx.extra['bench_ret'][market]

        closes = px.reindex(window_dates)
        stock_ret = closes.pct_change().dropna()
        mkt_ret = mkt_ret_full.reindex(stock_ret.index)
        valid = stock_ret.notna() & mkt_ret.notna()
        stock_ret, mkt_ret = stock_ret[valid], mkt_ret[valid]

        if len(stock_ret) < self.beta_window * 0.9:
            return CriterionResult(True, None, 'passed_insufficient_data', len(stock_ret), 'insufficient')
        if mkt_ret.empty or mkt_ret.std() == 0:
            return CriterionResult(True, None, 'invalid_data', len(stock_ret), 'invalid')

        x, y = mkt_ret.to_numpy(), stock_ret.to_numpy()
        beta, alpha = np.polyfit(x, y, 1)   # 절편 포함 OLS 고정(§3-D4)
        resid = y - (alpha + beta * x)      # 전체 합은 기계 오차 수준으로 0 (§3-D0)

        n_form = min(self.formation_days, len(resid))
        score = float(resid[-n_form:].sum())
        if self.standardize:
            sd = float(np.std(resid, ddof=2)) if len(resid) > 2 else 0.0
            if sd > 0:
                score = score / sd

        passed = score >= 0.0
        reason = 'passed_by_signal' if passed else 'rejected_by_signal'
        return CriterionResult(passed, score, reason, len(resid), 'ok', cutoff_distance=score)


# ── §4-5 배관 양성 대조군 — 기존 _momentum_filter() 어댑터 ────────────────────

class MADoubleAdapterCriterion:
    """F_pbr_ma_double_adapter 전용. 산식은 기존 _momentum_filter() 그대로 호출
    (재작성 금지) — 신규 배관(prepare→evaluate→stats_key→tape)만 검증한다."""
    name = 'ma_double_adapter'

    def __init__(self, ma_short: int = 20, ma_long: int = 60,
                 confirm_days: int = 5, slope_lookback: int = 20):
        self.ma_short = ma_short
        self.ma_long = ma_long
        self.confirm_days = confirm_days
        self.slope_lookback = slope_lookback

    def prepare(self, tickers, signal_date, conn) -> CriterionContext:
        # _momentum_filter()가 conn으로 직접 조회하므로 배치 컨텍스트가 필요 없다.
        return CriterionContext(calendar_anchor=[], prices={}, suspended={},
                                signal_date=signal_date, extra={'conn': conn})

    def evaluate(self, ticker, ctx: CriterionContext) -> CriterionResult:
        conn = ctx.extra['conn']
        passed = _momentum_filter(
            ticker, ctx.signal_date, conn,
            self.ma_short, self.ma_long, self.confirm_days, self.slope_lookback,
        )
        reason = 'passed_by_signal' if passed else 'rejected_by_signal'
        return CriterionResult(passed, None, reason, 0, 'ok')


CRITERION_REGISTRY = {
    'abs_return':        AbsReturnCriterion,
    'sign_count':        SignCountCriterion,
    'ma200':              MA200Criterion,
    '52w_high':           Week52HighCriterion,
    'ma_double_adapter':  MADoubleAdapterCriterion,
    'market_residual_blitz_subset': MarketResidualCriterion,
}


# ── UniverseFilter 어댑터 (§4-1) ─────────────────────────────────────────────

class MomentumCriterionFilter:
    """UniverseFilter Protocol 구현체. stats 키는 기존 tape 호환을 위해 항상
    'MomentumFilter'로 위장한다 — export_portfolios.py가 이 키만 조회하기 때문
    (2026-07-23 MC-1 코드 확인). 진단은 last_diagnostics + 별도 JSON 파일 이중 기록."""

    stats_key = 'MomentumFilter'

    def __init__(self, criterion: MomentumCriterion, tag: str, legacy_adapter: bool = False):
        self.criterion = criterion
        self.tag = tag
        self.legacy_adapter = legacy_adapter
        self.last_diagnostics: dict = {}

    def apply(self, tickers, rebalance_date, pit_series, conn):
        ctx = self.criterion.prepare(tickers, rebalance_date, conn)
        passed, rejected, diagnostics = [], {}, {}
        for ticker in tickers:
            result = self.criterion.evaluate(ticker, ctx)
            diagnostics[ticker] = result
            if result.passed:
                passed.append(ticker)
            else:
                rejected[ticker] = f'{self.criterion.name}: {result.reason_code} (score={result.score})'
        self.last_diagnostics = diagnostics
        self._write_diagnostics(rebalance_date, diagnostics)
        return passed, rejected

    def _write_diagnostics(self, rebalance_date, diagnostics: dict) -> None:
        DIAG_DIR.mkdir(parents=True, exist_ok=True)
        summary_path = DIAG_DIR / f'{self.tag}_diagnostics_summary.json'
        status_counts: dict = {}
        for r in diagnostics.values():
            status_counts[r.data_status] = status_counts.get(r.data_status, 0) + 1
        record = {
            'rebalance_date':      rebalance_date.isoformat(),
            'implementation':      'MomentumCriterionFilter',
            'criterion':           self.criterion.name,
            'legacy_adapter':      self.legacy_adapter,
            'n_passed':            sum(1 for r in diagnostics.values() if r.passed),
            'n_rejected':          sum(1 for r in diagnostics.values() if not r.passed),
            'data_status_counts':  status_counts,
        }
        existing = []
        if summary_path.exists():
            try:
                existing = json.loads(summary_path.read_text(encoding='utf-8'))
            except json.JSONDecodeError:
                log.warning(f'[{self.tag}] 진단 파일 손상 — 새로 시작: {summary_path}')
                existing = []
        existing.append(record)
        summary_path.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2, default=str), encoding='utf-8',
        )


def build_momentum_criterion_filter(config: dict) -> MomentumCriterionFilter:
    """§4-2 fail-fast 팩토리. config: {'type': str, 'tag': str, **criterion_kwargs}.

    알 수 없는 criterion / 소비되지 않는 파라미터는 즉시 예외 (ghost parameter 금지).
    """
    config = dict(config)
    ctype = config.pop('type', None)
    tag = config.pop('tag', None)
    if ctype is None:
        raise ValueError("momentum_criterion.type 필수 (예: 'abs_return')")
    if tag is None:
        raise ValueError("momentum_criterion.tag 필수 — 진단 파일명에 사용")
    cls = CRITERION_REGISTRY.get(ctype)
    if cls is None:
        raise ValueError(f"알 수 없는 momentum criterion: {ctype!r} — 등록: {list(CRITERION_REGISTRY)}")

    import inspect
    valid_params = set(inspect.signature(cls.__init__).parameters) - {'self'}
    unknown = set(config) - valid_params
    if unknown:
        raise ValueError(f"{ctype} criterion이 소비하지 않는 파라미터: {unknown}")

    criterion = cls(**config)
    return MomentumCriterionFilter(
        criterion=criterion, tag=tag, legacy_adapter=(ctype == 'ma_double_adapter'),
    )
