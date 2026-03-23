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
