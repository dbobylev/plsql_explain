from __future__ import annotations

import logging
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
    - Results are cached in SQLite keyed by (schema, object, subprogram, source_hash, summary_kind).
    - Diamond deduplication: _cache prevents re-calling LLM for nodes already
      processed in this call (even if the tree expands them multiple times).
    - force=True bypasses the SQLite cache and always calls LLM.
    - summary_kind: "brief" (2-4 sentences) or "detailed" (full analysis).
    - use_substatements: when True and substatements exist, uses chunk-based analysis.
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

    # Post-order: summarize all children first (always as "brief")
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
                conn, child, client, force,
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

    # Check SQLite cache (unless force)
    current_hash = sqlite_store.get_source_hash(
        conn, node.schema_name, node.object_name, node.object_type or ""
    )
    _logger.debug(
        "Loaded source hash: node=%s, has_hash=%s",
        _node_ref(node),
        bool(current_hash),
    )
    if not force and current_hash:
        cached = sqlite_store.get_summary(
            conn, node.schema_name, node.object_name, node.object_type or "",
            node.subprogram, summary_kind,
        )
        if cached and cached[0] == current_hash:
            _logger.debug(
                "Summary cache hit: node=%s, summary_kind=%s, summary_length=%d",
                _node_ref(node),
                summary_kind,
                len(cached[1]),
            )
            return cached[1]
        _logger.debug(
            "Summary cache miss: node=%s, summary_kind=%s, cached_hash_matches=%s",
            _node_ref(node),
            summary_kind,
            bool(cached and cached[0] == current_hash),
        )

    # Try substatement-based analysis
    if use_substatements:
        summary = _try_substatement_path(
            conn, node, client, child_summaries, summary_kind, force,
        )
        if summary is not None:
            _logger.debug(
                "Substatement path produced summary: node=%s, summary_kind=%s, summary_length=%d",
                _node_ref(node),
                summary_kind,
                len(summary),
            )
            _persist_summary(conn, node, current_hash, summary, summary_kind)
            return summary

    # Classic path: full source fragment
    _logger.debug("Falling back to classic summary path: node=%s", _node_ref(node))
    summary = _classic_summarize(conn, node, client, child_summaries, summary_kind)
    _persist_summary(conn, node, current_hash, summary, summary_kind)
    _logger.debug(
        "summarize_node completed: node=%s, summary_kind=%s, summary_length=%d",
        _node_ref(node),
        summary_kind,
        len(summary),
    )
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
        _logger.debug("Substatement path skipped: node=%s, reason=no_substatements", _node_ref(node))
        return None

    total_length = total_source_length(roots)
    _logger.debug(
        "Loaded substatement tree: node=%s, roots=%d, total_source_length=%d, threshold=%d, force=%s",
        _node_ref(node),
        len(roots),
        total_length,
        _SUBSTATEMENT_THRESHOLD_CHARS,
        force,
    )
    if total_length < _SUBSTATEMENT_THRESHOLD_CHARS:
        _logger.debug(
            "Substatement path skipped: node=%s, reason=below_threshold, total_source_length=%d",
            _node_ref(node),
            total_length,
        )
        return None

    chunks = chunk_substatements(roots)
    if not chunks:
        _logger.debug("Substatement path skipped: node=%s, reason=no_chunks", _node_ref(node))
        return None

    _logger.debug("Prepared chunked analysis: node=%s, chunks=%d", _node_ref(node), len(chunks))

    # Analyze chunks sequentially with context flow.
    # Chunk cache uses hash-based invalidation (independent of force flag).
    # force only bypasses the final summary cache, not chunk analyses.
    context = ""
    chunk_analyses: list[str] = []
    invalidated = False

    for i, chunk in enumerate(chunks):
        c_hash = compute_chunk_hash(chunk)
        _logger.debug(
            "Processing chunk: node=%s, chunk_index=%d, roots_in_chunk=%d, chunk_hash=%s, invalidated=%s",
            _node_ref(node),
            i,
            len(chunk),
            c_hash,
            invalidated,
        )

        # Check chunk cache (hash-based, not affected by force)
        analysis: Optional[str] = None
        if not invalidated:
            cached = sqlite_store.get_chunk_analysis(
                conn, node.schema_name, node.object_name,
                node.object_type or "", node.subprogram, i,
            )
            if cached and cached[0] == c_hash:
                analysis = cached[1]
                _logger.debug(
                    "Chunk cache hit: node=%s, chunk_index=%d, analysis_length=%d",
                    _node_ref(node),
                    i,
                    len(analysis),
                )

        if analysis is None:
            _logger.debug(
                "Chunk cache miss: node=%s, chunk_index=%d, context_length=%d, child_summaries=%d",
                _node_ref(node),
                i,
                len(context),
                len(child_summaries),
            )
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
            _logger.debug(
                "Chunk analysis saved: node=%s, chunk_index=%d, analysis_length=%d",
                _node_ref(node),
                i,
                len(analysis),
            )

        chunk_analyses.append(analysis)
        context = analysis if len(chunk_analyses) == 1 else context + "\n\n" + analysis
        _logger.debug(
            "Chunk context updated: node=%s, chunk_index=%d, context_length=%d",
            _node_ref(node),
            i,
            len(context),
        )

    # Final aggregation
    if summary_kind == "detailed":
        system, user = prompts.build_detailed_aggregation_prompt(node, chunk_analyses)
    else:
        system, user = prompts.build_brief_aggregation_prompt(node, chunk_analyses)

    _logger.debug(
        "Running aggregation prompt: node=%s, summary_kind=%s, analyses=%d",
        _node_ref(node),
        summary_kind,
        len(chunk_analyses),
    )
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

    _logger.debug(
        "Classic summary prepared: node=%s, source_length=%d, fragment_length=%d, child_summaries=%d, summary_kind=%s",
        _node_ref(node),
        len(source_text),
        len(fragment),
        len(child_summaries),
        summary_kind,
    )

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
        _logger.debug(
            "Persisting summary: node=%s, summary_kind=%s, hash=%s, summary_length=%d",
            _node_ref(node),
            summary_kind,
            current_hash,
            len(summary),
        )
        sqlite_store.upsert_summary(
            conn, node.schema_name, node.object_name,
            node.object_type or "", node.subprogram,
            current_hash, summary, summary_kind,
        )
    else:
        _logger.debug(
            "Skipping summary persistence: node=%s, reason=no_source_hash",
            _node_ref(node),
        )
