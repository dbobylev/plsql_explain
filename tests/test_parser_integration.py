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


# ---------------------------------------------------------------------------
# Subprogram / substatement extraction
# ---------------------------------------------------------------------------

_PKG_WITH_IF_LOOP = """\
CREATE OR REPLACE PACKAGE BODY TEST_PKG AS

  PROCEDURE PROCESS(p_id IN NUMBER) IS
    v_x NUMBER;
  BEGIN
    SELECT col1 INTO v_x FROM orders WHERE id = p_id;
    IF v_x > 0 THEN
      UPDATE orders SET status = 'A' WHERE id = p_id;
    ELSIF v_x = 0 THEN
      DELETE FROM orders WHERE id = p_id;
    ELSE
      INSERT INTO audit_log(order_id) VALUES (p_id);
    END IF;
    FOR i IN 1..3 LOOP
      UPDATE counters SET n = n + 1 WHERE id = i;
    END LOOP;
  EXCEPTION
    WHEN OTHERS THEN
      INSERT INTO error_log(order_id, msg) VALUES (p_id, SQLERRM);
  END PROCESS;

  FUNCTION GET_COUNT(p_id IN NUMBER) RETURN NUMBER IS
    v_n NUMBER;
  BEGIN
    SELECT COUNT(*) INTO v_n FROM orders WHERE id = p_id;
    RETURN v_n;
  END GET_COUNT;

END TEST_PKG;
"""

_PKG_DECLARE_BEFORE_BEGIN = """\
CREATE OR REPLACE PACKAGE BODY MYSCHEMA.MYPACKAGE AS

    vNum NUMBER;

    PROCEDURE TEST_PROCEDURE(pName IN VARCHAR2, pVal IN NUMBER) IS
        vRes NUMBER;
        vUpdated DATE;
    BEGIN

        --package4.proc2(pName, pVal);
        vRes := MYSCHEMA.PACKAGE2.PROC2(pName, pVal);

        BEGIN
            NULL;
        END;
    END;

END MYPACKAGE;
"""


@requires_binary
def test_subprogram_extraction_returns_both_subprograms():
    out = parse_object("S", "TEST_PKG", "PACKAGE BODY", _PKG_WITH_IF_LOOP)

    assert out.status == "ok"
    names = {sp.name for sp in out.subprograms}
    assert names == {"PROCESS", "GET_COUNT"}


@requires_binary
def test_subprogram_source_text_contains_procedure_name():
    out = parse_object("S", "TEST_PKG", "PACKAGE BODY", _PKG_WITH_IF_LOOP)

    proc = next(sp for sp in out.subprograms if sp.name == "PROCESS")
    assert "PROCESS" in proc.source_text
    assert proc.start_line < proc.end_line


@requires_binary
def test_substatements_include_expected_types():
    out = parse_object("S", "TEST_PKG", "PACKAGE BODY", _PKG_WITH_IF_LOOP)

    types = {s.statement_type for s in out.substatements}
    assert "SQL_SELECT" in types
    assert "IF" in types
    assert "IF_THEN" in types
    assert "IF_ELSIF" in types
    assert "IF_ELSE" in types
    assert "LOOP_FOR" in types
    assert "EXCEPTION_HANDLER" in types
    assert "DECLARE" in types


@requires_binary
def test_substatements_parent_child_tree_is_consistent():
    """Every substatement with a parent_seq must have a parent with that seq value."""
    out = parse_object("S", "TEST_PKG", "PACKAGE BODY", _PKG_WITH_IF_LOOP)

    by_key = {(s.subprogram, s.seq): s for s in out.substatements}
    for s in out.substatements:
        if s.parent_seq is not None:
            assert (s.subprogram, s.parent_seq) in by_key, (
                f"substatement seq={s.seq} type={s.statement_type} "
                f"has parent_seq={s.parent_seq} but no parent found"
            )


@requires_binary
def test_substatements_scoped_to_correct_subprogram():
    out = parse_object("S", "TEST_PKG", "PACKAGE BODY", _PKG_WITH_IF_LOOP)

    process_stmts = [s for s in out.substatements if s.subprogram == "PROCESS"]
    get_count_stmts = [s for s in out.substatements if s.subprogram == "GET_COUNT"]
    assert len(process_stmts) > 0
    assert len(get_count_stmts) > 0

    process_types = {s.statement_type for s in process_stmts}
    get_count_types = {s.statement_type for s in get_count_stmts}
    # IF/LOOP only in PROCESS; SELECT in both
    assert "IF" in process_types
    assert "LOOP_FOR" in process_types
    assert "IF" not in get_count_types


@requires_binary
def test_substatements_source_text_is_nonempty():
    out = parse_object("S", "TEST_PKG", "PACKAGE BODY", _PKG_WITH_IF_LOOP)

    for s in out.substatements:
        assert s.source_text.strip(), f"Empty source_text for seq={s.seq} type={s.statement_type}"


@requires_binary
def test_declare_block_stops_before_begin_even_with_nested_block():
    out = parse_object("MYSCHEMA", "MYPACKAGE", "PACKAGE BODY", _PKG_DECLARE_BEFORE_BEGIN)

    assert out.status == "ok", f"Expected ok, got {out.status!r}: {out.error_message}"

    declare_stmt = next(
        s for s in out.substatements
        if s.subprogram == "TEST_PROCEDURE" and s.statement_type == "DECLARE"
    )
    assert declare_stmt.start_line == 6
    assert declare_stmt.end_line == 7
    assert declare_stmt.source_text == "vRes NUMBER;\n        vUpdated DATE;"

    top_begin = next(
        s for s in out.substatements
        if s.subprogram == "TEST_PROCEDURE"
        and s.statement_type == "BEGIN_END"
        and s.parent_seq is None
    )
    assert top_begin.start_line == 8
    assert top_begin.source_text.upper() == "BEGIN"

    top_children = [
        s for s in out.substatements
        if s.subprogram == "TEST_PROCEDURE" and s.parent_seq == top_begin.seq
    ]
    assert any(
        child.statement_type == "OTHER"
        and child.start_line == 11
        and "PACKAGE2.PROC2" in child.source_text.upper()
        for child in top_children
    )
    assert any(
        child.statement_type == "BEGIN_END"
        and child.start_line == 13
        for child in top_children
    )
