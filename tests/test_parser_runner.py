import json
import subprocess
from unittest.mock import patch, MagicMock
import pytest

from parser.runner import parse_object, ParserError
from parser.models import SubprogramInfo, SubstatementInfo


def _make_result(payload: dict, returncode: int = 0) -> MagicMock:
    r = MagicMock()
    r.returncode = returncode
    r.stdout = json.dumps(payload)
    r.stderr = ""
    return r


OK_PAYLOAD = {
    "schema_name": "S", "object_name": "PKG_A", "object_type": "PACKAGE BODY",
    "status": "ok", "error_message": None,
    "call_edges": [
        {"caller_subprogram": "PROC1", "callee_schema": None,
         "callee_object": "PKG_B", "callee_subprogram": "GET_DATA"}
    ],
    "table_accesses": [
        {"subprogram": "PROC1", "table_schema": None, "table_name": "ORDERS", "operation": "SELECT"}
    ],
}

WRAPPED_PAYLOAD = {
    "schema_name": "S", "object_name": "PKG_SECRET", "object_type": "PACKAGE BODY",
    "status": "wrapped", "error_message": None,
    "call_edges": [], "table_accesses": [],
}

ERROR_PAYLOAD = {
    "schema_name": "S", "object_name": "PKG_BAD", "object_type": "PACKAGE BODY",
    "status": "error", "error_message": "line 5:3 mismatched input",
    "call_edges": [], "table_accesses": [],
}


def test_parse_ok_returns_output():
    with patch("subprocess.run", return_value=_make_result(OK_PAYLOAD)):
        out = parse_object("S", "PKG_A", "PACKAGE BODY", "source")

    assert out.status == "ok"
    assert len(out.call_edges) == 1
    assert out.call_edges[0].callee_object == "PKG_B"
    assert out.call_edges[0].callee_subprogram == "GET_DATA"
    assert len(out.table_accesses) == 1
    assert out.table_accesses[0].table_name == "ORDERS"
    assert out.table_accesses[0].operation == "SELECT"


def test_parse_wrapped_returns_wrapped_status():
    with patch("subprocess.run", return_value=_make_result(WRAPPED_PAYLOAD)):
        out = parse_object("S", "PKG_SECRET", "PACKAGE BODY", "wrapped source")

    assert out.status == "wrapped"
    assert out.call_edges == []
    assert out.table_accesses == []


def test_parse_error_status_propagated():
    with patch("subprocess.run", return_value=_make_result(ERROR_PAYLOAD)):
        out = parse_object("S", "PKG_BAD", "PACKAGE BODY", "bad source")

    assert out.status == "error"
    assert "mismatched" in out.error_message


def test_nonzero_exit_raises_parser_error():
    r = MagicMock()
    r.returncode = 1
    r.stdout = ""
    r.stderr = "fatal crash"
    with patch("subprocess.run", return_value=r):
        with pytest.raises(ParserError, match="exited with code 1"):
            parse_object("S", "PKG_X", "PACKAGE BODY", "src")


def test_timeout_raises_parser_error():
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=60)):
        with pytest.raises(ParserError, match="timed out"):
            parse_object("S", "PKG_X", "PACKAGE BODY", "src")


def test_binary_not_found_raises_parser_error():
    with patch("subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(ParserError, match="not found"):
            parse_object("S", "PKG_X", "PACKAGE BODY", "src")


def test_invalid_json_raises_parser_error():
    r = MagicMock()
    r.returncode = 0
    r.stdout = "not json"
    r.stderr = ""
    with patch("subprocess.run", return_value=r):
        with pytest.raises(ParserError, match="invalid JSON"):
            parse_object("S", "PKG_X", "PACKAGE BODY", "src")


# ---------------------------------------------------------------------------
# subprograms / substatements deserialization
# ---------------------------------------------------------------------------

SUBPROGRAMS_PAYLOAD = {
    "schema_name": "S", "object_name": "PKG_A", "object_type": "PACKAGE BODY",
    "status": "ok", "error_message": None,
    "call_edges": [], "table_accesses": [],
    "subprograms": [
        {
            "name": "PROC1", "subprogram_type": "PROCEDURE",
            "start_line": 2, "end_line": 10,
            "source_text": "PROCEDURE PROC1 IS\nBEGIN\n  NULL;\nEND PROC1;",
        }
    ],
    "substatements": [
        {
            "subprogram": "PROC1", "seq": 0, "parent_seq": None, "position": 0,
            "statement_type": "SQL_SELECT", "start_line": 5, "end_line": 5,
            "source_text": "SELECT 1 FROM dual",
        },
        {
            "subprogram": "PROC1", "seq": 1, "parent_seq": None, "position": 1,
            "statement_type": "IF", "start_line": 6, "end_line": 9,
            "source_text": "IF v_x > 0 THEN NULL; END IF;",
        },
        {
            "subprogram": "PROC1", "seq": 2, "parent_seq": 1, "position": 0,
            "statement_type": "IF_THEN", "start_line": 7, "end_line": 7,
            "source_text": "NULL",
        },
    ],
}


def test_parse_ok_returns_subprograms():
    with patch("subprocess.run", return_value=_make_result(SUBPROGRAMS_PAYLOAD)):
        out = parse_object("S", "PKG_A", "PACKAGE BODY", "source")

    assert len(out.subprograms) == 1
    sp = out.subprograms[0]
    assert sp.name == "PROC1"
    assert sp.subprogram_type == "PROCEDURE"
    assert sp.start_line == 2
    assert sp.end_line == 10
    assert "PROC1" in sp.source_text


def test_parse_ok_returns_substatements():
    with patch("subprocess.run", return_value=_make_result(SUBPROGRAMS_PAYLOAD)):
        out = parse_object("S", "PKG_A", "PACKAGE BODY", "source")

    assert len(out.substatements) == 3

    sql_stmt = out.substatements[0]
    assert sql_stmt.statement_type == "SQL_SELECT"
    assert sql_stmt.seq == 0
    assert sql_stmt.parent_seq is None
    assert sql_stmt.subprogram == "PROC1"

    if_stmt = out.substatements[1]
    assert if_stmt.statement_type == "IF"
    assert if_stmt.seq == 1
    assert if_stmt.parent_seq is None

    if_then = out.substatements[2]
    assert if_then.statement_type == "IF_THEN"
    assert if_then.seq == 2
    assert if_then.parent_seq == 1


def test_parse_payload_without_subprograms_returns_empty_lists():
    """Legacy payloads without subprograms/substatements keys → empty lists."""
    with patch("subprocess.run", return_value=_make_result(OK_PAYLOAD)):
        out = parse_object("S", "PKG_A", "PACKAGE BODY", "source")

    assert out.subprograms == []
    assert out.substatements == []
