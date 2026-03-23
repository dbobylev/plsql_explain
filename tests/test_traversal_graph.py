"""Tests for traversal.graph.build_tree."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from traversal.graph import build_tree
from traversal.models import DependencyNode


# ── Helpers ──────────────────────────────────────────────────────────────────

NOW = datetime.now(timezone.utc).isoformat()


def _insert_source(conn: sqlite3.Connection, schema: str, name: str, obj_type: str = "PACKAGE BODY") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO object_source "
        "(schema_name, object_name, object_type, source_text, source_hash, fetched_at) "
        "VALUES (?, ?, ?, '', 'hash', ?)",
        (schema, name, obj_type, NOW),
    )
    conn.commit()


def _insert_parse_result(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    obj_type: str = "PACKAGE BODY",
    status: str = "ok",
    error_message: str | None = None,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO parse_result "
        "(schema_name, object_name, object_type, parsed_at, source_hash, status, error_message) "
        "VALUES (?, ?, ?, ?, 'hash', ?, ?)",
        (schema, name, obj_type, NOW, status, error_message),
    )
    conn.commit()


def _insert_call_edge(
    conn: sqlite3.Connection,
    caller_schema: str,
    caller_object: str,
    caller_type: str,
    caller_subprogram: str | None,
    callee_object: str,
    callee_subprogram: str | None = None,
    callee_schema: str | None = None,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO call_edge "
        "(caller_schema, caller_object, caller_type, caller_subprogram, "
        " callee_schema, callee_object, callee_subprogram) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (caller_schema, caller_object, caller_type, caller_subprogram,
         callee_schema, callee_object, callee_subprogram),
    )
    conn.commit()


def _insert_table_access(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    obj_type: str,
    subprogram: str | None,
    table_name: str,
    operation: str,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO table_access "
        "(schema_name, object_name, object_type, subprogram, table_schema, table_name, operation) "
        "VALUES (?, ?, ?, ?, NULL, ?, ?)",
        (schema, name, obj_type, subprogram, table_name, operation),
    )
    conn.commit()


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_single_node_no_deps(mem_conn: sqlite3.Connection) -> None:
    _insert_source(mem_conn, "S", "PKG_A")
    _insert_parse_result(mem_conn, "S", "PKG_A")

    node = build_tree(mem_conn, "S", "PKG_A")

    assert node.status == "ok"
    assert node.object_name == "PKG_A"
    assert node.children == []
    assert node.table_accesses == []


def test_linear_chain(mem_conn: sqlite3.Connection) -> None:
    for name in ("PKG_A", "PKG_B", "PKG_C"):
        _insert_source(mem_conn, "S", name)
        _insert_parse_result(mem_conn, "S", name)

    _insert_call_edge(mem_conn, "S", "PKG_A", "PACKAGE BODY", None, "PKG_B")
    _insert_call_edge(mem_conn, "S", "PKG_B", "PACKAGE BODY", None, "PKG_C")

    node = build_tree(mem_conn, "S", "PKG_A")

    assert node.status == "ok"
    assert len(node.children) == 1
    b = node.children[0]
    assert b.object_name == "PKG_B"
    assert b.status == "ok"
    assert len(b.children) == 1
    c = b.children[0]
    assert c.object_name == "PKG_C"
    assert c.status == "ok"
    assert c.children == []


def test_cycle_detection(mem_conn: sqlite3.Connection) -> None:
    for name in ("PKG_A", "PKG_B"):
        _insert_source(mem_conn, "S", name)
        _insert_parse_result(mem_conn, "S", name)

    _insert_call_edge(mem_conn, "S", "PKG_A", "PACKAGE BODY", None, "PKG_B")
    _insert_call_edge(mem_conn, "S", "PKG_B", "PACKAGE BODY", None, "PKG_A")

    node = build_tree(mem_conn, "S", "PKG_A")

    assert node.status == "ok"
    b = node.children[0]
    assert b.status == "ok"
    # PKG_B calls PKG_A which is already in stack → cycle
    back = b.children[0]
    assert back.object_name == "PKG_A"
    assert back.status == "cycle"
    assert back.children == []


def test_diamond_expanded(mem_conn: sqlite3.Connection) -> None:
    """A → B → D and A → C → D: D must be fully expanded in both branches."""
    for name in ("PKG_A", "PKG_B", "PKG_C", "PKG_D"):
        _insert_source(mem_conn, "S", name)
        _insert_parse_result(mem_conn, "S", name)

    _insert_table_access(mem_conn, "S", "PKG_D", "PACKAGE BODY", None, "ORDERS", "SELECT")
    _insert_call_edge(mem_conn, "S", "PKG_A", "PACKAGE BODY", None, "PKG_B")
    _insert_call_edge(mem_conn, "S", "PKG_A", "PACKAGE BODY", None, "PKG_C")
    _insert_call_edge(mem_conn, "S", "PKG_B", "PACKAGE BODY", None, "PKG_D")
    _insert_call_edge(mem_conn, "S", "PKG_C", "PACKAGE BODY", None, "PKG_D")

    node = build_tree(mem_conn, "S", "PKG_A")

    names = {c.object_name for c in node.children}
    assert names == {"PKG_B", "PKG_C"}

    for child in node.children:
        assert len(child.children) == 1
        d = child.children[0]
        assert d.object_name == "PKG_D"
        assert d.status == "ok"
        assert len(d.table_accesses) == 1
        assert d.table_accesses[0].table_name == "ORDERS"


def test_missing_dependency(mem_conn: sqlite3.Connection) -> None:
    _insert_source(mem_conn, "S", "PKG_A")
    _insert_parse_result(mem_conn, "S", "PKG_A")
    # PKG_MISSING is never inserted
    _insert_call_edge(mem_conn, "S", "PKG_A", "PACKAGE BODY", None, "PKG_MISSING")

    node = build_tree(mem_conn, "S", "PKG_A")

    assert len(node.children) == 1
    missing = node.children[0]
    assert missing.object_name == "PKG_MISSING"
    assert missing.status == "missing"
    assert missing.children == []


def test_wrapped_dependency(mem_conn: sqlite3.Connection) -> None:
    _insert_source(mem_conn, "S", "PKG_A")
    _insert_parse_result(mem_conn, "S", "PKG_A")
    _insert_source(mem_conn, "S", "PKG_WRAPPED")
    _insert_parse_result(mem_conn, "S", "PKG_WRAPPED", status="wrapped")
    _insert_call_edge(mem_conn, "S", "PKG_A", "PACKAGE BODY", None, "PKG_WRAPPED")

    node = build_tree(mem_conn, "S", "PKG_A")

    wrapped = node.children[0]
    assert wrapped.object_name == "PKG_WRAPPED"
    assert wrapped.status == "wrapped"
    assert wrapped.children == []


def test_table_accesses_attached(mem_conn: sqlite3.Connection) -> None:
    _insert_source(mem_conn, "S", "PKG_A")
    _insert_parse_result(mem_conn, "S", "PKG_A")
    _insert_table_access(mem_conn, "S", "PKG_A", "PACKAGE BODY", None, "CUSTOMERS", "SELECT")
    _insert_table_access(mem_conn, "S", "PKG_A", "PACKAGE BODY", None, "ORDERS", "INSERT")

    node = build_tree(mem_conn, "S", "PKG_A")

    assert node.status == "ok"
    names = {a.table_name for a in node.table_accesses}
    assert names == {"CUSTOMERS", "ORDERS"}
    ops = {a.operation for a in node.table_accesses}
    assert ops == {"SELECT", "INSERT"}


def test_subprogram_filter(mem_conn: sqlite3.Connection) -> None:
    """Edges from subprogram PROC_X must not bleed into subprogram PROC_Y."""
    _insert_source(mem_conn, "S", "PKG_A")
    _insert_parse_result(mem_conn, "S", "PKG_A")
    _insert_source(mem_conn, "S", "PKG_B")
    _insert_parse_result(mem_conn, "S", "PKG_B")
    _insert_source(mem_conn, "S", "PKG_C")
    _insert_parse_result(mem_conn, "S", "PKG_C")

    # PROC_X calls PKG_B; PROC_Y calls PKG_C
    _insert_call_edge(mem_conn, "S", "PKG_A", "PACKAGE BODY", "PROC_X", "PKG_B")
    _insert_call_edge(mem_conn, "S", "PKG_A", "PACKAGE BODY", "PROC_Y", "PKG_C")

    node_x = build_tree(mem_conn, "S", "PKG_A", subprogram="PROC_X")
    assert len(node_x.children) == 1
    assert node_x.children[0].object_name == "PKG_B"

    node_y = build_tree(mem_conn, "S", "PKG_A", subprogram="PROC_Y")
    assert len(node_y.children) == 1
    assert node_y.children[0].object_name == "PKG_C"
