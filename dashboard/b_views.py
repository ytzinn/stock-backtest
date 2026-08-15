"""B형 전용 뷰 — 검정·진단 산출물을 읽는 화면.

`SeriesSpec.renderer` 가 여기 등록된 키를 가리키면 그 함수가 돈다. 없으면 페이지가
원본 파일 목록(raw fallback)으로 떨어진다 — 전용 뷰가 없다고 자료가 화면에서 사라지면
안 되기 때문이다 (무결성 검사 8번).

## 전용 뷰가 raw 목록보다 나은 진짜 이유

보기 좋아서가 아니다. **결과와 사전등록 조건을 반드시 함께 띄우기 위해서다.**

이 산출물들은 `pre_registered`·`disclaimer` 필드를 갖고 있다. 판정 문자열만 보면
`TIME_OVERFIT_CONFIRMED` 가 어떤 문턱을 어떻게 넘어서 나온 말인지 알 수 없고, 그러면
사람은 화면에 같이 떠 있는 다른 숫자로 **사후 설명을 만든다.** 원본 JSON 을 열어 보는
사람은 그 필드를 스크롤해서 지나친다. 화면이 강제로 나란히 놓는 편이 낫다.

**파일이 아니라 산출물이 소유한 값만 그린다.** 여기서 검정통계량을 다시 계산하지 않는다
(대시보드 재계산이 공식 수치를 1.86%p 오염시킨 사고).
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.series_view import (
    bootstrap_excludes_zero,
    rank_shift_rows,
    time_split,
    verdict_rule_rows,
)

VERDICT_COLOR = {
    'TIME_OVERFIT_CONFIRMED': '#dc2626',
    'TIME_ROBUST':            '#16a34a',
    'INCONCLUSIVE':           '#d97706',
}


def _rank_reversal_chart(rows: list[dict], n_tags: int) -> go.Figure:
    """앞 순위 → 뒤 순위 기울기 그래프. 선 하나가 태그 하나다.

    가로 두 칸(앞·뒤)에 순위를 세로로 놓고 이으면, 교차하는 선다발이 곧 "순위가
    유지되지 않는다"는 그림이다. 막대 두 벌로 그리면 같은 사실이 안 보인다 —
    사람이 두 그래프 사이에서 같은 태그를 눈으로 이어야 하기 때문이다.
    """
    fig = go.Figure()
    for r in rows:
        focal = r['초점']
        fig.add_trace(go.Scatter(
            x=['앞 절반<br>2016–2021', '뒤 절반<br>2021–2026'],
            y=[r['앞 순위'], r['뒤 순위']],
            mode='lines+markers+text',
            name=r['태그'],
            text=[r['태그'] if focal else '', r['태그'] if focal else ''],
            textposition=['middle left', 'middle right'],
            textfont=dict(size=11, color='#dc2626'),
            line=dict(color='#dc2626' if focal else '#cbd5e1',
                      width=3 if focal else 1.5),
            marker=dict(size=9 if focal else 5),
            opacity=1.0 if focal else 0.65,
            hovertemplate=f'<b>{r["태그"]}</b><br>%{{x}} %{{y}}위<extra></extra>'))
    fig.update_layout(
        height=460, showlegend=False, plot_bgcolor='white',
        margin=dict(t=20, b=20, l=90, r=90),
        yaxis=dict(title='순위 (1위가 위)', autorange='reversed',
                   dtick=1, range=[n_tags + 0.5, 0.5], gridcolor='#f1f5f9'),
        xaxis=dict(showgrid=False))
    return fig


def render_time_overfit() -> None:
    """SPEC_14 §14-3 시간분할 과적합 검정."""
    d = time_split()
    if d is None:
        st.info('`experiments/calendar_sens/time_split.json` 이 없습니다 — 산출물은 '
                '서버가 원본입니다. `venv/bin/python -m scripts.calendar_sens.time_split`')
        return

    pr, split, boot = d['pre_registered'], d['split'], d['bootstrap']
    verdict = d['verdict']

    # ── 판정 + 그것을 만든 규칙. 반드시 함께 뜬다 ──────────────────────────
    st.markdown(
        f"<span style='background:{VERDICT_COLOR.get(verdict, '#64748b')};color:white;"
        f"padding:3px 12px;border-radius:6px;font-weight:600'>{verdict}</span> "
        f"<span style='color:#6b7280;font-size:0.85rem'>· 초점 <code>{pr['focal_tag']}</code>"
        f" · 산출 {d['generated_at'][:10]}</span>", unsafe_allow_html=True)

    st.markdown('#### 판정 규칙 — 수치 산출 **전**에 확정됐다')
    rule = pd.DataFrame(verdict_rule_rows(d))
    st.dataframe(rule, use_container_width=True, hide_index=True,
                 column_config={'충족': st.column_config.CheckboxColumn(disabled=True)})
    st.caption(f"사전등록: {pr['note']} · 분할 기준일 `{pr['split_at']}` · "
               f"bootstrap {pr['n_boot']:,}회 seed `{pr['seed']}`. "
               f"두 조건이 **모두** 충족되면 `TIME_OVERFIT_CONFIRMED`, 앞뒤 둘 다 "
               f"{pr['robust_top']}위 이내면 `TIME_ROBUST`, 그 외 `INCONCLUSIVE` 입니다.")

    # ── 검정통계량 — 판정 근거가 아니라는 사실을 함께 ──────────────────────
    c = st.columns(4)
    c[0].metric('Spearman ρ (앞↔뒤)', f"{d['spearman_front_back']:.3f}")
    c[1].metric('bootstrap 95% CI',
                f"[{boot['ci_low']:.2f}, {boot['ci_high']:.2f}]")
    c[2].metric('초점 앞→뒤 순위',
                f"{d['focal']['front_rank']}위 → {d['focal']['back_rank']}위",
                delta=f"{d['focal']['back_rank'] - d['focal']['front_rank']:+d}",
                delta_color='inverse')
    c[3].metric('구간', f"앞 {split['n_front']} / 뒤 {split['n_back']}",
                help=f"완결 {split['n_closed']}구간을 반으로 나눴습니다.")

    if not bootstrap_excludes_zero(d):
        st.warning(
            f"**ρ 의 신뢰구간은 0 을 배제하지 못합니다** "
            f"(상한 {boot['ci_high']:+.3f}). 즉 이 판정은 순위상관이 통계적으로 "
            f"유의해서 나온 것이 **아니라**, 초점 태그가 위 사전등록 문턱을 넘었기 "
            f"때문입니다. ρ 는 방향을 보여주는 참고 통계량입니다 — "
            f"'상관이 유의하므로 과적합'이라고 인용하지 마세요.")

    # ── 순위 역전 ───────────────────────────────────────────────────────────
    st.markdown('#### 앞 절반 → 뒤 절반 순위 이동')
    rows = rank_shift_rows(d)
    st.plotly_chart(_rank_reversal_chart(rows, len(rows)), use_container_width=True)
    st.caption(
        f"태그 {len(rows)}개(가격 전용, 재무 스택 고정). 선이 서로 교차할수록 앞 절반의 "
        f"성적 순위가 뒤 절반에서 유지되지 않는다는 뜻입니다. 빨간 선이 초점 태그 "
        f"`{pr['focal_tag']}`(MA 20/60, 당시 현행안)입니다.")

    with st.expander('순위 표 — 태그별 앞·뒤 배수와 순위'):
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                     column_config={'초점': st.column_config.CheckboxColumn(disabled=True)})
        st.caption('배수는 반쪽별 net 총복리 Π(1+net) 입니다. 같은 구간 집합이라 '
                   '순위는 CAGR 대소와 동치입니다.')

    # ── 보조 통계 · 한계 ────────────────────────────────────────────────────
    sec = d.get('secondary_horizon_correlation') or {}
    if sec:
        with st.expander('보조 통계 — 관측기간↔성적 상관 (판정 비사용)'):
            cc = st.columns(2)
            cc[0].metric('앞 절반', f"{sec['front']:+.3f}")
            cc[1].metric('뒤 절반', f"{sec['back']:+.3f}")
            st.info(sec.get('status', ''))
            st.caption('§14-2 의 사후 가설("장기 창일수록 전이가 잘 된다")이 시간 축에서도 '
                       '보이는지 확인한 것입니다. **사후 가설이라 근거가 되지 않습니다** — '
                       '부호가 앞뒤로 뒤집힌 것 자체가 그 가설의 불안정성을 보여줍니다.')

    st.error(f"**{d['disclaimer']}**  \n"
             f"실행 전 명시된 한계: 반쪽당 {split['n_back']}구간뿐이라 잡음이 큽니다. "
             f"두 반쪽은 시장 국면이 다르므로(앞: 코로나 급락·급반등 포함) "
             f"**'과적합'과 '국면 차이'가 교락**됩니다. 결정적 증거가 아니라 세 번째 "
             f"각도입니다.")
    st.caption(f"근거 `{d['spec']}` · 산출물 `experiments/calendar_sens/time_split.json`")


#: `SeriesSpec.renderer` → 렌더 함수. 여기 없는 키는 raw fallback 으로 떨어진다.
B_RENDERERS = {
    'time_overfit': render_time_overfit,
}
