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
    summary_kind: str = "brief",
) -> Optional[tuple[str, str]]:
    """
    Returns (source_hash, summary_text) if a cached summary exists, else None.
    """
    row = conn.execute(
        "SELECT source_hash, summary_text FROM summary "
        "WHERE schema_name=? AND object_name=? AND object_type=? AND subprogram=? AND summary_kind=?",
        (schema.upper(), name.upper(), obj_type, _norm(subprogram), summary_kind),
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
    summary_kind: str = "brief",
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            """
            INSERT INTO summary
                (schema_name, object_name, object_type, subprogram, summary_kind,
                 source_hash, summary_text, summarized_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(schema_name, object_name, object_type, subprogram, summary_kind)
            DO UPDATE SET source_hash=excluded.source_hash,
                          summary_text=excluded.summary_text,
                          summarized_at=excluded.summarized_at
            """,
            (schema.upper(), name.upper(), obj_type, _norm(subprogram),
             summary_kind, source_hash, summary_text, now),
        )


def get_chunk_analysis(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    obj_type: str,
    subprogram: Optional[str],
    chunk_index: int,
) -> Optional[tuple[str, str]]:
    """
    Returns (chunk_hash, analysis_text) if a cached chunk analysis exists, else None.
    """
    row = conn.execute(
        "SELECT chunk_hash, analysis_text FROM chunk_analysis "
        "WHERE schema_name=? AND object_name=? AND object_type=? AND subprogram=? AND chunk_index=?",
        (schema.upper(), name.upper(), obj_type, _norm(subprogram), chunk_index),
    ).fetchone()
    return (row[0], row[1]) if row else None


def upsert_chunk_analysis(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    obj_type: str,
    subprogram: Optional[str],
    chunk_index: int,
    chunk_hash: str,
    analysis_text: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            """
            INSERT INTO chunk_analysis
                (schema_name, object_name, object_type, subprogram, chunk_index,
                 chunk_hash, analysis_text, analyzed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(schema_name, object_name, object_type, subprogram, chunk_index)
            DO UPDATE SET chunk_hash=excluded.chunk_hash,
                          analysis_text=excluded.analysis_text,
                          analyzed_at=excluded.analyzed_at
            """,
            (schema.upper(), name.upper(), obj_type, _norm(subprogram),
             chunk_index, chunk_hash, analysis_text, now),
        )
