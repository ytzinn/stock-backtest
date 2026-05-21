from __future__ import annotations

import logging
import os
import platform
from logging.handlers import RotatingFileHandler
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

    # 기존 FileHandler 모두 제거 후 교체 — 중복 기록 방지
    for h in root.handlers[:]:
        if isinstance(h, logging.FileHandler):
            root.removeHandler(h)
            h.close()

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / log_name,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
    except OSError as exc:
        logging.getLogger(__name__).warning("파일 로그 핸들러 설정 실패(%s): %s", log_dir / log_name, exc)
        return

    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
