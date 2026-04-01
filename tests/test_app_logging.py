from __future__ import annotations

import argparse
from datetime import datetime

from app_logging import SafeConsoleStream, build_log_filename


class _FakeConsoleStream:
    def __init__(self, encoding: str) -> None:
        self.encoding = encoding
        self.messages: list[str] = []

    def write(self, message: str) -> int:
        self.messages.append(message)
        return len(message)

    def flush(self) -> None:
        return None


def test_build_log_filename_uses_command_and_db_object_parts() -> None:
    args = argparse.Namespace(
        command="parse",
        schema="MYSCHEMA",
        object="PKG_FOO",
        subprogram=None,
    )

    filename = build_log_filename(args, timestamp=datetime(2026, 4, 1, 12, 30, 45, 123456))

    assert filename == "parse_myschema_pkg_foo_20260401_123045_123456.log"


def test_safe_console_stream_escapes_unencodable_characters() -> None:
    stream = _FakeConsoleStream("cp1251")
    safe_stream = SafeConsoleStream(lambda: stream)

    safe_stream.write("emoji 😀")

    assert stream.messages == ["emoji \\U0001f600"]
