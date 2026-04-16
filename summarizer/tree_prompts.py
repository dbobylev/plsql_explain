from __future__ import annotations

from typing import Optional

from dictconst.models import DictConstantUsage
from summarizer.description_tree import DescriptionNode
from traversal.models import TableAccessInfo

PROMPT_VERSION = "4"


def _description_length_hint(n_children: int) -> str:
    if n_children <= 3:
        return "Опиши 1-2 предложениями."
    if n_children <= 8:
        return (
            "Опиши 2-3 предложениями. "
            "Обязательно упомяни конкретные таблицы и ключевые объекты."
        )
    if n_children <= 15:
        return (
            "Опиши 3-5 предложениями. "
            "Обязательно перечисли все ключевые таблицы, условия и объекты БД."
        )
    # 16+ children — переходим к структурированному формату
    return (
        "Составь структурированное описание:\n"
        "— Назначение: одна фраза\n"
        "— Ключевые операции: 4-7 пунктов списком\n"
        "— Затронутые данные: таблицы, объекты, ключевые условия"
    )


def _display_statement_type(statement_type: str) -> str:
    normalized = statement_type.upper()
    if normalized.startswith("SQL_"):
        return normalized[4:]
    return normalized


def _format_children_descriptions(node: DescriptionNode) -> str:
    lines = []
    for i, child in enumerate(node.children, 1):
        label = child.title or child.statement_type
        lines.append(f"{i}. [{label}] {child.description}")
    return "\n".join(lines)


def _format_prompt_context(title: str, text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    return f"{title}:\n```\n{stripped}\n```\n\n"


def _format_preceding_comment(comment: str) -> str:
    stripped = comment.strip()
    if not stripped:
        return ""
    return f"Комментарий разработчика:\n```\n{stripped}\n```\n\n"


def _format_dict_constants(dict_constants: list[DictConstantUsage]) -> str:
    if not dict_constants:
        return ""

    lines = []
    for usage in dict_constants:
        parts = [f"c.get('{usage.const_name}')"]
        if usage.shortname:
            parts.append(f"shortname='{usage.shortname}'")
        if usage.fullname:
            parts.append(f"fullname='{usage.fullname}'")
        if usage.resolved_text and usage.resolved_text not in {usage.shortname, usage.fullname}:
            parts.append(f"resolved='{usage.resolved_text}'")
        if len(parts) == 1:
            parts.append("значение не найдено в ais.dicti")
        lines.append("  - " + " -> ".join([parts[0], ", ".join(parts[1:])]))
    return "Константы словаря:\n" + "\n".join(lines) + "\n\n"


# ---------------------------------------------------------------------------
# Table metadata formatting
# ---------------------------------------------------------------------------

def _format_table_metadata(
    table_accesses: list[TableAccessInfo],
    source_text: str,
) -> str:
    """Format table/column metadata filtered by what appears in source_text."""
    if not table_accesses:
        return ""
    upper = source_text.upper()
    lines = []
    for acc in table_accesses:
        if acc.table_name.upper() not in upper:
            continue
        comment = f" — {acc.table_comment}" if acc.table_comment else ""
        lines.append(f"  {acc.table_name}{comment}")
        for col in acc.columns:
            if col.column_name.upper() not in upper:
                continue
            type_str = f" ({col.data_type})" if col.data_type else ""
            col_comment = f" — {col.column_comment}" if col.column_comment else ""
            lines.append(f"    {col.column_name}{type_str}{col_comment}")
    if not lines:
        return ""
    return "Метаданные таблиц:\n" + "\n".join(lines) + "\n\n"


# ---------------------------------------------------------------------------
# Strategy functions
# ---------------------------------------------------------------------------

def _sql_strategy(node: DescriptionNode) -> tuple[str, str]:
    operation = _display_statement_type(node.statement_type)
    system = (
        "Ты аналитик PL/SQL кода Oracle. "
        "Опиши SQL-операцию кратко и точно на русском языке."
    )
    comment_block = _format_preceding_comment(node.preceding_comment)
    const_block = _format_dict_constants(node.dict_constants)
    if node.children:
        children_text = _format_children_descriptions(node)
        hint = _description_length_hint(len(node.children))
        context = _format_prompt_context("Локальный фрагмент", node.prompt_context)
        meta_block = _format_table_metadata(node.table_accesses, node.prompt_context)
        user = (
            f"{comment_block}"
            f"{const_block}"
            f"Операция: {operation}\n"
            f"{context}"
            f"{meta_block}"
            f"Вложенные элементы:\n{children_text}\n\n"
            f"Укажи какие таблицы затрагиваются, какие данные выбираются/изменяются, "
            f"ключевые условия WHERE/JOIN.\n{hint}"
        )
    else:
        meta_block = _format_table_metadata(node.table_accesses, node.source_text)
        user = (
            f"{comment_block}"
            f"{const_block}"
            f"Операция: {operation}\n"
            f"Код:\n```\n{node.source_text}\n```\n\n"
            f"{meta_block}"
            f"Укажи какие таблицы затрагиваются, какие данные выбираются/изменяются, "
            f"ключевые условия WHERE/JOIN.\nОпиши 1-2 предложениями."
        )
    return system, user


def _branching_strategy(node: DescriptionNode) -> tuple[str, str]:
    system = (
        "Ты аналитик PL/SQL кода Oracle. "
        "Опиши условную логику кратко и точно на русском языке."
    )
    comment_block = _format_preceding_comment(node.preceding_comment)
    const_block = _format_dict_constants(node.dict_constants)
    if node.children:
        children_text = _format_children_descriptions(node)
        hint = _description_length_hint(len(node.children))
        context = _format_prompt_context("Условие/заголовок", node.prompt_context)
        user = (
            f"{comment_block}"
            f"{const_block}"
            f"Конструкция: {node.statement_type}\n"
            f"{context}"
            f"Ветви:\n{children_text}\n\n"
            f"Опиши логику ветвления и её назначение.\n{hint}"
        )
    else:
        user = (
            f"{comment_block}"
            f"{const_block}"
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
    comment_block = _format_preceding_comment(node.preceding_comment)
    const_block = _format_dict_constants(node.dict_constants)
    if node.children:
        children_text = _format_children_descriptions(node)
        hint = _description_length_hint(len(node.children))
        context = _format_prompt_context("Заголовок", node.prompt_context)
        user = (
            f"{comment_block}"
            f"{const_block}"
            f"Цикл: {node.statement_type}\n"
            f"{context}"
            f"Тело цикла:\n{children_text}\n\n"
            f"Опиши что итерируется и зачем.\n{hint}"
        )
    else:
        user = (
            f"{comment_block}"
            f"{const_block}"
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
    comment_block = _format_preceding_comment(node.preceding_comment)
    const_block = _format_dict_constants(node.dict_constants)
    if node.children:
        children_text = _format_children_descriptions(node)
        hint = _description_length_hint(len(node.children))
        context = _format_prompt_context("Локальный фрагмент", node.prompt_context)
        user = (
            f"{comment_block}"
            f"{const_block}"
            f"Обработчик исключений:\n"
            f"{context}"
            f"Содержимое:\n{children_text}\n\n"
            f"Какие исключения перехватываются и как обрабатываются?\n{hint}"
        )
    else:
        user = (
            f"{comment_block}"
            f"{const_block}"
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
    comment_block = _format_preceding_comment(node.preceding_comment)
    const_block = _format_dict_constants(node.dict_constants)
    if node.children:
        children_text = _format_children_descriptions(node)
        n = len(node.children)
        if n >= 9:
            hint = (
                "Составь описание метода:\n"
                "— Назначение: одна фраза о цели метода\n"
                "— Основные шаги: 4-6 ключевых операций списком\n"
                "— Ключевые таблицы/объекты: перечисли с краткой ролью"
            )
        else:
            hint = _description_length_hint(n)
        user = (
            f"{comment_block}"
            f"{const_block}"
            f"Метод: {node.title}\n\n"
            f"Шаги метода:\n{children_text}\n\n"
            f"Опиши назначение метода.\n{hint}"
        )
    else:
        user = (
            f"{comment_block}"
            f"{const_block}"
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
    comment_block = _format_preceding_comment(node.preceding_comment)
    const_block = _format_dict_constants(node.dict_constants)
    if node.children:
        children_text = _format_children_descriptions(node)
        hint = _description_length_hint(len(node.children))
        context = _format_prompt_context("Локальный фрагмент", node.prompt_context)
        user = (
            f"{comment_block}"
            f"{const_block}"
            f"Тип: {node.statement_type}\n"
            f"{context}"
            f"Содержимое:\n{children_text}\n\n"
            f"{hint}"
        )
    else:
        user = (
            f"{comment_block}"
            f"{const_block}"
            f"Тип: {node.statement_type}\n"
            f"Код:\n```\n{node.source_text}\n```\n\n"
            f"Опиши кратко.\nОпиши 1-2 предложениями."
        )
    return system, user


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

_SQL_TYPES = {"SELECT", "INSERT", "UPDATE", "DELETE", "MERGE", "EXECUTE_IMMEDIATE"}
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

    st = _display_statement_type(node.statement_type)

    if st in _SQL_TYPES:
        return _sql_strategy(node)
    if st in _BRANCHING_TYPES:
        return _branching_strategy(node)
    if st in _LOOP_TYPES:
        return _loop_strategy(node)
    if st in _EXCEPTION_TYPES:
        return _exception_strategy(node)

    return _default_strategy(node)
