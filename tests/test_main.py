import pytest
from main import build_parser


def test_fetch_parses_schema():
    args = build_parser().parse_args(["fetch", "--schema", "MYSCHEMA"])
    assert args.schema == "MYSCHEMA"
    assert args.object is None


def test_fetch_parses_object():
    args = build_parser().parse_args(["fetch", "--schema", "S", "--object", "PKG_FOO"])
    assert args.object == "PKG_FOO"


def test_fetch_missing_schema_raises():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["fetch"])


def test_fetch_parse_flag_defaults_false():
    args = build_parser().parse_args(["fetch", "--schema", "S"])
    assert args.parse is False


def test_fetch_parse_flag_set():
    args = build_parser().parse_args(["fetch", "--schema", "S", "--parse"])
    assert args.parse is True


def test_parse_command_schema():
    args = build_parser().parse_args(["parse", "--schema", "MYSCHEMA"])
    assert args.schema == "MYSCHEMA"
    assert args.object is None
    assert args.force is False


def test_parse_command_with_object_and_force():
    args = build_parser().parse_args(["parse", "--schema", "S", "--object", "PKG_A", "--force"])
    assert args.object == "PKG_A"
    assert args.force is True


def test_parse_command_missing_schema_raises():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["parse"])


def test_explain_parses_schema_and_object():
    args = build_parser().parse_args(["explain", "--schema", "MYSCHEMA", "--object", "PKG_A"])
    assert args.schema == "MYSCHEMA"
    assert args.object == "PKG_A"
    assert args.subprogram is None


def test_explain_parses_subprogram():
    args = build_parser().parse_args(
        ["explain", "--schema", "S", "--object", "PKG_A", "--subprogram", "PROC_X"]
    )
    assert args.subprogram == "PROC_X"


def test_explain_missing_schema_raises():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["explain", "--object", "PKG_A"])


def test_explain_missing_object_raises():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["explain", "--schema", "S"])


def test_debug_defaults():
    args = build_parser().parse_args(["debug"])
    assert args.schema == "DEBUG"
    assert args.object == "ANONYMOUS"
    assert args.object_type == "PACKAGE BODY"
    assert args.source_file is None
    assert args.source is None
    assert args.output_json is False


def test_debug_schema_override():
    args = build_parser().parse_args(["debug", "--schema", "MYSCHEMA"])
    assert args.schema == "MYSCHEMA"


def test_debug_source_file():
    args = build_parser().parse_args(["debug", "--source-file", "foo.sql"])
    assert args.source_file == "foo.sql"
    assert args.source is None


def test_debug_inline_source():
    args = build_parser().parse_args(["debug", "--source", "BEGIN NULL; END;"])
    assert args.source == "BEGIN NULL; END;"
    assert args.source_file is None


def test_debug_type_override():
    args = build_parser().parse_args(["debug", "--type", "PROCEDURE"])
    assert args.object_type == "PROCEDURE"


def test_debug_json_flag():
    args = build_parser().parse_args(["debug", "--json"])
    assert args.output_json is True


def test_debug_source_and_source_file_mutually_exclusive():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["debug", "--source", "x", "--source-file", "f.sql"])
