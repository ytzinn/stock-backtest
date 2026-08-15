"""화면에 뿌릴 값의 정합성 (설계메모 v3 §8 검사 7·8번).

## 7번 — 기준 혼입 방지

`docs/CANONICAL.md` 는 "Sharpe·MDD 의 SSOT 는 일별 NAV"라고 못박는다. 원칙은 옳지만
일별 NAV 를 가진 태그는 76개 중 14개뿐이다. 있는 행만 일별 값으로 채우면 **한 열에 두
정의가 섞인다** — 같은 `F_pbr_ma200_n13` 에서 구간 −34.14% vs 일별 −58.12%, 24%p 차다.
라벨을 붙여도 사람 눈은 숫자 크기를 먼저 보므로 정렬하는 순간 순위가 뒤집힌다.

그래서 비교표는 **행이 아니라 열 단위로** 기준을 고정한다. 이 검사는 그 규칙이 코드에
실제로 지켜지는지 본다 — 규칙을 주석에만 적어두면 다음 사람이 "이 태그는 일별 값이
있으니 더 정확하겠지" 하고 섞는다.

## 8번 — raw fallback

전용 뷰가 없는 B형 축에서도 **자료가 화면에서 사라지면 안 된다.** 전용 뷰를 나중에
붙이더라도 그때까지 원본 링크는 떠 있어야 한다.
"""
from __future__ import annotations

import json

import pytest

from backtest.canonical_state import NAV_DIR, ROOT
from dashboard.artifacts import build_catalog
from dashboard.series import SERIES, SERIES_BY_ID, SeriesSpec, Status, resolve, resolve_all
from dashboard.series_view import (
    MDD_COL,
    SHARPE_COL,
    b_type_files,
    comparison_rows,
    compound_curve,
    excess_curve,
    provenance_rows,
)


@pytest.fixture(scope='module')
def catalog():
    c = build_catalog()
    if not len(c):
        pytest.skip('experiments/ablation 산출물이 없다 (git 미추적).')
    return c


# ── 7. 기준 혼입 방지 ────────────────────────────────────────────────────────

def test_column_titles_state_their_basis():
    """열 제목이 기준을 말해야 한다. 값만 있으면 어느 정의인지 알 수 없다."""
    assert '구간 기준' in MDD_COL and '구간 기준' in SHARPE_COL, (
        f'열 제목에 기준이 없다: {MDD_COL} / {SHARPE_COL}')


def test_every_mdd_comes_from_the_period_artifact(catalog):
    """비교표의 MDD·Sharpe 는 **전 행이** ablation 산출물 값이어야 한다.

    한 행이라도 일별 NAV 에서 오면 열의 의미가 깨진다.
    """
    wrong = []
    for series in resolve_all(catalog):
        if series.spec.kind != 'A':
            continue
        for ref, row in zip(series.members, comparison_rows(series, catalog)):
            m = catalog.require(ref.artifact_key).metrics
            want = None if m.get('mdd') is None else round(m['mdd'] * 100, 2)
            if row[MDD_COL] != want:
                wrong.append(f'{series.id}/{ref.artifact_key}: 표 {row[MDD_COL]} vs 산출물 {want}')
    assert not wrong, '비교표 MDD 가 구간 산출물 값과 다르다:\n  ' + '\n  '.join(wrong)


def test_daily_nav_value_never_leaks_into_the_comparison_table(catalog):
    """일별 NAV 를 **가진** 태그에서도 비교표에는 구간 값이 떠야 한다.

    이게 이 검사의 핵심이다. 일별 값이 없는 태그만 보면 규칙이 지켜지는지 알 수 없다 —
    유혹이 생기는 건 값이 있을 때다.
    """
    summary_path = NAV_DIR / 'summary.json'
    if not summary_path.exists():
        pytest.skip('daily_nav/summary.json 이 없다')
    daily = json.loads(summary_path.read_text(encoding='utf-8')).get('tags') or {}

    checked = 0
    for series in resolve_all(catalog):
        if series.spec.kind != 'A':
            continue
        rows = {r['시나리오'].replace(' ⟵ 기준', ''): r
                for r in comparison_rows(series, catalog)}
        for ref in series.members:
            nav = daily.get(ref.artifact_key)
            if not nav:
                continue
            daily_mdd = (nav.get('net') or {}).get('daily_mdd')
            if daily_mdd is None:
                continue
            row = rows.get(ref.display)
            if row is None or row[MDD_COL] is None:
                continue
            checked += 1
            assert abs(row[MDD_COL] - daily_mdd * 100) > 0.01, (
                f'{ref.artifact_key}: 비교표 MDD 가 **일별 NAV 값**({daily_mdd:.2%})이다. '
                f'열 기준이 섞였다 — 구간 기준으로 통일해야 한다.')
    assert checked, ('일별 NAV 를 가진 멤버가 한 축에도 없어 검사가 공허하다 — '
                     '이 검사는 값이 있을 때만 의미가 있다.')


def test_open_period_is_not_counted(catalog):
    """표의 `구간` 은 산출물의 `n_periods` 를 그대로 써야 한다.

    화면이 직접 세면 열린 구간이 섞인다 (2026-08-14: 21 vs 20 으로 CAGR 1.86%p 오차).
    """
    for series in resolve_all(catalog):
        if series.spec.kind != 'A':
            continue
        for ref, row in zip(series.members, comparison_rows(series, catalog)):
            a = catalog.require(ref.artifact_key)
            want = str(a.n_periods) if a.n_periods is not None else '—'
            assert row['구간'] == want, f'{ref.artifact_key}: 구간 수를 화면이 다시 셌다'


# ── 8. raw fallback ─────────────────────────────────────────────────────────

def test_every_b_series_falls_back_to_raw_files():
    """전용 뷰가 없어도 원본 파일이 나열돼야 한다."""
    empty = [s.id for s in SERIES if s.kind == 'B' and not b_type_files(s)]
    assert not empty, (f'B형인데 원본 파일이 하나도 안 잡히는 축: {empty}. '
                       f'전용 뷰가 없는 동안 자료가 화면에서 사라진다.')


def test_raw_fallback_is_quiet_on_dead_paths():
    """죽은 경로는 **예외가 아니라 빈 목록**이어야 한다 — 화면이 그 사실을 말할 수 있게."""
    bogus = SeriesSpec(
        id='__test__', title='t', kind='B', changes='t',
        paths=('experiments/does_not_exist/*.json',),
        status=Status('EXPLORING', 't', '2026-01-01', 'CLAUDE.md'))
    assert b_type_files(bogus) == []


def test_raw_fallback_lists_files_not_directories():
    for s in SERIES:
        if s.kind != 'B':
            continue
        for row in b_type_files(s):
            assert (ROOT / row['파일']).is_file(), f'{row["파일"]} 은 파일이 아니다'


# ── 계보 ────────────────────────────────────────────────────────────────────

def test_provenance_distinguishes_untracked_from_unknown(catalog):
    """`git 추적` 은 세 상태여야 한다 — 추적/미추적/**판정 불가**.

    git 이 없는 환경에서 "미추적"으로 단정하면 거짓말이 된다. 산출물 상당수가 실제로
    미추적이라(개발 PC 와 서버가 다른 사본) 이 구분이 화면 진단의 핵심이다.
    """
    series = resolve(SERIES_BY_ID['n_stocks'], catalog)
    values = {r['git 추적'] for r in provenance_rows(series, catalog)}
    assert values <= {'추적', '미추적', '판정 불가'}, f'알 수 없는 상태: {values}'
    assert values, '계보 행이 비었다'


# ── 누적 곡선 — 곱으로 쌓는가 ────────────────────────────────────────────────

def test_compound_curve_multiplies_and_does_not_add():
    """누적은 Π(1+r)−1 이다. 더하면 기간이 길수록 어긋난다.

    구간 수익률을 그냥 더해 "누적"이라 부르는 실수는 흔하고, 짧은 구간에서는 값이
    비슷해 눈으로 안 잡힌다. 20구간짜리 화면에서는 확실히 갈라진다.
    """
    r = [0.10, -0.10]
    assert compound_curve(r) == pytest.approx([0.10, -0.01])   # 합이면 0.0 이 된다
    assert compound_curve([]) == []
    assert compound_curve([0.05]) == pytest.approx([0.05])

    acc = 1.0
    for got, x in zip(compound_curve([0.1, 0.2, -0.05, 0.3]), [0.1, 0.2, -0.05, 0.3]):
        acc *= 1 + x
        assert got == pytest.approx(acc - 1)


def test_excess_curve_is_a_difference_of_curves_not_a_sum_of_alphas():
    """누적 초과 = 전략 누적 − 벤치 누적.

    구간 알파를 더한 값과 **다르다.** 화면 캡션이 그렇게 설명하므로, 둘이 실제로
    갈라지는지 여기서 못 박는다 — 갈라지지 않으면 그 설명이 공허하다.
    """
    s = [0.20, 0.15, -0.10, 0.25]
    b = [0.05, 0.10, -0.05, 0.05]

    got = excess_curve(s, b)
    assert got[-1] == pytest.approx(compound_curve(s)[-1] - compound_curve(b)[-1])

    naive = sum(x - y for x, y in zip(s, b))
    assert abs(got[-1] - naive) > 0.01, (
        f'누적 초과({got[-1]:.4f})와 구간 알파 합({naive:.4f})이 사실상 같다 — '
        f'"더한 게 아니다"라는 화면 설명이 의미를 잃는다')
