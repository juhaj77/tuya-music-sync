"""Application-wide logging setup.

Writes to a rotating log file in the app's settings folder (`logs` under
config.store.default_config_dir(): %APPDATA%\\AiramMusicLights on Windows,
~/.config/AiramMusicLights on Linux), echoes
to the console, and keeps a small in-memory ring buffer that the
Diagnostics tab polls to show recent events without re-reading the file.

Secrets policy: DeviceConfig.local_key must never be passed to logger calls.
Every place in this codebase that logs a device only logs its name/ip/id.
"""
from __future__ import annotations

import collections
import logging
import logging.handlers
from pathlib import Path
from typing import Deque, List

from ..config.store import default_config_dir


class LogBuffer(logging.Handler):
    """A logging.Handler that keeps the last N formatted records in memory
    for display in the UI, independent of the on-disk log file."""

    def __init__(self, capacity: int = 500):
        super().__init__()
        self._buffer: Deque[str] = collections.deque(maxlen=capacity)
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._buffer.append(self.format(record))
        except Exception:
            pass

    def get_recent(self, n: int = 200) -> List[str]:
        items = list(self._buffer)
        return items[-n:]


_log_buffer: "LogBuffer | None" = None


def get_log_buffer() -> LogBuffer:
    global _log_buffer
    if _log_buffer is None:
        _log_buffer = LogBuffer()
    return _log_buffer


def setup_logging(log_dir: "Path | None" = None, level: int = logging.INFO) -> LogBuffer:
    if log_dir is None:
        log_dir = default_config_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("airam_lights")
    root.setLevel(level)
    root.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "app.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)

    buffer = get_log_buffer()
    root.addHandler(buffer)

    root.info("Logging initialized, writing to %s", log_dir / "app.log")
    return buffer
