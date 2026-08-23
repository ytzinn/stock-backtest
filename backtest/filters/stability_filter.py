"""
Step 2 — 재무안정성 필터 (v4.3 신규).

가치 함정(Value Trap) 중 재무 구조적 위험이 명확한 종목을 선제 제거한다.
하드 룰 6개(R1~R6) + 참고 플래그 2개(F1~F2). 하드 룰은 Bayesian 튜닝 대상 제외.
"""
from datetime import date

from backtest.configs.constants import RF, RK  # noqa: F401


class StabilityFilter:
    """UniverseFilter Protocol 구현체. 생성자로 파라미터 주입."""

    _ALL_RULES = frozenset({'R1', 'R2', 'R3', 'R4', 'R5', 'R6'})

    def __init__(
        self,
        r2_exception: bool = True,
        active_rules: set[str] | None = None,
        use_r6:       bool | None = None,
        on_insufficient: dict[str, str] | None = None,
    ):
        """
        active_rules: 활성화할 규칙 집합 (예: {'R1','R2','R3','R4','R5'} — R6 제외).
                      None이면 use_r6로 하위 호환 판단.
        use_r6:       하위 호환 경로. active_rules가 주어지면 무시됨.
        on_insufficient: 규칙별 결측 정책 {'R1':'pass'|'reject', ...}. None 이면
                      CURRENT_INSUFFICIENT_POLICY 를 쓴다 — **판정 함수는 기본값을 주지
                      않지만**, 필터 클래스는 기존 태그 수십 개의 생성 지점을 전부
                      바꾸지 않고도 정책을 한 곳에서 갈아끼울 수 있어야 한다.
                      새 규칙을 짤 때 결정을 강제하는 지점은 `_financial_stability_filter`
                      쪽이다 (거기엔 기본값이 없다).
        """
        self.on_insufficient = dict(on_insufficient or CURRENT_INSUFFICIENT_POLICY)
        self.r2_exception = r2_exception
        if active_rules is not None:
            self.active_rules = frozenset(active_rules)
        elif use_r6 is not None:
            self.active_rules = self._ALL_RULES if use_r6 else (self._ALL_RULES - {'R6'})
        else:
            self.active_rules = self._ALL_RULES
        self.use_r6 = 'R6' in self.active_rules   # 기존 코드 호환용 읽기 속성

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
                t, rebalance_date, pit0, pit1, pit2, self.r2_exception, self.active_rules,
                on_insufficient=self.on_insufficient,
            )
            if ok:
                passed.append(t)
            else:
                rejected[t] = reasons
        return passed, rejected


#: `on_insufficient` 허용값. 'pass' = 판정 불가 시 통과(fail-open), 'reject' = 탈락.
INSUFFICIENT_CHOICES = ('pass', 'reject')

#: **현재 확정된 규칙별 결측 정책.** 기본값으로 쓰라고 둔 것이 아니라, 호출자가
#: 무엇을 넘기고 있는지 한 곳에서 보이게 하려고 둔다 — `_financial_stability_filter`
#: 는 이 값을 **필수 인자**로 요구하고 기본값을 주지 않는다.
#:
#: 지난 여섯 세션의 결함이 전부 한 문장으로 수렴한다 — **없는 것은 오류를 내지 않는다.**
#: 조건문 모양(`and x is not None`) 안에 숨어 있던 fail-open 을 표로 끌어내, 남은
#: 범위가 눈에 보이게 한다. 아래 'pass' 4개가 곧 미해결 범위다 (RULE-SILENT-PASS).
CURRENT_INSUFFICIENT_POLICY: dict[str, str] = {
    'R1': 'pass',      # 자본총계 결측/≤0 → 부채비율 판정 불가
    'R2': 'pass',      # 자본총계 결측/≤0, 또는 차입 계정 자체가 없음(= 무차입 오판)
    'R3': 'reject',    # A-2 (2026-08-22)
    'R4': 'reject',    # A-2 (2026-08-22)
    'R5': 'pass',      # 영업CF·재무CF 결측
    'R6': 'pass',      # 당기순이익·영업CF 결측, 지배지분 ≤0
}

# 차입 성격 계정 — R2 의 단일 정의 (복제 금지)
_BORROW_SPLIT = ('단기차입금', '유동성장기부채', '장기차입금', '사채', '유동성사채')
_LEASE_SPLIT  = ('리스부채', '비유동리스부채')
_LEASE_TOTAL  = '리스부채합계'


def _borrowings(pit: dict) -> float:
    """차입 총액. 계정 부재는 0 으로 읽히므로(= 무차입 판정) 매핑 누락이 곧 오탐이다.

    `[정의 변경 2026-08-16, 사용자 결정]` **리스부채를 포함한다.** IFRS16 이후 리스부채는
    실질적으로 차입인데 종전 정의(차입금 3종 + 사채)가 이를 제외하고 있었다. R2 의 목적이
    '차입 규모 측정' 이므로 정의 쪽이 실질을 놓친 것으로 보고 고쳤다. 표본 150종목에서
    리스부채가 175건으로 차입 계열 전체보다 많아, 이 변경은 R2 판정을 실질적으로 바꾼다.

    총액 태그(`리스부채합계`)는 **분리값이 하나도 없을 때만** 쓴다 — 유동/비유동과 함께
    더하면 중복 계상된다.
    """
    total = sum(pit.get(k, 0) or 0 for k in _BORROW_SPLIT)
    lease_split = [pit.get(k) for k in _LEASE_SPLIT]
    if any(v is not None for v in lease_split):
        total += sum(v or 0 for v in lease_split)
    else:
        total += pit.get(_LEASE_TOTAL, 0) or 0
    return total


def _financial_stability_filter(
    ticker:        str,
    rebalance_date: date,
    pit_data:      dict,
    pit_prev:      dict | None,
    pit_2y_ago:    dict | None,
    r2_exception:  bool = True,
    active_rules:  frozenset[str] = frozenset({'R1', 'R2', 'R3', 'R4', 'R5', 'R6'}),
    *,
    on_insufficient: dict[str, str],
) -> tuple[bool, list[str]]:
    """
    True = 통과. 반환: (pass_flag, fail_reasons)

    pit_data:   pit_series[ticker][0] — 최신 FY
    pit_prev:   pit_series[ticker][1] — t-1 FY
    pit_2y_ago: pit_series[ticker][2] — t-2 FY
    active_rules: 이번 판정에 실제로 적용할 규칙 집합 (leave-one-out 검증용)
    """
    bad = set(on_insufficient) ^ set(CURRENT_INSUFFICIENT_POLICY)
    if bad or any(v not in INSUFFICIENT_CHOICES for v in on_insufficient.values()):
        raise ValueError(
            f'on_insufficient 는 R1~R6 전부에 대해 {INSUFFICIENT_CHOICES} 중 하나여야 한다 '
            f'(누락/초과: {sorted(bad)})'
        )

    fails = []

    def _insufficient(rule: str, why: str) -> None:
        """판정 불가를 정책대로 처리한다. 'pass' 면 아무것도 하지 않는다(종전 동작)."""
        if on_insufficient[rule] == 'reject':
            fails.append(f'{rule} 판정 불가: {why}')

    # ── 하드 룰 (Bayesian 튜닝 제외) ──────────────────────────────────────────

    debt   = pit_data.get('부채총계', 0) or 0
    equity = pit_data.get('자본총계', 0) or 0

    # [R1] 부채비율 > 200%
    # `[정정 2026-08-17]` 종전 주석은 "금융업은 DQ Gate에서 is_financial=TRUE로 이미
    # 제거됨"이었으나 **사실이 아니었다** — DQ Gate 는 그 플래그를 읽지 않는다.
    # 배제는 HardFilter(exclude_financials=True) 몫이다 (GATE-FINANCIAL).
    if 'R1' in active_rules:
        if equity <= 0:
            _insufficient('R1', '자본총계 결측 또는 0 이하')
        elif (debt / equity) > 2.0:
            fails.append('부채비율 > 200%')

    # [R2] 차입금비율 > 150%
    # 예외: 최근 3FY 단조 감소 + 누적 10%p 이상 개선 시 통과
    borrowings = _borrowings(pit_data)

    def _borrow_ratio(pit: dict) -> float | None:
        eq = pit.get('자본총계', 0) or 0
        if eq <= 0:
            return None
        return _borrowings(pit) / eq

    # 차입 계정이 **하나도 없으면** _borrowings 가 0 을 돌려줘 '무차입' 으로 읽힌다.
    # 2026-08 실측에서 유니버스의 44.4% 가 이 경로로 통과했다(한국전력 부채 201조 포함).
    # 계정 부재와 실제 무차입은 다른 상태이므로 분리해 정책에 맡긴다.
    _has_borrow_account = any(
        pit_data.get(k) is not None for k in (*_BORROW_SPLIT, *_LEASE_SPLIT, _LEASE_TOTAL)
    )
    if 'R2' in active_rules:
        if equity <= 0:
            _insufficient('R2', '자본총계 결측 또는 0 이하')
        elif not _has_borrow_account:
            _insufficient('R2', '차입 계정 전무 (무차입과 구분 불가)')
        elif (borrowings / equity) > 1.5:
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
    # `[fail-closed 2026-08-22 — RULE-SILENT-PASS]` 매출 시계열이 2개 미만이면 종전에는
    # 조건문을 건너뛰어 **그 종목이 자동 통과**했다. R2 를 무력화한 것과 같은 구조이고,
    # 모멘텀이 on_insufficient='reject' 로 간 것과 방향을 맞춘다. 판정할 수 없으면 탈락이다.
    rev_series = _revenue_from_pit([pit_2y_ago, pit_prev, pit_data])
    if 'R3' in active_rules:
        if len(rev_series) < 2:
            _insufficient('R3', '매출 시계열 2개 미만')
        else:
            yoy_list = [
                rev_series[i] / rev_series[i - 1] - 1
                for i in range(1, len(rev_series))
                if rev_series[i - 1] != 0
            ]
            if sum(1 for yoy in yoy_list if yoy < -0.05) >= 2:
                fails.append('최근 3FY 내 매출 -5% 이상 역성장 2회 이상')

    # [R4] 영업CF 2년 연속 음수 — 결측은 통과가 아니라 탈락 (위와 동일)
    cfo_cur  = pit_data.get('영업활동현금흐름')
    cfo_prev = pit_prev.get('영업활동현금흐름') if pit_prev else None
    if 'R4' in active_rules:
        if cfo_cur is None or cfo_prev is None:
            _insufficient('R4', '영업CF 2개년 결측')
        elif cfo_cur < 0 and cfo_prev < 0:
            fails.append('영업CF 2년 연속 음수')

    # [R5] 영업CF < 0 AND 재무CF > 0 (차입으로 운영)
    fin_cf = pit_data.get('재무활동현금흐름')
    if 'R5' in active_rules:
        if cfo_cur is None or fin_cf is None:
            _insufficient('R5', '영업CF 또는 재무CF 결측')
        elif cfo_cur < 0 and fin_cf > 0:
            fails.append('영업CF(-) + 재무CF(+): 차입 운영')

    # [R6] adjROE < 요구수익률 r (RIM 기준 가치 파괴 구간)
    # adjROE = (0.5×NI + 0.5×CFO) / equity_rim — Dechow(1994) Method C
    # equity_rim: RIM과 동일하게 지배기업소유주지분 우선, 없으면 자본총계 fallback
    ni = pit_data.get('당기순이익')
    equity_rim = (pit_data.get('지배기업소유주지분')
                  or pit_data.get('지배기업소유주지분_1')
                  or equity)
    if 'R6' in active_rules:
        if ni is None or cfo_cur is None or equity_rim <= 0:
            _insufficient('R6', '당기순이익·영업CF 결측 또는 지배지분 0 이하')
        else:
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
