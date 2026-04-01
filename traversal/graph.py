from __future__ import annotations

import sqlite3
from typing import Optional

from traversal import sqlite_store
from traversal.models import DependencyNode


def build_tree(
    conn: sqlite3.Connection,
    schema: str,
    object_name: str,
    subprogram: Optional[str] = None,
    max_depth: Optional[int] = None,
    _in_stack: Optional[set[tuple[str, str, str]]] = None,
    _depth: int = 0,
) -> DependencyNode:
    """
    Build a dependency tree rooted at (schema, object_name, subprogram).

    Cycle detection: tracks the current DFS path in _in_stack. If a node is
    encountered that is already in the stack, it is returned with status='cycle'
    and no children (recursion stops).

    Diamond dependencies are fully expanded in every branch (pure tree).
    """
    if _in_stack is None:
        _in_stack = set()

    key = (schema.upper(), object_name.upper(), (subprogram or "").upper())

    if key in _in_stack:
        return DependencyNode(
            schema_name=schema.upper(),
            object_name=object_name.upper(),
            object_type=None,
            subprogram=subprogram,
            status="cycle",
            error_message=None,
        )

    info = sqlite_store.get_object_info(conn, schema, object_name)
    if info is None:
        return DependencyNode(
            schema_name=schema.upper(),
            object_name=object_name.upper(),
            object_type=None,
            subprogram=subprogram,
            status="missing",
            error_message=None,
        )

    object_type, status, error_message = info

    if status in ("wrapped", "error", "unindexed"):
        return DependencyNode(
            schema_name=schema.upper(),
            object_name=object_name.upper(),
            object_type=object_type,
            subprogram=subprogram,
            status=status,
            error_message=error_message,
        )

    _in_stack.add(key)

    accesses = sqlite_store.get_table_accesses(conn, schema, object_name, subprogram)

    # Depth limit: resolve node itself but do not expand children
    if max_depth is not None and _depth >= max_depth:
        _in_stack.discard(key)
        return DependencyNode(
            schema_name=schema.upper(),
            object_name=object_name.upper(),
            object_type=object_type,
            subprogram=subprogram,
            status="ok",
            error_message=None,
            table_accesses=accesses,
        )

    edges = sqlite_store.get_call_edges(conn, schema, object_name, subprogram)

    children = [
        build_tree(
            conn,
            callee_schema if callee_schema else schema,  # NULL callee_schema → same schema
            callee_object,
            callee_subprogram,
            max_depth=max_depth,
            _in_stack=_in_stack,
            _depth=_depth + 1,
        )
        for callee_schema, callee_object, callee_subprogram in edges
    ]

    _in_stack.discard(key)

    return DependencyNode(
        schema_name=schema.upper(),
        object_name=object_name.upper(),
        object_type=object_type,
        subprogram=subprogram,
        status="ok",
        error_message=None,
        table_accesses=accesses,
        children=children,
    )


def print_tree(node: DependencyNode, prefix: str = "", is_last: bool = True) -> None:
    """Print a DependencyNode tree using box-drawing characters."""
    connector = "└── " if is_last else "├── "
    label = _node_label(node)
    print(prefix + (connector if prefix else "") + label)

    child_prefix = prefix + ("    " if is_last else "│   ")

    # Print table accesses as leaf items before children
    items: list[str] = [
        f"TABLE {a.table_name} {a.operation}" for a in node.table_accesses
    ]
    all_leaves = items
    all_children = node.children
    total = len(all_leaves) + len(all_children)

    for i, leaf in enumerate(all_leaves):
        leaf_connector = "└── " if (i == total - 1) else "├── "
        print(child_prefix + leaf_connector + leaf)

    for i, child in enumerate(all_children):
        is_child_last = (len(items) + i == total - 1)
        print_tree(child, child_prefix, is_child_last)


def print_tree_verbose(node: DependencyNode, prefix: str = "", is_last: bool = True) -> None:
    """Print a DependencyNode tree with full debug details per node."""
    connector = "└── " if is_last else "├── "

    # Header line: SCHEMA.OBJECT[.SUBPROGRAM] (TYPE) [STATUS]
    if node.subprogram:
        name = f"{node.schema_name}.{node.object_name}.{node.subprogram}"
    else:
        name = f"{node.schema_name}.{node.object_name}"
    type_part = f" ({node.object_type})" if node.object_type else ""
    header = f"{name}{type_part} [{node.status}]"
    print(prefix + (connector if prefix else "") + header)

    child_prefix = prefix + ("    " if is_last else "│   ")

    # Error message
    if node.error_message:
        print(child_prefix + "  ! " + node.error_message)

    # Table accesses
    for a in node.table_accesses:
        table_ref = f"{a.table_schema}.{a.table_name}" if a.table_schema else a.table_name
        print(child_prefix + f"  TABLE: {table_ref} — {a.operation}")

    # Children
    for i, child in enumerate(node.children):
        is_child_last = i == len(node.children) - 1
        print_tree_verbose(child, child_prefix, is_child_last)


def _node_label(node: DependencyNode) -> str:
    if node.subprogram:
        name = f"{node.object_name}.{node.subprogram}"
    else:
        name = node.object_name
    type_part = f" ({node.object_type})" if node.object_type else ""
    return f"{name}{type_part} [{node.status}]"
