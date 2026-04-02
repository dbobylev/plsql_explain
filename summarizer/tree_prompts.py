from __future__ import annotations

from typing import Optional

from summarizer.description_tree import DescriptionNode

PROMPT_VERSION = "1"


def _description_length_hint(n_children: int) -> str:
    if n_children <= 3:
        return "Опиши 1-2 предложениями."
    if n_children <= 8:
        return "Опиши 2-3 предложениями."
    return "Опиши 2-4 предложениями, выдели основные шаги."


def _format_children_descriptions(node: DescriptionNode) -> str:
    lines = []
    for i, child in enumerate(node.children, 1):
        label = child.title or child.statement_type
        lines.append(f"{i}. [{label}] {child.description}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Strategy functions
# ---------------------------------------------------------------------------

def _sql_strategy(node: DescriptionNode) -> tuple[str, str]:
    system = (
        "Ты аналитик PL/SQL кода Oracle. "
        "Опиши SQL-операцию кратко и точно на русском языке."
    )
    if node.children:
        children_text = _format_children_descriptions(node)
        hint = _description_length_hint(len(node.children))
        user = (
            f"Операция: {node.statement_type}\n"
            f"Код:\n```\n{node.source_text}\n```\n\n"
            f"Вложенные элементы:\n{children_text}\n\n"
            f"Укажи какие таблицы затрагиваются, какие данные выбираются/изменяются, "
            f"ключевые условия WHERE/JOIN.\n{hint}"
        )
    else:
        user = (
            f"Операция: {node.statement_type}\n"
            f"Код:\n```\n{node.source_text}\n```\n\n"
            f"Укажи какие таблицы затрагиваются, какие данные выбираются/изменяются, "
            f"ключевые условия WHERE/JOIN.\nОпиши 1-2 предложениями."
        )
    return system, user


def _branching_strategy(node: DescriptionNode) -> tuple[str, str]:
    system = (
        "Ты аналитик PL/SQL кода Oracle. "
        "Опиши условную логику кратко и точно на русском языке."
    )
    if node.children:
        children_text = _format_children_descriptions(node)
        hint = _description_length_hint(len(node.children))
        user = (
            f"Конструкция: {node.statement_type}\n"
            f"Условие/заголовок:\n```\n{node.source_text}\n```\n\n"
            f"Ветви:\n{children_text}\n\n"
            f"Опиши логику ветвления и её назначение.\n{hint}"
        )
    else:
        user = (
            f"Конструкция: {node.statement_type}\n"
            f"Код:\n```\n{node.source_text}\n```\n\n"
            f"Опиши логику ветвления и её назначение.\nОпиши 1-2 предложениями."
        )
    return system, user


def _loop_strategy(node: DescriptionNode) -> tuple[str, str]:
    system = (
        "Ты аналитик PL/SQL кода Oracle. "
        "Опиши цикл кратко и точно на русском языке."
    )
    if node.children:
        children_text = _format_children_descriptions(node)
        hint = _description_length_hint(len(node.children))
        user = (
            f"Цикл: {node.statement_type}\n"
            f"Заголовок:\n```\n{node.source_text}\n```\n\n"
            f"Тело цикла:\n{children_text}\n\n"
            f"Опиши что итерируется и зачем.\n{hint}"
        )
    else:
        user = (
            f"Цикл: {node.statement_type}\n"
            f"Код:\n```\n{node.source_text}\n```\n\n"
            f"Опиши что итерируется и зачем.\nОпиши 1-2 предложениями."
        )
    return system, user


def _exception_strategy(node: DescriptionNode) -> tuple[str, str]:
    system = (
        "Ты аналитик PL/SQL кода Oracle. "
        "Опиши обработку исключений кратко и точно на русском языке."
    )
    if node.children:
        children_text = _format_children_descriptions(node)
        hint = _description_length_hint(len(node.children))
        user = (
            f"Обработчик исключений:\n"
            f"Код:\n```\n{node.source_text}\n```\n\n"
            f"Содержимое:\n{children_text}\n\n"
            f"Какие исключения перехватываются и как обрабатываются?\n{hint}"
        )
    else:
        user = (
            f"Обработчик исключений:\n"
            f"Код:\n```\n{node.source_text}\n```\n\n"
            f"Какие исключения перехватываются и как обрабатываются?\nОпиши 1-2 предложениями."
        )
    return system, user


def _call_strategy(node: DescriptionNode) -> tuple[str, str]:
    system = (
        "Ты аналитик PL/SQL кода Oracle. "
        "Опиши вызов метода кратко и точно на русском языке."
    )
    callee_name = node.title.replace("CALL -> ", "")
    children_text = _format_children_descriptions(node)
    hint = _description_length_hint(len(node.children))
    user = (
        f"Вызов метода: {callee_name}\n\n"
        f"Содержимое вызываемого метода:\n{children_text}\n\n"
        f"Опиши что делает вызываемый метод.\n{hint}"
    )
    return system, user


def _method_root_strategy(node: DescriptionNode) -> tuple[str, str]:
    system = (
        "Ты аналитик PL/SQL кода Oracle. "
        "Опиши метод кратко и точно на русском языке."
    )
    if node.children:
        children_text = _format_children_descriptions(node)
        hint = _description_length_hint(len(node.children))
        user = (
            f"Метод: {node.title}\n\n"
            f"Шаги метода:\n{children_text}\n\n"
            f"Опиши назначение метода.\n{hint}"
        )
    else:
        user = (
            f"Метод: {node.title}\n"
            f"Код:\n```\n{node.source_text}\n```\n\n"
            f"Опиши назначение метода.\nОпиши 1-2 предложениями."
        )
    return system, user


def _default_strategy(node: DescriptionNode) -> tuple[str, str]:
    system = (
        "Ты аналитик PL/SQL кода Oracle. "
        "Опиши фрагмент кратко и точно на русском языке."
    )
    if node.children:
        children_text = _format_children_descriptions(node)
        hint = _description_length_hint(len(node.children))
        user = (
            f"Тип: {node.statement_type}\n"
            f"Код:\n```\n{node.source_text}\n```\n\n"
            f"Содержимое:\n{children_text}\n\n"
            f"{hint}"
        )
    else:
        user = (
            f"Тип: {node.statement_type}\n"
            f"Код:\n```\n{node.source_text}\n```\n\n"
            f"Опиши кратко.\nОпиши 1-2 предложениями."
        )
    return system, user


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

_SQL_TYPES = {"SELECT", "INSERT", "UPDATE", "DELETE", "MERGE"}
_BRANCHING_TYPES = {"IF", "IF_THEN", "IF_ELSIF", "IF_ELSE", "CASE", "CASE_WHEN", "CASE_ELSE"}
_LOOP_TYPES = {"LOOP_FOR", "LOOP_WHILE", "LOOP_BASIC"}
_EXCEPTION_TYPES = {"EXCEPTION_HANDLER"}


def build_prompt(node: DescriptionNode) -> Optional[tuple[str, str]]:
    """Select and build prompt based on node_kind and statement_type."""
    if node.node_kind == "stub":
        return None

    if node.node_kind == "call":
        return _call_strategy(node)

    if node.node_kind == "method_root":
        return _method_root_strategy(node)

    st = node.statement_type.upper()

    if st in _SQL_TYPES:
        return _sql_strategy(node)
    if st in _BRANCHING_TYPES:
        return _branching_strategy(node)
    if st in _LOOP_TYPES:
        return _loop_strategy(node)
    if st in _EXCEPTION_TYPES:
        return _exception_strategy(node)

    return _default_strategy(node)
