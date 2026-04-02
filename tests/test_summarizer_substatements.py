"""Tests for summarizer.substatements — tree loading and recursive analysis planning."""
from __future__ import annotations

import sqlite3

import pytest

from summarizer.substatements import (
    SubstatementNode,
    iter_code_units,
    load_substatement_tree,
    plan_substatement_analysis,
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


# ── recursive analysis planner tests ─────────────────────────────────────────

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


def test_plan_empty_method() -> None:
    plan = plan_substatement_analysis([])
    assert plan.unit_kind == "method"
    assert plan.children == []


def test_plan_single_small_leaf() -> None:
    plan = plan_substatement_analysis([_make_node(0, "OTHER", "x := 1;")])
    code_units = iter_code_units(plan)
    assert len(code_units) == 1
    assert code_units[0].statement_type == "OTHER"
    assert code_units[0].body_nodes[0].source_text == "x := 1;"


def test_plan_marks_oversized_atomic_leaf() -> None:
    big_text = "A" * 5000
    roots = [
        _make_node(0, "OTHER", big_text),
        _make_node(1, "OTHER", big_text),
    ]
    plan = plan_substatement_analysis(roots, max_chunk_tokens=1000)

    assert len(plan.children) == 2
    assert all(child.unit_kind == "code_chunk" for child in plan.children)
    assert all(child.oversized for child in plan.children)


def test_plan_exception_handler_becomes_separate_scope() -> None:
    begin = _make_node(
        0,
        "BEGIN_END",
        "BEGIN",
        children=[
            _make_node(1, "OTHER", "stmt1"),
            _make_node(
                2,
                "EXCEPTION_HANDLER",
                "WHEN OTHERS THEN",
                children=[_make_node(3, "OTHER", "NULL;")],
            ),
            _make_node(4, "OTHER", "stmt2"),
        ],
    )

    plan = plan_substatement_analysis([begin], max_chunk_tokens=5)

    begin_block = plan.children[0]
    assert begin_block.statement_type == "BEGIN_END"
    assert [child.statement_type for child in begin_block.children] == [
        "OTHER",
        "EXCEPTION_HANDLER",
        "OTHER",
    ]
    assert begin_block.children[1].unit_kind == "branch"


def test_plan_keeps_small_compound_intact() -> None:
    import hashlib
    child = SubstatementNode(
        seq=1, parent_seq=0, position=0,
        statement_type="IF_THEN",
        start_line=2, end_line=5,
        source_text="B" * 3000,
        source_hash=hashlib.sha256(("B" * 3000).encode()).hexdigest(),
    )
    root = _make_node(0, "IF", "A" * 3000, children=[child])
    plan = plan_substatement_analysis([root], max_chunk_tokens=2000)
    code_units = iter_code_units(plan)

    assert len(code_units) == 1
    assert code_units[0].statement_type == "IF"
    assert not code_units[0].oversized


def test_plan_expands_top_level_begin_end_body() -> None:
    declare = _make_node(0, "DECLARE", "v_res NUMBER;")
    begin = _make_node(
        1,
        "BEGIN_END",
        "BEGIN",
        children=[
            _make_node(2, "OTHER", "v_res := 1;"),
            _make_node(
                3,
                "IF",
                "IF v_res > 0 THEN",
                children=[
                    _make_node(
                        4,
                        "IF_THEN",
                        "v_res := v_res + 1;",
                        children=[_make_node(5, "OTHER", "v_res := v_res + 1;")],
                    )
                ],
            ),
        ],
    )

    plan = plan_substatement_analysis([declare, begin], max_chunk_tokens=12)
    code_units = iter_code_units(plan)

    assert [unit.statement_type for unit in code_units] == ["DECLARE", "OTHER", "IF"]
    assert all(unit.statement_type != "BEGIN_END" for unit in code_units)


def test_plan_splits_expanded_method_body_on_budget() -> None:
    declare = _make_node(0, "DECLARE", "v_res NUMBER;")
    begin = _make_node(
        1,
        "BEGIN_END",
        "BEGIN",
        children=[
            _make_node(2, "OTHER", "A" * 3000),
            _make_node(3, "OTHER", "B" * 3000),
        ],
    )

    plan = plan_substatement_analysis([declare, begin], max_chunk_tokens=1000)
    code_units = iter_code_units(plan)

    assert [unit.statement_type for unit in code_units] == ["DECLARE", "OTHER", "OTHER"]
    assert [unit.part_no for unit in plan.children[1].children] == [1, 2]
    assert [unit.parts_total for unit in plan.children[1].children] == [2, 2]


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


def test_plan_large_if_splits_into_branch_parts() -> None:
    if_node = _make_node(
        0,
        "IF",
        "IF v_res > 0 THEN",
        children=[
            _make_node(
                1,
                "IF_THEN",
                "then_body",
                children=[
                    _make_node(2, "OTHER", "A" * 2500),
                    _make_node(3, "OTHER", "B" * 2500),
                ],
            ),
            _make_node(
                4,
                "IF_ELSE",
                "ELSE",
                children=[
                    _make_node(5, "OTHER", "C" * 2500),
                    _make_node(6, "OTHER", "D" * 2500),
                ],
            ),
        ],
    )

    plan = plan_substatement_analysis([if_node], max_chunk_tokens=1000)

    if_block = plan.children[0]
    assert if_block.unit_kind == "block"
    assert if_block.statement_type == "IF"
    assert [child.statement_type for child in if_block.children] == ["IF_THEN", "IF_ELSE"]

    then_branch, else_branch = if_block.children
    assert then_branch.unit_kind == "branch"
    assert else_branch.unit_kind == "branch"
    assert len(then_branch.children) == 2
    assert len(else_branch.children) == 2
    assert all(child.unit_kind == "code_chunk" for child in then_branch.children)
    assert all(child.unit_kind == "code_chunk" for child in else_branch.children)
    assert then_branch.header_context[-1] == "THEN"
    assert else_branch.header_context[-1] == "ELSE"
    assert [child.parts_total for child in then_branch.children] == [2, 2]
    assert [child.parts_total for child in else_branch.children] == [2, 2]


# ── unit_hash tests ──────────────────────────────────────────────────────────

def test_unit_hash_deterministic() -> None:
    roots = [_make_node(0, "OTHER", "stmt1"), _make_node(1, "OTHER", "stmt2")]
    h1 = plan_substatement_analysis(roots).unit_hash
    h2 = plan_substatement_analysis(roots).unit_hash
    assert h1 == h2


def test_unit_hash_changes_on_source_change() -> None:
    r1 = [_make_node(0, "OTHER", "stmt1")]
    r2 = [_make_node(0, "OTHER", "stmt2")]
    assert plan_substatement_analysis(r1).unit_hash != plan_substatement_analysis(r2).unit_hash


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
