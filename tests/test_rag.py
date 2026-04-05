from __future__ import annotations

import sqlite3
import struct
from typing import Optional

import pytest

from rag.embedder import build_embed_text
from rag.sqlite_store import (
    get_parent_chain,
    get_table_accesses_for_object,
    get_unembedded_nodes,
    iter_canonical_nodes,
    load_all_embeddings,
    pack_embedding,
    unpack_embedding,
    upsert_embedding,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_run(
    conn: sqlite3.Connection,
    schema: str,
    object_name: str,
    subprogram: str = "",
    run_id: Optional[str] = None,
    status: str = "completed",
    finished_at: str = "2025-01-01T00:00:00",
) -> str:
    rid = run_id or f"run-{schema}-{object_name}-{subprogram}"
    conn.execute(
        """
        INSERT INTO analysis_run
            (run_id, schema_name, object_name, object_type, subprogram,
             prompt_version, status, started_at, finished_at)
        VALUES (?, ?, ?, 'PACKAGE BODY', ?, '2', ?, '2025-01-01T00:00:00', ?)
        """,
        (rid, schema.upper(), object_name.upper(), subprogram.upper(), status, finished_at),
    )
    conn.commit()
    return rid


def _make_node(
    conn: sqlite3.Connection,
    run_id: str,
    schema: str,
    object_name: str,
    subprogram: str = "",
    node_id: Optional[str] = None,
    node_kind: str = "statement",
    statement_type: str = "SQL_SELECT",
    title: str = "SELECT",
    source_text: str = "SELECT 1 FROM DUAL",
    description: str = "Запрос возвращает единицу.",
    parent_node_id: Optional[str] = None,
    position: int = 0,
    start_line: int = 10,
    end_line: int = 12,
) -> int:
    nid = node_id or f"{schema}.{object_name}/seq:{position}"
    conn.execute(
        """
        INSERT INTO node_description
            (run_id, schema_name, object_name, object_type, subprogram,
             node_id, node_kind, statement_type, title,
             start_line, end_line, source_text, parent_node_id, position,
             source_hash, description, prompt_version, described_at)
        VALUES (?, ?, ?, 'PACKAGE BODY', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'hash', ?, '2', '2025-01-01')
        """,
        (
            run_id,
            schema.upper(), object_name.upper(), subprogram.upper(),
            nid, node_kind, statement_type, title,
            start_line, end_line, source_text, parent_node_id, position,
            description,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM node_description WHERE run_id=? AND node_id=?", (run_id, nid)).fetchone()
    return row["id"]


# ---------------------------------------------------------------------------
# pack / unpack
# ---------------------------------------------------------------------------

def test_pack_unpack_roundtrip():
    values = [0.1, 0.2, 0.3, -0.5, 1.0]
    blob = pack_embedding(values)
    assert isinstance(blob, bytes)
    assert len(blob) == len(values) * 4
    recovered = unpack_embedding(blob)
    assert len(recovered) == len(values)
    for a, b in zip(values, recovered):
        assert abs(a - b) < 1e-6


# ---------------------------------------------------------------------------
# iter_canonical_nodes
# ---------------------------------------------------------------------------

def test_iter_canonical_nodes_returns_only_embeddable(mem_conn):
    run_id = _make_run(mem_conn, "S", "PKG")
    _make_node(mem_conn, run_id, "S", "PKG", node_kind="method_root", statement_type="METHOD",
               node_id="S.PKG/root", position=0)
    _make_node(mem_conn, run_id, "S", "PKG", node_kind="statement", statement_type="SQL_SELECT",
               node_id="S.PKG/seq:1", position=1)
    # stub — should be excluded
    _make_node(mem_conn, run_id, "S", "PKG", node_kind="stub", statement_type="CALL",
               node_id="S.PKG/stub:0", position=2)
    # call — should be excluded
    _make_node(mem_conn, run_id, "S", "PKG", node_kind="call", statement_type="CALL",
               node_id="S.PKG/call:0", position=3)
    # statement with empty statement_type — should be excluded
    _make_node(mem_conn, run_id, "S", "PKG", node_kind="statement", statement_type="",
               node_id="S.PKG/seq:4", position=4)

    rows = iter_canonical_nodes(mem_conn, schema="S", object_name="PKG")
    kinds = {r["node_id"] for r in rows}
    assert "S.PKG/root" in kinds
    assert "S.PKG/seq:1" in kinds
    assert "S.PKG/stub:0" not in kinds
    assert "S.PKG/call:0" not in kinds
    assert "S.PKG/seq:4" not in kinds


def test_iter_canonical_nodes_latest_run_only(mem_conn):
    old_run = _make_run(mem_conn, "S", "PKG", finished_at="2024-01-01T00:00:00", run_id="old")
    new_run = _make_run(mem_conn, "S", "PKG", finished_at="2025-01-01T00:00:00", run_id="new")
    _make_node(mem_conn, old_run, "S", "PKG", node_id="S.PKG/old-node", node_kind="method_root",
               statement_type="METHOD", position=0)
    _make_node(mem_conn, new_run, "S", "PKG", node_id="S.PKG/new-node", node_kind="method_root",
               statement_type="METHOD", position=0)

    rows = iter_canonical_nodes(mem_conn, schema="S", object_name="PKG")
    node_ids = {r["node_id"] for r in rows}
    assert "S.PKG/new-node" in node_ids
    assert "S.PKG/old-node" not in node_ids


def test_iter_canonical_nodes_excludes_running_runs(mem_conn):
    _make_run(mem_conn, "S", "PKG", status="running", run_id="r1")
    _make_node(mem_conn, "r1", "S", "PKG", node_id="S.PKG/root", node_kind="method_root",
               statement_type="METHOD", position=0)

    rows = iter_canonical_nodes(mem_conn, schema="S")
    assert rows == []


# ---------------------------------------------------------------------------
# get_parent_chain
# ---------------------------------------------------------------------------

def test_get_parent_chain_empty_for_root(mem_conn):
    run_id = _make_run(mem_conn, "S", "PKG")
    _make_node(mem_conn, run_id, "S", "PKG", node_id="S.PKG/root", node_kind="method_root",
               statement_type="METHOD", parent_node_id=None, position=0)

    chain = get_parent_chain(mem_conn, run_id, "S.PKG/root")
    assert chain == []


def test_get_parent_chain_returns_ancestors_top_down(mem_conn):
    run_id = _make_run(mem_conn, "S", "PKG")
    _make_node(mem_conn, run_id, "S", "PKG", node_id="S.PKG/root", title="ROOT",
               node_kind="method_root", statement_type="METHOD", parent_node_id=None, position=0)
    _make_node(mem_conn, run_id, "S", "PKG", node_id="S.PKG/if", title="IF",
               node_kind="statement", statement_type="IF", parent_node_id="S.PKG/root", position=0)
    _make_node(mem_conn, run_id, "S", "PKG", node_id="S.PKG/select", title="SELECT",
               node_kind="statement", statement_type="SQL_SELECT", parent_node_id="S.PKG/if", position=0)

    chain = get_parent_chain(mem_conn, run_id, "S.PKG/select")
    titles = [r["title"] for r in chain]
    assert titles == ["ROOT", "IF"]


# ---------------------------------------------------------------------------
# get_table_accesses_for_object
# ---------------------------------------------------------------------------

def test_get_table_accesses(mem_conn):
    mem_conn.execute(
        """
        INSERT INTO table_access (schema_name, object_name, object_type, subprogram, table_schema, table_name, operation)
        VALUES ('S', 'PKG', 'PACKAGE BODY', NULL, 'S', 'ORDERS', 'SELECT')
        """
    )
    mem_conn.execute(
        """
        INSERT INTO table_access (schema_name, object_name, object_type, subprogram, table_schema, table_name, operation)
        VALUES ('S', 'PKG', 'PACKAGE BODY', NULL, 'S', 'LOG', 'INSERT')
        """
    )
    mem_conn.commit()

    rows = get_table_accesses_for_object(mem_conn, "S", "PKG", "")
    table_ops = {(r["table_name"], r["operation"]) for r in rows}
    assert ("ORDERS", "SELECT") in table_ops
    assert ("LOG", "INSERT") in table_ops


# ---------------------------------------------------------------------------
# upsert_embedding / get_unembedded_nodes / load_all_embeddings
# ---------------------------------------------------------------------------

def test_upsert_and_load_embedding(mem_conn):
    run_id = _make_run(mem_conn, "S", "PKG")
    node_id = _make_node(mem_conn, run_id, "S", "PKG", node_id="S.PKG/root",
                         node_kind="method_root", statement_type="METHOD", position=0)

    emb = [0.1, 0.2, 0.3]
    upsert_embedding(mem_conn, node_id, "test-model", "embed text", emb)

    entries = load_all_embeddings(mem_conn, "test-model", schema="S")
    assert len(entries) == 1
    row, vec = entries[0]
    assert row["node_id"] == "S.PKG/root"
    for a, b in zip(emb, vec):
        assert abs(a - b) < 1e-6


def test_get_unembedded_nodes(mem_conn):
    run_id = _make_run(mem_conn, "S", "PKG")
    node_db_id = _make_node(mem_conn, run_id, "S", "PKG", node_id="S.PKG/root",
                             node_kind="method_root", statement_type="METHOD", position=0)

    # Before embedding — should appear in unembedded list
    unembedded = get_unembedded_nodes(mem_conn, "test-model", schema="S")
    assert any(r["id"] == node_db_id for r in unembedded)

    # After embedding — should disappear
    upsert_embedding(mem_conn, node_db_id, "test-model", "text", [0.1, 0.2])
    unembedded_after = get_unembedded_nodes(mem_conn, "test-model", schema="S")
    assert not any(r["id"] == node_db_id for r in unembedded_after)


# ---------------------------------------------------------------------------
# build_embed_text
# ---------------------------------------------------------------------------

def test_build_embed_text_structure(mem_conn):
    run_id = _make_run(mem_conn, "MYSCHEMA", "PKG_ORDERS")
    _make_node(
        mem_conn, run_id, "MYSCHEMA", "PKG_ORDERS",
        node_id="MYSCHEMA.PKG_ORDERS/root",
        node_kind="method_root", statement_type="METHOD",
        title="PKG_ORDERS", position=0, parent_node_id=None,
    )
    # Build a fake sqlite3.Row-like dict using a real row
    row = mem_conn.execute(
        "SELECT * FROM node_description WHERE run_id=?", (run_id,)
    ).fetchone()

    class _FakeRow:
        """Wraps a dict so attribute access works like sqlite3.Row."""
        def __init__(self, data: dict):
            self._d = data
        def __getitem__(self, k):
            return self._d[k]

    fake_row = _FakeRow({
        "schema_name": "MYSCHEMA",
        "object_name": "PKG_ORDERS",
        "subprogram": "PROCESS_ORDER",
        "title": "SELECT статус заказа",
        "start_line": 45,
        "end_line": 60,
        "description": "Запрос проверяет статус заказа.",
        "source_text": "SELECT status FROM orders WHERE id = v_id;",
    })

    class _FakeTaRow:
        def __init__(self, table_name, operation):
            self._d = {"table_name": table_name, "operation": operation}
        def __getitem__(self, k):
            return self._d[k]

    table_accesses = [_FakeTaRow("ORDERS", "SELECT")]
    parent_titles = ["PKG_ORDERS", "PROCESS_ORDER"]

    text = build_embed_text(fake_row, parent_titles, table_accesses)

    assert "[MYSCHEMA.PKG_ORDERS.PROCESS_ORDER]" in text
    assert "PKG_ORDERS → PROCESS_ORDER" in text
    assert "строки 45–60" in text
    assert "Запрос проверяет статус заказа." in text
    assert "ORDERS (SELECT)" in text
    assert "SELECT status FROM orders" in text


# ---------------------------------------------------------------------------
# search (unit test with fake embeddings)
# ---------------------------------------------------------------------------

def test_search_returns_top_k(mem_conn):
    import numpy as np
    from rag.search import search

    run_id = _make_run(mem_conn, "S", "PKG")

    # Create two nodes
    id1 = _make_node(mem_conn, run_id, "S", "PKG", node_id="S.PKG/n1",
                     node_kind="method_root", statement_type="METHOD", position=0,
                     description="Процедура обрабатывает заказы клиентов.")
    id2 = _make_node(mem_conn, run_id, "S", "PKG", node_id="S.PKG/n2",
                     node_kind="statement", statement_type="SQL_SELECT", position=1,
                     description="Получает список пользователей из базы.")

    # Embeddings: node1 is close to query, node2 is far
    emb_query = [1.0, 0.0, 0.0]
    emb1 = [0.99, 0.1, 0.0]  # very close to query
    emb2 = [0.0, 0.0, 1.0]   # orthogonal to query

    upsert_embedding(mem_conn, id1, "m", "t", emb1)
    upsert_embedding(mem_conn, id2, "m", "t", emb2)

    class _FakeClient:
        model = "m"
        def embed(self, texts):
            return [emb_query] * len(texts)

    results = search(mem_conn, "запрос", _FakeClient(), top_k=2)
    assert len(results) == 2
    assert results[0].score > results[1].score
    assert results[0].node_kind == "method_root"  # node1 is closest
