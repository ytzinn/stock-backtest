"""
Step 2 — 재무안정성 필터 (v4.3 신규).

가치 함정(Value Trap) 중 재무 구조적 위험이 명확한 종목을 선제 제거한다.
하드 룰 6개(R1~R6) + 참고 플래그 2개(F1~F2). 하드 룰은 Bayesian 튜닝 대상 제외.
"""
from datetime import date

RF, RK = 0.0263, 0.0873   # CAPM 상수 — models/rim.py와 동일 값 유지


class StabilityFilter:
    """UniverseFilter Protocol 구현체. 생성자로 파라미터 주입."""

    def __init__(self, r2_exception: bool = True):
        self.r2_exception = r2_exception

    def apply(
        self,
        tickers:        list[str],
        rebalance_date: date,
        pit_series:     dict[str, list[dict]],
        conn,
    ) -> tuple[list[str], dict]:
        passed, rejected = [], {}
        for t in tickers:
            series  = pit_series.get(t, [])
            pit0    = series[0] if len(series) > 0 else {}
            pit1    = series[1] if len(series) > 1 else None
            pit2    = series[2] if len(series) > 2 else None
            ok, reasons = _financial_stability_filter(
                t, rebalance_date, pit0, pit1, pit2, self.r2_exception
            )
            if ok:
                passed.append(t)
            else:
                rejected[t] = reasons
        return passed, rejected


def _financial_stability_filter(
    ticker:        str,
    rebalance_date: date,
    pit_data:      dict,
    pit_prev:      dict | None,
    pit_2y_ago:    dict | None,
    r2_exception:  bool = True,
) -> tuple[bool, list[str]]:
    """
    True = 통과. 반환: (pass_flag, fail_reasons)

    pit_data:   pit_series[ticker][0] — 최신 FY
    pit_prev:   pit_series[ticker][1] — t-1 FY
    pit_2y_ago: pit_series[ticker][2] — t-2 FY
    """
    fails = []

    # ── 하드 룰 (Bayesian 튜닝 제외) ──────────────────────────────────────────

    debt   = pit_data.get('부채총계', 0) or 0
    equity = pit_data.get('자본총계', 0) or 0

    # [R1] 부채비율 > 200%
    # 금융업은 DQ Gate에서 is_financial=TRUE로 이미 제거됨.
    if equity > 0 and (debt / equity) > 2.0:
        fails.append('부채비율 > 200%')

    # [R2] 차입금비율 > 150%
    # 예외: 최근 3FY 단조 감소 + 누적 10%p 이상 개선 시 통과
    borrowings = sum(
        pit_data.get(k, 0) or 0
        for k in ['단기차입금', '유동성장기부채', '장기차입금', '사채']
    )

    def _borrow_ratio(pit: dict) -> float | None:
        eq = pit.get('자본총계', 0) or 0
        if eq <= 0:
            return None
        br = sum(pit.get(k, 0) or 0 for k in ['단기차입금', '유동성장기부채', '장기차입금', '사채'])
        return br / eq

    if equity > 0 and (borrowings / equity) > 1.5:
        trend_ok = False
        if r2_exception:
            available = [p for p in [pit_2y_ago, pit_prev, pit_data] if p is not None]
            br_series = [r for p in available if (r := _borrow_ratio(p)) is not None]
            if len(br_series) >= 2:
                monotonic = all(br_series[i] > br_series[i + 1] for i in range(len(br_series) - 1))
                drop_ok   = (br_series[0] - br_series[-1]) >= 0.10
                trend_ok  = monotonic and drop_ok
        if not trend_ok:
            fails.append('차입금비율 > 150% (개선 추세 없음)')

    # [R3] 매출 역성장 — 최근 3FY 중 2회 이상 YoY < -5%
    rev_series = _revenue_from_pit([pit_2y_ago, pit_prev, pit_data])
    if len(rev_series) >= 2:
        yoy_list = [
            rev_series[i] / rev_series[i - 1] - 1
            for i in range(1, len(rev_series))
            if rev_series[i - 1] != 0
        ]
        if sum(1 for yoy in yoy_list if yoy < -0.05) >= 2:
            fails.append('최근 3FY 내 매출 -5% 이상 역성장 2회 이상')

    # [R4] 영업CF 2년 연속 음수
    cfo_cur  = pit_data.get('영업활동현금흐름')
    cfo_prev = pit_prev.get('영업활동현금흐름') if pit_prev else None
    if cfo_cur is not None and cfo_prev is not None:
        if cfo_cur < 0 and cfo_prev < 0:
            fails.append('영업CF 2년 연속 음수')

    # [R5] 영업CF < 0 AND 재무CF > 0 (차입으로 운영)
    fin_cf = pit_data.get('재무활동현금흐름')
    if cfo_cur is not None and fin_cf is not None:
        if cfo_cur < 0 and fin_cf > 0:
            fails.append('영업CF(-) + 재무CF(+): 차입 운영')

    # [R6] adjROE < 요구수익률 r (RIM 기준 가치 파괴 구간)
    # adjROE = (0.5×NI + 0.5×CFO) / equity_rim — Dechow(1994) Method C
    # equity_rim: RIM과 동일하게 지배기업소유주지분 우선, 없으면 자본총계 fallback
    ni = pit_data.get('당기순이익')
    equity_rim = (pit_data.get('지배기업소유주지분')
                  or pit_data.get('지배기업소유주지분_1')
                  or equity)
    if ni is not None and cfo_cur is not None and equity_rim > 0:
        adj_roe = (0.5 * ni + 0.5 * cfo_cur) / equity_rim
        r       = RF + 1.0 * (RK - RF)   # β=1.0 고정 (Phase 2)
        if adj_roe < r:
            fails.append(f'adjROE({adj_roe:.1%}) < 요구수익률({r:.1%}): RIM 적정가 < 장부가')

    # ── 참고 플래그 (탈락 아닌 기록용) ──────────────────────────────────────────

    rev_cur = pit_data.get('매출액', 0) or 0

    # [F1] 재고자산 회전율 전년 대비 30% 이상 하락
    inv_cur  = pit_data.get('재고자산')
    inv_prev_val = pit_prev.get('재고자산') if pit_prev else None
    if inv_cur and inv_prev_val and inv_prev_val > 0 and rev_cur > 0:
        prev_rev = (pit_prev.get('매출액', 0) or 0) if pit_prev else 0
        t_cur  = rev_cur  / inv_cur       if inv_cur       > 0 else None
        t_prev = prev_rev / inv_prev_val  if inv_prev_val  > 0 else None
        if t_cur and t_prev and t_prev > 0 and (t_cur - t_prev) / t_prev < -0.30:
            pass  # 로깅은 Phase 3 이후

    # [F2] 매출채권 회전율 전년 대비 30% 이상 하락
    ar_cur  = pit_data.get('매출채권')
    ar_prev = pit_prev.get('매출채권') if pit_prev else None
    if ar_cur and ar_prev and ar_prev > 0 and rev_cur > 0:
        prev_rev = (pit_prev.get('매출액', 0) or 0) if pit_prev else 0
        t_cur  = rev_cur  / ar_cur  if ar_cur  > 0 else None
        t_prev = prev_rev / ar_prev if ar_prev > 0 else None
        if t_cur and t_prev and t_prev > 0 and (t_cur - t_prev) / t_prev < -0.30:
            pass  # 로깅은 Phase 3 이후

    return len(fails) == 0, fails


def _revenue_from_pit(pits: list) -> list[float]:
    """pit 리스트(오래된 순)에서 매출액을 추출. None 항목 건너뜀."""
    return [
        p.get('매출액')
        for p in pits
        if p is not None and p.get('매출액') is not None
    ]
