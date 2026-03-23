import json
import subprocess
from unittest.mock import patch, MagicMock
import pytest

from parser.runner import parse_object, ParserError


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
