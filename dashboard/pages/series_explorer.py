"""시리즈 탐색 — main 층. 드롭다운으로 **변수 축**을 고르고 그 축의 비교를 본다.

레거시 `ablation.py` 는 레이어 축(13태그) 전용이라 만들어 둔 76개 산출물 중 대부분이
화면에 없었다. 여기는 매니페스트(`dashboard/series.py`)의 16축 전부를 연다.
2026-08-14 에 레거시 페이지의 구간별·분포 탭까지 흡수하고 그쪽을 없앴다 — 화면이 둘이면
한쪽만 고쳐지는 상태가 되고, 실제로 그게 오염이 오래 살아남은 이유였다.

**이 화면은 지표를 계산하지 않는다.** 산출물이 기록한 값을 읽어 그리기만 한다
(화면 재계산이 공식 수치와 1.86%p 어긋나고 판정 배지를 뒤집은 사고).
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
FUNNEL_COLS = {
    'n_gate':           ('Gate PASS',       '#94a3b8'),
    'hard_passed':      ('Hard Filter',     '#60a5fa'),
    'stability_passed': ('Stability Filter', '#34d399'),
    'screener_passed':  ('Factor Screener', '#fbbf24'),
    'momentum_passed':  ('Momentum Filter', '#a78bfa'),
}


@st.cache_data(ttl=60)
def _catalog():
    return build_catalog()


@st.cache_data(ttl=60)
def _periods(path_str: str) -> pd.DataFrame:
    df = pd.read_csv(path_str)
    df['rebalance_date'] = pd.to_datetime(df['rebalance_date'])
    df['next_date'] = pd.to_datetime(df['next_date'])
    return df


def _pct(v, digits=2):
    return None if v is None else round(v * 100, digits)


def _render_n_curve() -> None:
    """종목 수 곡선. 재실행은 4개뿐이지만 tape 절단으로 n=1..20 을 볼 수 있다.

    `build_portfolio` 가 `candidates[:n]` 인 순수 접두어 슬라이스라 성립한다.
    gross·구간 기준이라는 한계를 화면에도 반드시 함께 띄운다.
    """
    path = ROOT / 'experiments/analysis/n_stocks_curve.json'
    if not path.exists():
        st.info('종목 수 곡선 산출물이 없습니다 — '
                '`python -m scripts.analysis.n_stocks_curve` 로 생성합니다.')
        return
    import json
    d = json.loads(path.read_text(encoding='utf-8'))
    pts = pd.DataFrame(d['points'])

    st.subheader('종목 수 곡선 (tape 절단, n=1..%d)' % pts['n'].max())
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=pts['n'], y=pts['gross_cagr'] * 100, name='gross CAGR',
                             mode='lines+markers', line=dict(color='#1d4ed8', width=2)))
    fig.add_trace(go.Scatter(x=pts['n'], y=pts['period_return_std'] * 100,
                             name='구간간 표준편차', mode='lines+markers', yaxis='y2',
                             line=dict(color='#dc2626', width=2, dash='dash')))
    fig.update_layout(
        height=340, xaxis_title='종목 수 n', yaxis_title='gross CAGR (%)',
        yaxis2=dict(title='구간간 표준편차 (%)', overlaying='y', side='right'),
        margin=dict(t=10, b=40), plot_bgcolor='white',
        legend=dict(orientation='h', y=-0.25))
    st.plotly_chart(fig, use_container_width=True)

    first, last = d['points'][0], d['points'][-1]
    st.caption(
        f"**낙폭이 종목 수로 안 풀리는 이유** — 구간간 표준편차가 n=1 "
        f"{first['period_return_std']:.2%} 에서 n={last['n']} {last['period_return_std']:.2%} 로 "
        f"**줄지 않습니다.** 종목을 늘려도 분산이 안 된다는 뜻이고(전 종목이 같은 저PBR "
        f"팩터에 물림), SPEC_10 G5 미해결 과제의 근거입니다. " + d['disclaimer'])

    cross = pd.DataFrame(d['cross_check_vs_rerun'])
    if not cross.empty:
        with st.expander('교차 검증 — 절단값 vs 실제 재실행'):
            st.write('차이는 tape 과 지표 산출물이 다른 실행일 때 벌어집니다 '
                     '(미해결 과제 `TAPE-ASYNC`). 절단 곡선을 읽을 때의 오차 범위입니다.')
            st.dataframe(pd.DataFrame({
                'n': cross['n'],
                '절단 gross CAGR': (cross['truncation_gross_cagr'] * 100).round(2),
                '재실행 gross CAGR': (cross['rerun_gross_cagr'] * 100).round(2),
                '차이 (%p)': (cross['delta'] * 100).round(2),
            }), use_container_width=True, hide_index=True)


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

if spec.notes:
    with st.expander('📖 이 축을 처음 본다면 — 배경 설명', expanded=False):
        st.markdown(spec.notes)

if series.missing:
    st.error(f'매니페스트가 가리키는데 산출물이 없는 키: `{"`, `".join(series.missing)}`')

# ── B형 — 검정/진단 산출물 ─────────────────────────────────────────────────

if spec.kind == 'B':
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

# ── A형 — 비교 / 구간별 / 분포 ──────────────────────────────────────────────

else:
    with_periods = [r for r in series.members
                    if 'periods' in catalog.require(r.artifact_key).sidecars]
    with_dist = [r for r in series.members
                 if 'dist' in catalog.require(r.artifact_key).sidecars]
    tab_cmp, tab_period, tab_dist = st.tabs(['비교', '구간별', '랜덤 분포'])

    # ── 비교 ────────────────────────────────────────────────────────────────
    with tab_cmp:
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
                # 레거시 산출물은 n_stocks·calendar 를 기록하지 않는다. "기록된 13"과
                # "이름으로 간주한 20"을 화면에서 구별할 수 있게 표기한다.
                # 한 열에 숫자와 '—' 를 섞으면 Arrow 직렬화가 터진다 — 열 단위로
                # 타입을 통일한다 (2026-08-14 실제 발생).
                '구간': str(a.n_periods) if a.n_periods is not None else '—',
                'n': str(a.n_stocks) if a.n_stocks is not None else '미기록',
                '캘린더': (m.get('calendar') or {}).get('id', '미기록'),
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
            '76개 중 14개뿐이라, 있는 행만 일별 값으로 채우면 한 열에 두 정의가 섞입니다 '
            '(같은 태그에서 −34.14% vs −58.12%). 일별 값은 위 현행 채택 배너에 있습니다. '
            '`분포집계` 행은 500회 반복의 중앙값이라 단일 실행 지표가 없습니다.')

        if spec.renderer == 'n_stocks_curve':
            _render_n_curve()

    # ── 구간별 ──────────────────────────────────────────────────────────────
    with tab_period:
        if not with_periods:
            st.info('이 축에는 구간 CSV(`*_periods.csv`)가 있는 멤버가 없습니다. '
                    '구간 CSV 는 git 미추적이라 개발 PC 에는 일부만 있습니다 — 서버가 원본입니다.')
        else:
            picked = st.multiselect(
                '시나리오', with_periods, default=with_periods[:3],
                format_func=lambda r: r.display, key='period_pick')
            if picked:
                base = _periods(str(catalog.require(picked[0].artifact_key).sidecars['periods']))
                gated = base[base['n_gate'] > 0] if 'n_gate' in base.columns else base
                dates = sorted(gated['rebalance_date'].dt.date.tolist())
                lo, hi = st.select_slider('분석 구간', options=dates,
                                          value=(dates[0], dates[-1]), key='period_range')
                st.caption(
                    f'게이트 통과 구간 {len(gated)}개 중 선택 범위. **공식 성과 지표는 완결 '
                    f'구간 기준**이라 이 개수와 다릅니다 — 이 탭의 구간값으로 CAGR 을 '
                    f'재계산하지 마세요.')

                fig = go.Figure()
                for ref in picked:
                    d = _periods(str(catalog.require(ref.artifact_key).sidecars['periods']))
                    d = d[(d['rebalance_date'].dt.date >= lo) & (d['rebalance_date'].dt.date <= hi)]
                    if 'n_gate' in d.columns:
                        d = d[d['n_gate'] > 0]
                    fig.add_trace(go.Bar(
                        x=d['rebalance_date'].dt.strftime('%Y-%m'),
                        y=d['period_return'] * 100, name=ref.display,
                        hovertemplate='%{x}<br>%{y:.2f}%<extra></extra>'))
                d0 = base[(base['rebalance_date'].dt.date >= lo)
                          & (base['rebalance_date'].dt.date <= hi)]
                for col, name, dash in (('kospi_return', 'KOSPI', 'dash'),
                                        ('kosdaq_return', 'KOSDAQ', 'dot')):
                    if col in d0.columns:
                        fig.add_trace(go.Scatter(
                            x=d0['rebalance_date'].dt.strftime('%Y-%m'),
                            y=d0[col] * 100, name=name, mode='lines+markers',
                            line=dict(width=1.5, dash=dash)))
                fig.update_layout(height=380, yaxis_title='구간 수익률 (%)', barmode='group',
                                  xaxis=dict(type='category', tickangle=-45, tickfont_size=10),
                                  margin=dict(t=10, b=60), plot_bgcolor='white',
                                  legend=dict(orientation='h', y=-0.35, font_size=10))
                st.plotly_chart(fig, use_container_width=True)

                st.subheader('필터별 통과 종목 수')
                d = _periods(str(catalog.require(picked[0].artifact_key).sidecars['periods']))
                d = d[(d['rebalance_date'].dt.date >= lo) & (d['rebalance_date'].dt.date <= hi)]
                fig2 = go.Figure()
                for col, (label, c) in FUNNEL_COLS.items():
                    if col in d.columns and d[col].notna().any():
                        fig2.add_trace(go.Scatter(
                            x=d['rebalance_date'], y=d[col], name=label, mode='lines+markers',
                            line=dict(color=c, width=2), marker=dict(size=5)))
                fig2.update_layout(height=260, yaxis_title='통과 종목 수', hovermode='x unified',
                                   margin=dict(t=10, b=50), plot_bgcolor='white',
                                   legend=dict(orientation='h', y=-0.25))
                st.plotly_chart(fig2, use_container_width=True)
                st.caption(f'기준 시나리오: {picked[0].display}')

                with st.expander('구간별 수치 테이블'):
                    for ref in picked:
                        d = _periods(str(catalog.require(ref.artifact_key).sidecars['periods']))
                        d = d[(d['rebalance_date'].dt.date >= lo)
                              & (d['rebalance_date'].dt.date <= hi)].copy()
                        out = pd.DataFrame({
                            '구간 시작': d['rebalance_date'].dt.date,
                            '전략': (d['period_return'] * 100).round(2),
                            'net': (d['net_return'] * 100).round(2) if 'net_return' in d else None,
                            'KOSPI': (d['kospi_return'] * 100).round(2),
                            'KOSDAQ': (d['kosdaq_return'] * 100).round(2)
                                      if 'kosdaq_return' in d else None,
                            'Alpha(vs KP)': ((d['period_return'] - d['kospi_return']) * 100).round(2),
                            '종목': d['n_stocks'] if 'n_stocks' in d else None,
                        })
                        st.markdown(f'**{ref.display}**')
                        st.dataframe(out, use_container_width=True, hide_index=True)

    # ── 랜덤 분포 ───────────────────────────────────────────────────────────
    with tab_dist:
        if not with_dist:
            st.info('이 축에는 랜덤 분포(`*_dist.csv`)가 있는 멤버가 없습니다. '
                    '분포는 A/B/C 같은 무작위 추첨 시나리오에만 있습니다.')
        else:
            det = [r for r in series.members
                   if catalog.require(r.artifact_key).metrics.get('cagr') is not None]
            for ref in with_dist:
                d = pd.read_csv(catalog.require(ref.artifact_key).sidecars['dist'])
                if 'cagr' not in d.columns:
                    continue
                cagr = d['cagr'] * 100
                p5, p95 = cagr.quantile(0.05), cagr.quantile(0.95)
                st.markdown(f'**{ref.display}** — {len(d)}회 추첨')
                c = st.columns(3)
                c[0].metric('중앙값 CAGR', f'{cagr.median():.1f}%')
                c[1].metric('p5', f'{p5:.1f}%')
                c[2].metric('p95', f'{p95:.1f}%')
                fig = go.Figure(go.Histogram(x=cagr, nbinsx=40, marker_color='#cbd5e1',
                                             name='랜덤 분포'))
                fig.add_vline(x=p95, line_dash='dash', line_color='#dc2626',
                              annotation_text=f'p95 {p95:.1f}%')
                for r in det:
                    v = catalog.require(r.artifact_key).metrics['cagr'] * 100
                    fig.add_vline(x=v, line_color='#1d4ed8', line_width=1.5,
                                  annotation_text=f'{r.display.split()[0]} {v:.1f}%',
                                  annotation_font_size=10)
                fig.update_layout(height=300, xaxis_title='CAGR (%)', bargap=0.05,
                                  margin=dict(t=30, b=30), plot_bgcolor='white',
                                  showlegend=False)
                st.plotly_chart(fig, use_container_width=True)
            st.caption('세로선은 이 축의 결정적 실행 CAGR 입니다. 빨간 파선(p95)을 넘어야 '
                       '"무작위로도 나올 수 있는 성적"이 아니라고 말할 수 있습니다.')

# ── 미배정 산출물 ───────────────────────────────────────────────────────────

left = unassigned(catalog)
if left:
    with st.expander(f'⚠️ 어느 축에도 배정되지 않은 산출물 {len(left)}개'):
        st.write('만들어 놓고 잊은 산출물입니다. 소속을 정하거나 지우세요.')
        st.code('\n'.join(left))
