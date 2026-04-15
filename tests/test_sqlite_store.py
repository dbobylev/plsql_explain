import hashlib
import sqlite3
from unittest.mock import patch
import pytest
import fetcher.sqlite_store as store


# --- get_hash ---

def test_get_hash_returns_none_for_unknown(mem_conn):
    assert store.get_hash(mem_conn, "S", "OBJ", "PACKAGE") is None


def test_get_hash_returns_hash_after_insert(mem_conn):
    store.upsert_object(mem_conn, "S", "OBJ", "PACKAGE", "source")
    h = store.get_hash(mem_conn, "S", "OBJ", "PACKAGE")
    assert h is not None
    assert len(h) == 64  # SHA256 hex


# --- upsert_object return values ---

def test_upsert_first_call_returns_inserted(mem_conn):
    assert store.upsert_object(mem_conn, "S", "OBJ", "PROCEDURE", "body") == "inserted"


def test_upsert_same_text_returns_unchanged(mem_conn):
    store.upsert_object(mem_conn, "S", "OBJ", "PROCEDURE", "body")
    assert store.upsert_object(mem_conn, "S", "OBJ", "PROCEDURE", "body") == "unchanged"


def test_upsert_changed_text_returns_updated(mem_conn):
    store.upsert_object(mem_conn, "S", "OBJ", "PROCEDURE", "v1")
    assert store.upsert_object(mem_conn, "S", "OBJ", "PROCEDURE", "v2") == "updated"


# --- persistence ---

def test_upsert_stores_correct_hash(mem_conn):
    text = "CREATE OR REPLACE PROCEDURE foo IS BEGIN NULL; END;"
    store.upsert_object(mem_conn, "S", "FOO", "PROCEDURE", text)
    expected = hashlib.sha256(text.encode()).hexdigest()
    assert store.get_hash(mem_conn, "S", "FOO", "PROCEDURE") == expected


def test_upsert_update_persists_new_source(mem_conn):
    store.upsert_object(mem_conn, "S", "OBJ", "PROCEDURE", "v1")
    store.upsert_object(mem_conn, "S", "OBJ", "PROCEDURE", "v2")
    row = mem_conn.execute(
        "SELECT source_text FROM object_source WHERE object_name='OBJ'"
    ).fetchone()
    assert row["source_text"] == "v2"


def test_upsert_unchanged_does_not_update_fetched_at(mem_conn):
    store.upsert_object(mem_conn, "S", "OBJ", "PROCEDURE", "body")
    ts_before = mem_conn.execute(
        "SELECT fetched_at FROM object_source WHERE object_name='OBJ'"
    ).fetchone()["fetched_at"]

    store.upsert_object(mem_conn, "S", "OBJ", "PROCEDURE", "body")

    ts_after = mem_conn.execute(
        "SELECT fetched_at FROM object_source WHERE object_name='OBJ'"
    ).fetchone()["fetched_at"]
    assert ts_before == ts_after


def test_upsert_same_name_different_types_creates_two_rows(mem_conn):
    store.upsert_object(mem_conn, "S", "OBJ", "PACKAGE", "pkg spec")
    store.upsert_object(mem_conn, "S", "OBJ", "PACKAGE BODY", "pkg body")
    count = mem_conn.execute("SELECT COUNT(*) FROM object_source").fetchone()[0]
    assert count == 2


# --- init_db ---

def test_init_db_creates_table():
    mem = sqlite3.connect(":memory:")
    mem.row_factory = sqlite3.Row
    with patch("fetcher.sqlite_store._connect", return_value=mem):
        store.init_db()
    row = mem.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='object_source'"
    ).fetchone()
    assert row is not None
    mem.close()


def test_init_db_is_idempotent():
    mem = sqlite3.connect(":memory:")
    mem.row_factory = sqlite3.Row
    with patch("fetcher.sqlite_store._connect", return_value=mem):
        store.init_db()
        store.init_db()  # second call must not raise
    mem.close()


def test_init_db_migrates_preceding_comment_columns_for_existing_tables():
    mem = sqlite3.connect(":memory:")
    mem.row_factory = sqlite3.Row
    mem.executescript(
        """
        CREATE TABLE subprogram (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            schema_name     TEXT NOT NULL,
            object_name     TEXT NOT NULL,
            object_type     TEXT NOT NULL,
            subprogram_name TEXT NOT NULL,
            subprogram_type TEXT NOT NULL,
            start_line      INTEGER NOT NULL,
            end_line        INTEGER NOT NULL,
            source_text     TEXT NOT NULL,
            source_hash     TEXT NOT NULL,
            UNIQUE(schema_name, object_name, object_type, subprogram_name)
        );

        CREATE TABLE substatement (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            schema_name    TEXT NOT NULL,
            object_name    TEXT NOT NULL,
            object_type    TEXT NOT NULL,
            subprogram     TEXT NOT NULL DEFAULT '',
            seq            INTEGER NOT NULL,
            parent_seq     INTEGER,
            position       INTEGER NOT NULL,
            statement_type TEXT NOT NULL,
            start_line     INTEGER NOT NULL,
            end_line       INTEGER NOT NULL,
            source_text    TEXT NOT NULL,
            source_hash    TEXT NOT NULL,
            UNIQUE(schema_name, object_name, object_type, subprogram, seq)
        );
        """
    )

    with patch("fetcher.sqlite_store._connect", return_value=mem):
        store.init_db()

    subprogram_columns = {
        row["name"]: row for row in mem.execute("PRAGMA table_info('subprogram')").fetchall()
    }
    substatement_columns = {
        row["name"]: row for row in mem.execute("PRAGMA table_info('substatement')").fetchall()
    }

    assert "preceding_comment" in subprogram_columns
    assert "preceding_comment" in substatement_columns
    assert subprogram_columns["preceding_comment"]["dflt_value"] == "''"
    assert substatement_columns["preceding_comment"]["dflt_value"] == "''"

    mem.close()
