from __future__ import annotations

import sqlite3
from typing import Optional


def get_source_text(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    obj_type: Optional[str] = None,
) -> Optional[str]:
    if obj_type is None:
        row = conn.execute(
            """
            SELECT source_text
            FROM object_source
            WHERE schema_name=? AND object_name=?
            ORDER BY CASE
                         WHEN object_type LIKE '% BODY' THEN 0
                         WHEN object_type IN ('PACKAGE', 'TYPE') THEN 1
                         ELSE 2
                     END,
                     object_type
            LIMIT 1
            """,
            (schema.upper(), name.upper()),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT source_text
            FROM object_source
            WHERE schema_name=? AND object_name=? AND object_type=?
            """,
            (schema.upper(), name.upper(), obj_type.upper()),
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
