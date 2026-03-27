from __future__ import annotations

import json
from typing import Optional
from unittest.mock import patch

import pytest

from main import build_parser, cmd_debug
from parser.models import (
    CallEdge,
    ParseOutput,
    SubprogramInfo,
    SubstatementInfo,
    TableAccess,
)


def _make_output(
    status: str = "ok",
    error_message: Optional[str] = None,
    call_edges=None,
    table_accesses=None,
    subprograms=None,
    substatements=None,
) -> ParseOutput:
    return ParseOutput(
        schema_name="DEBUG",
        object_name="ANONYMOUS",
        object_type="PACKAGE BODY",
        status=status,
        error_message=error_message,
        call_edges=call_edges or [],
        table_accesses=table_accesses or [],
        subprograms=subprograms or [],
        substatements=substatements or [],
    )


# ---------- argparse integration (smoke) ----------

def test_cmd_debug_ok_status_printed(capsys):
    result = _make_output(status="ok")
    args = build_parser().parse_args(["debug", "--source", "BEGIN NULL; END;"])
    with patch("parser.runner.parse_object", return_value=result):
        cmd_debug(args)
    out = capsys.readouterr().out
    assert "ok" in out
    assert "ANONYMOUS" in out


def test_cmd_debug_error_message_printed(capsys):
    result = _make_output(status="error", error_message="line 3:0 mismatched input")
    args = build_parser().parse_args(["debug", "--source", "GARBAGE"])
    with patch("parser.runner.parse_object", return_value=result):
        cmd_debug(args)
    out = capsys.readouterr().out
    assert "error" in out
    assert "mismatched input" in out


def test_cmd_debug_json_output_is_valid_json(capsys):
    result = _make_output(
        status="ok",
        call_edges=[CallEdge(caller_subprogram="P", callee_schema=None, callee_object="PKG_B", callee_subprogram=None)],
    )
    args = build_parser().parse_args(["debug", "--source", "x", "--json"])
    with patch("parser.runner.parse_object", return_value=result):
        cmd_debug(args)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["status"] == "ok"
    assert data["call_edges"][0]["callee_object"] == "PKG_B"


def test_cmd_debug_json_contains_all_keys(capsys):
    args = build_parser().parse_args(["debug", "--source", "x", "--json"])
    with patch("parser.runner.parse_object", return_value=_make_output()):
        cmd_debug(args)
    data = json.loads(capsys.readouterr().out)
    for key in ("schema_name", "object_name", "object_type", "status",
                 "error_message", "call_edges", "table_accesses", "subprograms", "substatements"):
        assert key in data


def test_cmd_debug_parser_error_exits(capsys):
    from parser.runner import ParserError
    args = build_parser().parse_args(["debug", "--source", "x"])
    with patch("parser.runner.parse_object", side_effect=ParserError("binary not found")):
        with pytest.raises(SystemExit) as exc_info:
            cmd_debug(args)
    assert exc_info.value.code == 1
    assert "binary not found" in capsys.readouterr().err


def test_cmd_debug_source_file_read(tmp_path, capsys):
    sql_file = tmp_path / "test.sql"
    sql_file.write_text("BEGIN NULL; END;", encoding="utf-8")
    args = build_parser().parse_args(["debug", "--source-file", str(sql_file)])
    with patch("parser.runner.parse_object", return_value=_make_output()) as mock_parse:
        cmd_debug(args)
    mock_parse.assert_called_once()
    _, _, _, source_text = mock_parse.call_args[0]
    assert source_text == "BEGIN NULL; END;"


def test_cmd_debug_missing_source_file_exits(capsys):
    args = build_parser().parse_args(["debug", "--source-file", "/nonexistent/path.sql"])
    with pytest.raises(SystemExit) as exc_info:
        cmd_debug(args)
    assert exc_info.value.code == 1
    assert "Cannot read file" in capsys.readouterr().err


def test_cmd_debug_call_edges_table_printed(capsys):
    result = _make_output(
        call_edges=[
            CallEdge(caller_subprogram="PROC_A", callee_schema="MYSCHEMA",
                     callee_object="PKG_B", callee_subprogram="GET_DATA"),
        ]
    )
    args = build_parser().parse_args(["debug", "--source", "x"])
    with patch("parser.runner.parse_object", return_value=result):
        cmd_debug(args)
    out = capsys.readouterr().out
    assert "PKG_B" in out
    assert "GET_DATA" in out


def test_cmd_debug_table_accesses_printed(capsys):
    result = _make_output(
        table_accesses=[
            TableAccess(subprogram="PROC_A", table_schema="S", table_name="ORDERS", operation="SELECT"),
        ]
    )
    args = build_parser().parse_args(["debug", "--source", "x"])
    with patch("parser.runner.parse_object", return_value=result):
        cmd_debug(args)
    out = capsys.readouterr().out
    assert "ORDERS" in out
    assert "SELECT" in out


def test_cmd_debug_subprograms_printed(capsys):
    result = _make_output(
        subprograms=[
            SubprogramInfo(name="PROC_A", subprogram_type="PROCEDURE", start_line=1, end_line=10, source_text="..."),
        ]
    )
    args = build_parser().parse_args(["debug", "--source", "x"])
    with patch("parser.runner.parse_object", return_value=result):
        cmd_debug(args)
    out = capsys.readouterr().out
    assert "PROC_A" in out
    assert "PROCEDURE" in out


def test_cmd_debug_substatement_tree_printed(capsys):
    result = _make_output(
        substatements=[
            SubstatementInfo(subprogram="PROC_A", seq=0, parent_seq=None, position=0,
                             statement_type="IF", start_line=3, end_line=7, source_text="IF ..."),
            SubstatementInfo(subprogram="PROC_A", seq=1, parent_seq=0, position=0,
                             statement_type="IF_THEN", start_line=4, end_line=5, source_text="..."),
        ]
    )
    args = build_parser().parse_args(["debug", "--source", "x"])
    with patch("parser.runner.parse_object", return_value=result):
        cmd_debug(args)
    out = capsys.readouterr().out
    assert "IF" in out
    assert "IF_THEN" in out
    assert "PROC_A" in out
