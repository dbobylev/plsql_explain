"""Tests for summarizer.substatements — tree loading and rendering."""
from __future__ import annotations

import sqlite3

import pytest

from summarizer.substatements import (
    SubstatementNode,
    load_substatement_tree,
    render_substatement,
    total_source_length,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

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
    import hashlib
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


# ── load_substatement_tree tests ─────────────────────────────────────────────

def test_load_empty_tree(mem_conn: sqlite3.Connection) -> None:
    roots = load_substatement_tree(mem_conn, "S", "PKG_A", "PACKAGE BODY", None)
    assert roots == []


def test_load_flat_tree(mem_conn: sqlite3.Connection) -> None:
    """Three top-level statements, no nesting."""
    for i, stype in enumerate(["SQL_SELECT", "OTHER", "SQL_INSERT"]):
        _insert_substatement(mem_conn, "S", "PKG_A", "PACKAGE BODY", "PROC1",
                             seq=i, parent_seq=None, position=i,
                             statement_type=stype, source_text=f"stmt_{i}")

    roots = load_substatement_tree(mem_conn, "S", "PKG_A", "PACKAGE BODY", "PROC1")

    assert len(roots) == 3
    assert [r.statement_type for r in roots] == ["SQL_SELECT", "OTHER", "SQL_INSERT"]
    assert all(r.children == [] for r in roots)


def test_load_nested_tree(mem_conn: sqlite3.Connection) -> None:
    """IF with two children: IF_THEN and IF_ELSE."""
    _insert_substatement(mem_conn, "S", "PKG_A", "PACKAGE BODY", "PROC1",
                         seq=0, parent_seq=None, position=0,
                         statement_type="IF", source_text="IF x > 0 THEN ...")
    _insert_substatement(mem_conn, "S", "PKG_A", "PACKAGE BODY", "PROC1",
                         seq=1, parent_seq=0, position=0,
                         statement_type="IF_THEN", source_text="v_result := 1;")
    _insert_substatement(mem_conn, "S", "PKG_A", "PACKAGE BODY", "PROC1",
                         seq=2, parent_seq=0, position=1,
                         statement_type="IF_ELSE", source_text="v_result := 0;")

    roots = load_substatement_tree(mem_conn, "S", "PKG_A", "PACKAGE BODY", "PROC1")

    assert len(roots) == 1
    assert roots[0].statement_type == "IF"
    assert len(roots[0].children) == 2
    assert roots[0].children[0].statement_type == "IF_THEN"
    assert roots[0].children[1].statement_type == "IF_ELSE"


def test_load_respects_subprogram_scope(mem_conn: sqlite3.Connection) -> None:
    """Substatements from PROC1 must not appear when querying PROC2."""
    _insert_substatement(mem_conn, "S", "PKG_A", "PACKAGE BODY", "PROC1",
                         seq=0, parent_seq=None, position=0,
                         statement_type="OTHER", source_text="proc1_stmt")
    _insert_substatement(mem_conn, "S", "PKG_A", "PACKAGE BODY", "PROC2",
                         seq=0, parent_seq=None, position=0,
                         statement_type="SQL_SELECT", source_text="proc2_stmt")

    roots = load_substatement_tree(mem_conn, "S", "PKG_A", "PACKAGE BODY", "PROC1")
    assert len(roots) == 1
    assert roots[0].source_text == "proc1_stmt"


def test_load_null_subprogram(mem_conn: sqlite3.Connection) -> None:
    """subprogram=None maps to empty string in DB."""
    _insert_substatement(mem_conn, "S", "PKG_A", "PACKAGE BODY", "",
                         seq=0, parent_seq=None, position=0,
                         statement_type="OTHER", source_text="pkg_level")

    roots = load_substatement_tree(mem_conn, "S", "PKG_A", "PACKAGE BODY", None)
    assert len(roots) == 1
    assert roots[0].source_text == "pkg_level"


# ── render_substatement tests ───────────────────────────────────────────────

def _make_node(seq: int, statement_type: str, source_text: str,
               children: list[SubstatementNode] | None = None) -> SubstatementNode:
    import hashlib
    return SubstatementNode(
        seq=seq, parent_seq=None, position=seq,
        statement_type=statement_type,
        start_line=1, end_line=10,
        source_text=source_text,
        source_hash=hashlib.sha256(source_text.encode()).hexdigest(),
        children=children or [],
    )


def test_render_substatement_reconstructs_compound_without_duplicate_body() -> None:
    if_node = _make_node(
        0,
        "IF",
        "IF v_res > 0 THEN",
        children=[
            _make_node(
                1,
                "IF_THEN",
                "v_res := v_res + 1;",
                children=[_make_node(2, "OTHER", "v_res := v_res + 1;")],
            ),
            _make_node(
                3,
                "IF_ELSE",
                "ELSE",
                children=[_make_node(4, "OTHER", "v_res := 0;")],
            ),
        ],
    )

    rendered = render_substatement(if_node)

    assert "IF v_res > 0 THEN" in rendered
    assert "ELSE" in rendered
    assert "END IF;" in rendered
    assert rendered.count("v_res := v_res + 1;") == 1


# ── total_source_length tests ───────────────────────────────────────────────

def test_total_source_length() -> None:
    import hashlib
    child = SubstatementNode(
        seq=1, parent_seq=0, position=0,
        statement_type="IF_THEN", start_line=2, end_line=3,
        source_text="child_text",
        source_hash=hashlib.sha256(b"child_text").hexdigest(),
    )
    root = _make_node(0, "IF", "root_text", children=[child])
    assert total_source_length([root]) == len("root_text") + len("child_text")
