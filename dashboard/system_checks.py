from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from dashboard.config import COMMAND_TIMEOUT_SEC, LOG_FILES, PROJECT_ROOT, PYTHON_BIN, SERVICE_NAME
from dashboard.sanitize import sanitize_text


def _run(argv: list[str], timeout: int = COMMAND_TIMEOUT_SEC) -> dict:
    executable = shutil.which(argv[0])
    if executable is None:
        return {"status": "UNKNOWN", "cmd": argv, "error": f"{argv[0]} not found"}

    try:
        result = subprocess.run(
            [executable, *argv[1:]],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(PROJECT_ROOT) if PROJECT_ROOT.exists() else None,
        )
        status = "OK" if result.returncode == 0 else "WARN"
        return {
            "status": status,
            "cmd": argv,
            "returncode": result.returncode,
            "stdout": sanitize_text(result.stdout.strip()),
            "stderr": sanitize_text(result.stderr.strip()),
        }
    except subprocess.TimeoutExpired:
        return {"status": "WARN", "cmd": argv, "error": f"Timed out after {timeout}s"}
    except Exception as exc:
        return {"status": "UNKNOWN", "cmd": argv, "error": f"{type(exc).__name__}: {exc}"}


def get_systemd_status() -> dict:
    return _run(["systemctl", "is-active", SERVICE_NAME])


def get_journal_tail() -> dict:
    return _run(["journalctl", "-u", SERVICE_NAME, "-n", "100", "--no-pager"])


def get_crontab() -> dict:
    return _run(["crontab", "-l"])


def get_disk_usage() -> dict:
    result = _run(["df", "-h", "/"])
    pct = None
    if result.get("stdout"):
        lines = result["stdout"].splitlines()
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 5 and parts[4].endswith("%"):
                try:
                    pct = float(parts[4].rstrip("%"))
                except ValueError:
                    pct = None
    result["used_pct"] = pct
    return result


def get_project_size() -> dict:
    return _run(["du", "-sh", str(PROJECT_ROOT)])


def get_git_info() -> dict:
    commit = _run(["git", "rev-parse", "HEAD"])
    status = _run(["git", "status", "--short"])
    return {"commit": commit, "status": status}


def get_processes() -> dict:
    if os.name == "nt":
        return _run(["powershell", "-Command", "Get-Process | Where-Object { $_.ProcessName -match 'python|streamlit' } | Select-Object -First 30 | Out-String"])
    return _run(["ps", "-eo", "pid,ppid,stat,etime,cmd"])


def get_package_list() -> dict:
    python_path = Path(PYTHON_BIN)
    if python_path.exists():
        return _run([str(python_path), "-m", "pip", "list"], timeout=COMMAND_TIMEOUT_SEC)
    return _run(["python", "-m", "pip", "list"], timeout=COMMAND_TIMEOUT_SEC)


def get_log_file_status() -> list[dict]:
    rows = []
    now = datetime.now(timezone.utc)
    for name, path in LOG_FILES.items():
        if not path.exists():
            rows.append({"name": name, "path": str(path), "status": "UNKNOWN", "error": "missing"})
            continue
        stat = path.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        rows.append(
            {
                "name": name,
                "path": str(path),
                "status": "OK",
                "size_bytes": stat.st_size,
                "modified_at": mtime.isoformat(),
                "age_seconds": int((now - mtime).total_seconds()),
            }
        )
    return rows


def collect_system_checks(include_expensive: bool = False) -> dict:
    data = {
        "systemd": get_systemd_status(),
        "crontab": get_crontab(),
        "disk": get_disk_usage(),
        "git": get_git_info(),
        "logs": get_log_file_status(),
    }
    if include_expensive:
        data["journal"] = get_journal_tail()
        data["project_size"] = get_project_size()
        data["processes"] = get_processes()
        data["packages"] = get_package_list()
    return data
