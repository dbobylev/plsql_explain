"""Tests for summarizer.tree_store — DB CRUD for node_description."""
from __future__ import annotations

import sqlite3

import pytest

from summarizer.description_tree import DescriptionNode
from summarizer.tree_store import (
    clear_tree,
    get_cached_description,
    save_tree,
    upsert_node_description,
)


def _node(node_id: str = "test/root", description: str = "Описание",
          children: list[DescriptionNode] | None = None) -> DescriptionNode:
    return DescriptionNode(
        node_id=node_id,
        node_kind="method_root",
        statement_type="METHOD",
        title="PROC",
        source_text="",
        start_line=1,
        end_line=10,
        description=description,
        children=children or [],
        source_hash="abc123",
    )


def test_upsert_and_get(mem_conn: sqlite3.Connection) -> None:
    node = _node()
    upsert_node_description(
        mem_conn, "S", "PKG_A", "PACKAGE BODY", None,
        node, None, 0, "1",
    )
    result = get_cached_description(
        mem_conn, "S", "PKG_A", "PACKAGE BODY", None, "test/root", "1",
    )
    assert result is not None
    cached_hash, cached_desc = result
    assert cached_hash == "abc123"
    assert cached_desc == "Описание"


def test_save_tree_persists_hierarchy(mem_conn: sqlite3.Connection) -> None:
    child = _node(node_id="test/seq:0", description="Дочерний")
    root = _node(children=[child])

    save_tree(mem_conn, "S", "PKG_A", "PACKAGE BODY", None, root, "1")

    rows = mem_conn.execute("SELECT * FROM node_description ORDER BY node_id").fetchall()
    assert len(rows) == 2

    root_row = next(r for r in rows if r["node_id"] == "test/root")
    child_row = next(r for r in rows if r["node_id"] == "test/seq:0")

    assert root_row["parent_node_id"] is None
    assert child_row["parent_node_id"] == "test/root"
    assert child_row["description"] == "Дочерний"


def test_clear_tree(mem_conn: sqlite3.Connection) -> None:
    node = _node()
    save_tree(mem_conn, "S", "PKG_A", "PACKAGE BODY", None, node, "1")

    rows_before = mem_conn.execute("SELECT COUNT(*) FROM node_description").fetchone()[0]
    assert rows_before > 0

    clear_tree(mem_conn, "S", "PKG_A", "PACKAGE BODY", None, "1")

    rows_after = mem_conn.execute("SELECT COUNT(*) FROM node_description").fetchone()[0]
    assert rows_after == 0


def test_upsert_updates_existing(mem_conn: sqlite3.Connection) -> None:
    node1 = _node(description="Первое описание")
    upsert_node_description(mem_conn, "S", "PKG_A", "PACKAGE BODY", None, node1, None, 0, "1")

    node2 = _node(description="Обновлённое описание")
    upsert_node_description(mem_conn, "S", "PKG_A", "PACKAGE BODY", None, node2, None, 0, "1")

    result = get_cached_description(mem_conn, "S", "PKG_A", "PACKAGE BODY", None, "test/root", "1")
    assert result is not None
    assert result[1] == "Обновлённое описание"
