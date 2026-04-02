from __future__ import annotations

import sqlite3
from typing import Optional

from traversal.models import ColumnMetadataInfo, TableAccessInfo


def get_object_info(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    subprogram: Optional[str] = None,
) -> Optional[tuple[str, str, Optional[str]]]:
    """
    Returns (object_type, status, error_message) for the given object, or None if
    not found in object_source.

    object_type comes from object_source; status and error_message come from
    parse_result. If parse_result row is missing (object fetched but never parsed),
    status defaults to 'unindexed'.

    When multiple rows exist for the same object name (for example PACKAGE and
    PACKAGE BODY), prefers the row that actually contains the requested
    subprogram. Otherwise prefers body objects over declarations/specifications.
    """
    if subprogram is None:
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
            ORDER BY CASE
                         WHEN os.object_type LIKE '% BODY' THEN 0
                         WHEN os.object_type IN ('PACKAGE', 'TYPE') THEN 1
                         ELSE 2
                     END,
                     os.object_type
            LIMIT 1
            """,
            (schema.upper(), name.upper()),
        ).fetchone()
    else:
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
            ORDER BY CASE
                         WHEN EXISTS (
                             SELECT 1
                             FROM subprogram sp
                             WHERE sp.schema_name = os.schema_name
                               AND sp.object_name = os.object_name
                               AND sp.object_type = os.object_type
                               AND sp.subprogram_name = ?
                         ) THEN 0
                         WHEN os.object_type LIKE '% BODY' THEN 1
                         WHEN os.object_type IN ('PACKAGE', 'TYPE') THEN 2
                         ELSE 3
                     END,
                     os.object_type
            LIMIT 1
            """,
            (schema.upper(), name.upper(), subprogram.upper()),
        ).fetchone()
    if row is None:
        return None
    return row[0], row[1], row[2]


def get_call_edges(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    obj_type: str,
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
            WHERE caller_schema = ? AND caller_object = ? AND caller_type = ? AND caller_subprogram IS NULL
            """,
            (schema.upper(), name.upper(), obj_type.upper()),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT callee_schema, callee_object, callee_subprogram
            FROM call_edge
            WHERE caller_schema = ? AND caller_object = ? AND caller_type = ? AND caller_subprogram = ?
            """,
            (schema.upper(), name.upper(), obj_type.upper(), subprogram.upper()),
        ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def get_table_accesses(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    obj_type: str,
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
            SELECT COALESCE(ta.table_schema, ta.schema_name) AS table_schema,
                   ta.table_name,
                   ta.operation,
                   tm.object_type AS table_object_type,
                   tm.table_comment
            FROM table_access ta
            LEFT JOIN table_metadata tm
                   ON tm.schema_name = COALESCE(ta.table_schema, ta.schema_name)
                  AND tm.table_name = ta.table_name
            WHERE ta.schema_name = ? AND ta.object_name = ? AND ta.object_type = ? AND ta.subprogram IS NULL
            ORDER BY table_schema, ta.table_name, ta.operation
            """,
            (schema.upper(), name.upper(), obj_type.upper()),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT COALESCE(ta.table_schema, ta.schema_name) AS table_schema,
                   ta.table_name,
                   ta.operation,
                   tm.object_type AS table_object_type,
                   tm.table_comment
            FROM table_access ta
            LEFT JOIN table_metadata tm
                   ON tm.schema_name = COALESCE(ta.table_schema, ta.schema_name)
                  AND tm.table_name = ta.table_name
            WHERE ta.schema_name = ? AND ta.object_name = ? AND ta.object_type = ? AND ta.subprogram = ?
            ORDER BY table_schema, ta.table_name, ta.operation
            """,
            (schema.upper(), name.upper(), obj_type.upper(), subprogram.upper()),
        ).fetchall()

    column_map = _load_column_metadata(conn, rows)
    return [
        TableAccessInfo(
            table_schema=row["table_schema"],
            table_name=row["table_name"],
            operation=row["operation"],
            table_object_type=row["table_object_type"],
            table_comment=row["table_comment"],
            columns=column_map.get((row["table_schema"], row["table_name"]), []),
        )
        for row in rows
    ]


def _load_column_metadata(
    conn: sqlite3.Connection,
    access_rows: list[sqlite3.Row],
) -> dict[tuple[str, str], list[ColumnMetadataInfo]]:
    table_keys = sorted(
        {
            (row["table_schema"], row["table_name"])
            for row in access_rows
            if row["table_schema"] and row["table_name"]
        }
    )
    if not table_keys:
        return {}

    where_clause = " OR ".join("(schema_name = ? AND table_name = ?)" for _ in table_keys)
    params = [item for key in table_keys for item in key]
    rows = conn.execute(
        f"""
        SELECT schema_name, table_name, column_name, column_id, data_type, nullable, column_comment
        FROM column_metadata
        WHERE {where_clause}
        ORDER BY schema_name, table_name, column_id, column_name
        """,
        params,
    ).fetchall()

    result: dict[tuple[str, str], list[ColumnMetadataInfo]] = {}
    for row in rows:
        result.setdefault((row["schema_name"], row["table_name"]), []).append(
            ColumnMetadataInfo(
                column_name=row["column_name"],
                data_type=row["data_type"],
                nullable=None if row["nullable"] is None else bool(row["nullable"]),
                column_comment=row["column_comment"],
            )
        )
    return result
