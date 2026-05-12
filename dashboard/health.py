from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from dashboard import logs, queries, system_checks
from dashboard.config import HEALTH_JSON, HEALTH_JSONL, LOG_DIR, PROJECT_ROOT, RUN_ENV, STATUS_DIR, SUMMARY_MD, THRESHOLDS


STATUS_ORDER = {"OK": 0, "UNKNOWN": 1, "WARN": 2, "FAIL": 3}


def _worst(statuses: list[str]) -> str:
    if not statuses:
        return "UNKNOWN"
    return max(statuses, key=lambda status: STATUS_ORDER.get(status, 1))


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, ensure_ascii=False, default=_json_default) + "\n")
    except Exception:
        pass


def _pct(part: float, total: float) -> float:
    return round(part * 100.0 / total, 2) if total else 0.0


def _status_by_pct(value: float, warn: float, fail: float) -> str:
    if value >= fail:
        return "FAIL"
    if value >= warn:
        return "WARN"
    return "OK"


def _freshness_status(latest_date: str | None) -> tuple[str, int | None]:
    if not latest_date:
        return "UNKNOWN", None
    try:
        latest = date.fromisoformat(str(latest_date)[:10])
    except ValueError:
        return "UNKNOWN", None
    lag = (date.today() - latest).days
    if lag >= THRESHOLDS["freshness_fail_days"]:
        return "FAIL", lag
    if lag >= THRESHOLDS["freshness_warn_days"]:
        return "WARN", lag
    return "OK", lag


def _collect_db() -> dict[str, Any]:
    calls = {
        "row_counts": queries.get_row_counts,
        "ingest_progress": queries.get_ingest_progress,
        "ingest_status_summary": queries.get_ingest_status_summary,
        "ingest_errors": queries.get_ingest_errors,
        "price_freshness": queries.get_price_freshness,
        "market_cap_freshness": queries.get_market_cap_freshness,
        "recent_price_coverage": queries.get_recent_price_coverage,
        "recent_market_cap_coverage": queries.get_recent_market_cap_coverage,
        "dq_gate_summary": queries.get_dq_gate_summary,
        "dq_gate_top_rejects": queries.get_dq_gate_top_rejects,
        "dq_gate_top_flags": queries.get_dq_gate_top_flags,
        "validation_summary": queries.get_validation_summary,
        "validation_top_tickers": queries.get_validation_top_tickers,
        "pit_fallback_rate": queries.get_pit_fallback_rate,
        "pit_available_from_anomalies": queries.get_pit_available_from_anomalies,
        "stocks_stats": queries.get_stocks_stats,
        "orphan_checks": queries.get_orphan_checks,
        "backtest_runs": queries.get_backtest_runs,
    }
    data: dict[str, Any] = {}
    errors: dict[str, str] = {}

    rows, error = queries.safe_call(calls["row_counts"])
    data["row_counts"] = rows
    if error:
        errors["row_counts"] = error
        for name in calls:
            data.setdefault(name, [])
            if name != "row_counts":
                errors[name] = "Skipped because initial DB connectivity/schema check failed."
        return {"data": data, "errors": errors}

    for name, fn in calls.items():
        if name == "row_counts":
            continue
        rows, error = queries.safe_call(fn)
        data[name] = rows
        if error:
            errors[name] = error
    return {"data": data, "errors": errors}


def _build_findings(raw: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    sections = {
        "db": {"status": "OK", "checks": []},
        "ingest": {"status": "OK", "checks": []},
        "data_integrity": {"status": "OK", "checks": []},
        "logs": {"status": "OK", "checks": []},
        "system": {"status": "OK", "checks": []},
        "backtest_runs": {"status": "OK", "checks": []},
    }

    db_errors = raw["db"]["errors"]
    for name, error in db_errors.items():
        if str(error).startswith("Skipped because"):
            continue
        finding = {
            "severity": "UNKNOWN",
            "area": "db",
            "title": f"DB check failed: {name}",
            "evidence": {"error": error},
            "suggested_next_check": "Check DB connectivity, schema, and dashboard query compatibility.",
        }
        findings.append(finding)
        sections["db"]["checks"].append(finding)
    if not sections["db"]["checks"]:
        sections["db"]["checks"].append(
            {
                "severity": "OK",
                "area": "db",
                "title": "DB connectivity/schema checks",
                "evidence": {"row_counts": len(raw["db"]["data"].get("row_counts", []))},
            }
        )

    data = raw["db"]["data"]
    progress = (data.get("ingest_progress") or [{}])[0]
    total = progress.get("total") or 0
    if total:
        error_pct = _pct(progress.get("error") or 0, total)
        pending_pct = _pct(progress.get("pending") or 0, total)
        for label, value, warn, fail in [
            ("DART ingest error ratio", error_pct, THRESHOLDS["dart_error_warn_pct"], THRESHOLDS["dart_error_fail_pct"]),
            ("DART ingest pending ratio", pending_pct, THRESHOLDS["dart_pending_warn_pct"], THRESHOLDS["dart_pending_fail_pct"]),
        ]:
            status = _status_by_pct(value, warn, fail)
            check = {"severity": status, "area": "ingest", "title": label, "evidence": {"pct": value, "total": total}}
            sections["ingest"]["checks"].append(check)
            if status != "OK":
                findings.append({**check, "suggested_next_check": "Inspect ingest_status errors and recent DART logs."})
    else:
        check = {"severity": "UNKNOWN", "area": "ingest", "title": "No ingest_status rows", "evidence": progress}
        sections["ingest"]["checks"].append(check)
        findings.append({**check, "suggested_next_check": "Run DART ingest or verify schema initialization."})

    for area_key, rows in [
        ("price_history", data.get("price_freshness") or []),
        ("market_cap_history", data.get("market_cap_freshness") or []),
    ]:
        row = rows[0] if rows else {}
        status, lag = _freshness_status(row.get("latest_date"))
        check = {
            "severity": status,
            "area": "data_integrity",
            "title": f"{area_key} freshness",
            "evidence": {"latest_date": row.get("latest_date"), "lag_days": lag, "ticker_count": row.get("ticker_count")},
        }
        sections["data_integrity"]["checks"].append(check)
        if status != "OK":
            findings.append({**check, "suggested_next_check": f"Inspect {area_key} ingest log and latest coverage."})

    pit = (data.get("pit_fallback_rate") or [{}])[0]
    fallback_pct = float(pit.get("fallback_pct") or 0)
    pit_status = _status_by_pct(fallback_pct, THRESHOLDS["pit_fallback_warn_pct"], THRESHOLDS["pit_fallback_fail_pct"])
    pit_check = {"severity": pit_status, "area": "data_integrity", "title": "PIT fallback rate", "evidence": pit}
    sections["data_integrity"]["checks"].append(pit_check)
    if pit_status != "OK":
        findings.append({**pit_check, "suggested_next_check": "Inspect disclosures matching and financials_pit.available_from coverage."})

    validation_rejects = sum(int(row.get("count") or 0) for row in data.get("validation_summary", []) if row.get("severity") == "REJECT")
    if validation_rejects >= THRESHOLDS["validation_reject_fail_count"]:
        validation_status = "FAIL"
    elif validation_rejects >= THRESHOLDS["validation_reject_warn_count"]:
        validation_status = "WARN"
    else:
        validation_status = "OK"
    validation_check = {"severity": validation_status, "area": "data_integrity", "title": "Validation REJECT count", "evidence": {"count": validation_rejects}}
    sections["data_integrity"]["checks"].append(validation_check)
    if validation_status != "OK":
        findings.append({**validation_check, "suggested_next_check": "Inspect validation top tickers and V01-V09 distribution."})

    for orphan in data.get("orphan_checks", []):
        status = "WARN" if int(orphan.get("cnt") or 0) else "OK"
        check = {"severity": status, "area": "data_integrity", "title": orphan.get("check_id"), "evidence": orphan}
        sections["data_integrity"]["checks"].append(check)
        if status != "OK":
            findings.append({**check, "suggested_next_check": "Inspect source table ticker coverage and stocks master load."})

    for log_summary in raw["logs"]:
        check = {
            "severity": log_summary.get("status", "UNKNOWN"),
            "area": "logs",
            "title": log_summary.get("name"),
            "evidence": {"hit_count": log_summary.get("hit_count"), "error": log_summary.get("error")},
        }
        sections["logs"]["checks"].append(check)
        if check["severity"] != "OK":
            findings.append({**check, "suggested_next_check": "Open Logs tab and inspect extracted context."})

    system = raw["system"]
    disk_pct = system.get("disk", {}).get("used_pct")
    disk_status = "UNKNOWN"
    if disk_pct is not None:
        disk_status = _status_by_pct(float(disk_pct), THRESHOLDS["disk_warn_pct"], THRESHOLDS["disk_fail_pct"])
    disk_check = {"severity": disk_status, "area": "system", "title": "Disk usage", "evidence": {"used_pct": disk_pct}}
    sections["system"]["checks"].append(disk_check)
    if disk_status not in ("OK", "UNKNOWN"):
        findings.append({**disk_check, "suggested_next_check": "Inspect disk usage and old logs/backups."})

    systemd_status = system.get("systemd", {}).get("status", "UNKNOWN")
    systemd_stdout = system.get("systemd", {}).get("stdout")
    service_status = "OK" if systemd_stdout == "active" else ("UNKNOWN" if systemd_status == "UNKNOWN" else "WARN")
    service_check = {"severity": service_status, "area": "system", "title": "Dashboard systemd status", "evidence": system.get("systemd")}
    sections["system"]["checks"].append(service_check)
    if service_status != "OK":
        findings.append({**service_check, "suggested_next_check": "Inspect systemd service and journal output."})

    runs = data.get("backtest_runs", [])
    failed_runs = [row for row in runs if row.get("status") == "failed"]
    run_status = "WARN" if failed_runs else ("UNKNOWN" if not runs else "OK")
    run_check = {"severity": run_status, "area": "backtest_runs", "title": "Recent backtest run failures", "evidence": {"failed_count": len(failed_runs), "run_count": len(runs)}}
    sections["backtest_runs"]["checks"].append(run_check)
    if run_status != "OK":
        findings.append({**run_check, "suggested_next_check": "Inspect recent run metadata and traceback."})

    for section in sections.values():
        section["status"] = _worst([check.get("severity", "UNKNOWN") for check in section["checks"]])

    return findings, sections


def build_health_snapshot(include_expensive_system: bool = False, write_files: bool = True) -> dict[str, Any]:
    raw = {
        "db": _collect_db(),
        "logs": logs.summarize_all_logs(),
        "system": system_checks.collect_system_checks(include_expensive=include_expensive_system),
    }
    findings, sections = _build_findings(raw)
    overall = _worst([section["status"] for section in sections.values()])
    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    snapshot = {
        "generated_at": generated_at,
        "overall_status": overall,
        "summary": _summary_sentence(overall, findings),
        "environment": {
            "run_env": RUN_ENV,
            "project_root": str(PROJECT_ROOT),
            "log_dir": str(LOG_DIR),
        },
        "sections": sections,
        "findings": findings,
        "raw": raw,
    }
    if write_files:
        write_snapshot(snapshot)
    return snapshot


def _summary_sentence(overall: str, findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "No dashboard findings were detected."
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1
    parts = ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
    return f"Overall {overall}; findings: {parts}."


def render_summary_markdown(snapshot: dict[str, Any]) -> str:
    red = [item for item in snapshot["findings"] if item["severity"] == "FAIL"]
    yellow = [item for item in snapshot["findings"] if item["severity"] in ("WARN", "UNKNOWN")]

    def lines(items: list[dict[str, Any]]) -> str:
        if not items:
            return "- None\n"
        return "".join(f"- [{item['severity']}] {item['area']}: {item['title']}\n" for item in items)

    suggestions = [item.get("suggested_next_check") for item in snapshot["findings"] if item.get("suggested_next_check")]
    suggestion_text = "- None\n" if not suggestions else "".join(f"- {text}\n" for text in dict.fromkeys(suggestions))

    return (
        "# Backtest Dev Health\n\n"
        f"Generated: {snapshot['generated_at']}\n"
        f"Overall: {snapshot['overall_status']}\n\n"
        "## Environment\n"
        f"- Run env: `{snapshot.get('environment', {}).get('run_env')}`\n"
        f"- Project root: `{snapshot.get('environment', {}).get('project_root')}`\n"
        f"- Log dir: `{snapshot.get('environment', {}).get('log_dir')}`\n\n"
        "## Red Flags\n"
        f"{lines(red)}\n"
        "## Yellow Flags\n"
        f"{lines(yellow)}\n"
        "## Suggested Next Checks\n"
        f"{suggestion_text}"
    )


def write_snapshot(snapshot: dict[str, Any]) -> None:
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    _write_atomic(HEALTH_JSON, json.dumps(snapshot, ensure_ascii=False, indent=2, default=_json_default))
    _write_atomic(SUMMARY_MD, render_summary_markdown(snapshot))
    _append_jsonl(
        HEALTH_JSONL,
        {
            "generated_at": snapshot["generated_at"],
            "overall_status": snapshot["overall_status"],
            "summary": snapshot["summary"],
            "finding_count": len(snapshot["findings"]),
        },
    )


if __name__ == "__main__":
    snapshot = build_health_snapshot(write_files=True)
    print(
        json.dumps(
            {
                "overall_status": snapshot["overall_status"],
                "finding_count": len(snapshot["findings"]),
                "environment": snapshot.get("environment", {}),
                "db_errors": snapshot.get("raw", {}).get("db", {}).get("errors", {}),
            },
            ensure_ascii=False,
            indent=2,
            default=_json_default,
        )
    )
