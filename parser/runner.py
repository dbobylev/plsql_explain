from __future__ import annotations

import json
import os
import subprocess

from parser.models import CallEdge, ParseOutput, SubprogramInfo, SubstatementInfo, TableAccess


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
        for candidate in [
            os.path.expanduser("~/.dotnet"),
            "/usr/local/share/dotnet",
        ]:
            if os.path.isdir(candidate):
                env["DOTNET_ROOT"] = candidate
                break
    return env


def parse_object(
    schema_name: str,
    object_name: str,
    object_type: str,
    source_text: str,
    timeout: int = 60,
) -> ParseOutput:
    """
    Invokes the C# parser binary via subprocess, passes the object via stdin as JSON,
    returns a ParseOutput dataclass.
    Raises ParserError on subprocess failure or JSON decode failure.
    """
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
        raise ParserError(
            f"Parser timed out after {timeout}s for {schema_name}.{object_name}"
        ) from e
    except FileNotFoundError as e:
        raise ParserError(
            f"Parser binary not found at: {_parser_path()}"
        ) from e

    if result.returncode != 0:
        raise ParserError(
            f"Parser exited with code {result.returncode} for "
            f"{schema_name}.{object_name}. stderr: {result.stderr.strip()}"
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise ParserError(
            f"Parser returned invalid JSON for {schema_name}.{object_name}: {e}"
        ) from e

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
