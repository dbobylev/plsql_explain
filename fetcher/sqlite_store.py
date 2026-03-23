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
