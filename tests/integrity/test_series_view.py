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
    grouped_chart_rows,
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


# ── 세트 묶기 — 아무도 화면에서 사라지지 않는가 ──────────────────────────────

def test_grouped_chart_keeps_every_member_and_separates_sets(catalog):
    """세트로 묶어 그려도 **멤버가 하나도 빠지면 안 된다.**

    빈 줄을 끼우는 코드가 멤버를 훑어 다시 늘어놓는 구조라, 세트에 안 적힌 태그가
    조용히 빠질 수 있다. 화면에서 사라진 행은 "그런 실행이 없다"로 읽힌다.
    """
    for series in resolve_all(catalog):
        if series.spec.kind != 'A':
            continue
        rows = comparison_rows(series, catalog)
        chart = grouped_chart_rows(series, rows)

        drawn = [c['label'] for c in chart if not c['spacer']]
        assert drawn == [r['시나리오'] for r in rows] or sorted(drawn) == sorted(
            r['시나리오'] for r in rows), (
            f'{series.id}: 차트에서 빠지거나 늘어난 행이 있다\n'
            f'  표 {len(rows)}행 / 차트 {len(drawn)}행')

        spacers = [c for c in chart if c['spacer']]
        if series.spec.groups:
            assert spacers, f'{series.id}: 세트가 있는데 빈 줄이 하나도 없다'
            labels = [c['label'] for c in spacers]
            assert len(labels) == len(set(labels)), (
                f'{series.id}: 빈 줄 라벨이 겹친다 — Plotly 가 한 칸으로 합쳐 버린다')
            assert all(c['value'] is None for c in spacers)
        else:
            assert not spacers, f'{series.id}: 세트가 없는데 빈 줄이 생겼다'


def test_r6_axis_puts_each_on_off_pair_side_by_side(catalog):
    """R6 축에서 켬/끔이 **붙어** 있어야 한다. 이 축의 요점이 그 대조다."""
    series = resolve(SERIES_BY_ID['r6_loo'], catalog)
    chart = grouped_chart_rows(series, comparison_rows(series, catalog))
    labels = [c['label'] for c in chart]

    for on, off in (('D  ', "D′"), ('E  ', "E′"), ('F  ', "F′"), ('G  ', "G′")):
        i = next((n for n, s in enumerate(labels) if s.startswith(on)), None)
        j = next((n for n, s in enumerate(labels) if s.startswith(off)), None)
        if i is None or j is None:
            continue
        assert j == i + 1, (
            f'{on.strip()} 와 {off} 사이에 다른 행이 끼었다 (i={i}, j={j}) — '
            f'on/off 대조가 그림에서 안 읽힌다')


# ── 분포 CSV 와 summary 가 같은 실행인가 ─────────────────────────────────────

def test_random_distribution_matches_the_summary_median(catalog):
    """히스토그램과 비교표가 **같은 실행**에서 왔는가.

    분포 탭은 히스토그램에 p95 선을 긋고 "무작위로도 나올 성적인가"를 눈으로 재게
    한다. 그런데 그 히스토그램이 폐기된 실행의 것이면 **합격선 자체가 낡은 것**이라,
    현행 수치를 그 선에 대보는 순간 판정이 조용히 틀린다.

    2026-08-15 확인: 분포 CSV 는 2026-07-18 배치이고 `summary.json` 중앙값은 07-30
    재발행(CORR-TTM-001 수정 후)이라 최대 0.48%p 어긋나 있다. 재생성은 기준선 재산출을
    수반하므로(드리프트 규칙) 사용자 승인 전까지 **경고로 관리한다.**
    """
    import warnings as _w

    import pandas as pd

    from dashboard.series_view import DIST_VINTAGE_TOL, dist_vintage_gap

    mismatched = []
    for a in catalog:
        path = a.sidecars.get('dist')
        if path is None:
            continue
        df = pd.read_csv(path)
        if 'cagr' not in df.columns:
            continue
        gap = dist_vintage_gap(a, float(df['cagr'].median()))
        if gap is not None and abs(gap) > DIST_VINTAGE_TOL:
            mismatched.append(f'{a.key}: {gap:+.2f}%p')

    if mismatched:
        _w.warn(
            '분포 CSV 와 summary 중앙값이 다르다 (다른 실행에서 왔다는 뜻): '
            + ', '.join(mismatched)
            + '. 화면이 경고로 알리고 있다. 해소하려면 랜덤 시나리오를 현행 코드로 '
              '재실행해야 하는데 그건 기준선 재산출이라 사용자 승인이 필요하다.',
            UserWarning, stacklevel=2)


def test_cut_inert_paths_really_bypass_the_valuation_cut():
    """"컷 n/a" 표시의 근거 — 그 경로들이 정말 `passes_rim_cut` 을 안 타는가.

    `rim_threshold` 는 객체에 남아 있지만 `score_and_rank` 를 통째로 오버라이드하면
    기본 구현의 컷 분기가 실행되지 않는다. 값만 보고 `✓` 로 적으면 "고평가 종목을
    뺐다"는 없는 사실이 화면에 생긴다 (2026-08-15 매트릭스 만들며 발견).

    RIM 경로는 반대로 **기본 구현을 그대로 써야** 컷이 걸린다 — 그것도 함께 못 박는다.
    """
    from backtest.ablation import ABLATION_CONFIGS, build_ablation_pipeline
    from backtest.pipeline import BacktestPipeline
    from dashboard.series_view import _CUT_INERT_SIGNALS, pipeline_facts

    seen = set()
    for tag in ABLATION_CONFIGS:
        facts = pipeline_facts(tag)
        if not facts:
            continue
        p = build_ablation_pipeline(tag, ABLATION_CONFIGS[tag])
        overrides = type(p).score_and_rank is not BacktestPipeline.score_and_rank
        signal = facts['랭킹 신호']
        seen.add(signal)

        if signal in _CUT_INERT_SIGNALS:
            assert overrides, (
                f'{tag}({signal}): 기본 `score_and_rank` 를 쓰는데 컷을 n/a 로 적고 있다 — '
                f'실제로는 컷이 걸린다')
            assert facts['밸류에이션 컷'] == 'n/a'
        elif signal == 'RIM 상승여력':
            assert not overrides, (
                f'{tag}: RIM 경로가 `score_and_rank` 를 오버라이드한다 — 컷 표시 근거를 '
                f'다시 확인하라')
            # RIM 경로라고 컷이 항상 켜진 것은 아니다. SPEC_14 §14-1 이 컷을 랭킹과
            # **독립 스위치**로 뺐고, `F_rimrank_no_r3r4` 가 그 2×2 의 한 칸이다.
            assert facts['밸류에이션 컷'] in ('✓', '—')

    assert {'무작위 추첨', 'RIM 상승여력'} <= seen, '검사가 공허하다 — 경로가 안 잡혔다'
