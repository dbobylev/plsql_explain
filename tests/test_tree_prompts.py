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
    prompt_context: str | None = None,
    preceding_comment: str = "",
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
        prompt_context=source_text if prompt_context is None else prompt_context,
        preceding_comment=preceding_comment,
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


def test_non_leaf_prompt_uses_child_descriptions_instead_of_child_source() -> None:
    child = _node(statement_type="IF_THEN", source_text="x := 1;")
    child.description = "Присваивает x значение 1"
    node = _node(
        statement_type="IF",
        source_text="IF x > 0 THEN\nx := 1;\nEND IF;",
        prompt_context="IF x > 0 THEN",
        children=[child],
    )

    result = build_prompt(node)

    assert result is not None
    _, user = result
    assert "IF x > 0 THEN" in user
    assert "Присваивает x значение 1" in user
    assert "x := 1;\nEND IF;" not in user


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


def test_prompt_includes_preceding_comment() -> None:
    node = _node(
        statement_type="SQL_SELECT",
        source_text="SELECT * FROM ORDERS",
        preceding_comment="-- берем только активные заказы",
    )
    result = build_prompt(node)
    assert result is not None
    _, user = result
    assert "Комментарий разработчика" in user
    assert "-- берем только активные заказы" in user


def test_description_length_hint_scales() -> None:
    # tier 1: ≤3 children
    assert "1-2" in _description_length_hint(1)
    assert "1-2" in _description_length_hint(3)
    # tier 2: 4-8 children — must mention tables
    hint_mid = _description_length_hint(5)
    assert "2-3" in hint_mid
    assert "таблицы" in hint_mid
    # tier 3: 9-15 children — 3-5 sentences, enumerate everything
    hint_large = _description_length_hint(10)
    assert "3-5" in hint_large
    assert "перечисли" in hint_large
    # tier 4: 16+ children — structured format
    hint_xl = _description_length_hint(16)
    assert "Ключевые операции" in hint_xl
    assert "Назначение" in hint_xl


def test_method_root_large_uses_structured_hint() -> None:
    """method_root with 9+ children должен получать структурированный hint."""
    children = [_node() for _ in range(9)]
    for i, c in enumerate(children):
        c.description = f"Шаг {i + 1}"
    node = _node(
        node_kind="method_root",
        title="PROC_BIG",
        children=children,
    )
    result = build_prompt(node)
    assert result is not None
    _, user = result
    assert "Основные шаги" in user
    assert "Ключевые таблицы" in user


def test_method_root_small_uses_sentence_hint() -> None:
    """method_root с 8 дочерними узлами должен использовать обычный hint."""
    children = [_node() for _ in range(8)]
    for i, c in enumerate(children):
        c.description = f"Шаг {i + 1}"
    node = _node(
        node_kind="method_root",
        title="PROC_SMALL",
        children=children,
    )
    result = build_prompt(node)
    assert result is not None
    _, user = result
    assert "2-3" in user
    assert "Основные шаги" not in user
