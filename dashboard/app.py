from __future__ import annotations

import json
from html import escape
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard import health, logs, queries, system_checks  # noqa: E402
from dashboard.config import HEALTH_JSON, SUMMARY_MD, THRESHOLDS  # noqa: E402


HELP = {
    "overall_status": "모든 진단 항목 중 가장 나쁜 상태입니다. FAIL > WARN > UNKNOWN > OK 순서로 심각합니다.",
    "OK": "정상입니다. 현재 기준으로 문제가 감지되지 않았습니다.",
    "WARN": "확인이 필요합니다. 즉시 장애는 아니지만 방치하면 문제가 될 수 있습니다.",
    "FAIL": "실패입니다. 현재 수집/검증/백테스트 결과를 신뢰하기 어려울 수 있습니다.",
    "UNKNOWN": "판단 불가입니다. 대시보드가 해당 정보를 읽지 못했거나 근거가 부족합니다.",
    "Summary": "사람이 빠르게 읽기 위한 최신 진단 요약입니다.",
    "Agent JSON": "Codex/Claude Code가 캡처 없이 읽을 수 있는 구조화된 최신 진단 결과입니다.",
    "Health Matrix": "각 진단 영역의 요약 상태표입니다. non_ok가 크면 확인할 항목이 많다는 뜻입니다.",
    "Findings": "대시보드가 문제 또는 확인 필요 항목으로 판단한 목록입니다.",
    "Freshness": "가격/시가총액 데이터가 얼마나 최신 거래일까지 들어왔는지 봅니다.",
    "PIT / DQ / Validation": "PIT, DQ Gate, validation은 백테스트 입력 데이터가 믿을 만한지 확인하는 영역입니다.",
    "Stocks / Orphans / Row Counts": "종목 마스터 통계, orphan 데이터, 주요 테이블 행 수를 보여줍니다.",
    "Log Summary": "로그에서 ERROR/WARN/FAIL/Traceback/Exception 같은 라인을 추출해 최근 원인 후보를 보여줍니다.",
    "System": "서버의 systemd, disk, git, crontab, 로그 파일 상태를 읽기 전용으로 보여줍니다.",
    "Recent Backtest Runs": "최근 백테스트 실행 이력입니다. 성과 분석이 아니라 실패/metadata 확인용입니다.",
    "price_history latest": "price_history 테이블에 들어온 가장 최신 날짜와 그 날짜의 종목 수입니다.",
    "recent price coverage": "최근 날짜별 price_history 종목 수입니다. 갑자기 줄면 수집 누락 가능성이 있습니다.",
    "market_cap_history latest": "market_cap_history 테이블에 들어온 가장 최신 날짜와 그 날짜의 종목 수입니다.",
    "recent market cap coverage": "최근 날짜별 market_cap_history 종목 수입니다. 갑자기 줄면 수집 누락 가능성이 있습니다.",
    "PIT fallback": "실제 공시일을 못 찾아 법정마감+5일 같은 보수적 날짜를 사용한 비율입니다. 높으면 공시일 매칭 개선이 필요합니다.",
    "PIT available_from anomalies": "available_from이 미래 날짜이거나 비정상적으로 오래된 PIT 데이터 후보입니다.",
    "DQ gate summary": "Data Quality Gate의 PASS/REJECT 분포입니다. REJECT가 높으면 유니버스 품질 문제가 있을 수 있습니다.",
    "DQ top rejects": "DQ Gate에서 가장 자주 나온 reject 사유입니다.",
    "Validation summary": "V01~V09 재무 이상치 검사 결과를 check_id와 severity별로 집계합니다.",
    "Validation top tickers": "validation 문제가 많이 나온 종목입니다.",
    "Sanitized tail": "민감정보를 마스킹한 로그 끝부분입니다.",
    "Extracted recent context": "로그에서 에러/경고 키워드가 나온 최근 위치와 앞뒤 문맥입니다.",
    "systemd": "대시보드 systemd 서비스 상태입니다. active면 서비스가 정상 실행 중입니다.",
    "disk": "서버 루트 파일시스템 사용량입니다. 80% 이상이면 주의, 90% 이상이면 위험으로 봅니다.",
    "git": "서버 프로젝트의 git commit과 변경 파일 상태입니다.",
    "log files": "대시보드가 읽는 로그 파일의 존재 여부, 크기, 수정 시각입니다.",
    "crontab": "현재 사용자 crontab입니다. 수집 작업 스케줄 확인용입니다.",
    "section": "진단 영역 이름입니다.",
    "what_it_means": "이 영역이 서버 컴퓨터에서 무엇을 의미하는지 설명합니다.",
    "status": "OK/WARN/FAIL/UNKNOWN 중 하나입니다.",
    "checks": "이 영역에서 대시보드가 확인한 항목의 총 개수입니다.",
    "non_ok": "OK가 아닌 항목 수입니다. 이 숫자가 0보다 크면 사람이 확인할 내용이 있다는 뜻입니다.",
    "how_to_read": "이 행을 어떻게 읽으면 되는지에 대한 짧은 안내입니다.",
    "check_summary": "이 영역에서 실제로 어떤 기준을 검사했는지 요약합니다.",
    "severity": "문제의 심각도입니다.",
    "area": "문제가 발견된 영역입니다.",
    "title": "문제 또는 진단 항목의 이름입니다.",
    "evidence": "대시보드가 판단에 사용한 근거 값입니다.",
    "suggested_next_check": "다음에 사람이 확인하면 좋은 위치나 작업입니다. 대시보드가 직접 조치하지는 않습니다.",
    "latest_date": "해당 테이블에 기록된 가장 최신 날짜입니다.",
    "ticker_count": "해당 날짜에 데이터가 존재하는 고유 종목 수입니다.",
    "lag_days": "오늘과 latest_date의 달력일 차이입니다. 주말/휴일이 섞일 수 있어 참고값입니다.",
    "fallback_pct": "financials_pit 중 fallback_used=True 비율입니다.",
    "hit_count": "로그 tail 범위 안에서 ERROR/WARN/FAIL/Traceback/Exception 키워드가 발견된 횟수입니다.",
}

STATUS_COLORS = {
    "OK": {"bg": "#dcfce7", "fg": "#166534", "border": "#86efac"},
    "WARN": {"bg": "#fef9c3", "fg": "#854d0e", "border": "#fde047"},
    "WARNING": {"bg": "#fef9c3", "fg": "#854d0e", "border": "#fde047"},
    "FAIL": {"bg": "#fee2e2", "fg": "#991b1b", "border": "#fca5a5"},
    "UNKNOWN": {"bg": "#f1f5f9", "fg": "#475569", "border": "#cbd5e1"},
}

SECTION_LABELS = {
    "db": "DB (데이터베이스)",
    "ingest": "Ingest (데이터 수집)",
    "data_integrity": "Data Integrity (데이터 품질)",
    "logs": "Logs (로그)",
    "system": "System (서버 상태)",
    "backtest_runs": "Backtest Runs (백테스트 실행)",
}

SECTION_DESCRIPTIONS = {
    "db": "PostgreSQL DB에 연결되고 주요 테이블을 읽을 수 있는지 봅니다.",
    "ingest": "DART/가격/시총 같은 데이터 수집 진행률과 에러를 봅니다.",
    "data_integrity": "수집된 데이터가 최신이고 백테스트 입력으로 믿을 만한지 봅니다.",
    "logs": "서버 로그에 최근 에러, 경고, Traceback이 있는지 봅니다.",
    "system": "대시보드 서비스, 디스크, git, cron 같은 서버 기본 상태를 봅니다.",
    "backtest_runs": "최근 백테스트 실행이 실패했는지와 실행 메타데이터를 봅니다.",
}

DATA_INTEGRITY_GLOSSARY = [
    ("PIT", "Point-in-Time의 약자입니다. 백테스트 날짜 당시 이미 공개되어 있었던 데이터만 쓰도록 날짜를 맞춘 재무 데이터입니다."),
    ("fallback", "정확한 공시일을 찾지 못했을 때 쓰는 보수적인 대체 날짜입니다. 실제보다 늦게 잡아 미래 정보를 미리 보는 문제를 피합니다."),
    ("available_from", "해당 재무 데이터가 백테스트에서 사용 가능해지는 날짜입니다. 리밸런싱일이 이 날짜 이후일 때만 사용합니다."),
    ("DQ Gate", "Data Quality Gate입니다. 백테스트에 넣기 전에 데이터가 최소 품질 기준을 통과했는지 보는 문턱입니다."),
    ("Validation", "재무 값의 이상치, 누락, 비정상 패턴을 검사한 결과입니다. WARN/REJECT가 많으면 원천 데이터를 먼저 봐야 합니다."),
    ("Orphan", "종목 마스터(stocks)에 없는 ticker를 참조하는 데이터입니다. 0이 아니면 삭제 종목 처리나 ticker 매핑을 확인해야 합니다."),
    ("Row Counts", "주요 테이블에 데이터가 얼마나 쌓였는지 보는 기본 건수입니다. 갑자기 0이거나 급감하면 수집/적재 문제 후보입니다."),
]


def read_hint(status: str, non_ok: int) -> str:
    if status == "OK":
        return "정상입니다."
    if status == "UNKNOWN":
        return "대시보드가 읽지 못했습니다. 권한/파일/연결 상태를 확인하세요."
    if status == "WARN":
        return f"확인 필요 항목 {non_ok}개가 있습니다."
    if status == "FAIL":
        return f"우선 확인해야 할 문제 {non_ok}개가 있습니다."
    return "상태를 확인하세요."


def format_check_summary(section_name: str, section: dict) -> str:
    if section_name != "ingest":
        return ""

    parts = []
    for check in section.get("checks", []):
        title = check.get("title")
        evidence = check.get("evidence", {})
        pct = evidence.get("pct")
        if pct is None:
            continue
        if title == "DART ingest error ratio":
            warn = THRESHOLDS["dart_error_warn_pct"]
            fail = THRESHOLDS["dart_error_fail_pct"]
            over = float(pct) - fail
            parts.append(f"에러 비율 {pct}% (WARN >= {warn}%, FAIL >= {fail}%, FAIL 기준 {over:+.2f}%p)")
        elif title == "DART ingest pending ratio":
            warn = THRESHOLDS["dart_pending_warn_pct"]
            fail = THRESHOLDS["dart_pending_fail_pct"]
            over = float(pct) - fail
            parts.append(f"대기 비율 {pct}% (WARN >= {warn}%, FAIL >= {fail}%, FAIL 기준 {over:+.2f}%p)")

    return " / ".join(parts)


def status_style(value):
    status = str(value).upper()
    color = STATUS_COLORS.get(status)
    if not color:
        return ""
    return (
        f"background-color: {color['bg']}; color: {color['fg']}; "
        f"border: 1px solid {color['border']}; font-weight: 700;"
    )


def tab_guide(title: str, lines: list[str]) -> None:
    with st.expander(f"{title} 읽는 법", expanded=False):
        for line in lines:
            st.markdown(f"- {line}")


st.set_page_config(
    page_title="Backtest Dev Health",
    page_icon="",
    layout="wide",
    initial_sidebar_state="collapsed",
)


def hint_caption(text: str, help_key: str | None = None) -> None:
    tooltip = HELP.get(help_key or text, "")
    if tooltip:
        st.markdown(
            f"<span title='{escape(tooltip)}' style='cursor:help;color:#59636e;font-size:0.875rem'>{escape(text)} ⓘ</span>",
            unsafe_allow_html=True,
        )
    else:
        st.caption(text)


def note_box(lines: list[str]) -> None:
    st.info("\n".join(f"- {line}" for line in lines))


def df(rows):
    return pd.DataFrame(rows or [])


def show_table(rows, height: int = 280):
    data = df(rows)
    if data.empty:
        st.caption("No rows")
    else:
        column_config = {}
        for column in data.columns:
            help_text = HELP.get(str(column))
            if help_text:
                column_config[column] = st.column_config.TextColumn(str(column), help=help_text)
        status_columns = [column for column in data.columns if str(column) in ("status", "severity", "overall_status")]
        if status_columns:
            styled = data.style.map(status_style, subset=status_columns)
            st.dataframe(styled, use_container_width=True, height=height, column_config=column_config)
        else:
            st.dataframe(data, use_container_width=True, height=height, column_config=column_config)


def status_badge(value: str) -> str:
    color = STATUS_COLORS.get(str(value).upper(), STATUS_COLORS["UNKNOWN"])
    return (
        f"<span style='background:{color['bg']};color:{color['fg']};"
        f"border:1px solid {color['border']};padding:2px 8px;"
        f"border-radius:4px;font-weight:700'>{escape(str(value))}</span>"
    )


with st.sidebar:
    st.header("Refresh")
    auto_refresh = st.toggle("Auto refresh every 30s", value=False, help="켜면 30초마다 화면을 다시 읽습니다. 읽는 중 불편하면 끄세요.")
    include_expensive_snapshot = st.toggle("Include slower system checks", value=False, help="journalctl, du, pip list 같은 상대적으로 느린 체크를 포함합니다.")
    if st.button("Refresh Now", type="primary", help="캐시를 지우고 지금 즉시 다시 진단합니다."):
        st.cache_data.clear()
        st.rerun()

if auto_refresh:
    st.markdown('<meta http-equiv="refresh" content="30">', unsafe_allow_html=True)

snapshot = health.build_health_snapshot(include_expensive_system=include_expensive_snapshot, write_files=True)
raw_db = snapshot["raw"]["db"]["data"]

st.title("Backtest Dev Health")
st.markdown(
    f"Generated `{snapshot['generated_at']}` &nbsp; "
    f"<span title='{escape(HELP['overall_status'])}' style='cursor:help'>Overall</span> "
    f"{status_badge(snapshot['overall_status'])}",
    unsafe_allow_html=True,
)
st.caption(
    f"Run env: {snapshot['environment']['run_env']} | "
    f"Project root: {snapshot['environment']['project_root']} | "
    f"Log dir: {snapshot['environment']['log_dir']}"
)

if snapshot["raw"]["db"]["errors"].get("row_counts"):
    st.warning(
        "DB에 연결하지 못해서 DB/Data Integrity/Backtest 탭은 UNKNOWN으로 표시됩니다. "
        "서버에서 실행하거나 로컬 PostgreSQL(5433)을 띄우면 실제 값이 표시됩니다."
    )

with st.expander("Status Legend / 화면 읽는 법", expanded=True):
    st.markdown(
        "<br>".join(
            [
                f"{status_badge('OK')} 초록색: {HELP['OK']}",
                f"{status_badge('WARN')} 노란색: {HELP['WARN']}",
                f"{status_badge('FAIL')} 붉은색: {HELP['FAIL']}",
                f"{status_badge('UNKNOWN')} 회색: {HELP['UNKNOWN']}",
            ]
        ),
        unsafe_allow_html=True,
    )
    st.caption("섹션 제목의 ?, ⓘ, 표의 컬럼 헤더에 마우스를 올리면 의미 설명이 표시됩니다.")

tabs = st.tabs(["Agent Summary", "Health Matrix", "Data Integrity", "Logs", "System", "Backtest Runs"])

with tabs[0]:
    tab_guide(
        "Agent Summary",
        [
            "왼쪽 Summary는 사람이 읽는 요약입니다. Red Flags와 Yellow Flags부터 보면 됩니다.",
            "오른쪽 Agent JSON은 Codex/Claude Code가 읽는 원본 진단 데이터입니다. 이 영역은 구조 유지를 위해 그대로 둡니다.",
            "overall_status가 FAIL이면 먼저 Findings 탭에서 붉은 항목을 확인하세요.",
        ],
    )
    left, right = st.columns([1, 1])
    with left:
        st.subheader("Summary", help=HELP["Summary"])
        st.code(health.render_summary_markdown(snapshot), language="markdown")
        st.caption(f"Markdown: {SUMMARY_MD}")
        st.caption(f"JSON: {HEALTH_JSON}")
    with right:
        st.subheader("Agent JSON", help=HELP["Agent JSON"])
        st.json(
            {
                "generated_at": snapshot["generated_at"],
                "overall_status": snapshot["overall_status"],
                "summary": snapshot["summary"],
                "sections": snapshot["sections"],
                "findings": snapshot["findings"],
            },
            expanded=False,
        )

with tabs[1]:
    st.subheader("Health Matrix", help=HELP["Health Matrix"])
    tab_guide(
        "Health Matrix",
        [
            "서버 상태를 DB, 데이터 수집, 데이터 품질, 로그, 서버 상태, 백테스트 실행으로 나눠 요약합니다.",
            "status 색상을 먼저 보세요. 붉은색 FAIL, 노란색 WARN, 회색 UNKNOWN, 초록색 OK 순서입니다.",
            "non_ok는 정상(OK)이 아닌 항목 수입니다. 0이면 특별히 볼 내용이 없다는 뜻입니다.",
        ],
    )
    st.markdown(
        "이 표는 서버를 여섯 영역으로 나눠서 `정상인지`, `확인이 필요한 항목이 몇 개인지`를 보여줍니다. "
        "`status`가 `FAIL`, `WARN`, `UNKNOWN`인 행부터 보면 됩니다."
    )
    matrix = []
    for name, section in snapshot["sections"].items():
        checks_total = len(section["checks"])
        non_ok = sum(1 for check in section["checks"] if check.get("severity") != "OK")
        matrix.append(
            {
                "section": SECTION_LABELS.get(name, name),
                "what_it_means": SECTION_DESCRIPTIONS.get(name, ""),
                "status": section["status"],
                "checks": checks_total,
                "non_ok": non_ok,
                "check_summary": format_check_summary(name, section),
                "how_to_read": read_hint(section["status"], non_ok),
            }
        )
    show_table(matrix, height=240)
    st.subheader("Findings", help=HELP["Findings"])
    show_table(snapshot["findings"], height=420)

with tabs[2]:
    st.subheader("Freshness", help=HELP["Freshness"])
    tab_guide(
        "Data Integrity",
        [
            "백테스트 입력 데이터가 최신이고 믿을 만한지 보는 탭입니다.",
            "Freshness는 가격/시총 데이터가 최신 날짜까지 들어왔는지 보여줍니다.",
            "PIT fallback이 높으면 실제 공시일 매칭이 부족하다는 뜻입니다.",
            "Validation과 DQ Gate에 FAIL/WARN이 많으면 백테스트보다 데이터 정제를 먼저 봐야 합니다.",
        ],
    )
    with st.expander("용어 설명", expanded=True):
        st.markdown(
            "\n".join(f"- **{term}**: {description}" for term, description in DATA_INTEGRITY_GLOSSARY)
        )
    c1, c2 = st.columns(2)
    with c1:
        hint_caption("price_history latest")
        show_table(raw_db.get("price_freshness"), height=120)
        hint_caption("recent price coverage")
        show_table(raw_db.get("recent_price_coverage"), height=260)
    with c2:
        hint_caption("market_cap_history latest")
        show_table(raw_db.get("market_cap_freshness"), height=120)
        hint_caption("recent market cap coverage")
        show_table(raw_db.get("recent_market_cap_coverage"), height=260)

    st.subheader("PIT / DQ / Validation", help=HELP["PIT / DQ / Validation"])
    note_box(
        [
            "PIT는 백테스트 시점에 이미 알 수 있었던 재무 데이터만 쓰기 위한 장치입니다.",
            "fallback_pct가 높으면 실제 공시일을 정확히 찾지 못한 비율이 높다는 뜻입니다. 보수적으로 처리되지만 데이터 품질 개선 대상입니다.",
            "DQ Gate와 Validation의 REJECT는 백테스트 투입 전에 사람이 확인해야 하는 데이터 후보입니다.",
        ]
    )
    c1, c2, c3 = st.columns(3)
    with c1:
        hint_caption("PIT fallback")
        show_table(raw_db.get("pit_fallback_rate"), height=140)
        hint_caption("PIT available_from anomalies")
        show_table(raw_db.get("pit_available_from_anomalies"), height=260)
    with c2:
        hint_caption("DQ gate summary")
        show_table(raw_db.get("dq_gate_summary"), height=220)
        hint_caption("DQ top rejects")
        show_table(raw_db.get("dq_gate_top_rejects"), height=220)
    with c3:
        hint_caption("Validation summary")
        show_table(raw_db.get("validation_summary"), height=220)
        hint_caption("Validation top tickers")
        show_table(raw_db.get("validation_top_tickers"), height=220)

    st.subheader("Stocks / Orphans / Row Counts", help=HELP["Stocks / Orphans / Row Counts"])
    note_box(
        [
            "Orphan은 상세 데이터에는 ticker가 있는데 종목 마스터에는 없는 상태입니다. 정상 목표는 0건입니다.",
            "Stocks는 종목 마스터의 시장별 구성이고, Row Counts는 주요 테이블 적재량을 빠르게 보는 숫자입니다.",
        ]
    )
    c1, c2, c3 = st.columns(3)
    with c1:
        show_table(raw_db.get("stocks_stats"), height=260)
    with c2:
        show_table(raw_db.get("orphan_checks"), height=260)
    with c3:
        show_table(raw_db.get("row_counts"), height=260)

with tabs[3]:
    st.subheader("Log Summary", help=HELP["Log Summary"])
    tab_guide(
        "Logs",
        [
            "각 로그 파일에서 ERROR, WARN, FAIL, Traceback, Exception 같은 키워드를 찾습니다.",
            "hit_count가 높거나 status가 FAIL이면 해당 로그 파일의 Sanitized tail과 Extracted context를 확인하세요.",
            "Sanitized tail은 API key, password 같은 민감정보를 마스킹한 원문 로그입니다.",
        ],
    )
    log_summaries = logs.summarize_all_logs()
    show_table(
        [
            {
                "name": item.get("name"),
                "status": item.get("status"),
                "hit_count": item.get("hit_count"),
                "error": item.get("error"),
                "path": item.get("path"),
            }
            for item in log_summaries
        ],
        height=260,
    )
    selected = st.selectbox("Log file", [item["name"] for item in log_summaries], help="tail로 볼 로그 파일을 고릅니다.")
    n_lines = st.slider("Lines", 20, 500, 100, step=20, help="로그 파일 끝에서 몇 줄을 읽을지 정합니다.")
    hint_caption("Sanitized tail")
    st.code(logs.read_log_tail(selected, n_lines).get("tail", ""), language=None)
    hint_caption("Extracted recent context")
    selected_summary = next((item for item in log_summaries if item["name"] == selected), {})
    st.json(selected_summary.get("findings", []), expanded=False)

with tabs[4]:
    st.subheader("System", help=HELP["System"])
    tab_guide(
        "System",
        [
            "대시보드 서비스, 디스크 사용량, git 상태, cron, 로그 파일 존재 여부를 봅니다.",
            "systemd가 active가 아니면 대시보드 서비스 자체가 불안정한 상태입니다.",
            "disk 사용률이 높으면 로그/백업/DB 용량을 확인해야 합니다.",
            "Include slower checks는 journal, du, pip list 같은 느린 확인을 수동으로 추가할 때만 켜세요.",
        ],
    )
    include_expensive = st.checkbox("Include slower checks", value=False, help="현재 탭에서만 느린 체크를 추가로 실행합니다.")
    system = snapshot["raw"]["system"] if not include_expensive else system_checks.collect_system_checks(include_expensive=True)
    c1, c2 = st.columns(2)
    with c1:
        hint_caption("systemd")
        st.json(system.get("systemd"), expanded=False)
        hint_caption("disk")
        st.json(system.get("disk"), expanded=False)
        hint_caption("git")
        st.json(system.get("git"), expanded=False)
    with c2:
        hint_caption("log files")
        show_table(system.get("logs"), height=260)
        hint_caption("crontab")
        st.code(system.get("crontab", {}).get("stdout") or system.get("crontab", {}).get("error") or "", language=None)

    for key in ("journal", "project_size", "processes", "packages"):
        if key in system:
            with st.expander(key):
                st.code(json.dumps(system[key], ensure_ascii=False, indent=2), language="json")

with tabs[5]:
    st.subheader("Recent Backtest Runs", help=HELP["Recent Backtest Runs"])
    tab_guide(
        "Backtest Runs",
        [
            "최근 백테스트가 성공/실패했는지와 실행 당시 metadata를 보는 탭입니다.",
            "여기서는 성과 분석보다 failed 상태, error_msg, git_commit, param_hash를 우선 확인합니다.",
            "실패 run이 있으면 error_msg를 보고 코드/데이터/파라미터 중 어느 쪽 문제인지 좁히면 됩니다.",
        ],
    )
    show_table(raw_db.get("backtest_runs"), height=520)
