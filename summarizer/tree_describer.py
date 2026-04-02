from __future__ import annotations

import hashlib
import logging
import sqlite3
from typing import Optional

from summarizer.description_tree import (
    DescriptionNode,
    _compute_tree_hash,
    _dependency_slug,
    _display_dependency_name,
    _find_callees_in_source,
    _make_stub_node,
    build_description_tree,
)
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
from traversal.graph import resolve_node_shallow
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
    run_id = describe_tree_run(
        conn,
        schema,
        object_name,
        subprogram,
        client,
        force=force,
        max_depth=max_depth,
    )
    tree = load_tree_from_run(conn, run_id)
    tree.analysis_run_id = run_id
    return tree


def describe_tree_run(
    conn: sqlite3.Connection,
    schema: str,
    object_name: str,
    subprogram: Optional[str],
    client: LlmClient,
    force: bool = False,
    max_depth: Optional[int] = None,
) -> str:
    """
    Memory-optimized production path.

    Builds and describes one method tree at a time, persists each finished node
    immediately, and renders final output from SQLite. This avoids holding the
    fully expanded transitive DescriptionNode tree in memory.
    """
    dep_node = resolve_node_shallow(
        conn,
        schema,
        object_name,
        subprogram,
        include_children=(max_depth is None or max_depth > 0),
    )
    obj_type = dep_node.object_type or ""

    _logger.info(
        "Построение дерева описаний: %s.%s%s",
        schema,
        object_name,
        f".{subprogram}" if subprogram else "",
    )

    _logger.info("Дерево построено, генерация описаний...")

    run_id = create_analysis_run(
        conn,
        schema,
        object_name,
        obj_type,
        subprogram,
        PROMPT_VERSION,
    )

    try:
        _run_incremental_analysis(
            conn,
            dep_node,
            client,
            schema,
            object_name,
            obj_type,
            subprogram,
            force,
            run_id,
            max_depth,
        )
    except Exception as exc:
        mark_analysis_run_failed(conn, run_id, str(exc))
        raise

    mark_analysis_run_completed(conn, run_id)

    return run_id


def _run_incremental_analysis(
    conn: sqlite3.Connection,
    dep_node: DependencyNode,
    client: LlmClient,
    root_schema: str,
    root_object_name: str,
    root_obj_type: str,
    root_subprogram: Optional[str],
    force: bool,
    run_id: str,
    max_depth: Optional[int],
) -> None:
    key = (
        dep_node.schema_name.upper(),
        dep_node.object_name.upper(),
        (dep_node.subprogram or "").upper(),
    )
    in_stack = {key}

    tree = build_description_tree(
        conn,
        dep_node,
        max_depth=max_depth,
        expand_calls=False,
    )
    _describe_local_node(
        conn,
        tree,
        dep_node,
        client,
        root_schema,
        root_object_name,
        root_obj_type,
        root_subprogram,
        force,
        run_id,
        parent_node_id=None,
        position=0,
        in_stack=in_stack,
        max_depth=max_depth,
        depth=0,
    )


def _describe_local_node(
    conn: sqlite3.Connection,
    node: DescriptionNode,
    dep_node: DependencyNode,
    client: LlmClient,
    root_schema: str,
    root_object_name: str,
    root_obj_type: str,
    root_subprogram: Optional[str],
    force: bool,
    run_id: str,
    parent_node_id: Optional[str],
    position: int,
    in_stack: set[tuple[str, str, str]],
    max_depth: Optional[int],
    depth: int,
) -> DescriptionNode:
    original_children = list(node.children)
    compact_children: list[DescriptionNode] = []

    for child_position, child in enumerate(original_children):
        compact_children.append(
            _describe_local_node(
                conn,
                child,
                dep_node,
                client,
                root_schema,
                root_object_name,
                root_obj_type,
                root_subprogram,
                force,
                run_id,
                parent_node_id=node.node_id,
                position=child_position,
                in_stack=in_stack,
                max_depth=max_depth,
                depth=depth,
            )
        )

    if not original_children and node.source_text:
        callees = _find_callees_in_source(node.source_text, dep_node)
        for call_position, callee_dep in enumerate(callees):
            compact_children.append(
                _describe_inline_call(
                    conn,
                    callee_dep,
                    caller_dep=dep_node,
                    parent_node=node,
                    position=call_position,
                    client=client,
                    root_schema=root_schema,
                    root_object_name=root_object_name,
                    root_obj_type=root_obj_type,
                    root_subprogram=root_subprogram,
                    force=force,
                    run_id=run_id,
                    in_stack=in_stack,
                    max_depth=max_depth,
                    depth=depth,
                )
            )

    node.children = compact_children
    _compute_tree_hash(node)
    _describe_and_persist_node(
        conn,
        node,
        client,
        root_schema,
        root_object_name,
        root_obj_type,
        root_subprogram,
        force,
        run_id,
        parent_node_id,
        position,
    )
    return _make_summary_ref(node)


def _describe_inline_call(
    conn: sqlite3.Connection,
    callee_dep: DependencyNode,
    caller_dep: DependencyNode,
    parent_node: DescriptionNode,
    position: int,
    client: LlmClient,
    root_schema: str,
    root_object_name: str,
    root_obj_type: str,
    root_subprogram: Optional[str],
    force: bool,
    run_id: str,
    in_stack: set[tuple[str, str, str]],
    max_depth: Optional[int],
    depth: int,
) -> DescriptionNode:
    callee_key = (
        callee_dep.schema_name.upper(),
        callee_dep.object_name.upper(),
        (callee_dep.subprogram or "").upper(),
    )

    if callee_key in in_stack:
        cycle_dep = DependencyNode(
            schema_name=callee_dep.schema_name,
            object_name=callee_dep.object_name,
            object_type=callee_dep.object_type,
            subprogram=callee_dep.subprogram,
            status="cycle",
            error_message=None,
        )
        stub = _make_stub_node(
            cycle_dep,
            parent_node.node_id,
            position,
            current_schema=caller_dep.schema_name,
        )
        _describe_and_persist_node(
            conn,
            stub,
            client,
            root_schema,
            root_object_name,
            root_obj_type,
            root_subprogram,
            force,
            run_id,
            parent_node.node_id,
            position,
        )
        return _make_summary_ref(stub)

    if max_depth is not None and depth + 1 >= max_depth:
        stub = _make_stub_node(
            callee_dep,
            parent_node.node_id,
            position,
            current_schema=caller_dep.schema_name,
        )
        _describe_and_persist_node(
            conn,
            stub,
            client,
            root_schema,
            root_object_name,
            root_obj_type,
            root_subprogram,
            force,
            run_id,
            parent_node.node_id,
            position,
        )
        return _make_summary_ref(stub)

    if callee_dep.status != "ok":
        stub = _make_stub_node(
            callee_dep,
            parent_node.node_id,
            position,
            current_schema=caller_dep.schema_name,
        )
        _describe_and_persist_node(
            conn,
            stub,
            client,
            root_schema,
            root_object_name,
            root_obj_type,
            root_subprogram,
            force,
            run_id,
            parent_node.node_id,
            position,
        )
        return _make_summary_ref(stub)

    resolved_callee = resolve_node_shallow(
        conn,
        callee_dep.schema_name,
        callee_dep.object_name,
        callee_dep.subprogram or None,
        include_children=True,
    )

    call_node_id = f"{parent_node.node_id}/call:{_dependency_slug(resolved_callee)}:{position}"
    local_tree = build_description_tree(
        conn,
        resolved_callee,
        max_depth=max_depth,
        expand_calls=False,
        prefix_override=call_node_id,
    )

    in_stack.add(callee_key)
    try:
        call_children: list[DescriptionNode] = []
        for child_position, child in enumerate(local_tree.children):
            call_children.append(
                _describe_local_node(
                    conn,
                    child,
                    resolved_callee,
                    client,
                    root_schema,
                    root_object_name,
                    root_obj_type,
                    root_subprogram,
                    force,
                    run_id,
                    parent_node_id=call_node_id,
                    position=child_position,
                    in_stack=in_stack,
                    max_depth=max_depth,
                    depth=depth + 1,
                )
            )
    finally:
        in_stack.discard(callee_key)

    local_tree.children = call_children
    _compute_tree_hash(local_tree)

    callee_name = _display_dependency_name(
        resolved_callee,
        current_schema=caller_dep.schema_name,
    )
    call_node = DescriptionNode(
        node_id=call_node_id,
        node_kind="call",
        statement_type="CALL",
        title=f"CALL -> {callee_name}",
        source_text="",
        start_line=local_tree.start_line,
        end_line=local_tree.end_line,
        description="",
        children=call_children,
        schema_name=resolved_callee.schema_name,
        object_name=resolved_callee.object_name,
        subprogram=resolved_callee.subprogram or "",
        source_hash=local_tree.source_hash,
    )
    _describe_and_persist_node(
        conn,
        call_node,
        client,
        root_schema,
        root_object_name,
        root_obj_type,
        root_subprogram,
        force,
        run_id,
        parent_node.node_id,
        position,
    )
    return _make_summary_ref(call_node)


def _describe_and_persist_node(
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


def _make_summary_ref(node: DescriptionNode) -> DescriptionNode:
    return DescriptionNode(
        node_id=node.node_id,
        node_kind=node.node_kind,
        statement_type=node.statement_type,
        title=node.title,
        source_text="",
        start_line=node.start_line,
        end_line=node.end_line,
        description=node.description,
        schema_name=node.schema_name,
        object_name=node.object_name,
        subprogram=node.subprogram,
        source_hash=node.source_hash,
        analysis_run_id=node.analysis_run_id,
    )


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

    _describe_and_persist_node(
        conn,
        node,
        client,
        root_schema,
        root_object_name,
        root_obj_type,
        root_subprogram,
        force,
        run_id,
        parent_node_id,
        position,
    )


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


def load_tree_from_run(conn: sqlite3.Connection, run_id: str) -> DescriptionNode:
    rows = iter_run_nodes(conn, run_id)
    if not rows:
        raise ValueError(f"No node_description rows found for run_id={run_id}")

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
        raise ValueError(f"Run {run_id} has no root node")
    return root


def render_tree_from_run(conn: sqlite3.Connection, run_id: str) -> str:
    return render_tree(load_tree_from_run(conn, run_id))


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
