from __future__ import annotations

import sqlite3
from typing import Optional

from summarizer import extractor, prompts, sqlite_store
from summarizer.llm_client import LlmClient
from traversal.models import DependencyNode

# Statuses that have no source code — return a stub without calling the LLM.
_STUB_STATUSES = {"missing", "cycle", "wrapped", "error", "unindexed"}


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
    _cache: Optional[dict[tuple[str, Optional[str]], str]] = None,
) -> str:
    """
    Summarize a DependencyNode tree bottom-up (post-order DFS).

    - Leaf nodes are summarized first.
    - Parent nodes receive child summaries in their prompt.
    - Results are cached in SQLite keyed by (schema, object, subprogram, source_hash).
    - Diamond deduplication: _cache prevents re-calling LLM for nodes already
      processed in this call (even if the tree expands them multiple times).
    - force=True bypasses the SQLite cache and always calls LLM.
    """
    if _cache is None:
        _cache = {}

    if node.status in _STUB_STATUSES:
        return _stub_summary(node)

    # Post-order: summarize all children first
    child_summaries: dict[tuple[str, Optional[str]], str] = {}
    for child in node.children:
        key = (child.object_name, child.subprogram)
        if key not in _cache:
            _cache[key] = summarize_node(conn, child, client, force, _cache)
        child_summaries[key] = _cache[key]

    # Check SQLite cache (unless force)
    current_hash = sqlite_store.get_source_hash(
        conn, node.schema_name, node.object_name, node.object_type or ""
    )
    if not force and current_hash:
        cached = sqlite_store.get_summary(
            conn, node.schema_name, node.object_name, node.object_type or "", node.subprogram
        )
        if cached and cached[0] == current_hash:
            return cached[1]

    # Extract the relevant source fragment
    source_text = sqlite_store.get_source_text(conn, node.schema_name, node.object_name) or ""
    if node.subprogram:
        fragment = extractor.extract_subprogram(source_text, node.subprogram)
    else:
        fragment = source_text

    # Build prompt and call LLM
    system, user = prompts.build_prompt(node, fragment, child_summaries)
    summary = client.complete(system, user)

    # Persist to cache
    if current_hash:
        sqlite_store.upsert_summary(
            conn,
            node.schema_name,
            node.object_name,
            node.object_type or "",
            node.subprogram,
            current_hash,
            summary,
        )

    return summary
