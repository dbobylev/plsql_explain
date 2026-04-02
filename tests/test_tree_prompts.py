"""Tests for summarizer.tree_prompts — adaptive prompt building."""
from __future__ import annotations

from summarizer.description_tree import DescriptionNode
from summarizer.tree_prompts import build_prompt, _description_length_hint


def _node(
    statement_type: str = "OTHER",
    node_kind: str = "statement",
    source_text: str = "v_x := 1;",
    title: str = "OTHER",
    children: list[DescriptionNode] | None = None,
) -> DescriptionNode:
    return DescriptionNode(
        node_id="test/seq:0",
        node_kind=node_kind,
        statement_type=statement_type,
        title=title,
        source_text=source_text,
        start_line=1,
        end_line=1,
        description="",
        children=children or [],
    )


def test_stub_returns_none() -> None:
    node = _node(node_kind="stub")
    assert build_prompt(node) is None


def test_leaf_statement_returns_prompt() -> None:
    node = _node(statement_type="OTHER", source_text="v_x := 1;")
    result = build_prompt(node)
    assert result is not None
    system, user = result
    assert "PL/SQL" in system
    assert "v_x := 1;" in user


def test_sql_strategy_mentions_tables() -> None:
    node = _node(statement_type="SELECT", source_text="SELECT * FROM ORDERS")
    result = build_prompt(node)
    assert result is not None
    _, user = result
    assert "таблицы" in user


def test_sql_prefixed_type_uses_sql_strategy() -> None:
    node = _node(statement_type="SQL_SELECT", source_text="SELECT * FROM ORDERS")
    result = build_prompt(node)
    assert result is not None
    _, user = result
    assert "таблицы" in user
    assert "Операция: SELECT" in user


def test_branching_strategy() -> None:
    child = _node(statement_type="IF_THEN", source_text="x := 1;")
    child.description = "Присваивает x значение 1"
    node = _node(
        statement_type="IF",
        source_text="IF x > 0 THEN",
        children=[child],
    )
    result = build_prompt(node)
    assert result is not None
    _, user = result
    assert "ветвлени" in user
    assert "Присваивает x значение 1" in user


def test_loop_strategy() -> None:
    node = _node(statement_type="LOOP_FOR", source_text="FOR i IN 1..10 LOOP")
    result = build_prompt(node)
    assert result is not None
    _, user = result
    assert "итерируется" in user


def test_exception_strategy() -> None:
    node = _node(statement_type="EXCEPTION_HANDLER", source_text="WHEN OTHERS THEN NULL;")
    result = build_prompt(node)
    assert result is not None
    _, user = result
    assert "исключени" in user


def test_call_strategy() -> None:
    child = _node()
    child.description = "Делает что-то"
    node = _node(
        node_kind="call",
        title="CALL -> PKG_AUDIT.LOG_EVENT",
        children=[child],
    )
    result = build_prompt(node)
    assert result is not None
    _, user = result
    assert "PKG_AUDIT.LOG_EVENT" in user
    assert "Делает что-то" in user


def test_method_root_strategy() -> None:
    child = _node()
    child.description = "Инициализация"
    node = _node(
        node_kind="method_root",
        title="PROC_MAIN",
        children=[child],
    )
    result = build_prompt(node)
    assert result is not None
    _, user = result
    assert "PROC_MAIN" in user


def test_description_length_hint_scales() -> None:
    assert "1-2" in _description_length_hint(1)
    assert "1-2" in _description_length_hint(3)
    assert "2-3" in _description_length_hint(5)
    assert "2-4" in _description_length_hint(10)
