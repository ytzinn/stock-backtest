"""종목 수 곡선 산출물의 정합성.

## 무엇을 지키나

`docs/CANONICAL.md` 의 미해결 과제 `G5-MDD` 는 "구간간 표준편차가 n=1 18.78% 에서
n=20 21.15% 로 줄지 않는다"를 근거로 든다. 2026-08-14 이전에는 **그 수치가 산출물
없이 산문과 코드 주석에만** 있었다 — 재현 스크립트도 없어 확인할 방법이 없었다.

이제 산출물이 있으므로, **산문에 적힌 수치가 산출물과 같은지**를 기계가 본다.
이 검사가 없으면 곡선을 다시 뽑았을 때 과제 설명만 옛 수치로 남고, 읽는 사람은
낡은 줄 모른다. 이 저장소가 반복해서 데인 패턴이 정확히 그것이다.

## 용어 — 종목 수는 `n` 이다

코드가 이미 `N_STOCKS`·`--n-stocks`·`n_stocks`·산출물 키 `_n13` 으로 전부 `n` 을 쓴다.
산문에서만 `k` 로 쓰던 것을 `n` 으로 통일했다(2026-08-14). 같은 것을 두 이름으로
부르면 "n=13 재실행"과 "k=13 절단"이 다른 것처럼 읽힌다 — 실제로는 방법이 다를 뿐
축은 하나다. 방법 구분은 이름이 아니라 `method` 필드가 한다.
"""
from __future__ import annotations

import json
import re
import warnings
from pathlib import Path

import pytest

from backtest.canonical_state import ABL_DIR, ROOT, _sha256

CURVE = ROOT / 'experiments/analysis/n_stocks_curve.json'
ISSUES = ROOT / 'docs/open_issues.yaml'

#: 절단값과 재실행값의 차이. tape 과 지표가 다른 실행이면 벌어진다 (TAPE-ASYNC).
WARN_DELTA = 0.005     # 0.5%p 넘으면 경고
FAIL_DELTA = 0.03      # 3%p 넘으면 곡선을 읽을 수 없는 상태다


@pytest.fixture(scope='module')
def curve():
    if not CURVE.exists():
        pytest.skip('n_stocks_curve.json 이 없다 — '
                    '`python -m scripts.analysis.n_stocks_curve` 로 생성한다.')
    return json.loads(CURVE.read_text(encoding='utf-8'))


def test_points_are_contiguous_from_one(curve):
    ns = [p['n'] for p in curve['points']]
    assert ns == list(range(1, len(ns) + 1)), f'n 이 1부터 연속이 아니다: {ns[:5]}…'
    assert all(p['method'] == 'truncation' for p in curve['points']), (
        '이 곡선은 재실행이 아니라 tape 절단이다 — method 를 바꾸려면 산출 방식부터 바꿔라.')


def test_curve_is_not_stale_relative_to_its_tape(curve):
    """곡선이 **지금의 tape** 에서 나온 것인가.

    tape 이 바뀌었는데 곡선을 다시 안 뽑으면, 곡선은 틀린 게 아니라 **낡은** 상태가
    된다. 낡음은 읽는 사람에게 보이지 않는다 — 그래서 지문을 대조한다.
    """
    tape = ROOT / curve['source']['tape']
    if not tape.exists():
        pytest.skip(f'기준 tape 이 없다: {tape} (git 미추적, 서버가 원본)')
    assert _sha256(tape) == curve['source']['tape_sha256'], (
        f'곡선이 기준 tape 과 어긋난다 — tape 이 바뀐 뒤 곡선을 재생성하지 않았다. '
        f'`python -m scripts.analysis.n_stocks_curve` 를 실행하라.')


def test_cross_check_against_reruns_is_within_tolerance(curve):
    """절단값이 실제 재실행값을 재현하는가.

    `build_portfolio` 가 `candidates[:n]` 인 순수 접두어 슬라이스이므로 원리상 같아야
    한다. 벌어지면 tape 과 지표 산출물이 다른 실행이라는 뜻이다(TAPE-ASYNC).
    """
    cross = curve['cross_check_vs_rerun']
    assert cross, '재실행이 하나도 없어 교차 검증이 불가능하다 — 곡선을 단독으로 믿지 마라.'

    broken = [c for c in cross if abs(c['delta']) >= FAIL_DELTA]
    assert not broken, (
        '절단값이 재실행값과 크게 다르다 — 곡선을 읽을 수 없는 상태다:\n  ' +
        '\n  '.join(f'n={c["n"]}: {c["delta"]:+.2%}' for c in broken))

    noisy = [c for c in cross if abs(c['delta']) >= WARN_DELTA]
    if noisy:
        warnings.warn(
            'tape 절단과 재실행의 차이가 0.5%p 를 넘는 n 이 있다 (TAPE-ASYNC): '
            + ', '.join(f'n={c["n"]} {c["delta"]:+.2%}' for c in noisy),
            UserWarning, stacklevel=2)


def test_prose_numbers_match_the_artifact(curve):
    """`open_issues.yaml` 에 적힌 표준편차가 산출물과 같은가.

    과제 설명은 산문이고 곡선은 산출물이다. 곡선을 다시 뽑으면 산문만 낡는다 —
    그 어긋남을 사람이 알아채는 경로가 없으므로 여기서 막는다.
    """
    if not ISSUES.exists():
        pytest.skip('open_issues.yaml 이 없다')
    text = ISSUES.read_text(encoding='utf-8')
    quoted = re.findall(r'n=(\d+)\s+([\d.]+)%', text)
    if not quoted:
        pytest.skip('과제 설명이 표준편차 수치를 인용하지 않는다')

    by_n = {p['n']: p['period_return_std'] for p in curve['points']}
    wrong = []
    for n_str, pct_str in quoted:
        n, pct = int(n_str), float(pct_str)
        if n not in by_n:
            continue
        actual = round(by_n[n] * 100, 2)
        if abs(actual - pct) > 0.01:
            wrong.append(f'n={n}: 산문 {pct}% vs 산출물 {actual}%')
    assert not wrong, ('과제 설명의 수치가 산출물과 다르다 — 곡선을 재생성했으면 '
                       'docs/open_issues.yaml 도 고쳐라:\n  ' + '\n  '.join(wrong))


def test_dispersion_claim_still_holds(curve):
    """"종목 수를 늘려도 분산이 안 된다"는 주장이 지금 데이터에서도 참인가.

    이건 정합성이 아니라 **주장의 유효기간** 검사다. 언젠가 데이터가 바뀌어 표준편차가
    실제로 줄면, G5-MDD 과제의 근거가 사라진 것이므로 과제 자체를 다시 써야 한다.
    그때 조용히 넘어가지 않도록 여기서 걸린다.
    """
    by_n = {p['n']: p['period_return_std'] for p in curve['points']}
    if 1 not in by_n or 20 not in by_n:
        pytest.skip('n=1 또는 n=20 지점이 없다')
    # 20종목 분산이 실제로 작동했다면 표준편차가 뚜렷이 줄어야 한다.
    assert by_n[20] > by_n[1] * 0.7, (
        f'구간간 표준편차가 n=1 {by_n[1]:.2%} → n=20 {by_n[20]:.2%} 로 **줄었다**. '
        f'G5-MDD 과제의 근거("종목 수로는 안 풀린다")가 더 이상 성립하지 않는다 — '
        f'docs/open_issues.yaml 의 과제 설명을 다시 써라.')


def test_gross_only_limitation_is_recorded(curve):
    """gross·구간 기준이라는 한계가 산출물 자체에 적혀 있어야 한다.

    이 값을 net 이나 일별 MDD 로 오독하면 G5 판정과 직접 충돌한다(구간 −33% vs
    일별 −58%). 한계를 화면 캡션에만 두면 산출물을 직접 읽는 소비자가 놓친다.
    """
    d = curve.get('disclaimer', '')
    assert 'gross' in d and '일별' in d, (
        'disclaimer 에 gross·일별 NAV 한계가 없다 — 산출물만 읽는 소비자가 오독한다.')
    assert all('gross_cagr' in p and 'net_cagr' not in p for p in curve['points']), (
        'net 은 절단으로 구할 수 없다 (회전율이 n 에 따라 달라진다). 필드가 있으면 오해를 부른다.')
