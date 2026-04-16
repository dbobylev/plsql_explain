from unittest.mock import patch

import dictconst.sync as sync
from dictconst.models import DictConstantRecord, DictConstantRef


def _insert_object_source(mem_conn, object_name: str, source_text: str) -> None:
    mem_conn.execute(
        """
        INSERT INTO object_source
            (schema_name, object_name, object_type, source_text, source_hash, fetched_at)
        VALUES ('S', ?, 'PACKAGE BODY', ?, ?, datetime('now'))
        """,
        (object_name, source_text, f"hash-{object_name}"),
    )
    mem_conn.commit()


def test_sync_loads_dict_constants_for_referenced_names(mem_conn) -> None:
    _insert_object_source(mem_conn, "PKG_A", "BEGIN v_x := c.get('foo'); END;")

    with patch("fetcher.sqlite_store.init_db"), \
         patch("fetcher.sqlite_store._connect", return_value=mem_conn), \
         patch(
             "dictconst.oracle_client.fetch_dict_constants",
             return_value=[
                 DictConstantRecord(
                     const_name="FOO",
                     shortname="DONE",
                     fullname="Готово",
                 ),
             ],
         ) as mock_fetch:
        sync.run(schema="S")

    mock_fetch.assert_called_once_with([DictConstantRef(const_name="FOO")])
    row = mem_conn.execute(
        "SELECT const_name, shortname, fullname, resolved_text FROM dict_constant"
    ).fetchone()
    assert tuple(row) == ("FOO", "DONE", "Готово", "Готово")


def test_sync_creates_placeholder_for_missing_constant(mem_conn) -> None:
    _insert_object_source(mem_conn, "PKG_A", "BEGIN v_x := c.get('foo'); END;")

    with patch("fetcher.sqlite_store.init_db"), \
         patch("fetcher.sqlite_store._connect", return_value=mem_conn), \
         patch("dictconst.oracle_client.fetch_dict_constants", return_value=[]):
        sync.run(schema="S")

    row = mem_conn.execute(
        "SELECT const_name, shortname, fullname, resolved_text FROM dict_constant"
    ).fetchone()
    assert tuple(row) == ("FOO", None, None, None)

