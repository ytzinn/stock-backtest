"""
Ablation Test 결과 분석 대시보드.
experiments/ablation/ JSON/CSV 파일을 읽어 시각화한다.
DB 연결 불필요 — 정적 파일 전용.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from dashboard.config import PROJECT_ROOT

ABLATION_DIR = PROJECT_ROOT / "experiments" / "ablation"

DET_TAGS  = ["D_rim_only", "E_screener_rim", "F_momentum_rim", "G_full"]
RAND_TAGS = ["A_random", "B_hard_random", "C_stability_random"]
ALL_TAGS  = RAND_TAGS + DET_TAGS

TAG_LABELS = {
    "A_random":           "A  랜덤 (필터 없음)",
    "B_hard_random":      "B  Hard + 랜덤",
    "C_stability_random": "C  Hard + Stability + 랜덤",
    "D_rim_only":         "D  Hard + Stability + RIM",
    "E_screener_rim":     "E  D + 팩터스크리닝",
    "F_momentum_rim":     "F  D + 모멘텀",
    "G_full":             "G  전체 (E + F)",
}

TAG_COLORS = {
    "D_rim_only":     "#3b82f6",
    "E_screener_rim": "#f59e0b",
    "F_momentum_rim": "#10b981",
    "G_full":         "#8b5cf6",
}

st.set_page_config(page_title="Ablation 분석", layout="wide", page_icon="📊")


# ── 데이터 로딩 ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_summary() -> dict:
    p = ABLATION_DIR / "summary.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


@st.cache_data(ttl=60)
def load_periods(tag: str) -> pd.DataFrame:
    p = ABLATION_DIR / f"{tag}_periods.csv"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)
    df["rebalance_date"] = pd.to_datetime(df["rebalance_date"])
    df["next_date"]      = pd.to_datetime(df["next_date"])
    return df


@st.cache_data(ttl=60)
def load_rand_dist(tag: str) -> pd.DataFrame:
    p = ABLATION_DIR / f"{tag}_dist.csv"
    return pd.read_csv(p) if p.exists() else pd.DataFrame()


# ── 메인 ────────────────────────────────────────────────────────────────────

summary   = load_summary()
scenarios = summary.get("scenarios", {})
judgements = summary.get("judgements", {})

st.title("📊 Ablation Test 결과 분석")
if summary:
    st.caption(f"생성: {summary.get('generated_at', '—')}  |  파일: {ABLATION_DIR}")
else:
    st.warning(f"summary.json을 찾을 수 없습니다: {ABLATION_DIR}")
    st.stop()

tab_overview, tab_period, tab_dist = st.tabs(["시나리오 비교", "구간별 분석", "랜덤 분포"])


# ══════════════════════════════════════════════════════════════════════════════
# 탭 1 — 시나리오 비교
# ══════════════════════════════════════════════════════════════════════════════

with tab_overview:

    # 판정 배지
    st.subheader("레이어별 기여도 판정")
    cols = st.columns(len(judgements) or 1)
    for col, (key, val) in zip(cols, judgements.items()):
        icon  = "✅" if val else "❌"
        bg    = "#dcfce7" if val else "#fee2e2"
        col.markdown(
            f"<div style='background:{bg};padding:10px 6px;border-radius:8px;"
            f"text-align:center;line-height:1.4'>"
            f"<span style='font-size:1.4rem'>{icon}</span><br>"
            f"<span style='font-size:0.78rem;color:#374151'>{key}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.divider()

    # CAGR 사다리 차트
    st.subheader("CAGR 사다리 (A → G)")

    cagr_rows = []
    for tag in ALL_TAGS:
        s    = scenarios.get(tag, {})
        cagr = s.get("cagr") or s.get("median_cagr")
        if cagr is not None:
            label = TAG_LABELS.get(tag, tag)
            is_rand = tag in RAND_TAGS
            cagr_rows.append({"tag": tag, "label": label, "cagr": cagr * 100, "rand": is_rand})

    if cagr_rows:
        benchmark = scenarios.get("D_rim_only", {}).get("benchmark_cagr", 0) * 100

        fig = go.Figure()
        for r in cagr_rows:
            color = "#94a3b8" if r["rand"] else TAG_COLORS.get(r["tag"], "#3b82f6")
            fig.add_trace(go.Bar(
                x=[r["cagr"]], y=[r["label"]],
                orientation="h",
                marker_color=color,
                text=f"{r['cagr']:.1f}%",
                textposition="outside",
                name=r["label"],
                showlegend=False,
                hovertemplate=f"{r['label']}<br>CAGR: {r['cagr']:.2f}%<extra></extra>",
            ))
        fig.add_vline(
            x=benchmark, line_dash="dash", line_color="red", line_width=1.5,
            annotation_text=f"KOSPI {benchmark:.1f}%",
            annotation_position="top right",
            annotation_font_color="red",
        )
        fig.update_layout(
            height=380,
            xaxis_title="CAGR (%)",
            yaxis={"categoryorder": "array", "categoryarray": [r["label"] for r in reversed(cagr_rows)]},
            margin=dict(l=10, r=80, t=10, b=30),
            plot_bgcolor="white",
        )
        st.plotly_chart(fig, use_container_width=True)

    # 전체 리스크 지표 테이블
    st.subheader("전체 시나리오 지표")
    table_rows = []
    for tag in ALL_TAGS:
        s = scenarios.get(tag, {})
        if not s:
            continue
        cagr = s.get("cagr") or s.get("median_cagr", 0)
        table_rows.append({
            "시나리오":     TAG_LABELS.get(tag, tag),
            "CAGR":        f"{cagr * 100:.1f}%",
            "Alpha":       f"{s['alpha'] * 100:.1f}%" if "alpha" in s else "—",
            "MDD":         f"{s['mdd'] * 100:.1f}%"  if "mdd"   in s else "—",
            "Sharpe":      f"{s['sharpe']:.2f}"       if "sharpe" in s else "—",
            "Robustness":  f"{s['robustness'] * 100:.0f}%" if "robustness" in s
                           else f"n={s.get('n_repeats', '—')}회",
        })
    st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# 탭 2 — 구간별 분석
# ══════════════════════════════════════════════════════════════════════════════

with tab_period:
    available = [t for t in DET_TAGS if not load_periods(t).empty]

    if not available:
        st.info(
            "구간별 데이터(`*_periods.csv`)가 없습니다. "
            "서버에서 `--det-only` 옵션으로 ablation을 재실행하면 생성됩니다."
        )
        st.stop()

    # ── 컨트롤 ────────────────────────────────────────────────────────────────
    ctrl1, ctrl2 = st.columns([3, 2])

    with ctrl1:
        selected_tags = st.multiselect(
            "시나리오 선택",
            options=available,
            default=available,
            format_func=lambda t: TAG_LABELS.get(t, t),
        )

    base_df   = load_periods("D_rim_only")
    all_dates = sorted(base_df["rebalance_date"].dt.date.tolist())

    with ctrl2:
        date_range = st.select_slider(
            "분석 구간",
            options=all_dates,
            value=(all_dates[0], all_dates[-1]),
        )

    if not selected_tags:
        st.info("시나리오를 하나 이상 선택하세요.")
        st.stop()

    start_dt = pd.Timestamp(date_range[0])
    end_dt   = pd.Timestamp(date_range[1])

    def filtered(tag: str) -> pd.DataFrame:
        df = load_periods(tag)
        return df[(df["rebalance_date"] >= start_dt) & (df["rebalance_date"] <= end_dt)].sort_values("rebalance_date").copy()

    # ── 누적 수익률 ────────────────────────────────────────────────────────────
    st.subheader("누적 수익률")
    cum_fig = go.Figure()

    for tag in selected_tags:
        df = filtered(tag)
        if df.empty:
            continue
        df["cum"] = (1 + df["period_return"]).cumprod() - 1
        cum_fig.add_trace(go.Scatter(
            x=df["rebalance_date"], y=df["cum"] * 100,
            name=TAG_LABELS.get(tag, tag),
            mode="lines+markers",
            line=dict(color=TAG_COLORS.get(tag, None), width=2),
            marker=dict(size=6),
            hovertemplate="%{x|%Y-%m-%d}<br>누적: %{y:.1f}%<extra>" + TAG_LABELS.get(tag, tag) + "</extra>",
        ))

    # KOSPI
    df_k = filtered("D_rim_only")
    if not df_k.empty:
        df_k["cum_k"] = (1 + df_k["kospi_return"]).cumprod() - 1
        cum_fig.add_trace(go.Scatter(
            x=df_k["rebalance_date"], y=df_k["cum_k"] * 100,
            name="KOSPI", mode="lines+markers",
            line=dict(dash="dot", color="#6b7280", width=1.5),
            marker=dict(size=4),
            hovertemplate="%{x|%Y-%m-%d}<br>KOSPI 누적: %{y:.1f}%<extra></extra>",
        ))

    cum_fig.update_layout(
        height=360, yaxis_title="누적 수익률 (%)",
        hovermode="x unified", legend=dict(orientation="h", y=-0.15),
        margin=dict(t=10, b=50), plot_bgcolor="white",
    )
    cum_fig.update_xaxes(showgrid=True, gridcolor="#f0f0f0")
    cum_fig.update_yaxes(showgrid=True, gridcolor="#f0f0f0", zeroline=True, zerolinecolor="#d1d5db")
    st.plotly_chart(cum_fig, use_container_width=True)

    # ── 구간 수익률 + Alpha ────────────────────────────────────────────────────
    col_ret, col_alpha = st.columns(2)

    with col_ret:
        st.subheader("구간별 수익률")
        bar_fig = go.Figure()
        for tag in selected_tags:
            df = filtered(tag)
            if df.empty:
                continue
            bar_fig.add_trace(go.Bar(
                x=df["rebalance_date"].dt.strftime("%y.%m"),
                y=df["period_return"] * 100,
                name=TAG_LABELS.get(tag, tag),
                marker_color=TAG_COLORS.get(tag, None),
                hovertemplate="%{x}<br>수익률: %{y:.1f}%<extra>" + TAG_LABELS.get(tag, tag) + "</extra>",
            ))
        bar_fig.add_hline(y=0, line_color="#9ca3af", line_width=1)
        bar_fig.update_layout(
            height=300, yaxis_title="수익률 (%)", barmode="group",
            margin=dict(t=10, b=10), plot_bgcolor="white",
            legend=dict(orientation="h", y=-0.2),
        )
        st.plotly_chart(bar_fig, use_container_width=True)

    with col_alpha:
        st.subheader("구간별 Alpha (전략 − KOSPI)")
        alpha_fig = go.Figure()
        for tag in selected_tags:
            df = filtered(tag)
            if df.empty:
                continue
            df["alpha"] = (df["period_return"] - df["kospi_return"]) * 100
            if len(selected_tags) == 1:
                colors = ["#22c55e" if v >= 0 else "#ef4444" for v in df["alpha"]]
            else:
                colors = TAG_COLORS.get(tag, None)
            alpha_fig.add_trace(go.Bar(
                x=df["rebalance_date"].dt.strftime("%y.%m"),
                y=df["alpha"],
                name=TAG_LABELS.get(tag, tag),
                marker_color=colors,
                hovertemplate="%{x}<br>Alpha: %{y:.1f}%<extra>" + TAG_LABELS.get(tag, tag) + "</extra>",
            ))
        alpha_fig.add_hline(y=0, line_color="#9ca3af", line_width=1)
        alpha_fig.update_layout(
            height=300, yaxis_title="Alpha (%)", barmode="group",
            margin=dict(t=10, b=10), plot_bgcolor="white",
            legend=dict(orientation="h", y=-0.2),
        )
        st.plotly_chart(alpha_fig, use_container_width=True)

    # ── 필터 퍼널 ──────────────────────────────────────────────────────────────
    st.subheader("필터별 통과 종목 수 (시나리오 선택 시 첫 번째 기준)")
    funnel_tag = selected_tags[0]
    df_f = filtered(funnel_tag)

    funnel_cols = {
        "n_gate":           ("Gate PASS", "#94a3b8"),
        "hard_passed":      ("Hard Filter", "#60a5fa"),
        "stability_passed": ("Stability Filter", "#34d399"),
        "screener_passed":  ("Factor Screener", "#fbbf24"),
        "momentum_passed":  ("Momentum Filter", "#a78bfa"),
    }
    avail_cols = [c for c in funnel_cols if c in df_f.columns and df_f[c].notna().any()]

    if avail_cols:
        funnel_fig = go.Figure()
        for col in avail_cols:
            label, color = funnel_cols[col]
            funnel_fig.add_trace(go.Scatter(
                x=df_f["rebalance_date"], y=df_f[col],
                name=label, mode="lines+markers",
                line=dict(color=color, width=2),
                marker=dict(size=5),
                hovertemplate="%{x|%Y-%m-%d}<br>" + label + ": %{y}종목<extra></extra>",
            ))
        funnel_fig.update_layout(
            height=280, yaxis_title="통과 종목 수",
            hovermode="x unified", legend=dict(orientation="h", y=-0.2),
            margin=dict(t=10, b=50), plot_bgcolor="white",
        )
        funnel_fig.update_xaxes(showgrid=True, gridcolor="#f0f0f0")
        funnel_fig.update_yaxes(showgrid=True, gridcolor="#f0f0f0")
        st.plotly_chart(funnel_fig, use_container_width=True)
        st.caption(f"기준 시나리오: {TAG_LABELS.get(funnel_tag, funnel_tag)}")

    # ── 구간별 상세 테이블 ────────────────────────────────────────────────────
    with st.expander("구간별 수치 테이블", expanded=False):
        for tag in selected_tags:
            df = filtered(tag)
            if df.empty:
                continue
            df_show = df.copy()
            df_show["period_return"] = (df_show["period_return"] * 100).round(2).astype(str) + "%"
            df_show["kospi_return"]  = (df_show["kospi_return"]  * 100).round(2).astype(str) + "%"
            df_show["alpha"]         = ((df["period_return"].astype(float) - df["kospi_return"].astype(float)) * 100).round(2).astype(str) + "%"
            st.caption(TAG_LABELS.get(tag, tag))
            st.dataframe(
                df_show[["rebalance_date", "period_return", "kospi_return", "alpha", "n_gate", "n_stocks"]],
                use_container_width=True, hide_index=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
# 탭 3 — 랜덤 분포
# ══════════════════════════════════════════════════════════════════════════════

with tab_dist:
    st.subheader("랜덤 벤치마크 분포 (500회 반복)")
    st.caption("회색 수직선: 결정적 시나리오 CAGR. 분포가 해당 선 왼쪽에 치우칠수록 전략이 랜덤 대비 우수.")

    det_cagrs = {
        tag: scenarios[tag]["cagr"] * 100
        for tag in DET_TAGS
        if tag in scenarios and "cagr" in scenarios[tag]
    }

    for rand_tag in RAND_TAGS:
        df_r = load_rand_dist(rand_tag)
        if df_r.empty:
            st.caption(f"{rand_tag}: 데이터 없음")
            continue

        cagrs = df_r["cagr"] * 100
        st.markdown(f"**{TAG_LABELS.get(rand_tag, rand_tag)}**")
        c_info, c_chart = st.columns([1, 4])

        with c_info:
            n = len(cagrs)
            st.metric("중앙값 CAGR", f"{cagrs.median():.1f}%")
            st.metric("p5",          f"{cagrs.quantile(0.05):.1f}%")
            st.metric("p95",         f"{cagrs.quantile(0.95):.1f}%")

        with c_chart:
            hist_fig = go.Figure()
            hist_fig.add_trace(go.Histogram(
                x=cagrs, nbinsx=40,
                marker_color="#94a3b8", opacity=0.8,
                name=TAG_LABELS.get(rand_tag, rand_tag),
                hovertemplate="CAGR: %{x:.1f}%<br>빈도: %{y}<extra></extra>",
            ))
            for det_tag, det_val in det_cagrs.items():
                hist_fig.add_vline(
                    x=det_val,
                    line_color=TAG_COLORS.get(det_tag, "#374151"),
                    line_width=2, line_dash="solid",
                    annotation_text=TAG_LABELS.get(det_tag, det_tag).split()[0],
                    annotation_position="top",
                    annotation_font_size=10,
                )
            hist_fig.update_layout(
                height=220, xaxis_title="CAGR (%)", yaxis_title="빈도",
                showlegend=False, margin=dict(t=20, b=20, l=10, r=10),
                plot_bgcolor="white",
            )
            st.plotly_chart(hist_fig, use_container_width=True)

        # 시나리오별 percentile
        pct_cols = st.columns(len(det_cagrs))
        for col, (det_tag, det_val) in zip(pct_cols, det_cagrs.items()):
            pct = (cagrs < det_val).mean() * 100
            col.metric(
                f"{TAG_LABELS.get(det_tag, det_tag).split()[0]} percentile",
                f"{pct:.0f}번째",
                help=f"랜덤 500회 중 {pct:.0f}%가 {det_tag}보다 낮은 CAGR",
            )
        st.divider()
