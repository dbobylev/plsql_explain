from __future__ import annotations

from typing import Optional

from traversal.models import DependencyNode

SYSTEM_PROMPT = (
    "Ты аналитик PL/SQL кода Oracle. "
    "Кратко и точно описывай на русском языке что делает переданный объект."
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
