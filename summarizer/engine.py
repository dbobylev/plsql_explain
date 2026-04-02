from __future__ import annotations

import hashlib
import logging
import sqlite3
from typing import Optional

from summarizer import extractor, prompts, sqlite_store
from summarizer.llm_client import LlmClient
from summarizer.substatements import (
    AnalysisUnit,
    PLANNER_VERSION,
    iter_code_units,
    load_substatement_tree,
    plan_substatement_analysis,
    total_source_length,
)
from traversal.models import DependencyNode

_STUB_STATUSES = {"missing", "cycle", "wrapped", "error", "unindexed"}
_SUBSTATEMENT_THRESHOLD_CHARS = 4000
_INTERMEDIATE_ANALYSIS_KIND = "analysis"
_CLASSIC_SUMMARY_VERSION = f"classic-p{prompts.PROMPT_VERSION}"
_SUBSTATEMENT_SUMMARY_VERSION = f"substatements-v{PLANNER_VERSION}-p{prompts.PROMPT_VERSION}"
_logger = logging.getLogger(__name__)


def _node_ref(node: DependencyNode) -> str:
    if node.subprogram:
        return f"{node.schema_name}.{node.object_name}.{node.subprogram}"
    return f"{node.schema_name}.{node.object_name}"


def _stub_summary(node: DependencyNode) -> str:
    if node.subprogram:
        ref = f"{node.object_name}.{node.subprogram}"
    else:
        ref = node.object_name
    return f"[{node.status}] {ref}"


def summarize_node(
    conn: sqlite3.Connection,
    node: DependencyNode,
    client: LlmClient,
    force: bool = False,
    summary_kind: str = "brief",
    use_substatements: bool = True,
    _cache: Optional[dict[tuple[str, Optional[str]], str]] = None,
) -> str:
    """
    Summarize a DependencyNode tree bottom-up (post-order DFS).

    - Leaf nodes are summarized first.
    - Parent nodes receive child summaries in their prompt.
    - Results are cached in SQLite keyed by (schema, object, subprogram, summary_kind).
    - Diamond deduplication: _cache prevents re-calling LLM for nodes already
      processed in this call (even if the tree expands them multiple times).
    - force=True bypasses the final summary cache but still reuses subtree caches.
    - summary_kind: "brief" (2-4 sentences) or "detailed" (full analysis).
    - use_substatements: when True and substatements exist, uses recursive tree-based analysis.
    """
    if _cache is None:
        _cache = {}

    _logger.debug(
        "summarize_node started: node=%s, status=%s, children=%d, force=%s, summary_kind=%s, use_substatements=%s, cache_entries=%d",
        _node_ref(node),
        node.status,
        len(node.children),
        force,
        summary_kind,
        use_substatements,
        len(_cache),
    )

    if node.status in _STUB_STATUSES:
        _logger.debug("Returning stub summary: node=%s, status=%s", _node_ref(node), node.status)
        return _stub_summary(node)

    child_summaries: dict[tuple[str, Optional[str]], str] = {}
    for child in node.children:
        key = (child.object_name, child.subprogram)
        if key not in _cache:
            _logger.debug(
                "Summarizing child node: parent=%s, child=%s",
                _node_ref(node),
                _node_ref(child),
            )
            _cache[key] = summarize_node(
                conn,
                child,
                client,
                force,
                summary_kind="brief",
                use_substatements=use_substatements,
                _cache=_cache,
            )
        else:
            _logger.debug(
                "Reusing in-memory child summary cache: parent=%s, child=%s",
                _node_ref(node),
                _node_ref(child),
            )
        child_summaries[key] = _cache[key]

    current_hash = sqlite_store.get_source_hash(
        conn, node.schema_name, node.object_name, node.object_type or ""
    )
    child_context_hash = _child_context_hash(child_summaries)
    analysis_plan = _prepare_substatement_plan(conn, node) if use_substatements else None
    pipeline_mode = "substatements" if analysis_plan is not None else "classic"
    summary_cache_hash = (
        _summary_cache_hash(current_hash, summary_kind, pipeline_mode, child_context_hash)
        if current_hash
        else None
    )

    _logger.debug(
        "Loaded source hash: node=%s, has_hash=%s, pipeline_mode=%s, child_context_hash=%s",
        _node_ref(node),
        bool(current_hash),
        pipeline_mode,
        child_context_hash,
    )

    if not force and summary_cache_hash:
        cached = sqlite_store.get_summary(
            conn,
            node.schema_name,
            node.object_name,
            node.object_type or "",
            node.subprogram,
            summary_kind,
        )
        if cached and cached[0] == summary_cache_hash:
            _logger.debug(
                "Summary cache hit: node=%s, summary_kind=%s, pipeline_mode=%s, summary_length=%d",
                _node_ref(node),
                summary_kind,
                pipeline_mode,
                len(cached[1]),
            )
            return cached[1]
        _logger.debug(
            "Summary cache miss: node=%s, summary_kind=%s, pipeline_mode=%s, cached_hash_matches=%s",
            _node_ref(node),
            summary_kind,
            pipeline_mode,
            bool(cached and cached[0] == summary_cache_hash),
        )

    if analysis_plan is not None:
        summary = _run_substatement_plan(
            conn,
            node,
            client,
            analysis_plan,
            child_summaries,
            summary_kind,
            child_context_hash,
        )
        _logger.debug(
            "Recursive substatement path produced summary: node=%s, summary_kind=%s, summary_length=%d",
            _node_ref(node),
            summary_kind,
            len(summary),
        )
    else:
        _logger.debug("Falling back to classic summary path: node=%s", _node_ref(node))
        summary = _classic_summarize(conn, node, client, child_summaries, summary_kind)

    _persist_summary(conn, node, summary_cache_hash, summary, summary_kind)
    _logger.debug(
        "summarize_node completed: node=%s, summary_kind=%s, summary_length=%d",
        _node_ref(node),
        summary_kind,
        len(summary),
    )
    return summary


def _prepare_substatement_plan(
    conn: sqlite3.Connection,
    node: DependencyNode,
) -> Optional[AnalysisUnit]:
    roots = load_substatement_tree(
        conn,
        node.schema_name,
        node.object_name,
        node.object_type or "",
        node.subprogram,
    )
    if not roots:
        _logger.debug("Substatement path skipped: node=%s, reason=no_substatements", _node_ref(node))
        return None

    total_length = total_source_length(roots)
    _logger.debug(
        "Loaded substatement tree: node=%s, roots=%d, total_source_length=%d, threshold=%d",
        _node_ref(node),
        len(roots),
        total_length,
        _SUBSTATEMENT_THRESHOLD_CHARS,
    )
    if total_length < _SUBSTATEMENT_THRESHOLD_CHARS:
        _logger.debug(
            "Substatement path skipped: node=%s, reason=below_threshold, total_source_length=%d",
            _node_ref(node),
            total_length,
        )
        return None

    plan = plan_substatement_analysis(roots)
    _logger.debug(
        "Prepared recursive analysis plan: node=%s, code_units=%d, root_children=%d",
        _node_ref(node),
        len(iter_code_units(plan)),
        len(plan.children),
    )
    return plan


def _run_substatement_plan(
    conn: sqlite3.Connection,
    node: DependencyNode,
    client: LlmClient,
    plan: AnalysisUnit,
    child_summaries: dict[tuple[str, Optional[str]], str],
    summary_kind: str,
    child_context_hash: str,
) -> str:
    return _analyze_unit(
        conn,
        node,
        client,
        plan,
        child_summaries,
        summary_kind,
        child_context_hash,
    )


def _analyze_unit(
    conn: sqlite3.Connection,
    node: DependencyNode,
    client: LlmClient,
    unit: AnalysisUnit,
    child_summaries: dict[tuple[str, Optional[str]], str],
    summary_kind: str,
    child_context_hash: str,
) -> str:
    effective_unit_hash = _effective_analysis_hash(unit.unit_hash, child_context_hash)

    if unit.unit_kind != "method":
        cached = sqlite_store.get_analysis_cache(
            conn,
            node.schema_name,
            node.object_name,
            node.object_type or "",
            node.subprogram,
            unit.unit_key,
            unit.unit_kind,
            _INTERMEDIATE_ANALYSIS_KIND,
            PLANNER_VERSION,
            prompts.PROMPT_VERSION,
        )
        if cached and cached[0] == effective_unit_hash:
            _logger.debug(
                "Analysis cache hit: node=%s, unit_key=%s, unit_kind=%s",
                _node_ref(node),
                unit.unit_key,
                unit.unit_kind,
            )
            return cached[1]

    if unit.unit_kind == "code_chunk":
        _logger.debug(
            "Analyzing leaf unit: node=%s, unit_key=%s, estimated_chars=%d, oversized=%s",
            _node_ref(node),
            unit.unit_key,
            unit.estimated_chars,
            unit.oversized,
        )
        system, user = prompts.build_analysis_unit_prompt(node, unit, child_summaries)
        analysis = client.complete(system, user)
    else:
        _logger.debug(
            "Aggregating unit: node=%s, unit_key=%s, unit_kind=%s, children=%d",
            _node_ref(node),
            unit.unit_key,
            unit.unit_kind,
            len(unit.children),
        )
        child_analyses = [
            (
                child,
                _analyze_unit(
                    conn,
                    node,
                    client,
                    child,
                    child_summaries,
                    summary_kind,
                    child_context_hash,
                ),
            )
            for child in unit.children
        ]
        aggregate_child_summaries = child_summaries if unit.unit_kind == "method" else {}
        reference_text = _load_node_source_fragment(conn, node) if unit.unit_kind == "method" else None
        system, user = prompts.build_aggregate_unit_prompt(
            node,
            unit,
            child_analyses,
            aggregate_child_summaries,
            summary_kind,
            reference_text=reference_text,
        )
        analysis = client.complete(system, user)

    if unit.unit_kind != "method":
        sqlite_store.upsert_analysis_cache(
            conn,
            node.schema_name,
            node.object_name,
            node.object_type or "",
            node.subprogram,
            unit.unit_key,
            unit.unit_kind,
            _INTERMEDIATE_ANALYSIS_KIND,
            PLANNER_VERSION,
            prompts.PROMPT_VERSION,
            effective_unit_hash,
            analysis,
        )
        _logger.debug(
            "Analysis cache saved: node=%s, unit_key=%s, unit_kind=%s",
            _node_ref(node),
            unit.unit_key,
            unit.unit_kind,
        )

    return analysis


def _classic_summarize(
    conn: sqlite3.Connection,
    node: DependencyNode,
    client: LlmClient,
    child_summaries: dict[tuple[str, Optional[str]], str],
    summary_kind: str,
) -> str:
    source_text = sqlite_store.get_source_text(
        conn,
        node.schema_name,
        node.object_name,
        node.object_type,
    ) or ""
    fragment = _extract_source_fragment(source_text, node.subprogram)

    _logger.debug(
        "Classic summary prepared: node=%s, source_length=%d, fragment_length=%d, child_summaries=%d, summary_kind=%s",
        _node_ref(node),
        len(source_text),
        len(fragment),
        len(child_summaries),
        summary_kind,
    )

    if summary_kind == "detailed":
        system, user = prompts.build_prompt(node, fragment, child_summaries)
        user = user.rsplit("Напиши краткое описание", 1)[0]
        user += (
            "Составь подробное описание объекта:\n"
            "- Входные параметры и их назначение\n"
            "- Последовательность действий с описанием каждого блока\n"
            "- Ключевые условия и ветвления\n"
            "- Обращения к таблицам и операции\n"
            "- Обработка исключений\n"
            "- Возвращаемые значения (если есть)"
        )
        system = prompts.SYSTEM_PROMPT_DETAILED
    else:
        system, user = prompts.build_prompt(node, fragment, child_summaries)

    return client.complete(system, user)


def _load_node_source_fragment(
    conn: sqlite3.Connection,
    node: DependencyNode,
) -> str:
    source_text = sqlite_store.get_source_text(
        conn,
        node.schema_name,
        node.object_name,
        node.object_type,
    ) or ""
    return _extract_source_fragment(source_text, node.subprogram)


def _extract_source_fragment(source_text: str, subprogram: Optional[str]) -> str:
    if subprogram:
        return extractor.extract_subprogram(source_text, subprogram)
    return source_text


def _summary_cache_hash(
    source_hash: str,
    summary_kind: str,
    pipeline_mode: str,
    child_context_hash: str,
) -> str:
    version = (
        _SUBSTATEMENT_SUMMARY_VERSION if pipeline_mode == "substatements" else _CLASSIC_SUMMARY_VERSION
    )
    payload = f"{source_hash}|{summary_kind}|{pipeline_mode}|{version}|{child_context_hash}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _effective_analysis_hash(unit_hash: str, child_context_hash: str) -> str:
    return hashlib.sha256(f"{unit_hash}|{child_context_hash}".encode()).hexdigest()


def _child_context_hash(child_summaries: dict[tuple[str, Optional[str]], str]) -> str:
    if not child_summaries:
        return ""
    items = []
    for (obj_name, sub), text in sorted(child_summaries.items()):
        items.append(f"{obj_name}|{sub or ''}|{text}")
    return hashlib.sha256("||".join(items).encode()).hexdigest()


def _persist_summary(
    conn: sqlite3.Connection,
    node: DependencyNode,
    summary_cache_hash: Optional[str],
    summary: str,
    summary_kind: str,
) -> None:
    if summary_cache_hash:
        _logger.debug(
            "Persisting summary: node=%s, summary_kind=%s, hash=%s, summary_length=%d",
            _node_ref(node),
            summary_kind,
            summary_cache_hash,
            len(summary),
        )
        sqlite_store.upsert_summary(
            conn,
            node.schema_name,
            node.object_name,
            node.object_type or "",
            node.subprogram,
            summary_cache_hash,
            summary,
            summary_kind,
        )
    else:
        _logger.debug(
            "Skipping summary persistence: node=%s, reason=no_source_hash",
            _node_ref(node),
        )
