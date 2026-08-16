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
    RUNS_DIR,
    alpha_survives_episode_22,
    block_sensitivity_rows,
    bootstrap_excludes_zero,
    contrast_rows,
    daily_nav_summary,
    decomposition,
    direction_hold_is_undefined,
    g5_verdict,
    mdd_layers,
    nav_gate_rows,
    layer2_frame,
    layer2_gate_rows,
    membership_verdict_rows,
    missing_provenance,
    paired_rows,
    phase_b_runs,
    preferred_scan,
    rank_shift_rows,
    reconciliation_rows,
    rule_membership,
    stage_b,
    time_split,
    verdict_rule_rows,
    victim_rows,
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
            name=r['전략'],
            text=[r['전략'] if focal else '', r['전략'] if focal else ''],
            textposition=['middle left', 'middle right'],
            textfont=dict(size=11, color='#dc2626'),
            line=dict(color='#dc2626' if focal else '#cbd5e1',
                      width=3 if focal else 1.5),
            marker=dict(size=9 if focal else 5),
            opacity=1.0 if focal else 0.65,
            hovertemplate=f'<b>{r["전략"]}</b><br>%{{x}} %{{y}}위<extra></extra>'))
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
            f"유의해서 나온 것이 **아니라**, 초점 전략이 위 사전등록 문턱을 넘었기 "
            f"때문입니다. ρ 는 방향을 보여주는 참고 통계량입니다 — "
            f"'상관이 유의하므로 과적합'이라고 인용하지 마세요.")

    # ── 순위 역전 ───────────────────────────────────────────────────────────
    st.markdown('#### 앞 절반 → 뒤 절반 순위 이동')
    rows = rank_shift_rows(d)
    st.plotly_chart(_rank_reversal_chart(rows, len(rows)), use_container_width=True)
    st.caption(
        f"전략 {len(rows)}개(가격 전용, 재무 스택 고정). 선이 서로 교차할수록 앞 절반의 "
        f"성적 순위가 뒤 절반에서 유지되지 않는다는 뜻입니다. 빨간 선이 초점 전략 "
        f"`{pr['focal_tag']}`(MA 20/60, 당시 현행안)입니다.")

    with st.expander('순위 표 — 전략별 앞·뒤 배수와 순위'):
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


def _forest_chart(d: dict) -> go.Figure:
    """contrast 별 효과 신뢰구간. 캘린더 두 안을 **같은 행에 나란히** 놓는다.

    Q2 가 묻는 것은 "룰에 대한 결론이 캘린더를 바꿔도 유지되나"다. 두 그림을 위아래로
    나누면 사람이 눈으로 짝을 맞춰야 하고, 그 순간 질문이 "각 캘린더에서 무엇이
    유의한가"로 바뀐다. 짝을 화면이 맞춰 줘야 원래 질문이 유지된다.
    """
    eps = d['pre_registered']['epsilon_net_log']
    contrasts = d['contrasts_single_axis'] + d['contrasts_multi_axis']
    fig = go.Figure()

    # 동등성 구간(±ε). 사전등록된 "실질적으로 같다"의 폭이라 배경으로 깔아야
    # 점추정의 크기를 그 기준으로 읽게 된다.
    fig.add_vrect(x0=-eps, x1=eps, fillcolor='#e2e8f0', opacity=0.55, line_width=0,
                  annotation_text=f'동등 구간 ±{eps}', annotation_position='top left',
                  annotation_font_size=10)
    fig.add_vline(x=0, line_color='#334155', line_width=1)

    for cal, key_e, key_ci, color in (
            ('현행 반기', 'e_semiannual_net', 'e_semiannual_ci95', '#1d4ed8'),
            ('안C (위상 이동)', 'e_altC_net', 'e_altC_ci95', '#ea580c')):
        labels = [f"{c['contrast_id']}  ({c['axis']})" for c in contrasts]
        pts = [c[key_e] for c in contrasts]
        fig.add_trace(go.Scatter(
            x=pts, y=labels, mode='markers', name=cal,
            marker=dict(size=10, color=color,
                        symbol=['circle' if c['single_axis'] else 'diamond'
                                for c in contrasts]),
            error_x=dict(type='data', symmetric=False,
                         array=[c[key_ci][1] - c[key_e] for c in contrasts],
                         arrayminus=[c[key_e] - c[key_ci][0] for c in contrasts],
                         color=color, thickness=1.4, width=5),
            hovertemplate='%{y}<br>' + cal +
                          ' e=%{x:.4f}<extra></extra>'))

    fig.update_layout(
        height=60 + 52 * len(contrasts), plot_bgcolor='white',
        xaxis_title='효과 e (net, 로그성장률 g 차이)',
        margin=dict(t=30, b=40, l=10, r=10),
        legend=dict(orientation='h', y=-0.18),
        yaxis=dict(autorange='reversed', gridcolor='#f1f5f9'))
    return fig


def render_calendar_bootstrap() -> None:
    """SPEC_14 B단계 — 캘린더 민감도 block-bootstrap."""
    d = stage_b()
    if d is None:
        st.info('`experiments/calendar_sens/stage_b.json` 이 없습니다 — 산출물은 '
                '서버가 원본입니다.')
        return

    pr, j, sm = d['pre_registered'], d['judgment'], d['summary_metrics']
    cp = d['common_period']

    # ── 판정 3종 + 조치 ─────────────────────────────────────────────────────
    st.markdown(
        f"<span style='background:#dc2626;color:white;padding:3px 12px;border-radius:6px;"
        f"font-weight:600'>{j['action']}</span> "
        f"<span style='color:#6b7280;font-size:0.85rem'>· 산출 {d['generated_at'][:10]}"
        f" · 공통구간 {cp['S']}~{cp['E']} ({cp['years']:.2f}년)</span>",
        unsafe_allow_html=True)
    st.caption(f"**{j['text']}**")

    c = st.columns(3)
    for col, (code, label) in zip(c, (
            (j['Q1'], 'Q1 — 캘린더 수준효과'),
            (j['Q2_D'], 'Q2-D — 룰 방향 견고성'),
            (j['Q2_M'], 'Q2-M — 효과크기 견고성'))):
        col.metric(label, code.split('_', 1)[-1] if '_' in code else code,
                   help=code)

    # ── 방향 일치율이 "0%" 가 아니라 "잴 수 없음" 이라는 사실 ────────────────
    if direction_hold_is_undefined(d):
        st.warning(
            f"**방향 일치율(J1)은 0% 가 아니라 정의되지 않습니다.** 분모가 "
            f"{sm['J1_denominator']} 입니다 — J 계열이 세는 단일축 룰 contrast "
            f"{sm['n_neutral_inconclusive']}개가 **전부 `neutral_or_inconclusive`** 라 "
            f"방향이 잡힌 것이 하나도 없고, 그래서 나눌 분모가 없습니다. "
            f"'0%'로 읽으면 *캘린더가 룰 결론을 전부 뒤집었다*는 뜻이 되는데, 실제 결과는 "
            f"*애초에 뒤집힐 만큼 뚜렷한 방향이 없었다* 입니다. "
            f"명확한 역전(J3)도 {sm['J3_clear_reversals']}건입니다.")

    # ── 사전등록 문턱 ───────────────────────────────────────────────────────
    with st.expander('📌 사전등록 문턱 — 수치 산출 **전**에 확정됐다', expanded=True):
        # 값 열은 **전부 문자열**로 통일한다. 숫자와 문자열을 섞으면 Arrow 직렬화가
        # 실패하고 Streamlit 이 조용히 타입을 고쳐 놓는다 (비교표에서 이미 겪은 함정).
        st.dataframe(pd.DataFrame([
            {'항목': '동등 구간 ε (net log)', '값': f"{pr['epsilon_net_log']}"},
            {'항목': '방향 확률 문턱', '값': f"{pr['sign_prob_threshold']}"},
            {'항목': 'Q1 큰 효과 / 동등 경계', '값': f"{pr['q1_large_abs']} / {pr['q1_equiv_bound']}"},
            {'항목': 'Q2-M 효과크기 문턱', '값': f"{pr['q2m_abs']}"},
            {'항목': 'Q2-D 역전 / 중립 허용 개수', '값': f"{pr['q2d_reversal_large']} / {pr['q2d_neutral_max']}"},
            {'항목': '블록 길이 · 재표본', '값': f"{pr['block_days']}일 · {pr['n_resamples']:,}회"},
            {'항목': 'RNG seed', '값': pr['rng_seed']},
        ]), use_container_width=True, hide_index=True)
        st.caption(f"{pr['note']} · g 정의: `{pr['g_definition']}`")

    # ── forest plot ─────────────────────────────────────────────────────────
    st.markdown('#### contrast 별 효과와 95% 신뢰구간')
    st.plotly_chart(_forest_chart(d), use_container_width=True)
    st.caption(
        '● 단일축 · ◆ 다축입니다. 다축(`C_R3R4`·`C_STAB`)은 룰을 여러 개 동시에 '
        '건드리므로 **어느 룰의 효과인지 말할 수 없습니다.** 회색 띠는 사전등록된 '
        '동등 구간(±ε)이고, 점추정이 그 안에 있으면 "실질적으로 차이 없음"입니다.')

    rows = contrast_rows(d)
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                 column_config={
                     '단일축': st.column_config.CheckboxColumn(disabled=True),
                     'δ 0 배제': st.column_config.CheckboxColumn(disabled=True),
                     **{k: st.column_config.NumberColumn(format='%.4f') for k in
                        ('반기 e (net)', '안C e (net)', 'δ (안C−반기)',
                         'δ CI95 하한', 'δ CI95 상한')}})
    st.caption('δ 는 캘린더를 안C 로 바꿨을 때 그 contrast 의 효과가 얼마나 달라지는가입니다. '
               'δ 의 CI 가 0을 배제하면 "캘린더가 이 룰의 결론을 바꾼다"가 됩니다.')

    # ── 블록 길이 민감도 ────────────────────────────────────────────────────
    bls_rows = block_sensitivity_rows(d)
    if bls_rows:
        flipped = [r for r in bls_rows if r['21일 대비 뒤집힘'] != '—']
        with st.expander(
                f'🧱 블록 길이 민감도 — 결론이 뒤집히는 블록이 {len(flipped)}개 있다',
                expanded=bool(flipped)):
            st.dataframe(pd.DataFrame(bls_rows), use_container_width=True, hide_index=True,
                         column_config={
                             '사전등록': st.column_config.CheckboxColumn(disabled=True),
                             **{k: st.column_config.NumberColumn(format='%.4f')
                                for k in ('δ_ew CI95 하한', 'δ_ew CI95 상한')}})
            st.caption(
                f"{d['block_length_sensitivity'].get('status', '')} — 즉 이 표로 판정을 "
                f"바꾸지 않습니다. 다만 `0 배제` 가 블록 길이에 따라 들락거린다면 그 "
                f"결론은 블록 선택에 얹혀 있다는 뜻이라, 단단한 사실로 인용하면 안 됩니다.")

    # ── 랭킹×컷 2×2 — 기간이 달라 같은 그림에 못 올린다 ─────────────────────
    rc = d.get('contrasts_rank_cut_2x2') or {}
    if rc:
        with st.expander(f"🔀 랭킹×컷 2×2 ({len(rc.get('cells', []))}셀) — 위 forest plot 과 "
                         f"**같이 읽으면 안 되는** 별도 설계"):
            w = rc['window']
            st.error(f"**{rc['not_comparable_with']}**  \n"
                     f"이 블록의 구간은 `{w['start']}~{w['end']}` ({w['years']:.2f}년)로 "
                     f"룰 contrast({cp['S']}~{cp['E']})와 다릅니다. 이유: {w['why']}")
            st.dataframe(pd.DataFrame([{
                'contrast': c['contrast_id'], '축': c['axis'],
                '단일축': c['single_axis'],
                '반기 e (net)': c['e_semiannual_net'],
                '안C e (net)': c['e_altC_net'],
                '방향': c['direction_class'],
            } for c in rc['cells']]), use_container_width=True, hide_index=True,
                column_config={'단일축': st.column_config.CheckboxColumn(disabled=True),
                               **{k: st.column_config.NumberColumn(format='%.4f')
                                  for k in ('반기 e (net)', '안C e (net)')}})
            st.caption(f"**{rc['status']}**")

    # ── EW 수준효과 · 재현 정보 ─────────────────────────────────────────────
    ew = d.get('delta_ew') or {}
    if ew:
        with st.expander('📐 EW 수준효과 (δ_ew) — "순수 캘린더 효과"가 아니다'):
            cc = st.columns(3)
            cc[0].metric('δ_ew (net)', f"{ew['point_net']:+.4f}")
            cc[1].metric('CI95', f"[{ew['ci95'][0]:.3f}, {ew['ci95'][1]:.3f}]")
            cc[2].metric('EW CAGR 반기 → 안C',
                         f"{ew['cagr_ew_semiannual_net']:.2%} → {ew['cagr_ew_altC_net']:.2%}")
            st.info(f"**{ew['name']}**")

    prov = d.get('bootstrap_provenance') or {}
    st.error(f"**{d['disclaimer']}**")
    if prov:
        st.caption(f"재현: seed `{pr['rng_seed']}` · 블록 시작점 `{prov['starts_file']}` · "
                   f"digest `{prov['block_index_digest_sha256'][:16]}…` · "
                   f"확장규칙 `{prov['expansion_rule']}` · 근거 `{d['spec']}`")


def render_live_decomposition() -> None:
    """성과 분해 / 라이브 전환 (SPEC_11). **사전등록이 없는 축이다.**"""
    d = decomposition()
    rm = rule_membership()
    ps = preferred_scan()
    if d is None and rm is None:
        st.info('`experiments/analysis/` 분해 산출물이 없습니다 — 서버가 원본입니다.')
        return

    # ── 이 축이 다른 B형 축과 다른 점을 맨 먼저 말한다 ──────────────────────
    missing = missing_provenance(d)
    if missing:
        st.warning(
            f"**이 축의 산출물에는 `{'`·`'.join(missing)}` 가 없습니다.** "
            f"캘린더 민감도 축(시간분할·bootstrap)은 문턱을 수치 산출 전에 커밋해 두고 "
            f"그 문턱으로 판정했지만, 여기 수치들은 **사후에 들여다본 탐색적 진단**입니다. "
            f"축 상태가 `EXPLORING` 인 이유이고, 아래 숫자를 판정이나 채택 근거로 "
            f"인용하면 안 됩니다 — 문턱 없이 나온 값은 어떤 문턱에도 맞출 수 있습니다.")

    if d is not None:
        p, mv, vc = d['paired'], d['momentum_victims'], d['value_momentum_conflict']
        n = d['n_periods']
        st.caption(f"완결 {n}구간 · 산출 {d['generated_at'][:10]}")

        # ── F vs D ──────────────────────────────────────────────────────────
        st.markdown('#### 모멘텀을 넣으면(F) 빼는 것(D)보다 나은가')
        c = st.columns(4)
        c[0].metric('평균 구간차 (net)', f"{p['mean_diff_net']:+.2%}")
        c[1].metric('F 가 이긴 구간', f"{p['f_wins_net']} / {n}",
                    help='승률이 아니라 개수입니다. 20구간은 동전던지기와 구별하기 어렵습니다.')
        c[2].metric('평균 회전율 F', f"{p['f_avg_turnover']:.1%}",
                    delta=f"{p['f_avg_turnover'] - p['d_avg_turnover']:+.1%} vs D",
                    delta_color='inverse')
        c[3].metric('평균 회전율 D', f"{p['d_avg_turnover']:.1%}")
        st.caption(
            f"모멘텀은 평균 {p['mean_diff_net']:+.2%}p 를 더 벌지만 회전율을 "
            f"{p['d_avg_turnover']:.1%} → {p['f_avg_turnover']:.1%} 로 올립니다. "
            f"{n}구간 중 {p['f_wins_net']}구간에서 이겼습니다 — 이 표본으로 "
            f"'모멘텀이 우월하다'를 주장할 수는 없습니다.")

        with st.expander('구간별 F vs D'):
            st.dataframe(pd.DataFrame(paired_rows(d)), use_container_width=True,
                         hide_index=True, column_config={
                             k: st.column_config.NumberColumn(format='%.2f%%')
                             for k in ('F net', 'D net', '차이 (F−D)',
                                       'F 회전율', 'D 회전율')})

        # ── 희생자 ──────────────────────────────────────────────────────────
        st.markdown('#### 모멘텀이 걸러낸 종목(희생자)은 실제로 못했나')
        rows = victim_rows(d)
        won = [r for r in rows if r['희생자가 이겼나']]
        cc = st.columns(3)
        cc[0].metric('희생자가 F 보다 못한 구간',
                     f"{mv['victims_underperformed_f']} / {mv['periods_with_victims']}")
        cc[1].metric('희생자 평균 수익', f"{mv['mean_victim_ret']:.2%}")
        cc[2].metric('같은 구간 F (gross)', f"{mv['mean_f_gross_same_periods']:.2%}")
        st.caption(
            f"평균만 보면 필터가 옳아 보이지만, **{len(won)}개 구간에서는 희생자가 "
            f"F 를 이겼습니다.** 모멘텀 필터는 늘 맞는 규칙이 아니라 평균적으로 "
            f"유리한 쪽에 거는 규칙입니다.")

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=[r['구간 시작'] for r in rows],
            y=[r['희생자 평균 수익'] * 100 for r in rows], name='희생자 평균',
            marker_color=['#dc2626' if r['희생자가 이겼나'] else '#cbd5e1' for r in rows],
            hovertemplate='%{x}<br>희생자 %{y:.2f}%<extra></extra>'))
        fig.add_trace(go.Scatter(
            x=[r['구간 시작'] for r in rows],
            y=[r['F 구간 수익 (gross)'] * 100 for r in rows], name='F (gross)',
            mode='lines+markers', line=dict(color='#1d4ed8', width=2)))
        fig.update_layout(height=320, plot_bgcolor='white', barmode='group',
                          yaxis_title='구간 수익률 (%)',
                          xaxis=dict(type='category', tickangle=-45, tickfont_size=10),
                          margin=dict(t=10, b=70), legend=dict(orientation='h', y=-0.4))
        st.plotly_chart(fig, use_container_width=True)
        st.caption('빨간 막대가 희생자가 F 를 이긴 구간입니다.')

        with st.expander('구간별 희생자 표'):
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                         column_config={
                             '희생자가 이겼나': st.column_config.CheckboxColumn(disabled=True),
                             '희생자 평균 수익': st.column_config.NumberColumn(format='%.2f%%'),
                             'F 구간 수익 (gross)': st.column_config.NumberColumn(format='%.2f%%')})

        # ── 밸류-모멘텀 상충 · 보유 중복 ────────────────────────────────────
        j = d['jaccard']
        cols = st.columns(2)
        with cols[0]:
            st.markdown('##### 밸류 ↔ 모멘텀 상충')
            st.metric('탈락 종목이 더 싼 구간',
                      f"{vc['periods_rejected_cheaper_median']} / {n}")
            st.caption('모멘텀이 떨어뜨린 종목의 PBR 중앙값이 통과 종목보다 낮았던 '
                       '구간 수입니다. 밸류 전략이 모멘텀 때문에 싼 것을 버린다는 뜻입니다.')
        with cols[1]:
            st.markdown('##### 보유 종목 중복 (Jaccard)')
            st.metric('F vs D 평균', f"{j['f_vs_d_mean']:.3f}",
                      delta=f"최소 {j['f_vs_d_min']:.3f}", delta_color='off')
            st.caption(
                f"F 와 RIM 짝은 평균 {j['f_vs_rim_counterpart_mean']:.3f}, 최소 "
                f"{j['f_vs_rim_min']:.3f} 입니다. **최소 {j['f_vs_d_min']:.3f}** 은 "
                f"한 종목도 겹치지 않은 구간이 있다는 뜻입니다 — 이름이 비슷한 전략도 "
                f"실제 보유는 전혀 다를 수 있습니다.")

    # ── 룰 멤버십 ───────────────────────────────────────────────────────────
    if rm is not None:
        st.markdown('#### 룰 멤버십 — 어떤 룰이 실제로 편입을 바꾸나')
        st.caption(f"현행 룰셋 `{{{', '.join(rm['ruleset_full'])}}}` · "
                   f"산출 {rm['generated_at'][:10]} · {len(rm['per_date'])}구간")
        st.dataframe(pd.DataFrame(membership_verdict_rows(rm)),
                     use_container_width=True, hide_index=True)
        st.caption('**어긋난 날짜가 곧 판정의 강도입니다.** 20구간 중 한두 날짜에서만 '
                   '갈렸다면 "바꾼다/못 뺀다"는 결론은 그 날짜들에 얹혀 있습니다.')

    # ── 우선주 스캔 ─────────────────────────────────────────────────────────
    if ps is not None:
        with st.expander('🔎 우선주 혼입 스캔'):
            c = st.columns(3)
            c[0].metric('보유 티커', f"{ps['n_held_tickers']:,}")
            c[1].metric('의심 (보유)', len(ps['held_suspects']))
            c[2].metric('강한 의심', len(ps['strong_suspects_held']))
            if ps['held_suspects']:
                st.dataframe(pd.DataFrame(ps['held_suspects']),
                             use_container_width=True, hide_index=True)
            st.warning('이 산출물에는 `generated_at` 이 없습니다 — **언제 스캔한 것인지 '
                       '알 수 없습니다.** 신선도를 판정할 수 없으니 현재 상태의 근거로 '
                       '쓰지 마세요.')


#: 히트맵 행/열 축. 144 = 8행 × 9열 × 2시나리오.
_HEAT_ROW = ('tilt_option', 'normalization', 'alt_sleeve')
_HEAT_COL = ('overlay_freq', 'k')


def _heatmap(df, scenario: str, value: str) -> go.Figure:
    """Signal×Tilt 그리드. 조합 하나가 셀 하나다."""
    d = df[df['scenario'] == scenario].copy()
    d['_row'] = d[list(_HEAT_ROW)].astype(str).agg(' · '.join, axis=1)
    d['_col'] = d[list(_HEAT_COL)].astype(str).agg(' · '.join, axis=1)
    piv = d.pivot_table(index='_row', columns='_col', values=value, aggfunc='mean')
    fig = go.Figure(go.Heatmap(
        z=piv.values * 100, x=list(piv.columns), y=list(piv.index),
        colorscale='RdBu', zmid=0, colorbar=dict(title='%p'),
        hovertemplate='%{y}<br>%{x}<br>%{z:.2f}%p<extra></extra>'))
    fig.update_layout(height=340, margin=dict(t=10, b=40, l=10, r=10),
                      plot_bgcolor='white',
                      xaxis=dict(tickangle=-30, tickfont_size=10),
                      yaxis=dict(tickfont_size=10))
    return fig


def render_regime_overlay() -> None:
    """SPEC_08 Phase B — 레짐 신호를 sleeve tilt 로 수확하려는 시도."""
    runs = phase_b_runs()
    if not runs:
        st.info('`experiments/runs/*phaseB*.csv` 가 없습니다 — 서버가 원본입니다.')
        return

    current, superseded = runs[0], runs[1:]

    # ── 철회된 실행이 있다는 사실을 맨 먼저 ────────────────────────────────
    if superseded:
        old_counts = []
        for r in superseded:
            if 'layer2' in r:
                od = layer2_frame(r['layer2'])
                old_counts.append(
                    f"**{r['date']}: {int(od['is_candidate'].sum())}/{len(od)} 통과**")
        st.error(
            f"**이 축에는 철회된 실행이 {len(superseded)}개 있습니다.** "
            + (' · '.join(old_counts) + ' — 이 수치는 인용하면 안 됩니다. '
               if old_counts else '')
            + f"최초 실행은 always-on 비교군의 **구간 불일치 버그**로 나온 결과였습니다: "
              f"`net_port_return` 은 그 행이 실제로 커버하는 3~8개월을 복리 계산한 값인데 "
              f"비교군 `base_return` 은 **1개월치 스냅샷**이라, 분기·반기 오버레이에서 "
              f"기간이 다른 둘을 빼서 알파라고 불렀습니다. "
              f"정본은 **{current['date']}** 재실행입니다 "
              f"(`2026.07.11._REGIME_PHASE_B.md` §3).")

    if 'layer2' not in current:
        st.warning(f"{current['date']} 실행에 layer2 CSV 가 없습니다.")
        return

    df = layer2_frame(current['layer2'])
    st.caption(f"정본 `{current['layer2'].name}` · 조합 {len(df)}개")

    # ── Layer2 게이트 ───────────────────────────────────────────────────────
    st.markdown('#### Layer 2 사전 고정 구속조건 (C1~C4)')
    gate = pd.DataFrame(layer2_gate_rows(df))
    cols = st.columns(len(gate))
    for col, (_, r) in zip(cols, gate.iterrows()):
        col.metric(r['게이트'], f"{r['통과']} / {r['전체']}")
    st.caption('개별 게이트는 꽤 통과합니다. **넷을 동시에 넘는 조합이 0개**라는 것이 '
               '이 축의 결론입니다 — 개별 통과 수만 보면 "절반쯤 된다"로 잘못 읽힙니다.')

    # ── 알파가 에피소드 #22 에 몰려 있다 ────────────────────────────────────
    a = alpha_survives_episode_22(df)
    c = st.columns(3)
    c[0].metric('total_alpha > 0', f"{a['total_positive']} / {a['n']}")
    c[1].metric('ex22_alpha > 0', f"{a['ex22_positive']} / {a['n']}",
                delta=f"{a['ex22_positive'] - a['total_positive']:+d}",
                delta_color='inverse')
    c[2].metric('#22 비중 경고', f"{a['share_warn']} / {a['n']}")
    st.warning(
        f"**알파는 한 에피소드에 몰려 있습니다.** 전체 기간으로는 {a['total_positive']}개 "
        f"조합이 양(+)의 알파를 내지만, 에피소드 #22 를 빼면 {a['ex22_positive']}개만 "
        f"남습니다. `total_alpha` 만 보고 '절반은 된다'고 읽으면 안 되는 이유입니다.")

    # ── 히트맵 ──────────────────────────────────────────────────────────────
    st.markdown('#### Signal × Tilt 그리드')
    metric = st.selectbox(
        '표시할 값', ['cagr_improve_vs_always_on', 'mdd_improve_vs_always_on',
                   'total_alpha', 'ex22_alpha'], key='regime_metric',
        help='always-on 대비 개선폭. 파란색이 개선, 빨간색이 악화입니다.')
    for scenario in sorted(df['scenario'].unique()):
        st.markdown(f'**{scenario}**')
        st.plotly_chart(_heatmap(df, scenario, metric), use_container_width=True)
    st.caption('행 = tilt · 정규화 · 대체 sleeve, 열 = 오버레이 빈도 · K 강도입니다. '
               '값은 always-on(항상 투자) 대비 개선폭이며 0 이 기준선입니다.')

    with st.expander('조합별 전체 표'):
        st.dataframe(df, use_container_width=True, hide_index=True)

    # ── Phase A 진단 그림 — 이 버그의 영향을 받지 않았다 ────────────────────
    pngs = sorted(RUNS_DIR.glob('2026-07-07_*.png'))
    if pngs:
        with st.expander(f'📈 Phase A 진단 그림 {len(pngs)}장 — **여전히 유효하다**'):
            st.info('Phase A 는 별도 코드 경로라 위 버그의 영향을 받지 않았습니다. '
                    '반증된 것은 "그 신호를 선형 sleeve-tilt 로 수확하려는 시도"뿐이고, '
                    'value_spread 가 레짐을 리드-랙으로 예측한다는 진단 자체는 유효합니다.')
            for p in pngs:
                st.image(str(p), caption=p.name, use_container_width=True)


def render_daily_nav() -> None:
    """일별 NAV — **G5 판정과 낙폭 세 층**.

    이 축이 없던 동안 일별 NAV 36개 파일이 화면 밖에 있었다. 그런데 **G5 FAIL 이
    거기서 나온다** — 채택 보류의 유일한 사유이고, Sharpe·MDD 의 SSOT 이기도 하다
    (SPEC_13 §9-1). 판정 근거가 화면에 없으면 아무도 그게 낡았는지 모른다
    (`C_pbr_path_random` 이 G1 귀무분포인데 안 보이던 것과 같은 상황이었다).
    """
    from backtest.canonical_state import collect

    d = daily_nav_summary()
    if d is None:
        st.info('일별 NAV 산출물이 없습니다 — `python -m scripts.run_daily_nav` 로 만듭니다. '
                '대용량이라 git 미추적이고 **서버가 원본**입니다.')
        return

    key = collect()['key']
    tags = d.get('tags') or {}
    if key not in tags:
        st.error(f'현행 채택 산출물 `{key}` 의 일별 NAV 가 없습니다 — '
                 f'요약에 있는 것: {", ".join(sorted(tags))}')
        return
    t = tags[key]

    # ── G5 판정 + 문턱 ──────────────────────────────────────────────────────
    v = g5_verdict(t)
    ok = v['pass']
    st.markdown(
        f"<span style='background:{'#16a34a' if ok else '#dc2626'};color:white;"
        f"padding:3px 12px;border-radius:6px;font-size:0.85rem'>"
        f"G5 {'PASS' if ok else 'FAIL'}</span> "
        f"<b>일별 net MDD {v['mdd']:.2%}</b> vs 사전등록 한계 "
        f"<b>{v['limit']:.0%}</b> · 대상 <code>{key}</code>", unsafe_allow_html=True)
    st.caption(
        f"한계선은 2026-07-19 **사전 등록**(SPEC_10 §5)이고 실행 후 수정하지 않습니다. "
        f"낙폭 구간은 {v['peak']} → {v['trough']}, 최악월은 {v['worst_month']} "
        f"({v['worst_month_return']:.2%}). 공식 판정은 "
        f"`experiments/robustness/gate_results_{key}.json` 이 소유하고, 이 화면은 "
        f"값과 문턱을 나란히 놓을 뿐 새로 판정하지 않습니다.")

    # ── 낙폭 세 층 ──────────────────────────────────────────────────────────
    st.subheader('낙폭은 축이 둘이다 — 측정 빈도 × 비용')
    st.dataframe(pd.DataFrame(mdd_layers(t)), use_container_width=True, hide_index=True,
                 column_config={'앞 줄 대비 (%p)':
                                st.column_config.NumberColumn(format='%+.2f')})
    st.caption(
        '같은 전략의 낙폭이 −34% 로도 −58% 로도 인용돼 왔습니다. **하나만 밝히고 '
        '인용하면 24%p 가 사라집니다.** 위 표가 그 24%p 를 두 축으로 갈라 놓습니다 — '
        '대부분이 측정 빈도(구간 종점만 보느냐 일별 경로를 보느냐)에서 오고, '
        '거래비용 몫은 1%p 남짓입니다.')

    c = st.columns(4)
    net = t.get('net') or {}
    c[0].metric('일별 net CAGR', f"{t['net_cagr']:.2%}")
    c[1].metric('Sharpe (일별)', f"{net.get('daily_sharpe', float('nan')):.3f}")
    c[2].metric('연율 변동성', f"{net.get('daily_vol_ann', float('nan')):.2%}")
    c[3].metric('CVaR 5% (1개월)', f"{net.get('cvar_5pct_1m', float('nan')):.2%}")
    st.caption(f"일별 {net.get('n_days')}일 · {net.get('n_months')}개월 · "
               f"TE(KOSPI) {t.get('tracking_error_kospi', float('nan')):.2%}")

    # ── 정합 게이트 ─────────────────────────────────────────────────────────
    st.subheader('정합 게이트 — 이 값을 인용해도 되나')
    rows = nav_gate_rows(d)
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    failed = [r['전략'] for r in rows if r['정합 게이트'] != 'PASS']
    if failed:
        st.warning(
            f"**정합 게이트가 실패한 전략: {', '.join(f'`{x}`' for x in failed)}** — "
            f"구간 지표와 일별 재구성이 허용오차 안에서 만나지 않습니다. 그 전략의 "
            f"일별 값은 판정에 쓰면 안 됩니다. `U_pbr_path_ew` 는 필터 통과 **전 종목**을 "
            f"담아 상폐 haircut 시점 차이가 누적되는 것이 원인이고, 그래서 G2 는 일별이 "
            f"아니라 **구간** net 을 씁니다.")
        with st.expander(f'구간별 정합 대조 — `{failed[0]}`'):
            rec = reconciliation_rows(failed[0])
            if rec:
                st.dataframe(pd.DataFrame(rec), use_container_width=True, hide_index=True)
                bad = sum(1 for r in rec if not r['통과'])
                st.caption(f'{len(rec)}구간 중 **{bad}구간**이 허용오차를 넘습니다. '
                           f'"게이트 실패"가 전 구간이 틀렸다는 뜻은 아닙니다.')
            else:
                st.info('정합 CSV 가 이 PC 에 없습니다 — 서버가 원본입니다.')


#: `SeriesSpec.renderer` → 렌더 함수. 여기 없는 키는 raw fallback 으로 떨어진다.
B_RENDERERS = {
    'time_overfit': render_time_overfit,
    'calendar_bootstrap': render_calendar_bootstrap,
    'live_decomposition': render_live_decomposition,
    'regime_overlay': render_regime_overlay,
    'daily_nav': render_daily_nav,
}
