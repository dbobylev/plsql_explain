from unittest.mock import patch
import fetcher.sync as sync


def run_sync(fake_objects, isolated_db, schema="MYSCHEMA", object_name=None):
    with patch("fetcher.oracle_client.fetch_objects", return_value=iter(fake_objects)), \
         patch("fetcher.sqlite_store.init_db"), \
         patch("fetcher.sqlite_store._connect", return_value=isolated_db):
        sync.run(schema=schema, object_name=object_name)
    return isolated_db


def test_sync_inserts_new_objects(mem_conn):
    objects = [
        ("MYSCHEMA", "PKG_A", "PACKAGE BODY", "source a"),
        ("MYSCHEMA", "PKG_B", "PACKAGE BODY", "source b"),
    ]
    db = run_sync(objects, mem_conn)
    count = db.execute("SELECT COUNT(*) FROM object_source").fetchone()[0]
    assert count == 2


def test_sync_empty_schema_inserts_nothing(mem_conn):
    db = run_sync([], mem_conn)
    count = db.execute("SELECT COUNT(*) FROM object_source").fetchone()[0]
    assert count == 0


def test_sync_second_run_with_same_source_unchanged(mem_conn):
    objects = [("S", "FOO", "PROCEDURE", "body")]
    run_sync(objects, mem_conn)
    run_sync(objects, mem_conn)
    count = mem_conn.execute("SELECT COUNT(*) FROM object_source").fetchone()[0]
    assert count == 1


def test_sync_update_changes_source(mem_conn):
    run_sync([("S", "FOO", "PROCEDURE", "v1")], mem_conn)
    run_sync([("S", "FOO", "PROCEDURE", "v2")], mem_conn)
    row = mem_conn.execute(
        "SELECT source_text FROM object_source WHERE object_name='FOO'"
    ).fetchone()
    assert row["source_text"] == "v2"


def test_sync_passes_object_name_to_fetch(mem_conn):
    with patch("fetcher.oracle_client.fetch_objects", return_value=iter([])) as mock_fetch, \
         patch("fetcher.sqlite_store.init_db"), \
         patch("fetcher.sqlite_store._connect", return_value=mem_conn):
        sync.run(schema="MYSCHEMA", object_name="PKG_FOO")
    mock_fetch.assert_called_once_with("MYSCHEMA", "PKG_FOO")


def test_sync_passes_none_object_name_by_default(mem_conn):
    with patch("fetcher.oracle_client.fetch_objects", return_value=iter([])) as mock_fetch, \
         patch("fetcher.sqlite_store.init_db"), \
         patch("fetcher.sqlite_store._connect", return_value=mem_conn):
        sync.run(schema="MYSCHEMA")
    mock_fetch.assert_called_once_with("MYSCHEMA", None)


def test_sync_output_contains_counts(mem_conn, capsys):
    objects = [("S", "OBJ_A", "PACKAGE BODY", "src")]
    run_sync(objects, mem_conn)
    captured = capsys.readouterr()
    assert "1" in captured.out
