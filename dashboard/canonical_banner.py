"""현행 채택 상태 배너 — `backtest.canonical_state.collect()` 의 화면 소비자.

`docs/CANONICAL.md` 를 파싱하지 않고, 수치를 문자열에 박지도 않는다. 문서 생성기
(`scripts/make_canonical.py`)와 **같은 수집 함수**를 쓴다. 그래야 문서와 화면이 갈라질
수 없다 — 갈라진 사례는 `backtest/canonical_state.py` 모듈 docstring 참조.

배너가 반드시 지키는 것 둘:

1. **`check()` 결과를 숨기지 않는다.** 재료끼리 어긋나 있으면 성적을 예쁘게 띄우는 게
   아니라 경고를 먼저 띄운다. 문서 쪽은 이때 종료 코드 1 로 끝나는데, 화면만 조용히
   정상인 척하면 화면이 더 위험한 소비자가 된다.
2. **"미산출"과 "FAIL"을 구분한다.** 게이트가 안 돌아간 것과 떨어진 것은 다른 사실이다.
"""
from __future__ import annotations

import streamlit as st

from backtest.canonical_state import (
    check,
    collect,
    gate_verdicts,
    material_stamps,
    momentum_label,
)

_GATE_STYLE = {
    True:  ('PASS', '#16a34a'),
    False: ('FAIL', '#dc2626'),
    None:  ('미산출', '#94a3b8'),
}


def _pct(v, digits: int = 2) -> str:
    return '—' if v is None else f'{v * 100:.{digits}f}%'


@st.cache_data(ttl=60)
def _state() -> tuple[dict, list[str]]:
    d = collect()
    return d, check(d)


def render_canonical_banner() -> None:
    """현행 채택 설정·성적·게이트·재료 신선도를 한 덩어리로 그린다."""
    try:
        d, problems = _state()
    except Exception as exc:                     # noqa: BLE001
        # 조용히 비워두면 "채택안이 없다"로 오독된다. 못 읽었다는 사실을 그대로 띄운다.
        st.error(f'현행 채택 상태를 읽지 못했습니다 — `backtest.canonical_state.collect()` '
                 f'실패: `{type(exc).__name__}: {exc}`')
        return

    cfg, abl, nav = d['config'], d['abl_tag'], d['nav_tag']

    if problems:
        st.error('**정합성 경고 %d건** — 아래 수치를 인용할 때 반드시 함께 인용하세요.\n\n'
                 % len(problems)
                 + '\n'.join(f'{i}. {t}' for i, t in enumerate(problems, 1)))

    rules = ', '.join(sorted(cfg.get('stability_rules') or [])) or '—'
    st.markdown(
        f"**현행 채택** `{d['tag']}` · **n={d['n_stocks']}** · 산출물 키 `{d['key']}` "
        f"· 랭킹 `{cfg.get('rank_mode', '—')}` · 안정성 {{{rules}}} "
        f"· 모멘텀 {momentum_label(cfg)}")

    c = st.columns(6)
    c[0].metric('구간 CAGR (gross)', _pct((abl or {}).get('cagr')))
    c[1].metric('구간 CAGR (net)',   _pct((abl or {}).get('net_cagr')))
    c[2].metric('완결 구간',          str((abl or {}).get('n_periods', '—')))
    net = (nav or {}).get('net') or {}
    c[3].metric('일별 net CAGR',     _pct((nav or {}).get('net_cagr')))
    c[4].metric('일별 net MDD',      _pct(net.get('daily_mdd')))
    c[5].metric('일별 net Sharpe',
                '—' if net.get('daily_sharpe') is None else f"{net['daily_sharpe']:.3f}")

    badges = []
    for name, verdict in gate_verdicts(d).items():
        label, color = _GATE_STYLE[verdict]
        badges.append(f"<span style='background:{color};color:white;padding:2px 8px;"
                      f"border-radius:6px;font-size:0.78rem;margin-right:6px'>"
                      f"{name} {label}</span>")
    stamps = ' · '.join(f'{k} {str(v)[:19]}' for k, v in material_stamps(d).items() if v)
    st.markdown(
        f"{''.join(badges)}<span style='font-size:0.78rem;color:#6b7280'>SPEC_10 게이트 "
        f"&nbsp;|&nbsp; 재료 산출: {stamps or '—'}</span>",
        unsafe_allow_html=True)

    st.caption('Sharpe·MDD 의 SSOT 는 일별 NAV 다 (SPEC_13 §9-1). 아래 시나리오 비교표의 '
               'MDD·Sharpe 는 구간 기준이라 이 값과 다르다 — 서로 다른 정의다.')
