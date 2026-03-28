from __future__ import annotations

import sqlite3
from typing import Optional

from summarizer import extractor, prompts, sqlite_store
from summarizer.llm_client import LlmClient
from summarizer.substatements import (
    chunk_substatements,
    compute_chunk_hash,
    load_substatement_tree,
    total_source_length,
)
from traversal.models import DependencyNode

# Statuses that have no source code — return a stub without calling the LLM.
_STUB_STATUSES = {"missing", "cycle", "wrapped", "error", "unindexed"}

# Methods with total source below this threshold use the classic single-call path.
_SUBSTATEMENT_THRESHOLD_CHARS = 4000


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
    - Results are cached in SQLite keyed by (schema, object, subprogram, source_hash, summary_kind).
    - Diamond deduplication: _cache prevents re-calling LLM for nodes already
      processed in this call (even if the tree expands them multiple times).
    - force=True bypasses the SQLite cache and always calls LLM.
    - summary_kind: "brief" (2-4 sentences) or "detailed" (full analysis).
    - use_substatements: when True and substatements exist, uses chunk-based analysis.
    """
    if _cache is None:
        _cache = {}

    if node.status in _STUB_STATUSES:
        return _stub_summary(node)

    # Post-order: summarize all children first (always as "brief")
    child_summaries: dict[tuple[str, Optional[str]], str] = {}
    for child in node.children:
        key = (child.object_name, child.subprogram)
        if key not in _cache:
            _cache[key] = summarize_node(
                conn, child, client, force,
                summary_kind="brief",
                use_substatements=use_substatements,
                _cache=_cache,
            )
        child_summaries[key] = _cache[key]

    # Check SQLite cache (unless force)
    current_hash = sqlite_store.get_source_hash(
        conn, node.schema_name, node.object_name, node.object_type or ""
    )
    if not force and current_hash:
        cached = sqlite_store.get_summary(
            conn, node.schema_name, node.object_name, node.object_type or "",
            node.subprogram, summary_kind,
        )
        if cached and cached[0] == current_hash:
            return cached[1]

    # Try substatement-based analysis
    if use_substatements:
        summary = _try_substatement_path(
            conn, node, client, child_summaries, summary_kind, force,
        )
        if summary is not None:
            _persist_summary(conn, node, current_hash, summary, summary_kind)
            return summary

    # Classic path: full source fragment
    summary = _classic_summarize(conn, node, client, child_summaries, summary_kind)
    _persist_summary(conn, node, current_hash, summary, summary_kind)
    return summary


def _try_substatement_path(
    conn: sqlite3.Connection,
    node: DependencyNode,
    client: LlmClient,
    child_summaries: dict[tuple[str, Optional[str]], str],
    summary_kind: str,
    force: bool,
) -> Optional[str]:
    """
    Attempt substatement-based analysis. Returns None if not applicable
    (no substatements or below threshold), forcing fallback to classic path.
    """
    roots = load_substatement_tree(
        conn, node.schema_name, node.object_name,
        node.object_type or "", node.subprogram,
    )
    if not roots:
        return None

    if total_source_length(roots) < _SUBSTATEMENT_THRESHOLD_CHARS:
        return None

    chunks = chunk_substatements(roots)
    if not chunks:
        return None

    # Analyze chunks sequentially with context flow.
    # Chunk cache uses hash-based invalidation (independent of force flag).
    # force only bypasses the final summary cache, not chunk analyses.
    context = ""
    chunk_analyses: list[str] = []
    invalidated = False

    for i, chunk in enumerate(chunks):
        c_hash = compute_chunk_hash(chunk)

        # Check chunk cache (hash-based, not affected by force)
        analysis: Optional[str] = None
        if not invalidated:
            cached = sqlite_store.get_chunk_analysis(
                conn, node.schema_name, node.object_name,
                node.object_type or "", node.subprogram, i,
            )
            if cached and cached[0] == c_hash:
                analysis = cached[1]

        if analysis is None:
            system, user = prompts.build_chunk_prompt(
                node, chunk, context, child_summaries,
            )
            analysis = client.complete(system, user)
            sqlite_store.upsert_chunk_analysis(
                conn, node.schema_name, node.object_name,
                node.object_type or "", node.subprogram,
                i, c_hash, analysis,
            )
            invalidated = True  # subsequent chunks depend on this context

        chunk_analyses.append(analysis)
        context = analysis if len(chunk_analyses) == 1 else context + "\n\n" + analysis

    # Final aggregation
    if summary_kind == "detailed":
        system, user = prompts.build_detailed_aggregation_prompt(node, chunk_analyses)
    else:
        system, user = prompts.build_brief_aggregation_prompt(node, chunk_analyses)

    return client.complete(system, user)


def _classic_summarize(
    conn: sqlite3.Connection,
    node: DependencyNode,
    client: LlmClient,
    child_summaries: dict[tuple[str, Optional[str]], str],
    summary_kind: str,
) -> str:
    """Classic single-call summarization using full source fragment."""
    source_text = sqlite_store.get_source_text(conn, node.schema_name, node.object_name) or ""
    if node.subprogram:
        fragment = extractor.extract_subprogram(source_text, node.subprogram)
    else:
        fragment = source_text

    if summary_kind == "detailed":
        # For detailed mode without substatements, use a richer prompt
        system, user = prompts.build_prompt(node, fragment, child_summaries)
        # Replace the final instruction with detailed request
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


def _persist_summary(
    conn: sqlite3.Connection,
    node: DependencyNode,
    current_hash: Optional[str],
    summary: str,
    summary_kind: str,
) -> None:
    """Persist summary to SQLite cache if hash is available."""
    if current_hash:
        sqlite_store.upsert_summary(
            conn, node.schema_name, node.object_name,
            node.object_type or "", node.subprogram,
            current_hash, summary, summary_kind,
        )
