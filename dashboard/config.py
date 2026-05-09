from __future__ import annotations

import os
import platform
from pathlib import Path


PROJECT_ROOT = Path(os.getenv("BACKTEST_PROJECT_ROOT", Path.cwd())).resolve()
IS_WINDOWS = platform.system() == "Windows"
RUN_ENV = os.getenv("DASHBOARD_RUN_ENV", "local-windows" if IS_WINDOWS else "server")
DASHBOARD_DIR = PROJECT_ROOT / "dashboard"
STATUS_DIR = DASHBOARD_DIR / "status"
HEALTH_JSON = STATUS_DIR / "health.json"
SUMMARY_MD = STATUS_DIR / "summary.md"

LOG_DIR = Path(os.getenv("BACKTEST_LOG_DIR", str(PROJECT_ROOT / "logs") if IS_WINDOWS else "/var/log/backtest"))
HEALTH_JSONL = LOG_DIR / "dashboard_health.jsonl"

COMMAND_TIMEOUT_SEC = int(os.getenv("DASHBOARD_COMMAND_TIMEOUT_SEC", "5"))
DB_STATEMENT_TIMEOUT_MS = int(os.getenv("DASHBOARD_DB_TIMEOUT_MS", "3000"))
CACHE_TTL_SEC = int(os.getenv("DASHBOARD_CACHE_TTL_SEC", "30"))

SERVICE_NAME = os.getenv("DASHBOARD_SERVICE_NAME", "backtest-dashboard")
PYTHON_BIN = os.getenv("BACKTEST_PYTHON_BIN", "/opt/stock-backtest/venv/bin/python")

LOG_FILES = {
    "dart.log": LOG_DIR / "dart.log",
    "dart_retry.log": LOG_DIR / "dart_retry.log",
    "price.log": LOG_DIR / "price.log",
    "market_cap.log": LOG_DIR / "market_cap.log",
    "pit.log": LOG_DIR / "pit.log",
    "dq_gate.log": LOG_DIR / "dq_gate.log",
    "healthcheck.log": LOG_DIR / "healthcheck.log",
}

THRESHOLDS = {
    "dart_error_warn_pct": 2.0,
    "dart_error_fail_pct": 10.0,
    "dart_pending_warn_pct": 5.0,
    "dart_pending_fail_pct": 20.0,
    "freshness_warn_days": 2,
    "freshness_fail_days": 4,
    "pit_fallback_warn_pct": 20.0,
    "pit_fallback_fail_pct": 35.0,
    "dq_reject_warn_pct": 40.0,
    "dq_reject_fail_pct": 60.0,
    "validation_reject_warn_count": 1,
    "validation_reject_fail_count": 100,
    "disk_warn_pct": 80.0,
    "disk_fail_pct": 90.0,
    "log_hard_fail_warn_count": 1,
    "log_hard_fail_fail_count": 3,
}
