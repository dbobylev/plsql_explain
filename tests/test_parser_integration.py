"""
Integration tests for the C# parser binary.
These tests call the real binary and require it to be built first:
    cd plsql_parser && dotnet build -c Release

Skipped automatically if the binary is missing or the required .NET runtime
is not installed on the current machine.
"""
import os
import subprocess
from pathlib import Path

import pytest

from parser.runner import parse_object, ParserError, _subprocess_env

_BINARY_PATH = os.environ.get(
    "PLSQL_PARSER_PATH",
    "./plsql_parser/bin/Release/net8.0/PlsqlParser",
)


def _binary_runnable() -> bool:
    """Return True only if the binary exists AND the required runtime is available."""
    if not Path(_BINARY_PATH).exists():
        return False
    try:
        r = subprocess.run(
            [_BINARY_PATH],
            input='{"schema_name":"S","object_name":"T","object_type":"PACKAGE BODY","source_text":""}',
            capture_output=True,
            text=True,
            timeout=10,
            env=_subprocess_env(),
        )
        # Any exit code other than a .NET "framework not found" (150) means it ran
        return r.returncode != 150
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


requires_binary = pytest.mark.skipif(
    not _binary_runnable(),
    reason=f"Parser binary not runnable at {_BINARY_PATH} (missing or .NET runtime unavailable)",
)


# ---------------------------------------------------------------------------
# Test sources
# ---------------------------------------------------------------------------

_PKG_KEEP_DENSE_RANK = """\
CREATE OR REPLACE PACKAGE BODY TEST_PKG AS

  PROCEDURE GET_FIRST_ISN(p_id IN NUMBER) IS
    v_val NUMBER;
  BEGIN
    SELECT MIN(d.isn) KEEP (DENSE_RANK FIRST ORDER BY d.datecre)
      INTO v_val
      FROM some_table d
     WHERE d.id = p_id;
  END GET_FIRST_ISN;

END TEST_PKG;
"""

_PKG_KEEP_LAST = """\
CREATE OR REPLACE PACKAGE BODY TEST_PKG AS

  FUNCTION GET_LATEST(p_id IN NUMBER) RETURN NUMBER IS
    v_val NUMBER;
  BEGIN
    SELECT MAX(d.amount) KEEP (DENSE_RANK LAST ORDER BY d.created_at)
      INTO v_val
      FROM transactions d
     WHERE d.id = p_id;
    RETURN v_val;
  END GET_LATEST;

END TEST_PKG;
"""

_PKG_KEEP_OVER = """\
CREATE OR REPLACE PACKAGE BODY TEST_PKG AS

  PROCEDURE CALC(p_id IN NUMBER) IS
    v_val NUMBER;
  BEGIN
    SELECT MIN(d.val) KEEP (DENSE_RANK FIRST ORDER BY d.ts) OVER (PARTITION BY d.grp)
      INTO v_val
      FROM analytics d
     WHERE d.id = p_id;
  END CALC;

END TEST_PKG;
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@requires_binary
def test_keep_dense_rank_first_no_parse_errors():
    """MIN(...) KEEP (DENSE_RANK FIRST ORDER BY ...) must not produce parse errors."""
    out = parse_object("S", "TEST_PKG", "PACKAGE BODY", _PKG_KEEP_DENSE_RANK)

    assert out.status == "ok", f"Expected ok, got {out.status!r}: {out.error_message}"
    assert out.error_message is None or out.error_message == ""


@requires_binary
def test_keep_dense_rank_first_captures_table_access():
    """Table access inside KEEP aggregate must be captured."""
    out = parse_object("S", "TEST_PKG", "PACKAGE BODY", _PKG_KEEP_DENSE_RANK)

    assert out.status == "ok"
    table_names = [ta.table_name for ta in out.table_accesses]
    assert "SOME_TABLE" in table_names


@requires_binary
def test_keep_dense_rank_last_no_parse_errors():
    """MAX(...) KEEP (DENSE_RANK LAST ORDER BY ...) must not produce parse errors."""
    out = parse_object("S", "TEST_PKG", "PACKAGE BODY", _PKG_KEEP_LAST)

    assert out.status == "ok", f"Expected ok, got {out.status!r}: {out.error_message}"


@requires_binary
def test_keep_with_over_clause_no_parse_errors():
    """KEEP (...) OVER (PARTITION BY ...) analytic form must not produce parse errors."""
    out = parse_object("S", "TEST_PKG", "PACKAGE BODY", _PKG_KEEP_OVER)

    assert out.status == "ok", f"Expected ok, got {out.status!r}: {out.error_message}"
