from unittest.mock import MagicMock, patch

from dictconst.models import DictConstantRef
from dictconst.oracle_client import fetch_dict_constants


def _make_mock_connect(rows):
    cursor_mock = MagicMock()
    cursor_mock.__enter__ = MagicMock(return_value=cursor_mock)
    cursor_mock.__iter__ = MagicMock(return_value=iter(rows))

    conn_mock = MagicMock()
    conn_mock.__enter__ = MagicMock(return_value=conn_mock)
    conn_mock.cursor.return_value = cursor_mock
    return conn_mock, cursor_mock


@patch("dictconst.oracle_client.source_oracle_client._connect")
def test_fetch_dict_constants_uses_upper_lookup_and_returns_records(mock_connect):
    conn, cursor = _make_mock_connect([
        ("FOO", "DONE", "Готово"),
    ])
    mock_connect.return_value = conn

    records = fetch_dict_constants([DictConstantRef(const_name="foo")])

    assert len(records) == 1
    assert records[0].const_name == "FOO"
    assert records[0].shortname == "DONE"
    assert records[0].fullname == "Готово"
    assert "constname = UPPER(:name_0)" in cursor.execute.call_args.args[0]
    assert cursor.execute.call_args.args[1] == {"name_0": "foo"}
