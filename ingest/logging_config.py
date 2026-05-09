from __future__ import annotations

import logging
import os
import platform
from pathlib import Path


LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


def default_log_dir() -> Path:
    if platform.system() == "Windows":
        return Path.cwd() / "logs"
    return Path("/var/log/backtest")


def configure_logging(log_name: str, level: int = logging.INFO) -> None:
    """Send ingest logs to stderr and to the dashboard log directory when writable."""
    log_dir = Path(os.getenv("BACKTEST_LOG_DIR", str(default_log_dir())))
    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(LOG_FORMAT)
    if not any(isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler) for handler in root.handlers):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)
    else:
        for handler in root.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                handler.setFormatter(formatter)

    log_path = log_dir / log_name
    if any(isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == log_path for handler in root.handlers):
        return

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
    except OSError as exc:
        logging.getLogger(__name__).warning("파일 로그 핸들러 설정 실패(%s): %s", log_path, exc)
        return

    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
