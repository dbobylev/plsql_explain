"""Tests for summarizer.tree_describer — LLM orchestration and tree rendering."""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Optional
from unittest.mock import MagicMock

import pytest

from summarizer.description_tree import DescriptionNode, build_description_tree
from summarizer.tree_describer import (
    _populate_descriptions,
    describe_tree,
    render_tree,
)
from summarizer.tree_prompts import PROMPT_VERSION
from traversal.models import DependencyNode


# ── Helpers ──────────────────────────────────────────────────────────────────


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
         position, statement_type, 1, 10, source_text, source_hash),
    )
    conn.commit()


def _ok_node(
    name: str,
    schema: str = "S",
    subprogram: Optional[str] = None,
    children: Optional[list[DependencyNode]] = None,
) -> DependencyNode:
    return DependencyNode(
        schema_name=schema,
        object_name=name,
        object_type="PACKAGE BODY",
        subprogram=subprogram,
        status="ok",
        error_message=None,
        children=children or [],
    )


# ── Tests ────────────────────────────────────────────────────────────────────


def test_populate_descriptions_calls_llm_for_leaf(mem_conn: sqlite3.Connection) -> None:
    _insert_source(mem_conn, "PKG_A")
    _insert_substatement(mem_conn, "S", "PKG_A", "PACKAGE BODY", "",
                         seq=0, parent_seq=None, position=0,
                         statement_type="OTHER", source_text="v_x := 1;")

    dep = _ok_node("PKG_A")
    tree = build_description_tree(mem_conn, dep)

    client = MagicMock()
    client.complete.return_value = "Присваивает v_x значение 1"

    _populate_descriptions(mem_conn, tree, client, "S", "PKG_A", "PACKAGE BODY", None, force=False)

    assert tree.children[0].description == "Присваивает v_x значение 1"
    assert tree.description  # method_root also described
    assert client.complete.call_count == 2  # leaf + method_root


def test_populate_descriptions_bottom_up_order(mem_conn: sqlite3.Connection) -> None:
    """Children are described before parents."""
    _insert_source(mem_conn, "PKG_A")
    _insert_substatement(mem_conn, "S", "PKG_A", "PACKAGE BODY", "",
                         seq=0, parent_seq=None, position=0,
                         statement_type="IF", source_text="IF x > 0 THEN")
    _insert_substatement(mem_conn, "S", "PKG_A", "PACKAGE BODY", "",
                         seq=1, parent_seq=0, position=0,
                         statement_type="IF_THEN", source_text="v_res := 1;")

    dep = _ok_node("PKG_A")
    tree = build_description_tree(mem_conn, dep)

    call_order = []
    client = MagicMock()

    def track_call(system, user):
        call_order.append(user[:30])
        return "description"

    client.complete.side_effect = track_call

    _populate_descriptions(mem_conn, tree, client, "S", "PKG_A", "PACKAGE BODY", None, force=False)

    # leaf (IF_THEN) should be called before parent (IF) before root
    assert client.complete.call_count == 3


def test_stub_node_not_sent_to_llm(mem_conn: sqlite3.Connection) -> None:
    dep = DependencyNode(
        schema_name="S", object_name="PKG_MISSING", object_type=None,
        subprogram=None, status="missing", error_message=None,
    )
    tree = build_description_tree(mem_conn, dep)

    client = MagicMock()
    _populate_descriptions(mem_conn, tree, client, "S", "PKG_MISSING", "", None, force=False)

    client.complete.assert_not_called()
    assert tree.description  # has pre-set stub description


def test_render_tree_output() -> None:
    child1 = DescriptionNode(
        node_id="test/seq:0", node_kind="statement", statement_type="OTHER",
        title="OTHER", source_text="v_x := 1;", start_line=1, end_line=1,
        description="Присваивает v_x значение 1",
    )
    child2 = DescriptionNode(
        node_id="test/seq:1", node_kind="statement", statement_type="SQL_SELECT",
        title="SQL_SELECT", source_text="SELECT 1 FROM DUAL", start_line=2, end_line=2,
        description="Выбирает единицу из DUAL",
    )
    root = DescriptionNode(
        node_id="test/root", node_kind="method_root", statement_type="METHOD",
        title="PROC_MAIN", source_text="", start_line=1, end_line=10,
        description="Основная процедура",
        children=[child1, child2],
    )

    output = render_tree(root)

    assert "PROC_MAIN" in output
    assert "Присваивает v_x" in output
    assert "Выбирает единицу" in output
    assert "├" in output or "└" in output


def test_describe_tree_end_to_end(mem_conn: sqlite3.Connection) -> None:
    """Full pipeline: build tree, describe, save to DB."""
    _insert_source(mem_conn, "PKG_A")
    _insert_substatement(mem_conn, "S", "PKG_A", "PACKAGE BODY", "",
                         seq=0, parent_seq=None, position=0,
                         statement_type="OTHER", source_text="v_x := 1;")

    client = MagicMock()
    client.complete.return_value = "Описание"

    tree = describe_tree(mem_conn, "S", "PKG_A", None, client)

    assert tree.node_kind == "method_root"
    assert tree.children[0].description == "Описание"

    # Check saved to DB
    rows = mem_conn.execute("SELECT * FROM node_description").fetchall()
    assert len(rows) >= 2  # at least root + one child


def test_cache_reuse(mem_conn: sqlite3.Connection) -> None:
    """Second call reuses cached descriptions."""
    _insert_source(mem_conn, "PKG_A")
    _insert_substatement(mem_conn, "S", "PKG_A", "PACKAGE BODY", "",
                         seq=0, parent_seq=None, position=0,
                         statement_type="OTHER", source_text="v_x := 1;")

    client = MagicMock()
    client.complete.return_value = "Описание"

    # First call
    tree1 = describe_tree(mem_conn, "S", "PKG_A", None, client)
    call_count_1 = client.complete.call_count

    # Second call — should use cache
    tree2 = describe_tree(mem_conn, "S", "PKG_A", None, client)
    call_count_2 = client.complete.call_count

    assert call_count_2 == call_count_1  # no new LLM calls
    assert tree2.children[0].description == "Описание"


def test_force_bypasses_cache(mem_conn: sqlite3.Connection) -> None:
    _insert_source(mem_conn, "PKG_A")
    _insert_substatement(mem_conn, "S", "PKG_A", "PACKAGE BODY", "",
                         seq=0, parent_seq=None, position=0,
                         statement_type="OTHER", source_text="v_x := 1;")

    client = MagicMock()
    client.complete.return_value = "Описание"

    # First call
    describe_tree(mem_conn, "S", "PKG_A", None, client)
    call_count_1 = client.complete.call_count

    # Second call with force
    describe_tree(mem_conn, "S", "PKG_A", None, client, force=True)
    call_count_2 = client.complete.call_count

    assert call_count_2 > call_count_1
