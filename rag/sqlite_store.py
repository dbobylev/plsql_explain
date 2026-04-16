from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from rag.models import RagDocument


def list_latest_completed_runs(
    conn: sqlite3.Connection,
    schema: str,
    object_name: Optional[str] = None,
    subprogram: Optional[str] = None,
    prompt_version: Optional[str] = None,
) -> list[sqlite3.Row]:
    query = """
        SELECT run_id, schema_name, object_name, object_type, subprogram,
               prompt_version, started_at, finished_at
        FROM analysis_run
        WHERE status = 'completed'
          AND schema_name = ?
    """
    params: list[str] = [schema.upper()]
    if object_name:
        query += " AND object_name = ?"
        params.append(object_name.upper())
    if subprogram is not None:
        query += " AND subprogram = ?"
        params.append(subprogram.upper())
    if prompt_version is not None:
        query += " AND prompt_version = ?"
        params.append(prompt_version)
    query += """
        ORDER BY schema_name,
                 object_name,
                 object_type,
                 subprogram,
                 finished_at DESC,
                 started_at DESC,
                 run_id DESC
    """

    rows = conn.execute(query, params).fetchall()
    latest: list[sqlite3.Row] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for row in rows:
        key = (
            row["schema_name"],
            row["object_name"],
            row["object_type"],
            row["subprogram"],
            row["prompt_version"],
        )
        if key in seen:
            continue
        seen.add(key)
        latest.append(row)
    return latest


def upsert_document(
    conn: sqlite3.Connection,
    doc: RagDocument,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO rag_document
            (chunk_id, source_kind, chunk_type, schema_name, object_name, object_type,
             subprogram, title, summary_text, content_text, code_text, parent_chunk_id,
             node_id, run_id, start_line, end_line, source_hash, prompt_version,
             metadata_json, refreshed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(chunk_id)
        DO UPDATE SET source_kind=excluded.source_kind,
                      chunk_type=excluded.chunk_type,
                      schema_name=excluded.schema_name,
                      object_name=excluded.object_name,
                      object_type=excluded.object_type,
                      subprogram=excluded.subprogram,
                      title=excluded.title,
                      summary_text=excluded.summary_text,
                      content_text=excluded.content_text,
                      code_text=excluded.code_text,
                      parent_chunk_id=excluded.parent_chunk_id,
                      node_id=excluded.node_id,
                      run_id=excluded.run_id,
                      start_line=excluded.start_line,
                      end_line=excluded.end_line,
                      source_hash=excluded.source_hash,
                      prompt_version=excluded.prompt_version,
                      metadata_json=excluded.metadata_json,
                      refreshed_at=excluded.refreshed_at
        """,
        (
            doc.chunk_id,
            doc.source_kind,
            doc.chunk_type,
            doc.schema_name,
            doc.object_name,
            doc.object_type,
            doc.subprogram,
            doc.title,
            doc.summary_text,
            doc.content_text,
            doc.code_text,
            doc.parent_chunk_id,
            doc.node_id,
            doc.run_id,
            doc.start_line,
            doc.end_line,
            doc.source_hash,
            doc.prompt_version,
            doc.metadata_json,
            now,
        ),
    )


def delete_method_documents(
    conn: sqlite3.Connection,
    schema: str,
    object_name: str,
    object_type: str,
    subprogram: Optional[str],
) -> None:
    conn.execute(
        """
        DELETE FROM rag_document
        WHERE source_kind = 'analysis_node'
          AND schema_name = ?
          AND object_name = ?
          AND object_type = ?
          AND subprogram = ?
        """,
        (
            schema.upper(),
            object_name.upper(),
            object_type.upper(),
            (subprogram or "").upper(),
        ),
    )


def load_table_row(
    conn: sqlite3.Connection,
    schema_name: str,
    table_name: str,
) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT schema_name, table_name, object_type, table_comment
        FROM table_metadata
        WHERE schema_name = ? AND table_name = ?
        """,
        (schema_name.upper(), table_name.upper()),
    ).fetchone()


def load_table_columns(
    conn: sqlite3.Connection,
    schema_name: str,
    table_name: str,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT column_name, column_id, data_type, nullable, column_comment
        FROM column_metadata
        WHERE schema_name = ? AND table_name = ?
        ORDER BY column_id, column_name
        """,
        (schema_name.upper(), table_name.upper()),
    ).fetchall()
