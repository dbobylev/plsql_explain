from __future__ import annotations

import sqlite3
import struct
from datetime import datetime, timezone
from typing import Optional


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Correlated subquery that selects the latest completed run_id per (schema, object, subprogram).
# Used by both iter_canonical_nodes and load_all_embeddings.
_LATEST_RUN_SUBQUERY = """
    ar.run_id = (
        SELECT run_id FROM analysis_run
        WHERE schema_name = ar.schema_name
          AND object_name  = ar.object_name
          AND subprogram   = ar.subprogram
          AND status       = 'completed'
        ORDER BY finished_at DESC, started_at DESC, run_id DESC
        LIMIT 1
    )
"""

_EMBEDDABLE_KINDS_CLAUSE = "nd.node_kind IN ('method_root', 'statement')"
_SKIP_TYPES_CLAUSE = "nd.statement_type NOT IN ('', 'CALL')"


def iter_canonical_nodes(
    conn: sqlite3.Connection,
    schema: Optional[str] = None,
    object_name: Optional[str] = None,
) -> list[sqlite3.Row]:
    """Return embeddable nodes from the latest completed run per object."""
    filters, params = _object_filters(schema, object_name, alias="ar")
    where = ("AND " + " AND ".join(filters)) if filters else ""
    return conn.execute(
        f"""
        SELECT nd.*
        FROM node_description nd
        JOIN analysis_run ar ON ar.run_id = nd.run_id
        WHERE ar.status = 'completed'
          {where}
          AND {_LATEST_RUN_SUBQUERY}
          AND {_EMBEDDABLE_KINDS_CLAUSE}
          AND {_SKIP_TYPES_CLAUSE}
        ORDER BY nd.schema_name, nd.object_name, nd.subprogram, nd.position
        """,
        params,
    ).fetchall()


def get_parent_chain(
    conn: sqlite3.Connection,
    run_id: str,
    node_id: str,
) -> list[sqlite3.Row]:
    """
    Return ancestor nodes from root down to the direct parent of node_id
    (the node itself is not included).  Order: root first.
    """
    rows_by_id: dict[str, sqlite3.Row] = {
        row["node_id"]: row
        for row in conn.execute(
            """
            SELECT node_id, parent_node_id, title, node_kind, statement_type
            FROM node_description
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchall()
    }

    chain: list[sqlite3.Row] = []
    current_id = node_id
    while True:
        row = rows_by_id.get(current_id)
        if row is None:
            break
        parent_id = row["parent_node_id"]
        if parent_id is None:
            break
        parent_row = rows_by_id.get(parent_id)
        if parent_row is None:
            break
        chain.append(parent_row)
        current_id = parent_id

    chain.reverse()
    return chain


def get_table_accesses_for_object(
    conn: sqlite3.Connection,
    schema: str,
    object_name: str,
    subprogram: str,
) -> list[sqlite3.Row]:
    """Return distinct (table_name, operation) pairs for the given object/subprogram."""
    return conn.execute(
        """
        SELECT DISTINCT table_name, operation
        FROM table_access
        WHERE schema_name = ? AND object_name = ?
          AND COALESCE(subprogram, '') = ?
        ORDER BY table_name, operation
        """,
        (schema.upper(), object_name.upper(), subprogram),
    ).fetchall()


def pack_embedding(values: list[float]) -> bytes:
    return struct.pack(f"{len(values)}f", *values)


def unpack_embedding(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def upsert_embedding(
    conn: sqlite3.Connection,
    node_description_id: int,
    model: str,
    embed_text: str,
    embedding: list[float],
) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO node_embedding
                (node_description_id, embedding_model, embed_text, embedding, embedded_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(node_description_id, embedding_model)
            DO UPDATE SET embed_text=excluded.embed_text,
                          embedding=excluded.embedding,
                          embedded_at=excluded.embedded_at
            """,
            (node_description_id, model, embed_text, pack_embedding(embedding), _utc_now()),
        )


def get_unembedded_nodes(
    conn: sqlite3.Connection,
    model: str,
    schema: Optional[str] = None,
    object_name: Optional[str] = None,
) -> list[sqlite3.Row]:
    """Return canonical nodes that have no embedding for the given model."""
    filters, params = _object_filters(schema, object_name, alias="ar")
    params.append(model)
    where = ("AND " + " AND ".join(filters)) if filters else ""
    return conn.execute(
        f"""
        SELECT nd.*
        FROM node_description nd
        JOIN analysis_run ar ON ar.run_id = nd.run_id
        WHERE ar.status = 'completed'
          {where}
          AND {_LATEST_RUN_SUBQUERY}
          AND {_EMBEDDABLE_KINDS_CLAUSE}
          AND {_SKIP_TYPES_CLAUSE}
          AND NOT EXISTS (
              SELECT 1 FROM node_embedding ne
              WHERE ne.node_description_id = nd.id
                AND ne.embedding_model = ?
          )
        ORDER BY nd.schema_name, nd.object_name, nd.subprogram, nd.position
        """,
        params,
    ).fetchall()


def load_all_embeddings(
    conn: sqlite3.Connection,
    model: str,
    schema: Optional[str] = None,
    object_name: Optional[str] = None,
) -> list[tuple[sqlite3.Row, list[float]]]:
    """Return (node_description row, embedding vector) for all embedded canonical nodes."""
    filters, params = _object_filters(schema, object_name, alias="ar")
    params.insert(0, model)
    where = ("AND " + " AND ".join(filters)) if filters else ""
    rows = conn.execute(
        f"""
        SELECT nd.*, ne.embed_text, ne.embedding
        FROM node_description nd
        JOIN analysis_run ar ON ar.run_id = nd.run_id
        JOIN node_embedding ne
          ON ne.node_description_id = nd.id
         AND ne.embedding_model = ?
        WHERE ar.status = 'completed'
          {where}
          AND {_LATEST_RUN_SUBQUERY}
          AND {_EMBEDDABLE_KINDS_CLAUSE}
          AND {_SKIP_TYPES_CLAUSE}
        ORDER BY nd.schema_name, nd.object_name, nd.subprogram, nd.position
        """,
        params,
    ).fetchall()
    return [(row, unpack_embedding(row["embedding"])) for row in rows]


def _object_filters(
    schema: Optional[str],
    object_name: Optional[str],
    alias: str = "nd",
) -> tuple[list[str], list]:
    filters: list[str] = []
    params: list = []
    if schema:
        filters.append(f"{alias}.schema_name = ?")
        params.append(schema.upper())
    if object_name:
        filters.append(f"{alias}.object_name = ?")
        params.append(object_name.upper())
    return filters, params
