from __future__ import annotations

import hashlib
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Iterable, Optional

from summarizer.description_tree import DescriptionNode


def _norm(subprogram: Optional[str]) -> str:
    return subprogram.upper() if subprogram else ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _legacy_run_id(
    schema: str,
    object_name: str,
    object_type: str,
    subprogram: Optional[str],
    prompt_version: str,
) -> str:
    payload = "|".join(
        [
            schema.upper(),
            object_name.upper(),
            object_type.upper(),
            _norm(subprogram),
            prompt_version,
        ]
    )
    return f"legacy::{hashlib.sha256(payload.encode()).hexdigest()}"


def _ensure_legacy_run(
    conn: sqlite3.Connection,
    schema: str,
    object_name: str,
    object_type: str,
    subprogram: Optional[str],
    prompt_version: str,
) -> str:
    run_id = _legacy_run_id(schema, object_name, object_type, subprogram, prompt_version)
    now = _utc_now()
    with conn:
        conn.execute(
            """
            INSERT INTO analysis_run
                (run_id, schema_name, object_name, object_type, subprogram,
                 prompt_version, status, started_at, finished_at, error_message)
            VALUES (?, ?, ?, ?, ?, ?, 'completed', ?, ?, NULL)
            ON CONFLICT(run_id)
            DO UPDATE SET finished_at=excluded.finished_at
            """,
            (
                run_id,
                schema.upper(),
                object_name.upper(),
                object_type.upper(),
                _norm(subprogram),
                prompt_version,
                now,
                now,
            ),
        )
    return run_id


def create_analysis_run(
    conn: sqlite3.Connection,
    schema: str,
    object_name: str,
    object_type: str,
    subprogram: Optional[str],
    prompt_version: str,
) -> str:
    run_id = str(uuid.uuid4())
    now = _utc_now()
    with conn:
        conn.execute(
            """
            INSERT INTO analysis_run
                (run_id, schema_name, object_name, object_type, subprogram,
                 prompt_version, status, started_at, finished_at, error_message)
            VALUES (?, ?, ?, ?, ?, ?, 'running', ?, NULL, NULL)
            """,
            (
                run_id,
                schema.upper(),
                object_name.upper(),
                object_type.upper(),
                _norm(subprogram),
                prompt_version,
                now,
            ),
        )
    return run_id


def mark_analysis_run_completed(conn: sqlite3.Connection, run_id: str) -> None:
    with conn:
        conn.execute(
            """
            UPDATE analysis_run
            SET status='completed', finished_at=?, error_message=NULL
            WHERE run_id=?
            """,
            (_utc_now(), run_id),
        )


def mark_analysis_run_failed(conn: sqlite3.Connection, run_id: str, error_message: str) -> None:
    with conn:
        conn.execute(
            """
            UPDATE analysis_run
            SET status='failed', finished_at=?, error_message=?
            WHERE run_id=?
            """,
            (_utc_now(), error_message, run_id),
        )


def get_latest_completed_run_id(
    conn: sqlite3.Connection,
    schema: str,
    object_name: str,
    object_type: str,
    subprogram: Optional[str],
    prompt_version: str,
) -> Optional[str]:
    row = conn.execute(
        """
        SELECT run_id
        FROM analysis_run
        WHERE schema_name = ? AND object_name = ? AND object_type = ?
          AND subprogram = ? AND prompt_version = ? AND status = 'completed'
        ORDER BY finished_at DESC, started_at DESC, run_id DESC
        LIMIT 1
        """,
        (
            schema.upper(),
            object_name.upper(),
            object_type.upper(),
            _norm(subprogram),
            prompt_version,
        ),
    ).fetchone()
    return row["run_id"] if row else None


def get_cached_analysis(
    conn: sqlite3.Connection,
    prompt_hash: str,
    prompt_version: str,
) -> Optional[str]:
    row = conn.execute(
        """
        SELECT description
        FROM analysis_cache
        WHERE prompt_hash = ? AND prompt_version = ?
        """,
        (prompt_hash, prompt_version),
    ).fetchone()
    return row["description"] if row else None


def upsert_cached_analysis(
    conn: sqlite3.Connection,
    prompt_hash: str,
    prompt_version: str,
    source_hash: str,
    node_kind: str,
    statement_type: str,
    description: str,
) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO analysis_cache
                (prompt_hash, prompt_version, source_hash, node_kind,
                 statement_type, description, described_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(prompt_hash, prompt_version)
            DO UPDATE SET source_hash=excluded.source_hash,
                          node_kind=excluded.node_kind,
                          statement_type=excluded.statement_type,
                          description=excluded.description,
                          described_at=excluded.described_at
            """,
            (
                prompt_hash,
                prompt_version,
                source_hash,
                node_kind,
                statement_type,
                description,
                _utc_now(),
            ),
        )


def get_node_description_for_run(
    conn: sqlite3.Connection,
    run_id: str,
    node_id: str,
) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM node_description
        WHERE run_id = ? AND node_id = ?
        """,
        (run_id, node_id),
    ).fetchone()


def iter_run_nodes(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM node_description
        WHERE run_id = ?
        ORDER BY CASE WHEN parent_node_id IS NULL THEN 0 ELSE 1 END,
                 parent_node_id,
                 position,
                 node_id
        """,
        (run_id,),
    ).fetchall()


def upsert_run_node_description(
    conn: sqlite3.Connection,
    run_id: str,
    schema: str,
    object_name: str,
    object_type: str,
    subprogram: Optional[str],
    node: DescriptionNode,
    parent_node_id: Optional[str],
    position: int,
    prompt_version: str,
) -> None:
    stored_subprogram = node.subprogram or ""
    with conn:
        conn.execute(
            """
            INSERT INTO node_description
                (run_id, schema_name, object_name, object_type, subprogram,
                 node_id, node_kind, statement_type, title,
                 start_line, end_line, source_text, parent_node_id, position,
                 source_hash, description, prompt_version, described_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, node_id)
            DO UPDATE SET schema_name=excluded.schema_name,
                          object_name=excluded.object_name,
                          object_type=excluded.object_type,
                          subprogram=excluded.subprogram,
                          node_kind=excluded.node_kind,
                          statement_type=excluded.statement_type,
                          title=excluded.title,
                          start_line=excluded.start_line,
                          end_line=excluded.end_line,
                          source_text=excluded.source_text,
                          parent_node_id=excluded.parent_node_id,
                          position=excluded.position,
                          source_hash=excluded.source_hash,
                          description=excluded.description,
                          prompt_version=excluded.prompt_version,
                          described_at=excluded.described_at
            """,
            (
                run_id,
                schema.upper(),
                object_name.upper(),
                object_type.upper(),
                _norm(stored_subprogram),
                node.node_id,
                node.node_kind,
                node.statement_type,
                node.title,
                node.start_line,
                node.end_line,
                node.source_text,
                parent_node_id,
                position,
                node.source_hash,
                node.description,
                prompt_version,
                _utc_now(),
            ),
        )


def clear_run_tree(conn: sqlite3.Connection, run_id: str) -> None:
    with conn:
        conn.execute("DELETE FROM node_description WHERE run_id = ?", (run_id,))


def delete_runs(
    conn: sqlite3.Connection,
    run_ids: Iterable[str],
) -> None:
    ids = list(run_ids)
    if not ids:
        return
    placeholders = ", ".join("?" for _ in ids)
    with conn:
        conn.execute(f"DELETE FROM node_description WHERE run_id IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM analysis_run WHERE run_id IN ({placeholders})", ids)


def get_cached_description(
    conn: sqlite3.Connection,
    schema: str,
    object_name: str,
    object_type: str,
    subprogram: Optional[str],
    node_id: str,
    prompt_version: str,
) -> Optional[tuple[str, str]]:
    """
    Compatibility helper for tests and legacy code.

    Returns (source_hash, description) from the latest completed run for the
    method. New summarization flow uses analysis_cache instead.
    """
    run_id = get_latest_completed_run_id(
        conn,
        schema,
        object_name,
        object_type,
        subprogram,
        prompt_version,
    )
    if run_id is None:
        run_id = _legacy_run_id(schema, object_name, object_type, subprogram, prompt_version)

    row = conn.execute(
        """
        SELECT source_hash, description
        FROM node_description
        WHERE run_id = ? AND node_id = ?
        """,
        (run_id, node_id),
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
    """
    Compatibility wrapper that stores rows in a deterministic legacy run.
    """
    run_id = _ensure_legacy_run(conn, schema, object_name, object_type, subprogram, prompt_version)
    upsert_run_node_description(
        conn,
        run_id,
        schema,
        object_name,
        object_type,
        subprogram,
        node,
        parent_node_id,
        position,
        prompt_version,
    )


def save_tree_for_run(
    conn: sqlite3.Connection,
    run_id: str,
    schema: str,
    object_name: str,
    object_type: str,
    subprogram: Optional[str],
    root: DescriptionNode,
    prompt_version: str,
) -> None:
    clear_run_tree(conn, run_id)
    _save_node_for_run(
        conn,
        run_id,
        schema,
        object_name,
        object_type,
        subprogram,
        root,
        None,
        0,
        prompt_version,
    )


def _save_node_for_run(
    conn: sqlite3.Connection,
    run_id: str,
    schema: str,
    object_name: str,
    object_type: str,
    subprogram: Optional[str],
    node: DescriptionNode,
    parent_node_id: Optional[str],
    position: int,
    prompt_version: str,
) -> None:
    upsert_run_node_description(
        conn,
        run_id,
        schema,
        object_name,
        object_type,
        subprogram,
        node,
        parent_node_id,
        position,
        prompt_version,
    )
    for i, child in enumerate(node.children):
        _save_node_for_run(
            conn,
            run_id,
            schema,
            object_name,
            object_type,
            subprogram,
            child,
            node.node_id,
            i,
            prompt_version,
        )


def save_tree(
    conn: sqlite3.Connection,
    schema: str,
    object_name: str,
    object_type: str,
    subprogram: Optional[str],
    root: DescriptionNode,
    prompt_version: str,
) -> str:
    """
    Compatibility helper for tests: store the tree in a deterministic legacy run.
    """
    run_id = _ensure_legacy_run(conn, schema, object_name, object_type, subprogram, prompt_version)
    save_tree_for_run(
        conn,
        run_id,
        schema,
        object_name,
        object_type,
        subprogram,
        root,
        prompt_version,
    )
    return run_id


def clear_tree(
    conn: sqlite3.Connection,
    schema: str,
    object_name: str,
    object_type: str,
    subprogram: Optional[str],
    prompt_version: str,
) -> None:
    run_ids = [
        row["run_id"]
        for row in conn.execute(
            """
            SELECT run_id
            FROM analysis_run
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
        ).fetchall()
    ]
    run_ids.append(_legacy_run_id(schema, object_name, object_type, subprogram, prompt_version))
    delete_runs(conn, run_ids)
