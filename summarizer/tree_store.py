from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from summarizer.description_tree import DescriptionNode


def _norm(subprogram: Optional[str]) -> str:
    return subprogram.upper() if subprogram else ""


def get_cached_description(
    conn: sqlite3.Connection,
    schema: str,
    object_name: str,
    object_type: str,
    subprogram: Optional[str],
    node_id: str,
    prompt_version: str,
) -> Optional[tuple[str, str]]:
    """Returns (source_hash, description) if cached, else None."""
    row = conn.execute(
        """
        SELECT source_hash, description
        FROM node_description
        WHERE schema_name = ? AND object_name = ? AND object_type = ?
          AND subprogram = ? AND node_id = ? AND prompt_version = ?
        """,
        (
            schema.upper(),
            object_name.upper(),
            object_type.upper(),
            _norm(subprogram),
            node_id,
            prompt_version,
        ),
    ).fetchone()
    return (row[0], row[1]) if row else None


def upsert_node_description(
    conn: sqlite3.Connection,
    schema: str,
    object_name: str,
    object_type: str,
    subprogram: Optional[str],
    node: DescriptionNode,
    parent_node_id: Optional[str],
    position: int,
    prompt_version: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            """
            INSERT INTO node_description
                (schema_name, object_name, object_type, subprogram,
                 node_id, node_kind, statement_type, title,
                 start_line, end_line, parent_node_id, position,
                 source_hash, description, prompt_version, described_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(schema_name, object_name, object_type, subprogram, node_id, prompt_version)
            DO UPDATE SET node_kind=excluded.node_kind,
                          statement_type=excluded.statement_type,
                          title=excluded.title,
                          start_line=excluded.start_line,
                          end_line=excluded.end_line,
                          parent_node_id=excluded.parent_node_id,
                          position=excluded.position,
                          source_hash=excluded.source_hash,
                          description=excluded.description,
                          described_at=excluded.described_at
            """,
            (
                schema.upper(),
                object_name.upper(),
                object_type.upper(),
                _norm(subprogram),
                node.node_id,
                node.node_kind,
                node.statement_type,
                node.title,
                node.start_line,
                node.end_line,
                parent_node_id,
                position,
                node.source_hash,
                node.description,
                prompt_version,
                now,
            ),
        )


def save_tree(
    conn: sqlite3.Connection,
    schema: str,
    object_name: str,
    object_type: str,
    subprogram: Optional[str],
    root: DescriptionNode,
    prompt_version: str,
) -> None:
    """Replace the stored tree for the method with the current version."""
    clear_tree(conn, schema, object_name, object_type, subprogram, prompt_version)
    _save_node(conn, schema, object_name, object_type, subprogram, root, None, 0, prompt_version)


def _save_node(
    conn: sqlite3.Connection,
    schema: str,
    object_name: str,
    object_type: str,
    subprogram: Optional[str],
    node: DescriptionNode,
    parent_node_id: Optional[str],
    position: int,
    prompt_version: str,
) -> None:
    upsert_node_description(
        conn, schema, object_name, object_type, subprogram,
        node, parent_node_id, position, prompt_version,
    )
    for i, child in enumerate(node.children):
        _save_node(
            conn, schema, object_name, object_type, subprogram,
            child, node.node_id, i, prompt_version,
        )


def clear_tree(
    conn: sqlite3.Connection,
    schema: str,
    object_name: str,
    object_type: str,
    subprogram: Optional[str],
    prompt_version: str,
) -> None:
    """Remove all node_description rows for a given method."""
    with conn:
        conn.execute(
            """
            DELETE FROM node_description
            WHERE schema_name = ? AND object_name = ? AND object_type = ?
              AND subprogram = ? AND prompt_version = ?
            """,
            (
                schema.upper(),
                object_name.upper(),
                object_type.upper(),
                _norm(subprogram),
                prompt_version,
            ),
        )
