from unittest.mock import patch, MagicMock
from fetcher.oracle_client import fetch_objects


def make_mock_connect(rows):
    """Return (conn_mock, cursor_mock) with cursor iterating over rows."""
    cursor_mock = MagicMock()
    cursor_mock.__enter__ = MagicMock(return_value=cursor_mock)
    cursor_mock.__iter__ = MagicMock(return_value=iter(rows))

    conn_mock = MagicMock()
    conn_mock.__enter__ = MagicMock(return_value=conn_mock)
    conn_mock.cursor.return_value = cursor_mock
    return conn_mock, cursor_mock


@patch("fetcher.oracle_client._connect")
def test_single_object_multiline_assembled(mock_connect):
    rows = [
        ("MYSCHEMA", "PKG_FOO", "PACKAGE BODY", "line1\n"),
        ("MYSCHEMA", "PKG_FOO", "PACKAGE BODY", "line2\n"),
    ]
    conn, _ = make_mock_connect(rows)
    mock_connect.return_value = conn

    result = list(fetch_objects("myschema"))

    assert result == [("MYSCHEMA", "PKG_FOO", "PACKAGE BODY", "line1\nline2\n")]


@patch("fetcher.oracle_client._connect")
def test_two_objects_boundary_detected(mock_connect):
    rows = [
        ("S", "OBJ_A", "PROCEDURE", "a1\n"),
        ("S", "OBJ_A", "PROCEDURE", "a2\n"),
        ("S", "OBJ_B", "FUNCTION", "b1\n"),
    ]
    conn, _ = make_mock_connect(rows)
    mock_connect.return_value = conn

    result = list(fetch_objects("s"))

    assert len(result) == 2
    assert result[0] == ("S", "OBJ_A", "PROCEDURE", "a1\na2\n")
    assert result[1] == ("S", "OBJ_B", "FUNCTION", "b1\n")


@patch("fetcher.oracle_client._connect")
def test_empty_schema_yields_nothing(mock_connect):
    conn, _ = make_mock_connect([])
    mock_connect.return_value = conn

    assert list(fetch_objects("EMPTY")) == []


@patch("fetcher.oracle_client._connect")
def test_object_name_filter_yields_only_matching(mock_connect):
    rows = [
        ("S", "PKG_A", "PACKAGE BODY", "a\n"),
        ("S", "PKG_B", "PACKAGE BODY", "b\n"),
    ]
    conn, _ = make_mock_connect(rows)
    mock_connect.return_value = conn

    result = list(fetch_objects("S", object_name="PKG_A"))

    assert len(result) == 1
    assert result[0][1] == "PKG_A"


@patch("fetcher.oracle_client._connect")
def test_object_name_filter_no_match_yields_nothing(mock_connect):
    rows = [
        ("S", "PKG_A", "PACKAGE BODY", "a\n"),
    ]
    conn, _ = make_mock_connect(rows)
    mock_connect.return_value = conn

    result = list(fetch_objects("S", object_name="PKG_MISSING"))

    assert result == []


@patch("fetcher.oracle_client._connect")
def test_schema_uppercased_in_query(mock_connect):
    conn, cursor = make_mock_connect([])
    mock_connect.return_value = conn

    list(fetch_objects("myschema"))

    assert cursor.execute.call_args.kwargs["schema"] == "MYSCHEMA"


@patch("fetcher.oracle_client._connect")
def test_always_uses_schema_only_query(mock_connect):
    conn, cursor = make_mock_connect([])
    mock_connect.return_value = conn

    list(fetch_objects("S", object_name="PKG_FOO"))

    cursor.execute.assert_called_once()
    assert ":name" not in cursor.execute.call_args.args[0]
