from __future__ import annotations

from typing import Optional

from summarizer.substatements import SubstatementNode
from traversal.models import DependencyNode

SYSTEM_PROMPT = (
    "Ты аналитик PL/SQL кода Oracle. "
    "Кратко и точно описывай на русском языке что делает переданный объект."
)

SYSTEM_PROMPT_CHUNK = (
    "Ты аналитик PL/SQL кода Oracle. "
    "Анализируй фрагменты кода последовательно, "
    "отслеживая ключевые переменные и состояние."
)

SYSTEM_PROMPT_DETAILED = (
    "Ты аналитик PL/SQL кода Oracle. "
    "Составь подробное описание объекта, "
    "сохраняя детали управляющих конструкций и бизнес-логики."
)


def build_prompt(
    node: DependencyNode,
    source_fragment: str,
    child_summaries: dict[tuple[str, Optional[str]], str],
) -> tuple[str, str]:
    """
    Build (system_prompt, user_prompt) for the given node.

    child_summaries keys: (object_name, subprogram) → summary_text
    """
    parts: list[str] = []

    # Header
    if node.subprogram:
        label = f"{node.schema_name}.{node.object_name}.{node.subprogram}"
    else:
        label = f"{node.schema_name}.{node.object_name}"
    obj_type = node.object_type or "UNKNOWN"
    parts.append(f"Объект: {label} ({obj_type})\n")

    # Source code
    parts.append("Исходный код:")
    parts.append("```plsql")
    parts.append(source_fragment.strip())
    parts.append("```")
    parts.append("")

    # Table accesses
    if node.table_accesses:
        parts.append("Обращения к таблицам:")
        for ta in node.table_accesses:
            schema_prefix = f"{ta.table_schema}." if ta.table_schema else ""
            parts.append(f"- {schema_prefix}{ta.table_name} ({ta.operation})")
        parts.append("")

    # Child summaries
    if child_summaries:
        parts.append("Вызываемые объекты и их описания:")
        for (obj_name, sub), text in child_summaries.items():
            if sub:
                ref = f"{obj_name}.{sub}"
            else:
                ref = obj_name
            parts.append(f"- {ref}: {text}")
        parts.append("")

    parts.append("Напиши краткое описание (2–4 предложения) что делает данный объект.")

    return SYSTEM_PROMPT, "\n".join(parts)


def _node_label(node: DependencyNode) -> str:
    if node.subprogram:
        return f"{node.schema_name}.{node.object_name}.{node.subprogram}"
    return f"{node.schema_name}.{node.object_name}"


def _format_chunk_source(chunk: list[SubstatementNode]) -> str:
    """Format chunk substatements with type annotations for the prompt."""
    lines: list[str] = []
    for root in chunk:
        lines.append(f"-- [{root.statement_type}] (lines {root.start_line}-{root.end_line})")
        lines.append(root.source_text.strip())
        lines.append("")
    return "\n".join(lines).rstrip()


def build_chunk_prompt(
    node: DependencyNode,
    chunk: list[SubstatementNode],
    previous_context: str,
    child_summaries: dict[tuple[str, Optional[str]], str],
) -> tuple[str, str]:
    """Build prompt for analyzing a single chunk of substatements."""
    parts: list[str] = []

    label = _node_label(node)
    obj_type = node.object_type or "UNKNOWN"
    parts.append(f"Объект: {label} ({obj_type})\n")

    if previous_context:
        parts.append("Контекст из предыдущих фрагментов:")
        parts.append(previous_context)
        parts.append("")

    parts.append("Текущий фрагмент:")
    parts.append("```plsql")
    parts.append(_format_chunk_source(chunk))
    parts.append("```")
    parts.append("")

    if node.table_accesses:
        parts.append("Обращения к таблицам:")
        for ta in node.table_accesses:
            schema_prefix = f"{ta.table_schema}." if ta.table_schema else ""
            parts.append(f"- {schema_prefix}{ta.table_name} ({ta.operation})")
        parts.append("")

    if child_summaries:
        parts.append("Вызываемые объекты и их описания:")
        for (obj_name, sub), text in child_summaries.items():
            ref = f"{obj_name}.{sub}" if sub else obj_name
            parts.append(f"- {ref}: {text}")
        parts.append("")

    parts.append(
        "Проанализируй этот фрагмент. Укажи:\n"
        "1. Ключевые переменные и их назначение\n"
        "2. Что делает этот фрагмент\n"
        "3. Важные условия и ветвления\n"
        "4. Обращения к данным"
    )

    return SYSTEM_PROMPT_CHUNK, "\n".join(parts)


def build_brief_aggregation_prompt(
    node: DependencyNode,
    chunk_analyses: list[str],
) -> tuple[str, str]:
    """Build prompt for aggregating chunk analyses into a brief summary."""
    parts: list[str] = []

    label = _node_label(node)
    obj_type = node.object_type or "UNKNOWN"
    parts.append(f"Объект: {label} ({obj_type})\n")

    parts.append("Полный анализ по фрагментам:")
    for i, analysis in enumerate(chunk_analyses, 1):
        parts.append(f"\n--- Фрагмент {i} ---")
        parts.append(analysis)
    parts.append("")

    parts.append("Напиши краткое описание (2–4 предложения) что делает данный объект.")

    return SYSTEM_PROMPT, "\n".join(parts)


def build_detailed_aggregation_prompt(
    node: DependencyNode,
    chunk_analyses: list[str],
) -> tuple[str, str]:
    """Build prompt for aggregating chunk analyses into a detailed summary."""
    parts: list[str] = []

    label = _node_label(node)
    obj_type = node.object_type or "UNKNOWN"
    parts.append(f"Объект: {label} ({obj_type})\n")

    parts.append("Полный анализ по фрагментам:")
    for i, analysis in enumerate(chunk_analyses, 1):
        parts.append(f"\n--- Фрагмент {i} ---")
        parts.append(analysis)
    parts.append("")

    parts.append(
        "Составь подробное описание объекта:\n"
        "- Входные параметры и их назначение\n"
        "- Последовательность действий с описанием каждого блока\n"
        "- Ключевые условия и ветвления\n"
        "- Обращения к таблицам и операции\n"
        "- Обработка исключений\n"
        "- Возвращаемые значения (если есть)"
    )

    return SYSTEM_PROMPT_DETAILED, "\n".join(parts)
