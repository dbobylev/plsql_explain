from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from summarizer.substatements import (
    SubstatementNode,
    load_substatement_tree,
    render_substatement,
)
from traversal.models import DependencyNode

_logger = logging.getLogger(__name__)

TREE_VERSION = "1"


@dataclass
class DescriptionNode:
    node_id: str
    node_kind: str  # "statement" | "call" | "method_root" | "stub"
    statement_type: str
    title: str
    source_text: str
    start_line: int
    end_line: int
    description: str
    children: list[DescriptionNode] = field(default_factory=list)
    schema_name: str = ""
    object_name: str = ""
    subprogram: str = ""
    source_hash: str = ""


def _node_prefix(schema: str, obj: str, sub: Optional[str]) -> str:
    parts = [schema.upper(), obj.upper()]
    if sub:
        parts.append(sub.upper())
    return ".".join(parts)


def _contains_identifier(text: str, identifier: str) -> bool:
    pattern = re.compile(rf"\b{re.escape(identifier)}\b", re.IGNORECASE)
    return bool(pattern.search(text))


def _find_callees_in_source(
    source_text: str,
    dep_node: DependencyNode,
) -> list[DependencyNode]:
    """Find which callees from dep_node.children are referenced in source_text."""
    matched = []
    for child in dep_node.children:
        if child.status not in ("ok", "wrapped", "error", "unindexed", "missing", "cycle"):
            continue
        if _contains_identifier(source_text, child.object_name):
            matched.append(child)
        elif child.subprogram and _contains_identifier(source_text, child.subprogram):
            matched.append(child)
    return matched


def _substatement_to_desc_node(
    node: SubstatementNode,
    prefix: str,
) -> DescriptionNode:
    """Convert a SubstatementNode to a DescriptionNode (without call expansion)."""
    rendered = render_substatement(node)
    children = [
        _substatement_to_desc_node(child, prefix)
        for child in node.children
    ]
    return DescriptionNode(
        node_id=f"{prefix}/seq:{node.seq}",
        node_kind="statement",
        statement_type=node.statement_type,
        title=node.statement_type,
        source_text=rendered,
        start_line=node.start_line,
        end_line=node.end_line,
        description="",
        children=children,
        source_hash=node.source_hash,
    )


def _make_stub_node(
    dep_child: DependencyNode,
    parent_prefix: str,
    position: int,
) -> DescriptionNode:
    callee_name = dep_child.object_name
    if dep_child.subprogram:
        callee_name = f"{dep_child.object_name}.{dep_child.subprogram}"

    status_labels = {
        "cycle": f"[cycle] {callee_name}",
        "missing": f"[missing] {callee_name}",
        "wrapped": f"[wrapped] {callee_name}",
        "error": f"[error] {callee_name}",
        "unindexed": f"[unindexed] {callee_name}",
    }
    desc = status_labels.get(dep_child.status, f"[{dep_child.status}] {callee_name}")

    return DescriptionNode(
        node_id=f"{parent_prefix}/stub:{callee_name}:{position}",
        node_kind="stub",
        statement_type="CALL",
        title=f"CALL -> {callee_name}",
        source_text="",
        start_line=0,
        end_line=0,
        description=desc,
        schema_name=dep_child.schema_name,
        object_name=dep_child.object_name,
        subprogram=dep_child.subprogram or "",
    )


def build_description_tree(
    conn: sqlite3.Connection,
    dep_node: DependencyNode,
    in_stack: Optional[set[tuple[str, str, str]]] = None,
    max_depth: Optional[int] = None,
    _depth: int = 0,
) -> DescriptionNode:
    """
    Build a DescriptionNode tree from a DependencyNode.

    Loads the substatement tree for the method, converts it, and inlines
    callee substatement trees at leaf nodes where the call is detected.
    """
    if in_stack is None:
        in_stack = set()

    schema = dep_node.schema_name
    obj_name = dep_node.object_name
    obj_type = dep_node.object_type or ""
    sub = dep_node.subprogram or ""
    prefix = _node_prefix(schema, obj_name, sub or None)

    key = (schema.upper(), obj_name.upper(), sub.upper())

    # Non-ok statuses produce stub nodes
    if dep_node.status != "ok":
        return _make_stub_node(dep_node, prefix, 0)

    # Load substatement tree
    roots = load_substatement_tree(conn, schema, obj_name, obj_type, sub or None)

    if not roots:
        # No substatements — method_root with full source
        source_text = _load_source_text(conn, schema, obj_name, obj_type)
        source_hash = _load_source_hash(conn, schema, obj_name, obj_type)
        return DescriptionNode(
            node_id=f"{prefix}/root",
            node_kind="method_root",
            statement_type="METHOD",
            title=f"{obj_name}.{sub}" if sub else obj_name,
            source_text=source_text or "",
            start_line=0,
            end_line=0,
            description="",
            schema_name=schema,
            object_name=obj_name,
            subprogram=sub,
            source_hash=source_hash or "",
        )

    # Convert substatement tree to description nodes
    desc_children = [
        _substatement_to_desc_node(root, prefix) for root in roots
    ]

    # Expand calls in leaf nodes
    in_stack.add(key)
    _expand_calls(conn, desc_children, dep_node, in_stack, prefix, max_depth, _depth)
    in_stack.discard(key)

    # Compute combined source hash
    source_hash = _load_source_hash(conn, schema, obj_name, obj_type) or ""

    method_root = DescriptionNode(
        node_id=f"{prefix}/root",
        node_kind="method_root",
        statement_type="METHOD",
        title=f"{obj_name}.{sub}" if sub else obj_name,
        source_text="",
        start_line=roots[0].start_line if roots else 0,
        end_line=roots[-1].end_line if roots else 0,
        description="",
        children=desc_children,
        schema_name=schema,
        object_name=obj_name,
        subprogram=sub,
        source_hash=source_hash,
    )

    # Compute tree hashes bottom-up
    _compute_tree_hash(method_root)

    return method_root


def _expand_calls(
    conn: sqlite3.Connection,
    nodes: list[DescriptionNode],
    dep_node: DependencyNode,
    in_stack: set[tuple[str, str, str]],
    parent_prefix: str,
    max_depth: Optional[int],
    depth: int,
) -> None:
    """Recursively expand call sites in leaf nodes."""
    for node in nodes:
        if node.children:
            # Recurse into children first
            _expand_calls(conn, node.children, dep_node, in_stack, parent_prefix, max_depth, depth)
            continue

        # Leaf node — check for call references
        if not node.source_text:
            continue

        callees = _find_callees_in_source(node.source_text, dep_node)
        for i, callee_dep in enumerate(callees):
            callee_key = (
                callee_dep.schema_name.upper(),
                callee_dep.object_name.upper(),
                (callee_dep.subprogram or "").upper(),
            )

            # Cycle or depth limit
            if callee_key in in_stack:
                node.children.append(_make_stub_node(
                    DependencyNode(
                        schema_name=callee_dep.schema_name,
                        object_name=callee_dep.object_name,
                        object_type=callee_dep.object_type,
                        subprogram=callee_dep.subprogram,
                        status="cycle",
                        error_message=None,
                    ),
                    parent_prefix, i,
                ))
                continue

            if max_depth is not None and depth + 1 >= max_depth:
                node.children.append(_make_stub_node(callee_dep, parent_prefix, i))
                continue

            if callee_dep.status != "ok":
                node.children.append(_make_stub_node(callee_dep, parent_prefix, i))
                continue

            # Build callee tree
            callee_tree = build_description_tree(
                conn, callee_dep, in_stack, max_depth, depth + 1,
            )

            # Wrap as a call node
            callee_name = callee_dep.object_name
            if callee_dep.subprogram:
                callee_name = f"{callee_dep.object_name}.{callee_dep.subprogram}"

            call_node = DescriptionNode(
                node_id=f"{parent_prefix}/call:{callee_name}:{i}",
                node_kind="call",
                statement_type="CALL",
                title=f"CALL -> {callee_name}",
                source_text="",
                start_line=callee_tree.start_line,
                end_line=callee_tree.end_line,
                description="",
                children=callee_tree.children if callee_tree.node_kind == "method_root" else [callee_tree],
                schema_name=callee_dep.schema_name,
                object_name=callee_dep.object_name,
                subprogram=callee_dep.subprogram or "",
                source_hash=callee_tree.source_hash,
            )
            node.children.append(call_node)


def _compute_tree_hash(node: DescriptionNode) -> str:
    """Compute source_hash bottom-up for interior nodes."""
    if node.children:
        child_hashes = [_compute_tree_hash(child) for child in node.children]
        payload = "|".join([
            TREE_VERSION,
            node.node_kind,
            node.statement_type,
            node.source_hash,
            *child_hashes,
        ])
        node.source_hash = hashlib.sha256(payload.encode()).hexdigest()
    elif not node.source_hash:
        payload = f"{TREE_VERSION}|{node.node_kind}|{node.statement_type}|{node.source_text}"
        node.source_hash = hashlib.sha256(payload.encode()).hexdigest()
    return node.source_hash


def _load_source_text(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    obj_type: str,
) -> Optional[str]:
    from summarizer.sqlite_store import get_source_text
    return get_source_text(conn, schema, name, obj_type or None)


def _load_source_hash(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    obj_type: str,
) -> Optional[str]:
    from summarizer.sqlite_store import get_source_hash
    return get_source_hash(conn, schema, name, obj_type)
