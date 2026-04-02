from __future__ import annotations

import logging
import sqlite3
from typing import Optional

from summarizer.description_tree import DescriptionNode, build_description_tree
from summarizer.llm_client import LlmClient
from summarizer.tree_prompts import PROMPT_VERSION, build_prompt
from summarizer.tree_store import (
    get_cached_description,
    save_tree,
)
from traversal.graph import build_tree
from traversal.models import DependencyNode

_logger = logging.getLogger(__name__)


def describe_tree(
    conn: sqlite3.Connection,
    schema: str,
    object_name: str,
    subprogram: Optional[str],
    client: LlmClient,
    force: bool = False,
    max_depth: Optional[int] = None,
) -> DescriptionNode:
    """
    Main entry point: build description tree and populate LLM descriptions.

    1. Build DependencyNode tree (for call edges and cycle detection)
    2. Build DescriptionNode tree (substatements + inlined callees)
    3. Populate descriptions bottom-up via LLM
    4. Save tree to SQLite
    """
    dep_node = build_tree(conn, schema, object_name, subprogram, max_depth=max_depth)
    obj_type = dep_node.object_type or ""

    _logger.info(
        "Построение дерева описаний: %s.%s%s",
        schema,
        object_name,
        f".{subprogram}" if subprogram else "",
    )

    tree = build_description_tree(conn, dep_node, max_depth=max_depth)

    _logger.info("Дерево построено, генерация описаний...")

    _populate_descriptions(conn, tree, client, schema, object_name, obj_type, subprogram, force)

    save_tree(conn, schema, object_name, obj_type, subprogram, tree, PROMPT_VERSION)

    return tree


def _populate_descriptions(
    conn: sqlite3.Connection,
    node: DescriptionNode,
    client: LlmClient,
    root_schema: str,
    root_object_name: str,
    root_obj_type: str,
    root_subprogram: Optional[str],
    force: bool,
) -> None:
    """Post-order DFS: describe children first, then this node."""
    for child in node.children:
        _populate_descriptions(
            conn, child, client, root_schema, root_object_name,
            root_obj_type, root_subprogram, force,
        )

    # Stub nodes already have descriptions
    if node.node_kind == "stub":
        return

    # Check cache
    if not force:
        cached = get_cached_description(
            conn,
            root_schema,
            root_object_name,
            root_obj_type,
            root_subprogram,
            node.node_id,
            PROMPT_VERSION,
        )
        if cached:
            cached_hash, cached_desc = cached
            if cached_hash == node.source_hash:
                node.description = cached_desc
                _logger.debug("Cache hit: %s", node.node_id)
                return

    # Build prompt
    prompt = build_prompt(node)
    if prompt is None:
        return

    system_msg, user_msg = prompt
    _logger.debug("LLM call for node: %s", node.node_id)
    node.description = client.complete(system_msg, user_msg)
    _logger.debug("LLM response for %s: %s", node.node_id, node.description[:100])


def render_tree(
    node: DescriptionNode,
    prefix: str = "",
    is_last: bool = True,
    is_root: bool = True,
) -> str:
    """Render the description tree as indented text with box-drawing characters."""
    lines: list[str] = []

    if is_root:
        label = _node_label(node)
        lines.append(label)
        child_prefix = ""
    else:
        connector = "\u2514\u2500\u2500 " if is_last else "\u251c\u2500\u2500 "
        label = _node_label(node)
        lines.append(prefix + connector + label)
        child_prefix = prefix + ("    " if is_last else "\u2502   ")

    for i, child in enumerate(node.children):
        is_child_last = i == len(node.children) - 1
        lines.append(render_tree(child, child_prefix, is_child_last, is_root=False))

    return "\n".join(lines)


def _node_label(node: DescriptionNode) -> str:
    if node.node_kind == "method_root":
        desc = f" \u2014 {node.description}" if node.description else ""
        return f"{node.title}{desc}"

    if node.node_kind == "call":
        desc = f" \u2014 {node.description}" if node.description else ""
        return f"[{node.title}]{desc}"

    if node.node_kind == "stub":
        return f"[{node.title}] \u2014 {node.description}"

    # statement
    desc = f" \u2014 {node.description}" if node.description else ""
    return f"[{node.statement_type}]{desc}"
