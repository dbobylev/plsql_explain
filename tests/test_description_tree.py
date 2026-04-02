"""Tests for summarizer.description_tree — tree building from substatements + call edges."""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Optional

import pytest

from summarizer.description_tree import (
    DescriptionNode,
    build_description_tree,
    _contains_identifier,
)
from traversal.models import DependencyNode


# ── Helpers ──────────────────────────────────────────────────────────────────


def _ok_node(
    name: str,
    schema: str = "S",
    subprogram: Optional[str] = None,
    children: Optional[list[DependencyNode]] = None,
    obj_type: str = "PACKAGE BODY",
) -> DependencyNode:
    return DependencyNode(
        schema_name=schema,
        object_name=name,
        object_type=obj_type,
        subprogram=subprogram,
        status="ok",
        error_message=None,
        children=children or [],
    )


def _stub_dep(name: str, status: str) -> DependencyNode:
    return DependencyNode(
        schema_name="S",
        object_name=name,
        object_type=None,
        subprogram=None,
        status=status,
        error_message=None,
    )


def _insert_substatement(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    obj_type: str,
    subprogram: str,
    seq: int,
    parent_seq: int | None,
    position: int,
    statement_type: str,
    source_text: str,
    start_line: int = 1,
    end_line: int = 10,
) -> None:
    source_hash = hashlib.sha256(source_text.encode()).hexdigest()
    conn.execute(
        """
        INSERT INTO substatement
            (schema_name, object_name, object_type, subprogram, seq, parent_seq,
             position, statement_type, start_line, end_line, source_text, source_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (schema, name, obj_type, subprogram, seq, parent_seq,
         position, statement_type, start_line, end_line, source_text, source_hash),
    )
    conn.commit()


def _insert_source(conn: sqlite3.Connection, name: str, source: str = "-- code",
                   obj_type: str = "PACKAGE BODY", schema: str = "S") -> None:
    source_hash = hashlib.sha256(source.encode()).hexdigest()
    conn.execute(
        "INSERT INTO object_source (schema_name, object_name, object_type, source_text, source_hash, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, datetime('now'))",
        (schema, name, obj_type, source, source_hash),
    )
    conn.execute(
        "INSERT INTO parse_result (schema_name, object_name, object_type, parsed_at, source_hash, status) "
        "VALUES (?, ?, ?, datetime('now'), ?, 'ok')",
        (schema, name, obj_type, source_hash),
    )
    conn.commit()


# ── Tests ────────────────────────────────────────────────────────────────────


def test_contains_identifier() -> None:
    assert _contains_identifier("call PKG_AUDIT.LOG_EVENT()", "PKG_AUDIT")
    assert _contains_identifier("call PKG_AUDIT.LOG_EVENT()", "LOG_EVENT")
    assert not _contains_identifier("call OTHER_PKG.DO()", "PKG_AUDIT")


def test_build_tree_no_substatements_returns_method_root(mem_conn: sqlite3.Connection) -> None:
    _insert_source(mem_conn, "PKG_A", "CREATE PACKAGE BODY PKG_A IS BEGIN NULL; END;")
    dep = _ok_node("PKG_A")
    tree = build_description_tree(mem_conn, dep)

    assert tree.node_kind == "method_root"
    assert tree.title == "PKG_A"
    assert tree.children == []
    assert tree.source_text  # has full source


def test_build_tree_with_substatements(mem_conn: sqlite3.Connection) -> None:
    _insert_source(mem_conn, "PKG_A")
    _insert_substatement(mem_conn, "S", "PKG_A", "PACKAGE BODY", "",
                         seq=0, parent_seq=None, position=0,
                         statement_type="OTHER", source_text="v_x := 1;")
    _insert_substatement(mem_conn, "S", "PKG_A", "PACKAGE BODY", "",
                         seq=1, parent_seq=None, position=1,
                         statement_type="SQL_SELECT", source_text="SELECT 1 FROM DUAL")

    dep = _ok_node("PKG_A")
    tree = build_description_tree(mem_conn, dep)

    assert tree.node_kind == "method_root"
    assert len(tree.children) == 2
    assert tree.children[0].node_kind == "statement"
    assert tree.children[0].statement_type == "OTHER"
    assert tree.children[1].statement_type == "SQL_SELECT"


def test_build_tree_nested_substatements(mem_conn: sqlite3.Connection) -> None:
    _insert_source(mem_conn, "PKG_A")
    _insert_substatement(mem_conn, "S", "PKG_A", "PACKAGE BODY", "",
                         seq=0, parent_seq=None, position=0,
                         statement_type="IF", source_text="IF x > 0 THEN")
    _insert_substatement(mem_conn, "S", "PKG_A", "PACKAGE BODY", "",
                         seq=1, parent_seq=0, position=0,
                         statement_type="IF_THEN", source_text="v_res := 1;")

    dep = _ok_node("PKG_A")
    tree = build_description_tree(mem_conn, dep)

    assert tree.node_kind == "method_root"
    assert len(tree.children) == 1
    if_node = tree.children[0]
    assert if_node.statement_type == "IF"
    assert len(if_node.children) == 1
    assert if_node.children[0].statement_type == "IF_THEN"


def test_nested_substatement_prompt_context_keeps_only_local_header(mem_conn: sqlite3.Connection) -> None:
    _insert_source(mem_conn, "PKG_A")
    _insert_substatement(mem_conn, "S", "PKG_A", "PACKAGE BODY", "",
                         seq=0, parent_seq=None, position=0,
                         statement_type="IF", source_text="IF x > 0 THEN")
    _insert_substatement(mem_conn, "S", "PKG_A", "PACKAGE BODY", "",
                         seq=1, parent_seq=0, position=0,
                         statement_type="IF_THEN", source_text="v_res := 1;")

    dep = _ok_node("PKG_A")
    tree = build_description_tree(mem_conn, dep)

    if_node = tree.children[0]
    assert if_node.source_text == "IF x > 0 THEN\nv_res := 1;\nEND IF;"
    assert if_node.prompt_context == "IF x > 0 THEN"


def test_stub_dep_produces_stub_node(mem_conn: sqlite3.Connection) -> None:
    dep = _stub_dep("PKG_MISSING", "missing")
    tree = build_description_tree(mem_conn, dep)

    assert tree.node_kind == "stub"
    assert "missing" in tree.description


def test_call_expansion(mem_conn: sqlite3.Connection) -> None:
    """When a leaf node references a callee, the callee tree is inlined."""
    _insert_source(mem_conn, "PKG_A")
    _insert_source(mem_conn, "PKG_B")

    # PKG_A has a statement that calls PKG_B
    _insert_substatement(mem_conn, "S", "PKG_A", "PACKAGE BODY", "",
                         seq=0, parent_seq=None, position=0,
                         statement_type="OTHER", source_text="PKG_B.DO_SOMETHING();")

    # PKG_B has its own substatements
    _insert_substatement(mem_conn, "S", "PKG_B", "PACKAGE BODY", "",
                         seq=0, parent_seq=None, position=0,
                         statement_type="SQL_INSERT", source_text="INSERT INTO T VALUES(1);")

    callee_dep = _ok_node("PKG_B")
    dep = _ok_node("PKG_A", children=[callee_dep])

    tree = build_description_tree(mem_conn, dep)

    assert tree.node_kind == "method_root"
    assert len(tree.children) == 1
    stmt = tree.children[0]
    assert stmt.statement_type == "OTHER"
    # The call to PKG_B should be expanded as a child
    assert len(stmt.children) == 1
    call_node = stmt.children[0]
    assert call_node.node_kind == "call"
    assert "PKG_B" in call_node.title


def test_repeated_call_sites_get_unique_node_ids(mem_conn: sqlite3.Connection) -> None:
    _insert_source(mem_conn, "PKG_A")
    _insert_source(mem_conn, "PKG_B")

    _insert_substatement(mem_conn, "S", "PKG_A", "PACKAGE BODY", "",
                         seq=0, parent_seq=None, position=0,
                         statement_type="OTHER", source_text="PKG_B.DO_SOMETHING();")
    _insert_substatement(mem_conn, "S", "PKG_A", "PACKAGE BODY", "",
                         seq=1, parent_seq=None, position=1,
                         statement_type="OTHER", source_text="PKG_B.DO_SOMETHING();")
    _insert_substatement(mem_conn, "S", "PKG_B", "PACKAGE BODY", "",
                         seq=0, parent_seq=None, position=0,
                         statement_type="OTHER", source_text="v_x := 1;")

    dep = _ok_node("PKG_A", children=[_ok_node("PKG_B")])
    tree = build_description_tree(mem_conn, dep)

    call_nodes = [child.children[0] for child in tree.children]
    assert len(call_nodes) == 2
    assert len({node.node_id for node in call_nodes}) == 2
    assert len({node.children[0].node_id for node in call_nodes}) == 2


def test_schema_qualified_call_matches_only_exact_callee(mem_conn: sqlite3.Connection) -> None:
    _insert_source(mem_conn, "TEST_PKG", schema="LOCAL")
    _insert_source(mem_conn, "SHARED_PKG", schema="SCHEMA_A")
    _insert_source(mem_conn, "SHARED_PKG", schema="SCHEMA_B")

    _insert_substatement(mem_conn, "LOCAL", "TEST_PKG", "PACKAGE BODY", "",
                         seq=0, parent_seq=None, position=0,
                         statement_type="OTHER", source_text="schema_a.shared_pkg.do_work(1);")
    _insert_substatement(mem_conn, "SCHEMA_A", "SHARED_PKG", "PACKAGE BODY", "DO_WORK",
                         seq=0, parent_seq=None, position=0,
                         statement_type="OTHER", source_text="v_a := 1;")
    _insert_substatement(mem_conn, "SCHEMA_B", "SHARED_PKG", "PACKAGE BODY", "DO_WORK",
                         seq=0, parent_seq=None, position=0,
                         statement_type="OTHER", source_text="v_b := 1;")

    dep = _ok_node(
        "TEST_PKG",
        schema="LOCAL",
        children=[
            _ok_node("SHARED_PKG", schema="SCHEMA_A", subprogram="DO_WORK"),
            _ok_node("SHARED_PKG", schema="SCHEMA_B", subprogram="DO_WORK"),
        ],
    )
    tree = build_description_tree(mem_conn, dep)

    stmt = tree.children[0]
    assert len(stmt.children) == 1
    assert stmt.children[0].schema_name == "SCHEMA_A"
    assert "SCHEMA_A" in stmt.children[0].title


def test_cycle_detection_in_calls(mem_conn: sqlite3.Connection) -> None:
    """Cycle in call graph produces stub node."""
    _insert_source(mem_conn, "PKG_A")
    _insert_source(mem_conn, "PKG_B")

    _insert_substatement(mem_conn, "S", "PKG_A", "PACKAGE BODY", "",
                         seq=0, parent_seq=None, position=0,
                         statement_type="OTHER", source_text="PKG_B.DO();")
    _insert_substatement(mem_conn, "S", "PKG_B", "PACKAGE BODY", "",
                         seq=0, parent_seq=None, position=0,
                         statement_type="OTHER", source_text="PKG_A.DO();")

    dep_b = _ok_node("PKG_B", children=[_ok_node("PKG_A")])
    dep_a = _ok_node("PKG_A", children=[dep_b])

    tree = build_description_tree(mem_conn, dep_a)

    # Navigate: root -> stmt (calls PKG_B) -> call(PKG_B) -> stmt (calls PKG_A) -> stub(cycle)
    assert tree.node_kind == "method_root"
    call_b = tree.children[0].children[0]
    assert call_b.node_kind == "call"
    # PKG_B's statement references PKG_A which should be a cycle stub
    pkg_b_stmt = call_b.children[0]
    assert len(pkg_b_stmt.children) == 1
    assert pkg_b_stmt.children[0].node_kind == "stub"
    assert "cycle" in pkg_b_stmt.children[0].description


def test_tree_hash_computed(mem_conn: sqlite3.Connection) -> None:
    _insert_source(mem_conn, "PKG_A")
    _insert_substatement(mem_conn, "S", "PKG_A", "PACKAGE BODY", "",
                         seq=0, parent_seq=None, position=0,
                         statement_type="OTHER", source_text="v_x := 1;")

    dep = _ok_node("PKG_A")
    tree = build_description_tree(mem_conn, dep)

    assert tree.source_hash
    assert tree.children[0].source_hash
