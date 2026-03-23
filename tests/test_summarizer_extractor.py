"""Tests for summarizer.extractor.extract_subprogram."""
from __future__ import annotations

from summarizer.extractor import extract_subprogram

# ── Sample PL/SQL source with two subprograms ────────────────────────────────

PACKAGE_BODY = """\
CREATE OR REPLACE PACKAGE BODY PKG_A AS

  PROCEDURE PROC_X(p_id IN NUMBER) IS
    v_name VARCHAR2(100);
  BEGIN
    SELECT name INTO v_name FROM customers WHERE id = p_id;
    DBMS_OUTPUT.PUT_LINE(v_name);
  END PROC_X;

  FUNCTION FUNC_Y(p_code IN VARCHAR2) RETURN NUMBER IS
  BEGIN
    RETURN LENGTH(p_code);
  END FUNC_Y;

END PKG_A;
"""

NESTED_BEGIN = """\
CREATE OR REPLACE PACKAGE BODY PKG_B AS

  PROCEDURE PROC_Z IS
  BEGIN
    BEGIN
      INSERT INTO log_table VALUES (1, 'test');
    EXCEPTION
      WHEN OTHERS THEN NULL;
    END;
  END PROC_Z;

END PKG_B;
"""

# ── Tests ─────────────────────────────────────────────────────────────────────

def test_extract_procedure_body() -> None:
    result = extract_subprogram(PACKAGE_BODY, "PROC_X")
    assert "PROCEDURE PROC_X" in result
    assert "SELECT name INTO v_name" in result
    assert "END PROC_X" in result
    # Should not include the function
    assert "FUNC_Y" not in result


def test_extract_function_body() -> None:
    result = extract_subprogram(PACKAGE_BODY, "FUNC_Y")
    assert "FUNCTION FUNC_Y" in result
    assert "RETURN LENGTH" in result
    assert "END FUNC_Y" in result
    # Should not include the procedure
    assert "PROC_X" not in result


def test_extract_nested_begin_end() -> None:
    """Nested BEGIN/END inside a procedure must not terminate extraction early."""
    result = extract_subprogram(NESTED_BEGIN, "PROC_Z")
    assert "PROCEDURE PROC_Z" in result
    assert "INSERT INTO log_table" in result
    assert "END PROC_Z" in result


def test_extract_fallback_on_missing() -> None:
    """If subprogram not found, returns the full source_text."""
    result = extract_subprogram(PACKAGE_BODY, "NONEXISTENT_PROC")
    assert result == PACKAGE_BODY


def test_extract_none_subprogram_returns_full() -> None:
    """subprogram=None → full source_text returned unchanged."""
    result = extract_subprogram(PACKAGE_BODY, "")
    assert result == PACKAGE_BODY
