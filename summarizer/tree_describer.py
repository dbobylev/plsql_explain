from __future__ import annotations

import hashlib
import logging
import sqlite3
from typing import Optional

from summarizer.description_tree import DescriptionNode, build_description_tree
from summarizer.llm_client import LlmClient
from summarizer.tree_prompts import PROMPT_VERSION, build_prompt
from summarizer.tree_store import (
    create_analysis_run,
    get_cached_analysis,
    iter_run_nodes,
    mark_analysis_run_completed,
    mark_analysis_run_failed,
    upsert_cached_analysis,
    upsert_run_node_description,
)
from traversal.graph import build_tree

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

    run_id = create_analysis_run(
        conn,
        schema,
        object_name,
        obj_type,
        subprogram,
        PROMPT_VERSION,
    )
    tree.analysis_run_id = run_id

    try:
        _populate_descriptions(
            conn,
            tree,
            client,
            schema,
            object_name,
            obj_type,
            subprogram,
            force,
            run_id=run_id,
            parent_node_id=None,
            position=0,
        )
    except Exception as exc:
        mark_analysis_run_failed(conn, run_id, str(exc))
        raise

    mark_analysis_run_completed(conn, run_id)

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
    run_id: str,
    parent_node_id: Optional[str],
    position: int,
) -> None:
    """Post-order DFS: describe children first, then this node."""
    for child_position, child in enumerate(node.children):
        _populate_descriptions(
            conn,
            child,
            client,
            root_schema,
            root_object_name,
            root_obj_type,
            root_subprogram,
            force,
            run_id=run_id,
            parent_node_id=node.node_id,
            position=child_position,
        )
        _compact_processed_subtree(child)

    # Stub nodes already have descriptions
    if node.node_kind == "stub":
        upsert_run_node_description(
            conn,
            run_id,
            root_schema,
            root_object_name,
            root_obj_type,
            root_subprogram,
            node,
            parent_node_id,
            position,
            PROMPT_VERSION,
        )
        _release_prompt_payload(node)
        return

    # Build prompt
    prompt = build_prompt(node)
    if prompt is None:
        upsert_run_node_description(
            conn,
            run_id,
            root_schema,
            root_object_name,
            root_obj_type,
            root_subprogram,
            node,
            parent_node_id,
            position,
            PROMPT_VERSION,
        )
        _release_prompt_payload(node)
        return

    system_msg, user_msg = prompt
    prompt_hash = _prompt_hash(system_msg, user_msg)

    if not force:
        cached_desc = get_cached_analysis(conn, prompt_hash, PROMPT_VERSION)
        if cached_desc is not None:
            node.description = cached_desc
            _logger.debug("Analysis cache hit: %s", node.node_id)
            upsert_run_node_description(
                conn,
                run_id,
                root_schema,
                root_object_name,
                root_obj_type,
                root_subprogram,
                node,
                parent_node_id,
                position,
                PROMPT_VERSION,
            )
            _release_prompt_payload(node)
            return

    _logger.debug("LLM call for node: %s", node.node_id)
    node.description = client.complete(system_msg, user_msg)
    _logger.debug("LLM response for %s: %s", node.node_id, node.description[:100])
    upsert_cached_analysis(
        conn,
        prompt_hash,
        PROMPT_VERSION,
        node.source_hash,
        node.node_kind,
        node.statement_type,
        node.description,
    )
    upsert_run_node_description(
        conn,
        run_id,
        root_schema,
        root_object_name,
        root_obj_type,
        root_subprogram,
        node,
        parent_node_id,
        position,
        PROMPT_VERSION,
    )
    _release_prompt_payload(node)


def _prompt_hash(system_msg: str, user_msg: str) -> str:
    payload = f"{system_msg}\n---\n{user_msg}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _release_prompt_payload(node: DescriptionNode) -> None:
    # The node description is already persisted at this point. Dropping the raw
    # prompt payload reduces peak memory while keeping the tree shape intact.
    node.source_text = ""
    node.prompt_context = ""


def _compact_processed_subtree(node: DescriptionNode) -> None:
    _release_prompt_payload(node)


def render_tree_from_run(conn: sqlite3.Connection, run_id: str) -> str:
    rows = iter_run_nodes(conn, run_id)
    if not rows:
        return ""

    nodes: dict[str, DescriptionNode] = {}
    root: DescriptionNode | None = None
    for row in rows:
        node = DescriptionNode(
            node_id=row["node_id"],
            node_kind=row["node_kind"],
            statement_type=row["statement_type"],
            title=row["title"],
            source_text="",
            start_line=row["start_line"],
            end_line=row["end_line"],
            description=row["description"],
            schema_name=row["schema_name"],
            object_name=row["object_name"],
            subprogram=row["subprogram"],
            source_hash=row["source_hash"],
            analysis_run_id=row["run_id"],
        )
        nodes[node.node_id] = node

    for row in rows:
        node = nodes[row["node_id"]]
        parent_node_id = row["parent_node_id"]
        if parent_node_id is None:
            root = node
            continue
        nodes[parent_node_id].children.append(node)

    if root is None:
        return ""

    return render_tree(root)


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
