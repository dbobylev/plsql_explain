from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, TextIO

LOG_LEVEL_NAMES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
DEFAULT_LOG_LEVEL = "DEBUG"
LOG_LEVEL_ENV_VAR = "PLSQL_EXPLAIN_LOG_LEVEL"

_PROJECT_HANDLER_ATTR = "_plsql_explain_handler"


class _MaxLevelFilter(logging.Filter):
    def __init__(self, max_level: int) -> None:
        super().__init__()
        self._max_level = max_level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= self._max_level


class SafeConsoleStream:
    """
    Stream wrapper that never raises on console encoding mismatches.

    PowerShell may use a legacy code page that cannot print emoji returned by the
    LLM. We escape unsupported characters instead of crashing the command.
    """

    def __init__(self, stream_getter: Callable[[], TextIO]) -> None:
        self._stream_getter = stream_getter

    def _stream(self) -> TextIO:
        return self._stream_getter()

    @property
    def encoding(self) -> str:
        return getattr(self._stream(), "encoding", None) or "utf-8"

    def write(self, message: str) -> int:
        stream = self._stream()
        if not isinstance(message, str):
            message = str(message)
        encoding = self.encoding
        safe_message = message.encode(encoding, errors="backslashreplace").decode(encoding)
        written = stream.write(safe_message)
        return len(safe_message) if written is None else written

    def flush(self) -> None:
        self._stream().flush()

    def isatty(self) -> bool:
        return bool(getattr(self._stream(), "isatty", lambda: False)())

    def __getattr__(self, item: str):
        return getattr(self._stream(), item)


@dataclass(frozen=True)
class LoggingSession:
    log_path: Path | None
    log_level: str


def _is_project_handler(handler: logging.Handler) -> bool:
    return bool(getattr(handler, _PROJECT_HANDLER_ATTR, False))


def _mark_project_handler(handler: logging.Handler) -> logging.Handler:
    setattr(handler, _PROJECT_HANDLER_ATTR, True)
    return handler


def _remove_project_handlers(root_logger: logging.Logger) -> None:
    for handler in list(root_logger.handlers):
        if _is_project_handler(handler):
            root_logger.removeHandler(handler)
            handler.close()


def _normalize_log_level(level_name: str | None) -> tuple[int, str]:
    normalized = (level_name or DEFAULT_LOG_LEVEL).upper()
    if normalized not in LOG_LEVEL_NAMES:
        raise ValueError(
            f"Unsupported log level {level_name!r}. Expected one of: {', '.join(LOG_LEVEL_NAMES)}"
        )
    return getattr(logging, normalized), normalized


def default_log_level() -> str:
    env_level = os.environ.get(LOG_LEVEL_ENV_VAR, DEFAULT_LOG_LEVEL)
    _, normalized = _normalize_log_level(env_level)
    return normalized


def build_log_filename(args: argparse.Namespace, timestamp: datetime | None = None) -> str:
    timestamp = timestamp or datetime.now()
    parts = [_sanitize_component(getattr(args, "command", "run"))]

    for attr in ("schema", "object", "subprogram"):
        value = getattr(args, attr, None)
        if value:
            parts.append(_sanitize_component(value))

    parts.append(timestamp.strftime("%Y%m%d_%H%M%S_%f"))
    return "_".join(part for part in parts if part) + ".log"


def build_log_path(args: argparse.Namespace, logs_dir: str | Path = "logs") -> Path:
    return Path(logs_dir) / build_log_filename(args)


def configure_logging(args: argparse.Namespace, logs_dir: str | Path = "logs") -> LoggingSession:
    level_value, level_name = _normalize_log_level(getattr(args, "log_level", default_log_level()))
    log_path = build_log_path(args, logs_dir=logs_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    _remove_project_handlers(root_logger)
    root_logger.setLevel(logging.DEBUG)

    for handler in _build_console_handlers(level_value):
        root_logger.addHandler(handler)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(level_value)
    file_handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root_logger.addHandler(_mark_project_handler(file_handler))

    return LoggingSession(log_path=log_path, log_level=level_name)


def ensure_logging_configured(log_level: str | None = None) -> None:
    root_logger = logging.getLogger()
    if any(_is_project_handler(handler) for handler in root_logger.handlers):
        return

    level_value, _ = _normalize_log_level(log_level or default_log_level())
    root_logger.setLevel(logging.DEBUG)
    for handler in _build_console_handlers(level_value):
        root_logger.addHandler(handler)


def shutdown_logging() -> None:
    _remove_project_handlers(logging.getLogger())


def _build_console_handlers(level_value: int) -> list[logging.Handler]:
    formatter = logging.Formatter("%(message)s")
    stdout_level = max(level_value, logging.INFO)
    stderr_level = max(level_value, logging.WARNING)

    stdout_handler = logging.StreamHandler(SafeConsoleStream(lambda: sys.stdout))
    stdout_handler.setLevel(stdout_level)
    stdout_handler.addFilter(_MaxLevelFilter(logging.INFO))
    stdout_handler.setFormatter(formatter)

    stderr_handler = logging.StreamHandler(SafeConsoleStream(lambda: sys.stderr))
    stderr_handler.setLevel(stderr_level)
    stderr_handler.setFormatter(formatter)

    return [
        _mark_project_handler(stdout_handler),
        _mark_project_handler(stderr_handler),
    ]


def _sanitize_component(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z_.-]+", "_", value.strip().lower())
    return normalized.strip("._") or "unknown"
