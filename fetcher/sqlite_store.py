from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _db_path() -> str:
    return os.environ.get("SQLITE_PATH", "./data/plsql.db")


def _connect() -> sqlite3.Connection:
    path = _db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    schema = Path(__file__).parent.parent / "db" / "schema.sql"
    with _connect() as conn:
        conn.executescript(schema.read_text())
        _migrate_call_edge_table(conn)
        _migrate_table_access_table(conn)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def get_hash(conn: sqlite3.Connection, schema: str, name: str, obj_type: str) -> str | None:
    row = conn.execute(
        "SELECT source_hash FROM object_source WHERE schema_name=? AND object_name=? AND object_type=?",
        (schema, name, obj_type),
    ).fetchone()
    return row["source_hash"] if row else None


def upsert_object(
    conn: sqlite3.Connection, schema: str, name: str, obj_type: str, source_text: str
) -> str:
    """
    Inserts or updates the object if its source has changed.
    Returns 'inserted', 'updated', or 'unchanged'.
    """
    new_hash = _hash(source_text)
    existing_hash = get_hash(conn, schema, name, obj_type)

    if existing_hash == new_hash:
        return "unchanged"

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO object_source (schema_name, object_name, object_type, source_text, source_hash, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(schema_name, object_name, object_type)
        DO UPDATE SET source_text=excluded.source_text,
                      source_hash=excluded.source_hash,
                      fetched_at=excluded.fetched_at
        """,
        (schema, name, obj_type, source_text, new_hash, now),
    )

    return "updated" if existing_hash else "inserted"


def _migrate_call_edge_table(conn: sqlite3.Connection) -> None:
    expected_columns = (
        "caller_schema",
        "caller_object",
        "caller_type",
        "caller_subprogram",
        "callee_schema",
        "callee_object",
        "callee_subprogram",
    )
    if _table_has_unique_index(conn, "call_edge", expected_columns):
        return

    conn.executescript(
        """
        ALTER TABLE call_edge RENAME TO call_edge__old;

        CREATE TABLE call_edge (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            caller_schema     TEXT NOT NULL,
            caller_object     TEXT NOT NULL,
            caller_type       TEXT NOT NULL,
            caller_subprogram TEXT,
            callee_schema     TEXT,
            callee_object     TEXT NOT NULL,
            callee_subprogram TEXT,
            UNIQUE(caller_schema, caller_object, caller_type, caller_subprogram,
                   callee_schema, callee_object, callee_subprogram)
        );

        INSERT OR IGNORE INTO call_edge
            (caller_schema, caller_object, caller_type, caller_subprogram,
             callee_schema, callee_object, callee_subprogram)
        SELECT caller_schema, caller_object, caller_type, caller_subprogram,
               COALESCE(callee_schema, caller_schema), callee_object, callee_subprogram
        FROM call_edge__old;

        DROP TABLE call_edge__old;
        """
    )


def _migrate_table_access_table(conn: sqlite3.Connection) -> None:
    expected_columns = (
        "schema_name",
        "object_name",
        "object_type",
        "subprogram",
        "table_schema",
        "table_name",
        "operation",
    )
    if _table_has_unique_index(conn, "table_access", expected_columns):
        return

    conn.executescript(
        """
        ALTER TABLE table_access RENAME TO table_access__old;

        CREATE TABLE table_access (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            schema_name    TEXT NOT NULL,
            object_name    TEXT NOT NULL,
            object_type    TEXT NOT NULL,
            subprogram     TEXT,
            table_schema   TEXT,
            table_name     TEXT NOT NULL,
            operation      TEXT NOT NULL,
            UNIQUE(schema_name, object_name, object_type, subprogram,
                   table_schema, table_name, operation)
        );

        INSERT OR IGNORE INTO table_access
            (schema_name, object_name, object_type, subprogram,
             table_schema, table_name, operation)
        SELECT schema_name, object_name, object_type, subprogram,
               COALESCE(table_schema, schema_name), table_name, operation
        FROM table_access__old;

        DROP TABLE table_access__old;
        """
    )


def _table_has_unique_index(
    conn: sqlite3.Connection,
    table_name: str,
    expected_columns: tuple[str, ...],
) -> bool:
    indexes = conn.execute(f"PRAGMA index_list('{table_name}')").fetchall()
    for index in indexes:
        if not index["unique"]:
            continue
        index_name = index["name"]
        columns = tuple(
            row["name"]
            for row in conn.execute(f"PRAGMA index_info('{index_name}')").fetchall()
        )
        if columns == expected_columns:
            return True
    return False
