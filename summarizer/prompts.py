from __future__ import annotations

from typing import Optional

from summarizer.substatements import AnalysisUnit, render_analysis_unit_source
from traversal.models import DependencyNode

PROMPT_VERSION = "2"

SYSTEM_PROMPT = (
    "Ты аналитик PL/SQL кода Oracle. "
    "Кратко и точно описывай на русском языке что делает переданный объект."
)

SYSTEM_PROMPT_ANALYSIS = (
    "Ты аналитик PL/SQL кода Oracle. "
    "Анализируй фрагмент как часть более крупного блока, "
    "сохраняй управляющий контекст и не теряй смысл ветвлений."
)

SYSTEM_PROMPT_AGGREGATION = (
    "Ты аналитик PL/SQL кода Oracle. "
    "Собирай итоговое описание из дочерних анализов, "
    "сохраняя порядок выполнения, ветвления и побочные эффекты."
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

    child_summaries keys: (object_name, subprogram) -> summary_text
    """
    parts: list[str] = []

    label = _node_label(node)
    obj_type = node.object_type or "UNKNOWN"
    parts.append(f"Объект: {label} ({obj_type})\n")

    parts.append("Исходный код:")
    parts.append("```plsql")
    parts.append(source_fragment.strip())
    parts.append("```")
    parts.append("")

    _append_table_accesses(parts, node)
    _append_child_summaries(parts, child_summaries)

    parts.append("Напиши краткое описание (2-4 предложения) что делает данный объект.")

    return SYSTEM_PROMPT, "\n".join(parts)


def build_analysis_unit_prompt(
    node: DependencyNode,
    unit: AnalysisUnit,
    child_summaries: dict[tuple[str, Optional[str]], str],
) -> tuple[str, str]:
    """Build prompt for a leaf code chunk produced by the recursive planner."""
    parts: list[str] = []

    label = _node_label(node)
    obj_type = node.object_type or "UNKNOWN"
    parts.append(f"Объект: {label} ({obj_type})\n")
    parts.append(f"Область анализа: {unit.title}")
    if len(unit.path) > 1:
        parts.append(f"Путь: {_format_unit_path(unit)}")
    if unit.parts_total > 1:
        parts.append(f"Часть внутри области: {unit.part_no} из {unit.parts_total}")
    parts.append("")

    if unit.header_context:
        parts.append("Структурный контекст:")
        parts.append("```plsql")
        parts.append("\n".join(unit.header_context))
        parts.append("```")
        parts.append("")

    if unit.oversized:
        parts.append(
            "Примечание: этот фрагмент превышает целевой размер, "
            "но ниже уже не делится без потери структуры."
        )
        parts.append("")

    parts.append("Текущий фрагмент кода:")
    parts.append("```plsql")
    parts.append(render_analysis_unit_source(unit))
    parts.append("```")
    parts.append("")

    _append_table_accesses(parts, node)
    _append_child_summaries(parts, child_summaries)

    parts.append(
        "Проанализируй этот фрагмент как самостоятельную часть логики. Укажи:\n"
        "1. Какие данные и переменные здесь ключевые\n"
        "2. Что делает этот фрагмент пошагово\n"
        "3. Какие условия, ветвления или циклы здесь важны\n"
        "4. Какие обращения к данным и побочные эффекты присутствуют\n"
        "5. Какие результаты этот фрагмент готовит для следующего кода"
    )

    return SYSTEM_PROMPT_ANALYSIS, "\n".join(parts)


def build_aggregate_unit_prompt(
    node: DependencyNode,
    unit: AnalysisUnit,
    child_analyses: list[tuple[AnalysisUnit, str]],
    child_summaries: dict[tuple[str, Optional[str]], str],
    summary_kind: str,
) -> tuple[str, str]:
    """Build prompt for aggregating child analyses into a higher-level summary."""
    parts: list[str] = []

    label = _node_label(node)
    obj_type = node.object_type or "UNKNOWN"
    parts.append(f"Объект: {label} ({obj_type})\n")
    parts.append(f"Область агрегации: {unit.title}")
    if len(unit.path) > 1:
        parts.append(f"Путь: {_format_unit_path(unit)}")
    parts.append("")

    if unit.header_context and unit.unit_kind != "method":
        parts.append("Контекст блока:")
        parts.append("```plsql")
        parts.append("\n".join(unit.header_context))
        parts.append("```")
        parts.append("")

    parts.append("Дочерние анализы в порядке выполнения:")
    for child, analysis in child_analyses:
        parts.append(f"\n--- {child.title} ---")
        parts.append(analysis)
    parts.append("")

    if unit.unit_kind == "method":
        _append_child_summaries(parts, child_summaries)
        if summary_kind == "detailed":
            parts.append(
                "Составь подробное описание объекта:\n"
                "- Входные параметры и их назначение\n"
                "- Последовательность действий с описанием каждого ключевого блока\n"
                "- Ключевые условия, ветвления и циклы\n"
                "- Обращения к таблицам и операции\n"
                "- Обработка исключений\n"
                "- Возвращаемые значения и побочные эффекты"
            )
            return SYSTEM_PROMPT_DETAILED, "\n".join(parts)

        parts.append("Напиши краткое описание (2-4 предложения) что делает данный объект.")
        return SYSTEM_PROMPT, "\n".join(parts)

    parts.append(_aggregate_instruction(unit))
    return SYSTEM_PROMPT_AGGREGATION, "\n".join(parts)


def _node_label(node: DependencyNode) -> str:
    if node.subprogram:
        return f"{node.schema_name}.{node.object_name}.{node.subprogram}"
    return f"{node.schema_name}.{node.object_name}"


def _format_unit_path(unit: AnalysisUnit) -> str:
    return " -> ".join(unit.path[1:]) if len(unit.path) > 1 else unit.title


def _append_table_accesses(parts: list[str], node: DependencyNode) -> None:
    if not node.table_accesses:
        return
    parts.append("Обращения к таблицам:")
    for ta in node.table_accesses:
        schema_prefix = f"{ta.table_schema}." if ta.table_schema else ""
        parts.append(f"- {schema_prefix}{ta.table_name} ({ta.operation})")
    parts.append("")


def _append_child_summaries(
    parts: list[str],
    child_summaries: dict[tuple[str, Optional[str]], str],
) -> None:
    if not child_summaries:
        return
    parts.append("Вызываемые объекты и их описания:")
    for (obj_name, sub), text in child_summaries.items():
        ref = f"{obj_name}.{sub}" if sub else obj_name
        parts.append(f"- {ref}: {text}")
    parts.append("")


def _aggregate_instruction(unit: AnalysisUnit) -> str:
    if unit.unit_kind == "branch":
        return (
            "Собери целостное описание этой ветки. "
            "Сохрани порядок шагов, ключевые условия, обращения к данным и итоговый эффект ветки."
        )

    if unit.statement_type == "IF":
        return (
            "Собери описание всего IF-блока. "
            "Объясни, какие ветви существуют, по каким условиям они выбираются "
            "и чем отличаются их действия."
        )

    if unit.statement_type in {"LOOP_BASIC", "LOOP_FOR", "LOOP_WHILE"}:
        return (
            "Собери описание этого цикла. "
            "Укажи механизм итерации, действия тела, внутренние ветвления и побочные эффекты."
        )

    if unit.statement_type == "CASE":
        return (
            "Собери описание этого CASE-блока. "
            "Укажи, какие варианты обрабатываются и чем отличаются ветви."
        )

    if unit.statement_type == "BEGIN_END":
        return (
            "Собери описание этого BEGIN-блока. "
            "Сохрани порядок выполнения дочерних шагов и их роль в общей логике."
        )

    return (
        "Собери цельное описание этого блока из дочерних анализов. "
        "Сохрани порядок действий, условия, обращения к данным и важные побочные эффекты."
    )
