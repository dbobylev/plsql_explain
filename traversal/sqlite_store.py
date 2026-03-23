from __future__ import annotations

import sqlite3
from typing import Optional

from traversal.models import TableAccessInfo


def get_object_info(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
) -> Optional[tuple[str, str, Optional[str]]]:
    """
    Returns (object_type, status, error_message) for the given object, or None if
    not found in object_source.

    object_type comes from object_source; status and error_message come from
    parse_result. If parse_result row is missing (object fetched but never parsed),
    status defaults to 'unindexed'.
    """
    row = conn.execute(
        """
        SELECT os.object_type,
               COALESCE(pr.status, 'unindexed') AS status,
               pr.error_message
        FROM object_source os
        LEFT JOIN parse_result pr
               ON pr.schema_name = os.schema_name
              AND pr.object_name  = os.object_name
              AND pr.object_type  = os.object_type
        WHERE os.schema_name = ? AND os.object_name = ?
        """,
        (schema.upper(), name.upper()),
    ).fetchone()
    if row is None:
        return None
    return row[0], row[1], row[2]


def get_call_edges(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    subprogram: Optional[str],
) -> list[tuple[Optional[str], str, Optional[str]]]:
    """
    Returns outgoing call edges as list of (callee_schema, callee_object, callee_subprogram).

    Filters by caller_subprogram:
      - subprogram is None  → WHERE caller_subprogram IS NULL
      - subprogram is set   → WHERE caller_subprogram = ?
    """
    if subprogram is None:
        rows = conn.execute(
            """
            SELECT callee_schema, callee_object, callee_subprogram
            FROM call_edge
            WHERE caller_schema = ? AND caller_object = ? AND caller_subprogram IS NULL
            """,
            (schema.upper(), name.upper()),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT callee_schema, callee_object, callee_subprogram
            FROM call_edge
            WHERE caller_schema = ? AND caller_object = ? AND caller_subprogram = ?
            """,
            (schema.upper(), name.upper(), subprogram.upper()),
        ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def get_table_accesses(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    subprogram: Optional[str],
) -> list[TableAccessInfo]:
    """
    Returns table accesses for the given object/subprogram.

    Filters by subprogram:
      - subprogram is None  → WHERE subprogram IS NULL
      - subprogram is set   → WHERE subprogram = ?
    """
    if subprogram is None:
        rows = conn.execute(
            """
            SELECT table_schema, table_name, operation
            FROM table_access
            WHERE schema_name = ? AND object_name = ? AND subprogram IS NULL
            """,
            (schema.upper(), name.upper()),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT table_schema, table_name, operation
            FROM table_access
            WHERE schema_name = ? AND object_name = ? AND subprogram = ?
            """,
            (schema.upper(), name.upper(), subprogram.upper()),
        ).fetchall()
    return [TableAccessInfo(table_schema=r[0], table_name=r[1], operation=r[2]) for r in rows]
