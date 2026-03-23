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
