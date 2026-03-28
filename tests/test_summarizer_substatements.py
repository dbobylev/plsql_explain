"""Tests for summarizer.substatements — tree loading and chunking."""
from __future__ import annotations

import sqlite3

import pytest

from summarizer.substatements import (
    SubstatementNode,
    chunk_substatements,
    compute_chunk_hash,
    load_substatement_tree,
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


# ── chunk_substatements tests ────────────────────────────────────────────────

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


def test_chunk_empty() -> None:
    assert chunk_substatements([]) == []


def test_chunk_single_small() -> None:
    roots = [_make_node(0, "OTHER", "x := 1;")]
    chunks = chunk_substatements(roots)
    assert len(chunks) == 1
    assert len(chunks[0]) == 1


def test_chunk_splits_on_budget() -> None:
    """Large roots should be split into separate chunks."""
    big_text = "A" * 5000  # > 2000*4 = 8000 default char budget
    roots = [
        _make_node(0, "OTHER", big_text),
        _make_node(1, "OTHER", big_text),
    ]
    chunks = chunk_substatements(roots)
    assert len(chunks) == 2


def test_chunk_exception_handler_starts_new_chunk() -> None:
    roots = [
        _make_node(0, "OTHER", "stmt1"),
        _make_node(1, "OTHER", "stmt2"),
        _make_node(2, "EXCEPTION_HANDLER", "WHEN OTHERS THEN NULL;"),
        _make_node(3, "OTHER", "stmt3"),
    ]
    chunks = chunk_substatements(roots, max_chunk_tokens=10000)
    # Even with large budget, EXCEPTION_HANDLER splits
    assert len(chunks) == 2
    assert chunks[0][-1].statement_type == "OTHER"
    assert chunks[1][0].statement_type == "EXCEPTION_HANDLER"


def test_chunk_keeps_compound_intact() -> None:
    """An IF with children must stay in one chunk."""
    import hashlib
    child = SubstatementNode(
        seq=1, parent_seq=0, position=0,
        statement_type="IF_THEN",
        start_line=2, end_line=5,
        source_text="B" * 3000,
        source_hash=hashlib.sha256(("B" * 3000).encode()).hexdigest(),
    )
    root = _make_node(0, "IF", "A" * 3000, children=[child])
    # Total: 6000 chars > default 8000 budget? Let's use small budget
    chunks = chunk_substatements([root], max_chunk_tokens=500)
    # Even with tiny budget, the root+child must be in one chunk
    assert len(chunks) == 1
    assert len(chunks[0]) == 1
    assert chunks[0][0].statement_type == "IF"


# ── compute_chunk_hash tests ────────────────────────────────────────────────

def test_chunk_hash_deterministic() -> None:
    roots = [_make_node(0, "OTHER", "stmt1"), _make_node(1, "OTHER", "stmt2")]
    h1 = compute_chunk_hash(roots)
    h2 = compute_chunk_hash(roots)
    assert h1 == h2


def test_chunk_hash_changes_on_source_change() -> None:
    r1 = [_make_node(0, "OTHER", "stmt1")]
    r2 = [_make_node(0, "OTHER", "stmt2")]
    assert compute_chunk_hash(r1) != compute_chunk_hash(r2)


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
