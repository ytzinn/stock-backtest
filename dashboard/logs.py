from __future__ import annotations

from collections import deque
from pathlib import Path

from dashboard.config import LOG_FILES
from dashboard.sanitize import sanitize_text


KEYWORDS = ("ERROR", "WARN", "FAIL", "Traceback", "Exception", "[FAIL]")


def _tail(path: Path, lines: int) -> list[str]:
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return list(deque(fh, maxlen=lines))


def read_log_tail(name: str, lines: int = 100) -> dict:
    path = LOG_FILES.get(name)
    if path is None:
        return {"name": name, "status": "UNKNOWN", "error": "Unknown log file", "tail": ""}
    if not path.exists():
        return {"name": name, "path": str(path), "status": "UNKNOWN", "error": "File does not exist", "tail": ""}
    try:
        text = "".join(_tail(path, lines))
        return {"name": name, "path": str(path), "status": "OK", "tail": sanitize_text(text)}
    except Exception as exc:
        return {"name": name, "path": str(path), "status": "UNKNOWN", "error": f"{type(exc).__name__}: {exc}", "tail": ""}


def summarize_log(name: str, lines: int = 500, context: int = 2, max_findings: int = 3) -> dict:
    path = LOG_FILES.get(name)
    if path is None:
        return {"name": name, "status": "UNKNOWN", "findings": [], "error": "Unknown log file"}
    if not path.exists():
        return {"name": name, "path": str(path), "status": "UNKNOWN", "findings": [], "error": "File does not exist"}

    try:
        rows = _tail(path, lines)
    except Exception as exc:
        return {"name": name, "path": str(path), "status": "UNKNOWN", "findings": [], "error": f"{type(exc).__name__}: {exc}"}

    hits = []
    for idx, line in enumerate(rows):
        if any(keyword in line for keyword in KEYWORDS):
            start = max(0, idx - context)
            end = min(len(rows), idx + context + 1)
            hits.append(
                {
                    "line": sanitize_text(line.strip()),
                    "context": sanitize_text("".join(rows[start:end]).strip()),
                }
            )

    findings = hits[-max_findings:]
    status = "OK"
    if len(hits) >= 3:
        status = "FAIL"
    elif hits:
        status = "WARN"

    return {
        "name": name,
        "path": str(path),
        "status": status,
        "hit_count": len(hits),
        "findings": findings,
    }


def summarize_all_logs() -> list[dict]:
    return [summarize_log(name) for name in LOG_FILES]

