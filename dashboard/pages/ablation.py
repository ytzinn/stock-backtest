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

DET_TAGS    = ["D_rim_only", "E_screener_rim", "F_momentum_rim", "G_full", "H_no_stability"]
NO_R6_TAGS  = ["D_no_r6", "E_no_r6", "F_no_r6", "G_no_r6"]
RAND_TAGS   = ["A_random", "B_hard_random", "C_stability_random", "C_no_r6"]
ALL_TAGS    = RAND_TAGS + DET_TAGS

TAG_LABELS = {
    "A_random":           "A  랜덤 (필터 없음)",
    "B_hard_random":      "B  Hard + 랜덤",
    "C_stability_random": "C  Hard + Stability + 랜덤",
    "C_no_r6":            "C′ Hard + Stability(−R6) + 랜덤",
    "D_rim_only":         "D  Hard + Stability + RIM",
    "D_no_r6":            "D′ RIM (R6 제외)",
    "E_screener_rim":     "E  D + 팩터스크리닝",
    "E_no_r6":            "E′ E (R6 제외)",
    "F_momentum_rim":     "F  D + 모멘텀",
    "F_no_r6":            "F′ F (R6 제외)",
    "G_full":             "G  전체 (E + F)",
    "G_no_r6":            "G′ 전체 (R6 제외)",
    "H_no_stability":     "H  G − Stability",
}

TAG_COLORS = {
    "D_rim_only":     "#3b82f6",
    "D_no_r6":        "#93c5fd",
    "E_screener_rim": "#f59e0b",
    "E_no_r6":        "#fcd34d",
    "F_momentum_rim": "#10b981",
    "F_no_r6":        "#6ee7b7",
    "G_full":         "#8b5cf6",
    "G_no_r6":        "#c4b5fd",
    "H_no_stability": "#06b6d4",
}

st.set_page_config(page_title="Ablation 분석", layout="wide", page_icon="📊")


# ── 데이터 로딩 ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_summary() -> dict:
    p = ABLATION_DIR / "summary.json"
    summary = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    if "scenarios" not in summary:
        summary["scenarios"] = {}
    # summary.json에 없는 태그는 개별 {tag}.json으로 보완
    for tag in DET_TAGS:
        if tag not in summary["scenarios"]:
            jp = ABLATION_DIR / f"{tag}.json"
            if jp.exists():
                d = json.loads(jp.read_text(encoding="utf-8"))
                summary["scenarios"][tag] = {
                    k: v for k, v in d.items() if k not in ("tag", "run_at", "seed")
                }
    return summary


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


@st.cache_data(ttl=3600)
def fetch_index_returns(period_list: tuple, index_code: str) -> dict[str, float]:
    """(start_str, end_str) 튜플 목록 → {start_str: 구간수익률}. FDR 사용."""
    import FinanceDataReader as fdr
    result: dict[str, float] = {}
    for start_str, end_str in period_list:
        try:
            df = fdr.DataReader(index_code, start_str, end_str)
            if df is not None and not df.empty:
                c = df["Close"].dropna()
                result[start_str] = float(c.iloc[-1] / c.iloc[0] - 1) if len(c) >= 2 else 0.0
            else:
                result[start_str] = 0.0
        except Exception:
            result[start_str] = 0.0
    return result


# ── 메인 ────────────────────────────────────────────────────────────────────

summary    = load_summary()
scenarios  = summary.get("scenarios", {})
judgements = summary.get("judgements", {})

st.title("📊 Ablation Test 결과 분석")
if summary:
    st.caption(f"생성: {summary.get('generated_at', '—')}  |  파일: {ABLATION_DIR}")
else:
    st.warning(f"summary.json을 찾을 수 없습니다: {ABLATION_DIR}")
    st.stop()

tab_overview, tab_period, tab_dist = st.tabs(["시나리오 비교", "구간별 분석", "랜덤 분포"])

# ── 필터·시나리오 설명 상수 ────────────────────────────────────────────────────

FILTER_DESCRIPTIONS = [
    {
        "icon": "🔒",
        "name": "Hard Filter",
        "subtitle": "유동성 · 상장기간 필터",
        "body": (
            "백테스트에 참여할 수 있는 최소 자격을 검사합니다. "
            "일 평균 거래대금 1억 원 미만이거나 상장 6개월 미만인 종목은 제외합니다. "
            "거래량이 너무 적으면 실제 매매 시 가격이 크게 움직이거나(슬리피지) "
            "원하는 가격에 사고팔기 어렵기 때문에 현실적인 백테스트를 위해 필수입니다."
        ),
    },
    {
        "icon": "🏦",
        "name": "Stability Filter",
        "subtitle": "재무안정성 필터 — 하드 룰 6개 (하나라도 해당하면 탈락)",
        "body": (
            "6개 룰 중 하나라도 해당하면 탈락합니다.<br>"
            "<b>R1</b> 부채비율 &gt; 200%<br>"
            "<b>R2</b> 차입금비율 &gt; 150% — 단, 최근 3FY 단조 감소 + 10%p 이상 개선 중이면 예외<br>"
            "<b>R3</b> 최근 3FY 중 매출 YoY &minus;5% 이하 2회 이상<br>"
            "<b>R4</b> 영업현금흐름 2년 연속 음수<br>"
            "<b>R5</b> 영업CF &lt; 0 이면서 재무CF &gt; 0 (차입으로 운영)<br>"
            "<b>R6</b> adjROE &lt; 요구수익률 — 순이익·영업CF 혼합 ROE(Dechow 1994)가 "
            "자본비용에 못 미치면 RIM 적정가가 장부가를 밑도는 가치 파괴 구간"
        ),
    },
    {
        "icon": "🔍",
        "name": "Factor Screener",
        "subtitle": "팩터 스크리닝",
        "body": (
            "여러 재무 지표를 조합한 복합 점수로 상위 20% 종목을 선별합니다. "
            "사용 팩터: 매출 YoY 성장률(1/6), 영업이익 YoY 성장률(1/6), "
            "GPA—자산 대비 매출총이익(1/3), PBR 역수—저PBR 선호(1/3). "
            "성장성과 수익성이 높으면서도 저평가된 종목을 중점적으로 찾습니다."
        ),
    },
    {
        "icon": "📈",
        "name": "Momentum Filter",
        "subtitle": "모멘텀 필터",
        "body": (
            "현재 주가의 '방향성'을 확인합니다. "
            "주가가 20일·60일 이동평균선 위에 있고, 최근 20거래일 동안 추세가 상승 방향인 "
            "종목만 통과합니다. '좋은 기업이라도 지금 하락 중이면 사지 않는다'는 원칙으로, "
            "저가 매수 함정(Value Trap)을 피하고 가격이 실제로 움직이기 시작한 종목에 진입합니다."
        ),
    },
    {
        "icon": "💡",
        "name": "RIM 적정가 모델",
        "subtitle": "잔여이익모델 (Residual Income Model)",
        "body": (
            "기업의 이론적 적정 주가를 계산합니다. "
            "주주자본(Book Value)에서 출발해 앞으로 창출할 초과이익(ROE − 자본비용)을 "
            "더해 적정가를 산출합니다. 현재가가 적정가를 5% 이상 초과한 '고평가' 종목은 제외하고, "
            "남은 종목을 상승여력(적정가/현재가 − 1) 순으로 정렬해 상위 20개를 편입합니다."
        ),
    },
]

SCENARIO_TABLE = [
    # (label, hard, stability, screener, momentum, rim, selection)
    ("A  랜덤 (필터 없음)",            "—", "—", "—", "—", "—", "무작위 20개"),
    ("B  Hard + 랜덤",               "✓", "—", "—", "—", "—", "무작위 20개"),
    ("C  Hard + Stability + 랜덤",   "✓", "✓", "—", "—", "—", "무작위 20개"),
    ("D  Hard + Stability + RIM",   "✓", "✓", "—", "—", "✓", "RIM 상승여력순"),
    ("E  D + 팩터스크리닝",            "✓", "✓", "✓", "—", "✓", "RIM 상승여력순"),
    ("F  D + 모멘텀",                 "✓", "✓", "—", "✓", "✓", "RIM 상승여력순"),
    ("G  전체 (E + F)",               "✓", "✓", "✓", "✓", "✓", "RIM 상승여력순"),
    ("H  G − Stability",             "✓", "—", "✓", "✓", "✓", "RIM 상승여력순"),
]


# ══════════════════════════════════════════════════════════════════════════════
# 탭 1 — 시나리오 비교
# ══════════════════════════════════════════════════════════════════════════════

with tab_overview:

    # ── 시나리오·필터 설명 ────────────────────────────────────────────────────
    with st.expander("📖 시나리오 및 필터 설명 — 처음 보시는 분은 여기를 펼쳐보세요", expanded=False):

        st.markdown(
            "**Ablation Test**란 필터를 하나씩 추가해 가며 "
            "각 구성 요소가 수익률에 얼마나 기여하는지 측정하는 실험입니다. "
            "A(아무 필터 없는 랜덤 매매)에서 시작해 G(모든 필터 적용)까지 "
            "단계별로 성과를 비교합니다."
        )

        st.markdown("#### 필터 레이어 설명")
        for i in range(0, len(FILTER_DESCRIPTIONS), 2):
            cols = st.columns(2)
            for j, col in enumerate(cols):
                if i + j >= len(FILTER_DESCRIPTIONS):
                    break
                fd = FILTER_DESCRIPTIONS[i + j]
                col.markdown(
                    f"<div style='background:#f8fafc;border:1px solid #e2e8f0;"
                    f"border-radius:10px;padding:14px 16px;height:100%'>"
                    f"<div style='font-size:1.4rem;margin-bottom:4px'>{fd['icon']} "
                    f"<strong>{fd['name']}</strong></div>"
                    f"<div style='font-size:0.8rem;color:#64748b;margin-bottom:8px'>"
                    f"{fd['subtitle']}</div>"
                    f"<div style='font-size:0.85rem;color:#374151;line-height:1.6'>"
                    f"{fd['body']}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            st.write("")

        # RIM 설명은 마지막 카드 (혼자 남는 경우 처리)
        if len(FILTER_DESCRIPTIONS) % 2 == 1:
            pass  # 위 루프에서 이미 처리됨

        st.markdown("#### 시나리오 구성 (A → G)")
        st.markdown(
            "A~C는 **랜덤 피킹** 기준선 (필터 통과 후 무작위 20개 선택, 500회 반복)이고 "
            "D~G는 **RIM 모델 기반** 결정적 실행입니다. "
            "C→D 성과 차이가 RIM 모델 자체의 유효성을 보여줍니다."
        )

        sc_df = pd.DataFrame(
            SCENARIO_TABLE,
            columns=["시나리오", "Hard Filter", "Stability Filter",
                     "Factor Screener", "Momentum Filter", "RIM 모델", "종목 선택 방식"],
        )

        def _color_cell(val: str) -> str:
            if val == "✓":
                return "background-color:#dcfce7;color:#166534;font-weight:bold;text-align:center"
            if val == "—":
                return "background-color:#f1f5f9;color:#94a3b8;text-align:center"
            return ""

        st.dataframe(
            sc_df.style.applymap(
                _color_cell,
                subset=["Hard Filter", "Stability Filter",
                        "Factor Screener", "Momentum Filter", "RIM 모델"],
            ),
            use_container_width=True,
            hide_index=True,
        )

        st.markdown(
            "<div style='font-size:0.82rem;color:#6b7280;margin-top:8px'>"
            "✓ 활성 &nbsp;|&nbsp; — 비활성 &nbsp;|&nbsp; "
            "A/B/C: 500회 반복 실행 후 분포로 표현 &nbsp;|&nbsp; D~G: 단일 결정적 실행"
            "</div>",
            unsafe_allow_html=True,
        )

    st.subheader("레이어별 기여도 판정")
    cols = st.columns(len(judgements) or 1)
    for col, (key, val) in zip(cols, judgements.items()):
        icon = "✅" if val else "❌"
        bg   = "#dcfce7" if val else "#fee2e2"
        col.markdown(
            f"<div style='background:{bg};padding:10px 6px;border-radius:8px;"
            f"text-align:center;line-height:1.4'>"
            f"<span style='font-size:1.4rem'>{icon}</span><br>"
            f"<span style='font-size:0.78rem;color:#374151'>{key}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.divider()

    st.subheader("CAGR 사다리 (A → G, R6 제외 변형 포함)")
    cagr_rows = []
    # 표시 순서: 랜덤 기준선 → 결정론적(D/E/F/G 바로 뒤에 no_r6 삽입) → H
    _det_order = []
    for tag in DET_TAGS:
        _det_order.append(tag)
        no_r6 = tag.replace("_rim_only","_no_r6").replace("_screener_rim","_no_r6") \
                   .replace("_momentum_rim","_no_r6").replace("_full","_no_r6")
        if no_r6 in NO_R6_TAGS:
            _det_order.append(no_r6)
    _display_order = RAND_TAGS + _det_order
    for tag in _display_order:
        s    = scenarios.get(tag, {})
        cagr = s.get("cagr") or s.get("median_cagr")
        if cagr is not None:
            cagr_rows.append({"tag": tag, "label": TAG_LABELS.get(tag, tag),
                               "cagr": cagr * 100, "rand": tag in RAND_TAGS,
                               "no_r6": tag in NO_R6_TAGS})

    if cagr_rows:
        benchmark = scenarios.get("D_rim_only", {}).get("benchmark_cagr", 0) * 100
        fig = go.Figure()
        for r in cagr_rows:
            if r["rand"]:
                color, opacity, line = "#94a3b8", 1.0, dict(width=0)
            elif r["no_r6"]:
                color   = TAG_COLORS.get(r["tag"], "#93c5fd")
                opacity = 0.55
                line    = dict(color="#374151", width=1.5)
            else:
                color, opacity, line = TAG_COLORS.get(r["tag"], "#3b82f6"), 1.0, dict(width=0)
            fig.add_trace(go.Bar(
                x=[r["cagr"]], y=[r["label"]], orientation="h",
                marker_color=color, marker_opacity=opacity, marker_line=line,
                text=f"{r['cagr']:.1f}%", textposition="outside",
                name=r["label"], showlegend=False,
                hovertemplate=f"{r['label']}<br>CAGR: {r['cagr']:.2f}%<extra></extra>",
            ))
        fig.add_vline(x=benchmark, line_dash="dash", line_color="red", line_width=1.5,
                      annotation_text=f"KOSPI {benchmark:.1f}%",
                      annotation_position="top right", annotation_font_color="red")
        fig.update_layout(
            height=max(380, len(cagr_rows) * 32), xaxis_title="CAGR (%)",
            yaxis={"categoryorder": "array",
                   "categoryarray": [r["label"] for r in reversed(cagr_rows)]},
            margin=dict(l=10, r=80, t=10, b=30), plot_bgcolor="white",
        )
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("전체 시나리오 지표")
    table_rows = []
    for tag in ALL_TAGS:
        s = scenarios.get(tag, {})
        if not s:
            continue
        cagr = s.get("cagr") or s.get("median_cagr", 0)
        table_rows.append({
            "시나리오":    TAG_LABELS.get(tag, tag),
            "CAGR":       f"{cagr * 100:.1f}%",
            "Alpha":      f"{s['alpha'] * 100:.1f}%"    if "alpha"      in s else "—",
            "MDD":        f"{s['mdd'] * 100:.1f}%"      if "mdd"        in s else "—",
            "Sharpe":     f"{s['sharpe']:.2f}"           if "sharpe"     in s else "—",
            "Robustness": f"{s['robustness'] * 100:.0f}%" if "robustness" in s
                          else f"n={s.get('n_repeats', '—')}회",
        })
    st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

    # ── R6 필터 민감도 ────────────────────────────────────────────────────────
    r6_pairs = [("D_rim_only", "D_no_r6"), ("E_screener_rim", "E_no_r6"),
                ("F_momentum_rim", "F_no_r6"), ("G_full", "G_no_r6")]
    r6_avail = [(a, b) for a, b in r6_pairs
                if a in scenarios and b in scenarios]
    if r6_avail:
        st.divider()
        st.subheader("R6 필터 민감도 (adjROE < r 기준 탈락 On/Off)")
        st.caption("R6 제외 시 CAGR 변화. R6가 수익을 제한하면 제외 시 상승, 노이즈를 제거하면 하락.")
        r6_rows = []
        for tag_on, tag_off in r6_avail:
            s_on  = scenarios[tag_on]
            s_off = scenarios[tag_off]
            diff  = (s_off["cagr"] - s_on["cagr"]) * 100
            r6_rows.append({
                "시나리오":       TAG_LABELS.get(tag_on, tag_on),
                "R6 포함 CAGR":  f"{s_on['cagr'] * 100:.1f}%",
                "R6 제외 CAGR":  f"{s_off['cagr'] * 100:.1f}%",
                "차이 (제외−포함)": f"{diff:+.1f}%p",
            })
        st.dataframe(pd.DataFrame(r6_rows), use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# 탭 2 — 구간별 분석
# ══════════════════════════════════════════════════════════════════════════════

with tab_period:
    available = [t for t in DET_TAGS + NO_R6_TAGS if not load_periods(t).empty]

    if not available:
        st.info("구간별 데이터(`*_periods.csv`)가 없습니다. 서버에서 `--det-only`로 재실행하면 생성됩니다.")
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
        date_range = st.select_slider("분석 구간", options=all_dates,
                                      value=(all_dates[0], all_dates[-1]))

    if not selected_tags:
        st.info("시나리오를 하나 이상 선택하세요.")
        st.stop()

    start_dt = pd.Timestamp(date_range[0])
    end_dt   = pd.Timestamp(date_range[1])

    def filtered(tag: str) -> pd.DataFrame:
        df = load_periods(tag)
        return (df[(df["rebalance_date"] >= start_dt) & (df["rebalance_date"] <= end_dt)]
                .sort_values("rebalance_date").copy())

    # ── KOSDAQ 수익률 계산 (캐시) ─────────────────────────────────────────────
    period_list = tuple(
        (r["rebalance_date"].strftime("%Y-%m-%d"), r["next_date"].strftime("%Y-%m-%d"))
        for _, r in base_df.iterrows()
    )
    with st.spinner("KOSDAQ 데이터 로딩 중..."):
        kosdaq_dict = fetch_index_returns(period_list, "KQ11")

    base_df["kosdaq_return"] = (
        base_df["rebalance_date"].dt.strftime("%Y-%m-%d").map(kosdaq_dict)
    )

    def bench_filtered() -> pd.DataFrame:
        df = base_df.copy()
        return (df[(df["rebalance_date"] >= start_dt) & (df["rebalance_date"] <= end_dt)]
                .sort_values("rebalance_date"))

    # A_random 중앙값 CAGR → 기간별 참조값 계산
    a_median_cagr = scenarios.get("A_random", {}).get("median_cagr", None)

    # ── 누적 수익률 ────────────────────────────────────────────────────────────
    st.subheader("누적 수익률")
    cum_fig = go.Figure()

    # 전략 시나리오 (실선)
    for tag in selected_tags:
        df = filtered(tag)
        if df.empty:
            continue
        df["cum"] = (1 + df["period_return"]).cumprod() - 1
        cum_fig.add_trace(go.Scatter(
            x=df["rebalance_date"], y=df["cum"] * 100,
            name=TAG_LABELS.get(tag, tag), mode="lines+markers",
            line=dict(color=TAG_COLORS.get(tag, None), width=2),
            marker=dict(size=5),
            hovertemplate="%{x|%Y-%m-%d}<br>누적: %{y:.1f}%<extra>"
                          + TAG_LABELS.get(tag, tag) + "</extra>",
        ))

    df_b = bench_filtered()

    # KOSPI (점선 회색)
    if not df_b.empty and "kospi_return" in df_b.columns:
        df_b["cum_kospi"] = (1 + df_b["kospi_return"]).cumprod() - 1
        cum_fig.add_trace(go.Scatter(
            x=df_b["rebalance_date"], y=df_b["cum_kospi"] * 100,
            name="KOSPI", mode="lines+markers",
            line=dict(dash="dot", color="#6b7280", width=1.5), marker=dict(size=4),
            hovertemplate="%{x|%Y-%m-%d}<br>KOSPI 누적: %{y:.1f}%<extra></extra>",
        ))

    # KOSDAQ (점선 주황)
    if not df_b.empty and "kosdaq_return" in df_b.columns:
        df_b["cum_kosdaq"] = (1 + df_b["kosdaq_return"]).cumprod() - 1
        cum_fig.add_trace(go.Scatter(
            x=df_b["rebalance_date"], y=df_b["cum_kosdaq"] * 100,
            name="KOSDAQ", mode="lines+markers",
            line=dict(dash="dot", color="#f97316", width=1.5), marker=dict(size=4),
            hovertemplate="%{x|%Y-%m-%d}<br>KOSDAQ 누적: %{y:.1f}%<extra></extra>",
        ))

    # A_random 중앙값 (점선 연회색, CAGR 기반 직선 참조)
    if a_median_cagr is not None and not df_b.empty:
        start_d = df_b["rebalance_date"].min()
        years_e = (df_b["rebalance_date"] - start_d).dt.days / 365.25
        a_cum   = (1 + a_median_cagr) ** years_e - 1
        cum_fig.add_trace(go.Scatter(
            x=df_b["rebalance_date"], y=a_cum * 100,
            name=f"A 랜덤 중앙값 ({a_median_cagr*100:.1f}%/년)",
            mode="lines", line=dict(dash="dot", color="#a3a3a3", width=1.5),
            hovertemplate="%{x|%Y-%m-%d}<br>A 랜덤(참조): %{y:.1f}%<extra></extra>",
        ))

    cum_fig.update_layout(
        height=380, yaxis_title="누적 수익률 (%)",
        hovermode="x unified",
        legend=dict(orientation="h", y=-0.18, font_size=11),
        margin=dict(t=10, b=60), plot_bgcolor="white",
    )
    cum_fig.update_xaxes(showgrid=True, gridcolor="#f0f0f0")
    cum_fig.update_yaxes(showgrid=True, gridcolor="#f0f0f0",
                         zeroline=True, zerolinecolor="#d1d5db")
    st.plotly_chart(cum_fig, use_container_width=True)

    # ── 구간 수익률 + Alpha ────────────────────────────────────────────────────
    col_ret, col_alpha = st.columns(2)

    # 공통 x 레이블 (YYYY-MM 형식 → 숫자로 오인되지 않음)
    df_b_f = bench_filtered()
    x_labels = df_b_f["rebalance_date"].dt.strftime("%Y-%m").tolist()

    with col_ret:
        st.subheader("구간별 수익률")
        bar_fig = go.Figure()

        for tag in selected_tags:
            df = filtered(tag)
            if df.empty:
                continue
            bar_fig.add_trace(go.Bar(
                x=df["rebalance_date"].dt.strftime("%Y-%m"),
                y=df["period_return"] * 100,
                name=TAG_LABELS.get(tag, tag),
                marker_color=TAG_COLORS.get(tag, None),
                hovertemplate="%{x}<br>수익률: %{y:.1f}%<extra>"
                              + TAG_LABELS.get(tag, tag) + "</extra>",
            ))

        # KOSPI 라인 오버레이
        if not df_b_f.empty and "kospi_return" in df_b_f.columns:
            bar_fig.add_trace(go.Scatter(
                x=df_b_f["rebalance_date"].dt.strftime("%Y-%m"),
                y=df_b_f["kospi_return"] * 100,
                name="KOSPI", mode="lines+markers",
                line=dict(color="#6b7280", width=1.5, dash="dot"),
                marker=dict(size=5, symbol="diamond"),
                hovertemplate="%{x}<br>KOSPI: %{y:.1f}%<extra></extra>",
            ))

        # KOSDAQ 라인 오버레이
        if not df_b_f.empty and "kosdaq_return" in df_b_f.columns:
            bar_fig.add_trace(go.Scatter(
                x=df_b_f["rebalance_date"].dt.strftime("%Y-%m"),
                y=df_b_f["kosdaq_return"] * 100,
                name="KOSDAQ", mode="lines+markers",
                line=dict(color="#f97316", width=1.5, dash="dot"),
                marker=dict(size=5, symbol="diamond"),
                hovertemplate="%{x}<br>KOSDAQ: %{y:.1f}%<extra></extra>",
            ))

        # A_random 평균 수익률 참조선
        if a_median_cagr is not None:
            a_period_ret = (1 + a_median_cagr) ** 0.5 - 1  # 반기 환산
            bar_fig.add_hline(
                y=a_period_ret * 100,
                line_color="#a3a3a3", line_width=1.5, line_dash="dot",
                annotation_text=f"A 랜덤 중앙값 ({a_period_ret*100:.1f}%/반기)",
                annotation_font_size=10, annotation_position="top left",
            )

        bar_fig.add_hline(y=0, line_color="#9ca3af", line_width=0.8)
        bar_fig.update_layout(
            height=340, yaxis_title="수익률 (%)",
            barmode="group", bargap=0.15, bargroupgap=0.03,
            xaxis=dict(type="category", tickangle=-45, tickfont_size=10),
            margin=dict(t=10, b=60), plot_bgcolor="white",
            legend=dict(orientation="h", y=-0.3, font_size=10),
        )
        st.plotly_chart(bar_fig, use_container_width=True)

    with col_alpha:
        st.subheader("구간별 Alpha (전략 − KOSPI)")
        alpha_fig = go.Figure()

        single = len(selected_tags) == 1
        for tag in selected_tags:
            df = filtered(tag)
            if df.empty:
                continue
            df["alpha"] = (df["period_return"] - df["kospi_return"]) * 100
            if single:
                colors = ["#22c55e" if v >= 0 else "#ef4444" for v in df["alpha"]]
            else:
                colors = TAG_COLORS.get(tag, None)
            alpha_fig.add_trace(go.Bar(
                x=df["rebalance_date"].dt.strftime("%Y-%m"),
                y=df["alpha"],
                name=TAG_LABELS.get(tag, tag),
                marker_color=colors,
                showlegend=not single,
                hovertemplate="%{x}<br>Alpha: %{y:.1f}%<extra>"
                              + TAG_LABELS.get(tag, tag) + "</extra>",
            ))

        # 단일 시나리오일 때: 색상 의미를 legend에 표시
        if single:
            for name, color in [("양수 Alpha (초과수익)", "#22c55e"),
                                 ("음수 Alpha (미달)", "#ef4444")]:
                alpha_fig.add_trace(go.Bar(
                    x=[None], y=[None], name=name,
                    marker_color=color, showlegend=True,
                ))

        # KOSDAQ Alpha 라인 (KOSDAQ − KOSPI)
        if not df_b_f.empty and "kosdaq_return" in df_b_f.columns:
            kq_vs_kp = (df_b_f["kosdaq_return"] - df_b_f["kospi_return"]) * 100
            alpha_fig.add_trace(go.Scatter(
                x=df_b_f["rebalance_date"].dt.strftime("%Y-%m"),
                y=kq_vs_kp,
                name="KOSDAQ vs KOSPI", mode="lines+markers",
                line=dict(color="#f97316", width=1.5, dash="dot"),
                marker=dict(size=5, symbol="diamond"),
                hovertemplate="%{x}<br>KOSDAQ-KOSPI: %{y:.1f}%<extra></extra>",
            ))

        alpha_fig.add_hline(y=0, line_color="#9ca3af", line_width=0.8)
        alpha_fig.update_layout(
            height=340, yaxis_title="Alpha (%)",
            barmode="group", bargap=0.15, bargroupgap=0.03,
            xaxis=dict(type="category", tickangle=-45, tickfont_size=10),
            margin=dict(t=10, b=60), plot_bgcolor="white",
            legend=dict(orientation="h", y=-0.3, font_size=10),
        )
        st.plotly_chart(alpha_fig, use_container_width=True)

    # ── 필터 퍼널 ──────────────────────────────────────────────────────────────
    st.subheader("필터별 통과 종목 수")
    funnel_tag = selected_tags[0]
    df_f = filtered(funnel_tag)
    funnel_cols = {
        "n_gate":           ("Gate PASS",        "#94a3b8"),
        "hard_passed":      ("Hard Filter",       "#60a5fa"),
        "stability_passed": ("Stability Filter",  "#34d399"),
        "screener_passed":  ("Factor Screener",   "#fbbf24"),
        "momentum_passed":  ("Momentum Filter",   "#a78bfa"),
    }
    avail_cols = [c for c in funnel_cols if c in df_f.columns and df_f[c].notna().any()]
    if avail_cols:
        funnel_fig = go.Figure()
        for col in avail_cols:
            label, color = funnel_cols[col]
            funnel_fig.add_trace(go.Scatter(
                x=df_f["rebalance_date"], y=df_f[col],
                name=label, mode="lines+markers",
                line=dict(color=color, width=2), marker=dict(size=5),
                hovertemplate="%{x|%Y-%m-%d}<br>" + label + ": %{y}종목<extra></extra>",
            ))
        funnel_fig.update_layout(
            height=260, yaxis_title="통과 종목 수",
            hovermode="x unified",
            legend=dict(orientation="h", y=-0.2),
            margin=dict(t=10, b=50), plot_bgcolor="white",
        )
        st.plotly_chart(funnel_fig, use_container_width=True)
        st.caption(f"기준 시나리오: {TAG_LABELS.get(funnel_tag, funnel_tag)}")

    # ── 구간별 상세 테이블 ────────────────────────────────────────────────────
    with st.expander("구간별 수치 테이블", expanded=False):
        for tag in selected_tags:
            df = filtered(tag).copy()
            if df.empty:
                continue
            df = df.merge(
                bench_filtered()[["rebalance_date", "kosdaq_return"]],
                on="rebalance_date", how="left",
            )
            df["전략수익률"]   = (df["period_return"] * 100).round(2).astype(str) + "%"
            df["KOSPI"]       = (df["kospi_return"]   * 100).round(2).astype(str) + "%"
            df["KOSDAQ"]      = (df["kosdaq_return"]  * 100).round(2).astype(str) + "%"
            df["Alpha(vs KP)"] = ((df["period_return"] - df["kospi_return"]) * 100).round(2).astype(str) + "%"
            st.caption(TAG_LABELS.get(tag, tag))
            st.dataframe(
                df[["rebalance_date", "전략수익률", "KOSPI", "KOSDAQ", "Alpha(vs KP)",
                    "n_gate", "n_stocks"]],
                use_container_width=True, hide_index=True,
            )


# ══════════════════════════════════════════════════════════════════════════════
# 탭 3 — 랜덤 분포
# ══════════════════════════════════════════════════════════════════════════════

with tab_dist:
    st.subheader("랜덤 벤치마크 분포 (500회 반복)")
    st.caption("수직선: 결정적 시나리오 CAGR. 분포가 해당 선 왼쪽에 치우칠수록 전략이 랜덤 대비 우수.")

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
            st.metric("중앙값 CAGR", f"{cagrs.median():.1f}%")
            st.metric("p5",          f"{cagrs.quantile(0.05):.1f}%")
            st.metric("p95",         f"{cagrs.quantile(0.95):.1f}%")

        with c_chart:
            hist_fig = go.Figure()
            hist_fig.add_trace(go.Histogram(
                x=cagrs, nbinsx=40,
                marker_color="#94a3b8", opacity=0.8,
                hovertemplate="CAGR: %{x:.1f}%<br>빈도: %{y}<extra></extra>",
            ))
            for det_tag, det_val in det_cagrs.items():
                hist_fig.add_vline(
                    x=det_val,
                    line_color=TAG_COLORS.get(det_tag, "#374151"),
                    line_width=2,
                    annotation_text=TAG_LABELS.get(det_tag, det_tag).split()[0],
                    annotation_position="top",
                    annotation_font_size=10,
                )
            hist_fig.update_layout(
                height=220, xaxis_title="CAGR (%)", yaxis_title="빈도",
                showlegend=False,
                margin=dict(t=20, b=20, l=10, r=10),
                plot_bgcolor="white",
            )
            st.plotly_chart(hist_fig, use_container_width=True)

        pct_cols = st.columns(len(det_cagrs))
        for col, (det_tag, det_val) in zip(pct_cols, det_cagrs.items()):
            pct = (cagrs < det_val).mean() * 100
            col.metric(
                f"{TAG_LABELS.get(det_tag, det_tag).split()[0]} percentile",
                f"{pct:.0f}번째",
            )
        st.divider()
