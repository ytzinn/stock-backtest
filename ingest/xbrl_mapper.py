"""
DART XBRL 인스턴스 문서 파싱 + IFRS 계정명 → 표준 account_nm 매핑.

주요 진입점:
    parse_xbrl_zip(zip_bytes)  → list[XbrlEntry]
    auto_scale(entries, db_amounts) → int   # 단위 스케일 자동 감지 (1 / 1_000 / 1_000_000)
    entries_to_amounts(entries, scale) → dict[(account_nm, fs_div, period_type), float]

단위 처리:
    XBRL 값은 항상 원(won) 단위이나, 기업마다 공시 스케일이 다름.
    (예: 대기업 → 백만원 스케일 XBRL = DB 값 ×1_000_000 차이 가능)
    auto_scale()이 자산총계·부채총계 등 안정적 계정과 DB 값을 비교해 배수를 감지한다.
    배수가 감지되지 않으면 1로 기본 설정 (단위 불일치 경고 로그 출력).
"""
from __future__ import annotations

import io
import logging
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass

log = logging.getLogger(__name__)

# ── IFRS taxonomy local-name → 표준 account_nm ────────────────────────────────
# IFRS-full 및 DART 전용 XBRL 태그명을 dart_ingest.py의 ACCOUNT_ALIASES와 동일 계정명으로 매핑.
# 동일 account_nm에 여러 local-name이 가리키는 경우: 먼저 등장하는 것 우선 (parse 결과에서 dedup).
XBRL_TO_ACCOUNT: dict[str, str] = {
    # 손익계산서 (IS)
    'Revenue':                                          '매출액',
    'RevenueFromContractsWithCustomers':                '매출액',
    'SalesRevenue':                                     '매출액',       # DART 전용
    'GrossProfit':                                      '매출총이익',
    'ProfitLossFromOperatingActivities':                '영업이익',
    'OperatingIncomeLoss':                              '영업이익',
    'OperatingIncome':                                  '영업이익',
    'ProfitLoss':                                       '당기순이익',
    'NetIncome':                                        '당기순이익',
    'ProfitLossAttributableToOwnersOfParent':           '지배기업소유주지분당기순이익',
    'IncomeTaxExpenseContinuingOperations':             '법인세비용',
    'FinanceCosts':                                     '금융비용',
    'FinanceIncome':                                    '금융수익',
    'OtherIncome':                                      '기타수익',
    'OtherExpense':                                     '기타비용',
    'DepreciationAndAmortisationExpense':               '감가상각비',
    # 재무상태표 (BS)
    'Assets':                                           '자산총계',
    'Liabilities':                                      '부채총계',
    'Equity':                                           '자본총계',
    'EquityAttributableToOwnersOfParent':               '지배기업소유주지분',
    'NoncontrollingInterests':                          '비지배지분',
    'CurrentAssets':                                    '유동자산',
    'NoncurrentAssets':                                 '비유동자산',
    'CurrentLiabilities':                               '유동부채',
    'NoncurrentLiabilities':                            '비유동부채',
    'CashAndCashEquivalents':                           '현금및현금성자산',
    'TradeAndOtherCurrentReceivables':                  '매출채권및기타유동채권',
    'Inventories':                                      '재고자산',
    'PropertyPlantAndEquipment':                        '유형자산',
    'IntangibleAssetsAndGoodwill':                      '무형자산',
    'IntangibleAssets':                                 '무형자산',
    'Goodwill':                                         '영업권',
    'TradeAndOtherPayables':                            '매입채무및기타채무',
    'BorrowingsCurrentAndNoncurrent':                   '차입금합계',
    # ── 차입 계열 (R2 입력) ──────────────────────────────────────────────────
    # R2 는 `sum(pit.get(k, 0) or 0 ...)` 라 **계정이 없으면 0 = 무차입**으로 읽고
    # 무조건 통과시킨다. 한글 별칭이 네 개뿐이라 표기가 조금만 달라도 차입이 통째로
    # 사라졌다 — 2026-08-16 실측: 게이트 통과 유니버스의 44.4%(2025-08)가 차입금 계정
    # 전무였고, 그중 13.3%는 부채비율 100% 초과였다(코다코 793억 차입이 0으로 잡힘).
    # **유동/비유동을 반드시 다른 표준명에 매핑한다.** 같은 이름에 넣으면
    # `ON CONFLICT DO UPDATE` 로 뒤엣것이 앞엣것을 덮어써 절반이 날아간다.
    'ShorttermBorrowings':                              '단기차입금',
    'CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings': '단기차입금',
    'CurrentLoansReceivedAndCurrentPortionOfNoncurrentLoansReceived': '단기차입금',
    'CurrentPortionOfLongtermBorrowings':               '유동성장기부채',
    'LongtermBorrowings':                               '장기차입금',
    'LongTermBorrowingsGross':                          '장기차입금',
    'NoncurrentPortionOfNoncurrentLoansReceived':       '장기차입금',
    'NoncurrentPortionOfOtherNoncurrentBorrowings':     '장기차입금',
    # 사채 계열 — 전환·교환·신주인수권부사채는 현행 정의의 '사채' 에 해당한다.
    # 유동/비유동을 갈라야 서로 덮어쓰지 않는다.
    'ConvertibleBonds':                                 '사채',
    'ExchangeableBonds':                                '사채',
    'CurrentPortionOfConvertibleBonds':                 '유동성사채',
    'CurrentPortionOfExchangeableBond':                 '유동성사채',
    'CurrentPortionOfBondWithWarrant':                  '유동성사채',
    # ── 리스부채 (R2 정의 확장, 2026-08-16 사용자 결정) ──────────────────────
    # IFRS16 이후 리스부채는 실질적으로 차입이다. 종전 R2 정의(차입금 3종 + 사채)는
    # 이를 제외했는데, R2 의 목적이 '차입 규모 측정' 이므로 정의 쪽이 실질을 놓친
    # 것으로 보고 포함하기로 했다. 표본 150종목에서 리스부채가 175건으로 차입 계열
    # 전체보다 많다 — 즉 이 결정이 R2 판정을 실질적으로 바꾼다.
    # 총액 태그(LeaseLiabilities)는 별도 이름으로 둔다 — 유동/비유동과 함께 더하면
    # 중복 계상되므로, 소비처가 '분리값이 없을 때만' 쓰도록 한다.
    'CurrentLeaseLiabilities':                          '리스부채',
    'CurentPortionOfFinanceLeaseLiabilities':           '리스부채',
    'NoncurrentLeaseLiabilities':                       '비유동리스부채',
    'NonCurrentFinanceLeaseLiabilities':                '비유동리스부채',
    'LeaseLiabilities':                                 '리스부채합계',
    'IssuedCapital':                                    '자본금',
    'RetainedEarnings':                                 '이익잉여금',
    'OtherEquity':                                      '기타자본',
    # 현금흐름표 (CF)
    'CashFlowsFromUsedInOperatingActivities':           '영업활동현금흐름',
    'CashFlowsFromUsedInInvestingActivities':           '투자활동현금흐름',
    'CashFlowsFromUsedInFinancingActivities':           '재무활동현금흐름',
    'IncreaseDecreaseInCashAndCashEquivalents':         '현금및현금성자산순증감',
    'PurchaseOfPropertyPlantAndEquipment':              '유형자산취득',
    'DividendsPaid':                                    '배당금지급',
    'InterestPaid':                                     '이자지급',
    'IncomeTaxesPaidRefund':                            '법인세납부',
    # EPS
    'BasicEarningsLossPerShare':                        '기본주당순이익(손실)',
    'DilutedEarningsLossPerShare':                      '희석주당순이익(손실)',
}

# BS 계정 (재무상태표) — instant context(기말 시점) 우선 선택에 사용
_BS_ACCOUNTS = {
    '자산총계', '부채총계', '자본총계', '지배기업소유주지분', '비지배지분',
    '유동자산', '비유동자산', '유동부채', '비유동부채',
    '현금및현금성자산', '재고자산', '유형자산', '무형자산',
    '이익잉여금', '자본금', '기타자본',
    '단기차입금', '유동성장기부채', '장기차입금', '사채', '유동성사채',
    '리스부채', '비유동리스부채', '리스부채합계',
}

# 단위 스케일 자동 감지에 사용할 안정적 기준 계정
_SCALE_REF_ACCOUNTS = ['자산총계', '부채총계', '자본총계']


@dataclass(frozen=True)
class XbrlEntry:
    account_nm:  str    # 표준 account_nm (XBRL_TO_ACCOUNT 매핑 결과)
    xbrl_local:  str    # 원본 XBRL local name (디버그용)
    fs_div:      str    # 'CFS' | 'OFS' | 'UNKNOWN'
    period_type: str    # 'current' | 'prior'
    amount:      float
    decimals:    int | None  # XBRL decimals 속성 (-3=천원 정밀도, -6=백만원 정밀도 등)


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

def _split_tag(tag: str) -> tuple[str, str]:
    if tag.startswith('{'):
        ns, local = tag[1:].split('}', 1)
        return ns, local
    return '', tag


def _parse_contexts(root: ET.Element) -> tuple[dict[str, dict], str | None, str | None]:
    """
    context 요소들을 파싱해 {id → meta_dict} 반환.
    meta_dict = {fs_div, period_date, is_instant}
    당기/전기 기준일도 함께 반환.
    """
    FS_DIMENSION        = 'ConsolidatedAndSeparateFinancialStatementsAxis'
    CONSOLIDATED_MEMBER = 'ConsolidatedMember'
    SEPARATE_MEMBER     = 'SeparateMember'

    contexts: dict[str, dict] = {}
    for elem in root.iter():
        _, local = _split_tag(elem.tag)
        if local != 'context':
            continue
        ctx_id = elem.get('id', '')
        if not ctx_id:
            continue

        fs_div = 'UNKNOWN'
        period_date = ''
        is_instant = False

        for child in elem.iter():
            _, child_local = _split_tag(child.tag)
            if child_local == 'explicitMember':
                dim = child.get('dimension', '')
                val = (child.text or '').strip()
                if FS_DIMENSION in dim:
                    if CONSOLIDATED_MEMBER in val:
                        fs_div = 'CFS'
                    elif SEPARATE_MEMBER in val:
                        fs_div = 'OFS'
            elif child_local == 'instant':
                period_date = (child.text or '').strip()
                is_instant = True
            elif child_local == 'endDate':
                if not is_instant:
                    period_date = (child.text or '').strip()

        contexts[ctx_id] = {
            'fs_div':     fs_div,
            'period_date': period_date,
            'is_instant':  is_instant,
        }

    # 가장 늦은 날짜 = 당기, 두 번째 = 전기
    all_dates = sorted(
        {c['period_date'] for c in contexts.values() if c.get('period_date')},
        reverse=True,
    )
    current_date = all_dates[0] if len(all_dates) >= 1 else None
    prior_date   = all_dates[1] if len(all_dates) >= 2 else None
    return contexts, current_date, prior_date


def _resolve_period(ctx: dict, current_date: str | None, prior_date: str | None) -> str | None:
    pd = ctx.get('period_date', '')
    if pd == current_date:
        return 'current'
    if pd == prior_date:
        return 'prior'
    return None


# ── 공개 API ──────────────────────────────────────────────────────────────────

def parse_xbrl_zip(zip_bytes: bytes) -> list[XbrlEntry]:
    """
    XBRL ZIP 바이트를 파싱해 XbrlEntry 목록 반환.

    - context → CFS/OFS + current/prior 분류
    - XBRL_TO_ACCOUNT 매핑으로 표준 account_nm 변환
    - 동일 (account_nm, fs_div, period_type) 조합: 절댓값이 큰 것 우선 (단, BS 계정은 instant 우선)
    - 매핑 없는 XBRL 태그는 무시 (디버그 레벨 로그)
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        xbrl_files = [n for n in zf.namelist() if n.lower().endswith('.xbrl')]
        if not xbrl_files:
            log.debug('parse_xbrl_zip: ZIP에 .xbrl 파일 없음')
            return []
        # 여러 .xbrl가 있으면 가장 큰 파일(인스턴스 문서) 선택
        xbrl_name = max(xbrl_files, key=lambda n: zf.getinfo(n).file_size)
        try:
            root = ET.fromstring(zf.read(xbrl_name))
        except ET.ParseError as e:
            log.warning(f'parse_xbrl_zip: XML 파싱 실패 — {e}')
            return []

    contexts, current_date, prior_date = _parse_contexts(root)
    if not contexts:
        log.debug('parse_xbrl_zip: context 요소 없음')
        return []

    # (account_nm, fs_div, period_type) → 후보 목록 (is_instant, amount, decimals, xbrl_local)
    candidates: dict[tuple[str, str, str], list[tuple[bool, float, int | None, str]]] = {}

    for elem in root.iter():
        ctx_ref = elem.get('contextRef')
        if ctx_ref is None:
            continue  # context 참조 없으면 메타 요소

        ctx = contexts.get(ctx_ref)
        if ctx is None:
            continue

        period = _resolve_period(ctx, current_date, prior_date)
        if period is None:
            continue  # 당기/전기 외 다른 기간

        _, local = _split_tag(elem.tag)
        account_nm = XBRL_TO_ACCOUNT.get(local)
        if account_nm is None:
            log.debug(f'  미매핑 XBRL 태그: {local}')
            continue

        raw = (elem.text or '').strip()
        if not raw:
            continue
        try:
            amount = float(raw)
        except ValueError:
            continue

        decimals_str = elem.get('decimals')
        try:
            decimals = int(decimals_str) if decimals_str else None
        except ValueError:
            decimals = None

        fs_div = ctx['fs_div']
        is_instant = ctx['is_instant']
        key = (account_nm, fs_div, period)
        candidates.setdefault(key, []).append((is_instant, amount, decimals, local))

    # 후보 중 최선 선택:
    #   BS 계정 → instant(기말 잔액) 우선, 없으면 가장 큰 절댓값
    #   IS/CF 계정 → 가장 큰 절댓값 (duration이 여러 개일 경우)
    entries: list[XbrlEntry] = []
    for (account_nm, fs_div, period), cands in candidates.items():
        if account_nm in _BS_ACCOUNTS:
            instant_cands = [c for c in cands if c[0]]  # is_instant=True
            pool = instant_cands if instant_cands else cands
        else:
            pool = cands
        # 절댓값 기준 최대
        best = max(pool, key=lambda c: abs(c[1]))
        _, amount, decimals, xbrl_local = best
        entries.append(XbrlEntry(
            account_nm=account_nm,
            xbrl_local=xbrl_local,
            fs_div=fs_div,
            period_type=period,
            amount=amount,
            decimals=decimals,
        ))

    log.debug(f'parse_xbrl_zip: {len(entries)}개 XbrlEntry 추출 (current_date={current_date})')
    return entries


def auto_scale(
    entries: list[XbrlEntry],
    db_amounts: dict[tuple[str, str], float],
) -> int:
    """
    XBRL 값과 DB 값(천원 단위)을 비교해 스케일 배수 감지.

    db_amounts: {(account_nm, fs_div): amount_in_db_unit}
    반환: 1, 1_000, 또는 1_000_000 (XBRL ÷ 반환값 = DB 단위)

    예: XBRL Assets = 120_699_031_311_000, DB Assets = 120_699_031_311 → 1_000 반환.
    """
    ratios: list[float] = []
    for entry in entries:
        if entry.account_nm not in _SCALE_REF_ACCOUNTS:
            continue
        if entry.period_type != 'current':
            continue
        if not entry.amount:
            continue
        db_val = db_amounts.get((entry.account_nm, entry.fs_div))
        if not db_val:
            continue
        ratios.append(abs(entry.amount / db_val))

    if not ratios:
        log.warning('auto_scale: 기준 계정 없음 — 스케일 1로 가정')
        return 1

    median = sorted(ratios)[len(ratios) // 2]
    if 500 <= median <= 5_000:
        return 1_000
    if 500_000 <= median <= 5_000_000:
        return 1_000_000
    if 0.2 <= median <= 5.0:
        return 1
    log.warning(f'auto_scale: 예상 외 비율 {median:.1f} — 스케일 1로 가정')
    return 1


def entries_to_amounts(
    entries: list[XbrlEntry],
    scale: int = 1,
) -> dict[tuple[str, str, str], float]:
    """
    XbrlEntry 목록을 {(account_nm, fs_div, period_type): amount} dict로 변환.
    scale: auto_scale() 반환값. XBRL amount ÷ scale = DB 단위 금액.
    """
    return {
        (e.account_nm, e.fs_div, e.period_type): e.amount / scale
        for e in entries
    }
