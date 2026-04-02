from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from tablemeta.models import ColumnMetadataRecord, TableMetadataRecord, TableRef


def list_referenced_tables(
    conn: sqlite3.Connection,
    schema: str,
    object_name: Optional[str] = None,
) -> list[TableRef]:
    query = """
        SELECT DISTINCT COALESCE(table_schema, schema_name) AS table_schema, table_name
        FROM table_access
        WHERE schema_name = ?
    """
    params: list[str] = [schema.upper()]
    if object_name:
        query += " AND object_name = ?"
        params.append(object_name.upper())
    query += " ORDER BY table_schema, table_name"

    rows = conn.execute(query, params).fetchall()
    return [TableRef(schema_name=row["table_schema"], table_name=row["table_name"]) for row in rows]


def replace_table_metadata(
    conn: sqlite3.Connection,
    record: TableMetadataRecord,
    columns: list[ColumnMetadataRecord],
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO table_metadata (schema_name, table_name, object_type, table_comment, refreshed_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(schema_name, table_name)
        DO UPDATE SET object_type=excluded.object_type,
                      table_comment=excluded.table_comment,
                      refreshed_at=excluded.refreshed_at
        """,
        (
            record.schema_name,
            record.table_name,
            record.object_type,
            record.table_comment,
            now,
        ),
    )
    conn.execute(
        "DELETE FROM column_metadata WHERE schema_name=? AND table_name=?",
        (record.schema_name, record.table_name),
    )
    if not columns:
        return

    conn.executemany(
        """
        INSERT INTO column_metadata
            (schema_name, table_name, column_name, column_id,
             data_type, nullable, column_comment, refreshed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                column.schema_name,
                column.table_name,
                column.column_name,
                column.column_id,
                column.data_type,
                None if column.nullable is None else int(column.nullable),
                column.column_comment,
                now,
            )
            for column in columns
        ],
    )

