"""시리즈 탐색 — main 층. 드롭다운으로 **변수 축**을 고르고 그 축의 비교를 본다.

레거시 `ablation.py` 는 레이어 축(13태그) 전용이라 만들어 둔 72개 산출물 중 대부분이
화면에 없었다. 여기는 매니페스트(`dashboard/series.py`)의 16축 전부를 연다.

**이 화면은 지표를 계산하지 않는다.** 산출물이 기록한 값을 읽어 그리기만 한다
(2026-08-14: 화면 재계산이 공식 수치와 1.86%p 어긋나고 판정 배지를 뒤집은 사고).
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from backtest.canonical_state import ROOT
from dashboard.artifacts import build_catalog
from dashboard.canonical_banner import render_canonical_banner
from dashboard.series import SERIES, resolve, unassigned

st.set_page_config(page_title='시리즈 탐색', layout='wide', page_icon='🧭')

STATUS_COLOR = {
    'ADOPTED':     '#16a34a',
    'CLOSED_PASS': '#0891b2',
    'CLOSED_FAIL': '#dc2626',
    'EXPLORING':   '#d97706',
    'ARCHIVED':    '#64748b',
}


@st.cache_data(ttl=60)
def _catalog():
    return build_catalog()


def _pct(v, digits=2):
    return None if v is None else round(v * 100, digits)


st.title('🧭 시리즈 탐색')
render_canonical_banner()
st.divider()

catalog = _catalog()
if not len(catalog):
    st.warning('`experiments/ablation/` 산출물이 없습니다. 산출물은 서버가 원본입니다.')
    st.stop()

# ── 축 선택 ─────────────────────────────────────────────────────────────────

col_kind, col_status, col_series = st.columns([1, 1.4, 3])
kinds = col_kind.multiselect('유형', ['A', 'B'], default=['A', 'B'],
                             help='A=태그 성과 비교 · B=검정/진단 산출물')
codes = sorted({s.status.code for s in SERIES})
picked_codes = col_status.multiselect('상태', codes, default=codes)

candidates = [s for s in SERIES if s.kind in kinds and s.status.code in picked_codes]
if not candidates:
    st.info('조건에 맞는 시리즈가 없습니다.')
    st.stop()

spec = col_series.selectbox(
    '시리즈 (변수 축)', candidates,
    format_func=lambda s: f'[{s.kind}] {s.title} — {s.changes}')

series = resolve(spec, catalog)
color = STATUS_COLOR.get(spec.status.code, '#64748b')
st.markdown(
    f"<span style='background:{color};color:white;padding:2px 10px;border-radius:6px;"
    f"font-size:0.8rem'>{spec.status.code}</span> "
    f"<b>{spec.status.label}</b> "
    f"<span style='color:#6b7280;font-size:0.82rem'>· {spec.status.as_of} · 근거 "
    f"<code>{spec.status.source}</code></span>",
    unsafe_allow_html=True)
st.caption(f'**무엇을 바꿨나** — {spec.changes}')

if series.missing:
    st.error(f'매니페스트가 가리키는데 산출물이 없는 키: `{"`, `".join(series.missing)}`')

# ── A형 — 태그 성과 비교 ────────────────────────────────────────────────────

if spec.kind == 'A':
    rows = []
    for ref in series.members:
        a = catalog.require(ref.artifact_key)
        m = a.metrics
        rows.append({
            '시나리오': ref.display + (' ⟵ 기준' if ref.artifact_key == spec.baseline else ''),
            'CAGR': _pct(m.get('cagr') if m.get('cagr') is not None else m.get('median_cagr')),
            'net CAGR': _pct(m.get('net_cagr')),
            'Alpha': _pct(m.get('alpha')),
            'MDD (구간 기준)': _pct(m.get('mdd')),
            'Sharpe (구간 기준)': None if m.get('sharpe') is None else round(m['sharpe'], 2),
            'Robustness': _pct(m.get('robustness'), 0),
            '회전율': _pct(m.get('avg_turnover'), 0),
            # 레거시 산출물은 n_stocks 를 기록하지 않는다 (72개 중 68개). 20 으로
            # 간주하되 화면에서 "기록된 13"과 "간주한 20"을 구별할 수 있게 표기한다.
            '구간': a.n_periods if a.n_periods is not None else '—',
            'n': a.n_stocks if a.n_stocks is not None else '미기록',
            '산출': (a.generated_at or '')[:10],
            '출처': '분포집계' if a.source == 'summary' else '단일실행',
        })
    df = pd.DataFrame(rows)

    fig = go.Figure(go.Bar(
        x=df['CAGR'], y=df['시나리오'], orientation='h',
        marker_color=['#1d4ed8' if '⟵ 기준' in s else '#93c5fd' for s in df['시나리오']],
        text=[f'{v:.1f}%' if v is not None else '—' for v in df['CAGR']],
        textposition='outside', hovertemplate='%{y}<br>CAGR %{x:.2f}%<extra></extra>'))
    fig.update_layout(height=max(320, len(df) * 30), xaxis_title='CAGR (%)',
                      yaxis={'categoryorder': 'array',
                             'categoryarray': list(reversed(df['시나리오']))},
                      margin=dict(l=10, r=80, t=10, b=30), plot_bgcolor='white')
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption(
        'MDD·Sharpe 는 **구간 기준**으로 열 전체를 통일했습니다. 일별 NAV 가 있는 태그는 '
        '72개 중 14개뿐이라, 있는 행만 일별 값으로 채우면 한 열에 두 정의가 섞입니다 '
        '(같은 태그에서 −34.14% vs −58.12%). 일별 값은 위 현행 채택 배너에 있습니다. '
        '`분포집계` 행은 500회 반복의 중앙값이라 단일 실행 지표가 없습니다.')

# ── B형 — 검정/진단 산출물 ─────────────────────────────────────────────────

else:
    st.info(f'검정·진단 산출물입니다. 전용 뷰(`renderer={spec.renderer}`)는 미구현이라 '
            f'원본 파일을 나열합니다.')
    found = []
    for pattern in spec.paths:
        for p in sorted(glob.glob(str(ROOT / pattern))):
            path = Path(p)
            found.append({'파일': str(path.relative_to(ROOT)),
                          '크기': f'{path.stat().st_size / 1024:,.0f} KB',
                          '수정': pd.Timestamp(path.stat().st_mtime, unit='s').date()})
    if found:
        st.dataframe(pd.DataFrame(found), use_container_width=True, hide_index=True)
    else:
        st.error('원본 파일이 하나도 없습니다 — 산출물이 서버에만 있거나 경로가 죽었습니다.')

# ── 미배정 산출물 ───────────────────────────────────────────────────────────

left = unassigned(catalog)
if left:
    with st.expander(f'⚠️ 어느 축에도 배정되지 않은 산출물 {len(left)}개'):
        st.write('만들어 놓고 잊은 산출물입니다. 소속을 정하거나 지우세요.')
        st.code('\n'.join(left))
