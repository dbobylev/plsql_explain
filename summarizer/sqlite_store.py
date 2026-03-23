from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional


def _norm(subprogram: Optional[str]) -> str:
    """Normalize subprogram to empty string for storage (NULL-safe UNIQUE key)."""
    return subprogram.upper() if subprogram else ""


def get_source_text(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
) -> Optional[str]:
    row = conn.execute(
        "SELECT source_text FROM object_source WHERE schema_name=? AND object_name=?",
        (schema.upper(), name.upper()),
    ).fetchone()
    return row[0] if row else None


def get_source_hash(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    obj_type: str,
) -> Optional[str]:
    row = conn.execute(
        "SELECT source_hash FROM parse_result "
        "WHERE schema_name=? AND object_name=? AND object_type=?",
        (schema.upper(), name.upper(), obj_type),
    ).fetchone()
    return row[0] if row else None


def get_summary(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    obj_type: str,
    subprogram: Optional[str],
) -> Optional[tuple[str, str]]:
    """
    Returns (source_hash, summary_text) if a cached summary exists, else None.
    """
    row = conn.execute(
        "SELECT source_hash, summary_text FROM summary "
        "WHERE schema_name=? AND object_name=? AND object_type=? AND subprogram=?",
        (schema.upper(), name.upper(), obj_type, _norm(subprogram)),
    ).fetchone()
    return (row[0], row[1]) if row else None


def upsert_summary(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    obj_type: str,
    subprogram: Optional[str],
    source_hash: str,
    summary_text: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            """
            INSERT INTO summary
                (schema_name, object_name, object_type, subprogram,
                 source_hash, summary_text, summarized_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(schema_name, object_name, object_type, subprogram)
            DO UPDATE SET source_hash=excluded.source_hash,
                          summary_text=excluded.summary_text,
                          summarized_at=excluded.summarized_at
            """,
            (schema.upper(), name.upper(), obj_type, _norm(subprogram),
             source_hash, summary_text, now),
        )
