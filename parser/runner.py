from __future__ import annotations

import json
import logging
import os
import subprocess
import sys

from parser.models import CallEdge, ParseOutput, SubprogramInfo, SubstatementInfo, TableAccess

_DEFAULT_PARSER_TIMEOUT_SECONDS = 600
_logger = logging.getLogger(__name__)


class ParserError(Exception):
    """Raised when the C# binary exits with non-zero or produces invalid JSON."""


def _parser_path() -> str:
    return os.environ.get(
        "PLSQL_PARSER_PATH",
        "./plsql_parser/bin/Release/net8.0/PlsqlParser",
    )


def _subprocess_env() -> dict:
    """Return env for subprocess, injecting DOTNET_ROOT from common fallback locations
    if not already set. Needed on machines where .NET is installed outside system PATH."""
    env = os.environ.copy()
    if "DOTNET_ROOT" not in env:
        if sys.platform == "win32":
            candidates = [
                r"C:\Program Files\dotnet",
                r"C:\Program Files (x86)\dotnet",
                os.path.expanduser(r"~\.dotnet"),
            ]
        else:
            candidates = [
                os.path.expanduser("~/.dotnet"),
                "/usr/local/share/dotnet",
                "/usr/share/dotnet"
            ]
        for candidate in candidates:
            if os.path.isdir(candidate):
                env["DOTNET_ROOT"] = candidate
                break
    return env


def _parser_timeout_seconds() -> int:
    raw = os.environ.get("PARSER_TIMEOUT_SECONDS")
    if raw is None:
        return _DEFAULT_PARSER_TIMEOUT_SECONDS

    try:
        timeout = int(raw)
    except ValueError as e:
        raise ParserError(
            f"Invalid PARSER_TIMEOUT_SECONDS value: {raw!r}. Expected integer seconds."
        ) from e

    if timeout <= 0:
        raise ParserError(
            f"Invalid PARSER_TIMEOUT_SECONDS value: {raw!r}. Expected positive integer seconds."
        )

    return timeout


def parse_object(
    schema_name: str,
    object_name: str,
    object_type: str,
    source_text: str,
    timeout: int | None = None,
) -> ParseOutput:
    """
    Invokes the C# parser binary via subprocess, passes the object via stdin as JSON,
    returns a ParseOutput dataclass.
    Raises ParserError on subprocess failure or JSON decode failure.
    """
    if source_text and not source_text.endswith('\n'):
        source_text += '\n'

    timeout = _parser_timeout_seconds() if timeout is None else timeout
    _logger.debug(
        "parser.run started: schema=%s, object=%s, type=%s, timeout=%s, source_length=%d",
        schema_name,
        object_name,
        object_type,
        timeout,
        len(source_text),
    )

    input_payload = json.dumps(
        {
            "schema_name": schema_name,
            "object_name": object_name,
            "object_type": object_type,
            "source_text": source_text,
        },
        ensure_ascii=False,
    )

    try:
        result = subprocess.run(
            [_parser_path()],
            input=input_payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            env=_subprocess_env(),
        )
    except subprocess.TimeoutExpired as e:
        _logger.exception(
            "Parser timed out: schema=%s, object=%s, timeout=%s",
            schema_name,
            object_name,
            timeout,
        )
        raise ParserError(
            f"Parser timed out after {timeout}s for {schema_name}.{object_name}"
        ) from e
    except FileNotFoundError as e:
        _logger.exception("Parser binary not found: %s", _parser_path())
        raise ParserError(
            f"Parser binary not found at: {_parser_path()}"
        ) from e

    if result.returncode != 0:
        _logger.error(
            "Parser exited with non-zero code: schema=%s, object=%s, returncode=%s, stderr=%s",
            schema_name,
            object_name,
            result.returncode,
            result.stderr.strip(),
        )
        raise ParserError(
            f"Parser exited with code {result.returncode} for "
            f"{schema_name}.{object_name}. stderr: {result.stderr.strip()}"
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        _logger.exception(
            "Parser returned invalid JSON: schema=%s, object=%s, stdout_length=%d",
            schema_name,
            object_name,
            len(result.stdout),
        )
        raise ParserError(
            f"Parser returned invalid JSON for {schema_name}.{object_name}: {e}"
        ) from e

    _logger.debug(
        "parser.run completed: schema=%s, object=%s, status=%s, call_edges=%d, table_accesses=%d, subprograms=%d, substatements=%d",
        schema_name,
        object_name,
        data["status"],
        len(data.get("call_edges", [])),
        len(data.get("table_accesses", [])),
        len(data.get("subprograms", [])),
        len(data.get("substatements", [])),
    )

    return ParseOutput(
        schema_name=data["schema_name"],
        object_name=data["object_name"],
        object_type=data["object_type"],
        status=data["status"],
        error_message=data.get("error_message"),
        call_edges=[
            CallEdge(
                caller_subprogram=edge["caller_subprogram"],
                callee_schema=edge["callee_schema"],
                callee_object=edge["callee_object"],
                callee_subprogram=edge["callee_subprogram"],
            )
            for edge in data.get("call_edges", [])
        ],
        table_accesses=[
            TableAccess(
                subprogram=acc["subprogram"],
                table_schema=acc["table_schema"],
                table_name=acc["table_name"],
                operation=acc["operation"],
            )
            for acc in data.get("table_accesses", [])
        ],
        subprograms=[
            SubprogramInfo(
                name=sp["name"],
                subprogram_type=sp["subprogram_type"],
                start_line=sp["start_line"],
                end_line=sp["end_line"],
                source_text=sp["source_text"],
            )
            for sp in data.get("subprograms", [])
        ],
        substatements=[
            SubstatementInfo(
                subprogram=s["subprogram"],
                seq=s["seq"],
                parent_seq=s["parent_seq"],
                position=s["position"],
                statement_type=s["statement_type"],
                start_line=s["start_line"],
                end_line=s["end_line"],
                source_text=s["source_text"],
            )
            for s in data.get("substatements", [])
        ],
    )
