from __future__ import annotations

import os
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from dashboard.config import COMMAND_TIMEOUT_SEC, DEV_PC_PROJECT_ROOT, LOG_FILES, PROJECT_ROOT, PYTHON_BIN, RUN_ENV, SERVICE_NAME
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


def get_storage_overview() -> dict:
    if os.name == "nt":
        return _run([
            "powershell",
            "-Command",
            "Get-CimInstance Win32_LogicalDisk | "
            "Select-Object DeviceID,VolumeName,Size,FreeSpace | ConvertTo-Json -Compress",
        ])
    return _run(["df", "-h", "/", str(PROJECT_ROOT), str(LOG_FILES["dart.log"].parent)])


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


def get_admin_processes() -> dict:
    if os.name == "nt":
        return get_processes()
    return _run(["ps", "-eo", "pid,ppid,%cpu,%mem,etime,cmd", "--sort=-%mem"], timeout=COMMAND_TIMEOUT_SEC)


def get_memory_status() -> dict:
    if os.name == "nt":
        return _run([
            "powershell",
            "-Command",
            "Get-CimInstance Win32_OperatingSystem | "
            "Select-Object TotalVisibleMemorySize,FreePhysicalMemory | ConvertTo-Json -Compress",
        ])
    return _run(["free", "-h"])


def get_temperature_status() -> dict:
    data = {"status": "UNKNOWN", "platform": platform.system(), "readings": []}
    if os.name == "nt":
        result = _run([
            "powershell",
            "-Command",
            "Get-CimInstance MSAcpi_ThermalZoneTemperature -Namespace root/wmi | "
            "Select-Object InstanceName,CurrentTemperature | ConvertTo-Json -Compress",
        ])
        data["command"] = result
        data["status"] = result.get("status", "UNKNOWN")
        return data

    sensors = _run(["sensors"])
    data["sensors"] = sensors
    readings = []
    for path in sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp")):
        try:
            raw = path.read_text(encoding="utf-8").strip()
            temp_c = float(raw) / 1000.0
            type_path = path.with_name("type")
            sensor_type = type_path.read_text(encoding="utf-8").strip() if type_path.exists() else path.parent.name
            readings.append({"zone": path.parent.name, "type": sensor_type, "temp_c": round(temp_c, 1)})
        except Exception:
            continue
    data["readings"] = readings
    data["status"] = "OK" if readings or sensors.get("status") == "OK" else "UNKNOWN"
    return data


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


def _tree_rows(root: Path, max_depth: int = 2, max_entries: int = 200) -> list[dict]:
    rows: list[dict] = []
    if not root.exists():
        return [{"path": str(root), "kind": "missing", "depth": 0}]

    root = root.resolve()

    def walk(path: Path, depth: int) -> None:
        if len(rows) >= max_entries:
            return
        try:
            stat = path.stat()
            kind = "dir" if path.is_dir() else "file"
            rows.append(
                {
                    "path": str(path.relative_to(root)) if path != root else ".",
                    "kind": kind,
                    "depth": depth,
                    "size_bytes": None if kind == "dir" else stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                }
            )
        except Exception as exc:
            rows.append({"path": str(path), "kind": "error", "depth": depth, "error": f"{type(exc).__name__}: {exc}"})
            return
        if depth >= max_depth or not path.is_dir():
            return
        try:
            children = sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        except Exception:
            return
        for child in children:
            if child.name in {".git", "__pycache__", ".pytest_cache", ".venv", "venv"}:
                continue
            walk(child, depth + 1)

    walk(root, 0)
    if len(rows) >= max_entries:
        rows.append({"path": f"... truncated at {max_entries} entries", "kind": "note", "depth": max_depth})
    return rows


def get_workspace_overview() -> dict:
    dev_root = DEV_PC_PROJECT_ROOT.expanduser()
    dev_accessible = dev_root.exists()
    return {
        "run_env": RUN_ENV,
        "host": platform.node(),
        "platform": platform.platform(),
        "server_project_root": str(PROJECT_ROOT),
        "server_project_exists": PROJECT_ROOT.exists(),
        "dev_pc_project_root": str(dev_root),
        "dev_pc_project_accessible_from_this_runtime": dev_accessible,
        "note": (
            "서버에서 실행 중이면 개발 PC 경로는 참조용으로만 표시됩니다. "
            "로컬 Windows에서 실행하면 접근 가능 여부가 True로 표시됩니다."
        ),
    }


def collect_workspace_admin(max_depth: int = 2) -> dict:
    data = {
        "overview": get_workspace_overview(),
        "server_tree": _tree_rows(PROJECT_ROOT, max_depth=max_depth),
        "dev_pc_tree": _tree_rows(DEV_PC_PROJECT_ROOT.expanduser(), max_depth=max_depth)
        if DEV_PC_PROJECT_ROOT.expanduser().exists()
        else [],
        "storage": get_storage_overview(),
        "disk": get_disk_usage(),
        "memory": get_memory_status(),
        "temperature": get_temperature_status(),
        "logs": get_log_file_status(),
        "processes": get_admin_processes(),
    }
    return data


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
