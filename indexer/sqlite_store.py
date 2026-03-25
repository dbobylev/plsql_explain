from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from parser.models import CallEdge, SubprogramInfo, SubstatementInfo, TableAccess


def get_parse_hash(
    conn: sqlite3.Connection, schema: str, name: str, obj_type: str
) -> Optional[str]:
    """Returns source_hash from parse_result for this object, or None if never parsed."""
    row = conn.execute(
        "SELECT source_hash FROM parse_result "
        "WHERE schema_name=? AND object_name=? AND object_type=?",
        (schema, name, obj_type),
    ).fetchone()
    return row["source_hash"] if row else None


def upsert_parse_result(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    obj_type: str,
    source_hash: str,
    status: str,
    error_message: Optional[str],
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO parse_result (schema_name, object_name, object_type, parsed_at, source_hash, status, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(schema_name, object_name, object_type)
        DO UPDATE SET parsed_at=excluded.parsed_at,
                      source_hash=excluded.source_hash,
                      status=excluded.status,
                      error_message=excluded.error_message
        """,
        (schema, name, obj_type, now, source_hash, status, error_message),
    )


def replace_call_edges(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    obj_type: str,
    edges: list[CallEdge],
) -> None:
    """Deletes all existing call_edge rows for this object, then bulk-inserts new ones."""
    conn.execute(
        "DELETE FROM call_edge WHERE caller_schema=? AND caller_object=? AND caller_type=?",
        (schema, name, obj_type),
    )
    conn.executemany(
        """
        INSERT OR IGNORE INTO call_edge
            (caller_schema, caller_object, caller_type, caller_subprogram,
             callee_schema, callee_object, callee_subprogram)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (schema, name, obj_type, e.caller_subprogram,
             e.callee_schema, e.callee_object, e.callee_subprogram)
            for e in edges
        ],
    )


def replace_table_accesses(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    obj_type: str,
    accesses: list[TableAccess],
) -> None:
    """Deletes all existing table_access rows for this object, then bulk-inserts new ones."""
    conn.execute(
        "DELETE FROM table_access WHERE schema_name=? AND object_name=? AND object_type=?",
        (schema, name, obj_type),
    )
    conn.executemany(
        """
        INSERT OR IGNORE INTO table_access
            (schema_name, object_name, object_type, subprogram,
             table_schema, table_name, operation)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (schema, name, obj_type, a.subprogram,
             a.table_schema, a.table_name, a.operation)
            for a in accesses
        ],
    )


def replace_subprograms(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    obj_type: str,
    subprograms: list[SubprogramInfo],
) -> None:
    """Deletes all existing subprogram rows for this object, then bulk-inserts new ones."""
    conn.execute(
        "DELETE FROM subprogram WHERE schema_name=? AND object_name=? AND object_type=?",
        (schema, name, obj_type),
    )
    conn.executemany(
        """
        INSERT OR IGNORE INTO subprogram
            (schema_name, object_name, object_type, subprogram_name, subprogram_type,
             start_line, end_line, source_text, source_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                schema, name, obj_type,
                sp.name, sp.subprogram_type,
                sp.start_line, sp.end_line,
                sp.source_text,
                hashlib.sha256(sp.source_text.encode()).hexdigest(),
            )
            for sp in subprograms
        ],
    )


def replace_substatements(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    obj_type: str,
    substatements: list[SubstatementInfo],
) -> None:
    """Deletes all existing substatement rows for this object, then bulk-inserts new ones."""
    conn.execute(
        "DELETE FROM substatement WHERE schema_name=? AND object_name=? AND object_type=?",
        (schema, name, obj_type),
    )
    conn.executemany(
        """
        INSERT OR IGNORE INTO substatement
            (schema_name, object_name, object_type, subprogram,
             seq, parent_seq, position, statement_type,
             start_line, end_line, source_text, source_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                schema, name, obj_type,
                s.subprogram if s.subprogram is not None else "",
                s.seq, s.parent_seq, s.position,
                s.statement_type,
                s.start_line, s.end_line,
                s.source_text,
                hashlib.sha256(s.source_text.encode()).hexdigest(),
            )
            for s in substatements
        ],
    )
