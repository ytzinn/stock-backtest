"""
Pass 2 — P0 항목 재현용 최소 실패 테스트 (합성 PostgreSQL 필요분).

⚠ 이 파일의 테스트는 **의도적으로 실패 상태**다 (tests/oracle/README.md 표 참조).
  Pass 3에서 프로덕션을 고치면 저절로 통과한다. 통과시키려고 테스트를 고치지 마라.
  (같은 디렉토리의 test_pit_sql_contracts.py 중 *_LOOKAHEAD/_MIXING_DOCUMENTED 테스트는
   "현행 동작 문서화"용으로 지금 통과 상태다 — 그 둘은 여기 실패 테스트가 요구하는 수정이
   들어가면 반대로 깨진다. 수정 PR에서 문서화 테스트를 새 계약으로 교체하는 것까지가
   한 세트다.)
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backtest.data_access import get_avg_turnover, load_gate_passed_tickers, load_pit_series
from ingest.dq_gate import _load_accounts, run_dq_gate

pytestmark = pytest.mark.integration

REBAL = date(2024, 4, 3)


def _insert_pit(conn, ticker, year, account, amount, available_from,
                fs_div='CFS', report_type='FY', original_amount=None, amendment_from=None):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO financials_pit
                (ticker, year, report_type, fs_div, account_nm, amount,
                 available_from, original_amount, amendment_from)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (ticker, year, report_type, fs_div, account, amount,
             available_from, original_amount, amendment_from),
        )


# ── PIT-AMEND-001 ───────────────────────────────────────────────────────────────

def test_amended_row_without_original_must_not_leak_amended_value(conn, make_stock):
    """
    [PIT-AMEND-001 재현]
    SPEC_02 §3-1-2 계약: "정정 미공개 시점 → 원본값 사용 (PIT 보존)".
    원본(original_amount)이 캡처되지 않은 행은 원본값을 알 수 없으므로, PIT를 보존하는
    유일한 방법은 **정정값을 쓰지 않는 것**이다 (계정 제외 등 — 구체 방식은 수정 PR에서).
    운영 DB 실측: 정정 행 86,379개 중 18,676개(21.6%)가 이 상태다.

    ⚠ 의도적 실패 — 현재 CASE ELSE 분기가 정정값(200)을 그대로 반환한다.
    """
    make_stock('PA2001')
    _insert_pit(conn, 'PA2001', 2023, '당기순이익', amount=200.0,
                available_from=REBAL - timedelta(days=10),
                original_amount=None, amendment_from=REBAL + timedelta(days=30))

    result = load_pit_series(conn, REBAL, n_years=3, report_type='FY')
    leaked = result.get('PA2001', [{}])[0].get('당기순이익')
    assert leaked != 200.0, (
        f'정정 미공개 구간인데 원본 미캡처 행의 정정값({leaked})이 그대로 사용됐다 — '
        f'조용한 룩어헤드 (PIT-AMEND-001)'
    )


# ── CORR-GATE-001 ───────────────────────────────────────────────────────────────

def test_gate_load_accounts_must_prefer_cfs_deterministically(conn, make_stock):
    """
    [CORR-GATE-001 확정 계약 — Pass 3 수정으로 통과 전환]
    종전 _load_accounts()는 fs_div 필터·ORDER BY 없이 dict를 덮어써 CFS/OFS 승자가
    비결정적이었다. 수정: ORDER BY로 CFS가 항상 마지막에 덮어쓰도록 고정
    (load_pit_series와 일관된 CFS 우선).

    합성: CFS 자본총계=+100(정상), OFS=-100(자본잠식) — 승자에 따라 R02가 뒤집힌다.
    삽입 순서와 무관하게 CFS(+100)여야 한다.
    """
    make_stock('GA2001')
    with conn.cursor() as cur:
        # CFS를 먼저 INSERT (종전 구현이라면 seq scan 순서상 OFS가 덮어썼을 배치)
        cur.execute(
            "INSERT INTO financials (ticker, year, report_type, fs_div, account_nm, amount) "
            "VALUES ('GA2001', 2023, 'FY', 'CFS', '자본총계', 100.0)")
        cur.execute(
            "INSERT INTO financials (ticker, year, report_type, fs_div, account_nm, amount) "
            "VALUES ('GA2001', 2023, 'FY', 'OFS', '자본총계', -100.0)")

    with conn.cursor() as cur:
        first, amended = _load_accounts(cur, 'GA2001', 2023, 'FY')

    assert first['자본총계'] == 100.0, (
        f"게이트 입력 자본총계가 {first['자본총계']} — CFS(+100)가 아니라 OFS(-100)가 "
        f"이겼다. fs_div 미구분 비결정 병합 (CORR-GATE-001)"
    )
    assert amended['자본총계'] == 100.0   # 정정값 뷰도 동일 CFS 우선 규칙


# ── CORR-GATE-002 ───────────────────────────────────────────────────────────────

def test_gate_verdict_must_reflect_values_known_at_rebalance(conn, make_stock):
    """
    [CORR-GATE-002 확정 계약 — Pass 3 수정으로 통과 전환, end-to-end]
    시나리오: 원본 공시 자본총계 = -100(자본잠식, R02 REJECT 대상). 이후 정정공시로
    +100으로 수정됨 (financials.amount는 정정값으로 덮어써지고 original_amount에
    원본 보존 — dart_ingest/amendment_checker 동작).

    확정 계약 (사용자 결정 (a), 2026-07-12): 게이트는 **최초 공시값**(COALESCE(original,
    amount)) 기준으로 판정한다 — 정정 이전 리밸런싱일에서 시장이 알던 값. 원본 -100
    → R02 REJECT → 유니버스 제외. (운영 DB 실측: 부호 플립 145행이 이 경로의 대상.)
    """
    make_stock('GB2001')
    # financials: 정정 후 상태 (amount=정정값 +100, original_amount=원본 -100)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO financials (ticker, year, report_type, fs_div, account_nm, "
            " amount, original_amount) "
            "VALUES ('GB2001', 2023, 'FY', 'CFS', '자본총계', 100.0, -100.0)")
    # financials_pit: 게이트 JOIN용 가용성 행 (최신 보고서 = 2023 FY)
    _insert_pit(conn, 'GB2001', 2023, '자본총계', amount=100.0,
                available_from=REBAL - timedelta(days=10),
                original_amount=-100.0, amendment_from=REBAL + timedelta(days=30))

    # 수정된 dq_gate 평가 경로를 그대로 구동 (이중 판정 → upsert, CORR-GATE-003)
    with conn.cursor() as cur:
        first, amended = _load_accounts(cur, 'GB2001', 2023, 'FY')
    assert first['자본총계'] == -100.0    # PIT: 원본값이 최초 판정 입력이어야 한다
    assert amended['자본총계'] == 100.0   # 정정값 뷰 (정정 공시 이후 시점용)
    run_dq_gate('GB2001', 2023, 'FY', first,
                accounts_cur_amended=amended,
                amendment_from=REBAL + timedelta(days=30),
                conn=conn)

    with conn.cursor() as cur:
        cur.execute("SELECT status, status_amended FROM universe_gate_pit "
                    "WHERE ticker='GB2001' AND year=2023 AND report_type='FY'")
        status, status_amended = cur.fetchone()
        assert status == 'REJECT'          # R02: 자본총계 < 0 (원본 기준)
        assert status_amended == 'PASS'    # 정정 기준 판정 — 정정일 이후에만 사용

    tickers = load_gate_passed_tickers(conn, REBAL, report_type='FY')
    assert 'GB2001' not in tickers, (
        'REBAL 시점 시장이 알던 자본총계는 -100(자본잠식)인데 유니버스에 포함됐다 — '
        '게이트 경유 룩어헤드 (CORR-GATE-002)'
    )


# ── CORR-DA-001 ─────────────────────────────────────────────────────────────────

def test_avg_turnover_missing_data_must_not_be_silent_zero(conn, make_stock):
    """
    [CORR-DA-001 재현]
    가격 데이터가 아예 없는 종목(수집 실패)과 실제 무거래 종목이 모두 0.0으로 수렴한다.
    기대 계약(AUDIT_03 재발 방지 규칙): 데이터 부재는 조용한 기본값이 아니라 예외
    (또는 명시적 allow_missing). Hard Filter가 이 값으로 종목을 조용히 제외하므로,
    수집 장애가 유니버스 왜곡으로 이어져도 아무 경고가 없다.

    ⚠ 의도적 실패 — 현재 COALESCE(AVG(turnover), 0)이 0.0을 반환한다.
    """
    make_stock('DA2001')   # price_history 행 없음 = 수집 실패 상황
    with pytest.raises(Exception):
        get_avg_turnover(conn, 'DA2001', REBAL)
