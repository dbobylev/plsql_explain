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
    prompt_context: str = ""
    analysis_run_id: str = ""


def _node_prefix(schema: str, obj: str, sub: Optional[str]) -> str:
    parts = [schema.upper(), obj.upper()]
    if sub:
        parts.append(sub.upper())
    return ".".join(parts)


def _dependency_slug(dep_node: DependencyNode) -> str:
    parts = []
    if dep_node.schema_name:
        parts.append(dep_node.schema_name.upper())
    parts.append(dep_node.object_name.upper())
    if dep_node.subprogram:
        parts.append(dep_node.subprogram.upper())
    return ".".join(parts)


def _display_dependency_name(
    dep_node: DependencyNode,
    current_schema: Optional[str] = None,
) -> str:
    parts = []
    if dep_node.schema_name and (
        current_schema is None or dep_node.schema_name.upper() != current_schema.upper()
    ):
        parts.append(dep_node.schema_name.upper())
    parts.append(dep_node.object_name.upper())
    if dep_node.subprogram:
        parts.append(dep_node.subprogram.upper())
    return ".".join(parts)


def _contains_identifier(text: str, identifier: str) -> bool:
    pattern = re.compile(rf"\b{re.escape(identifier)}\b", re.IGNORECASE)
    return bool(pattern.search(text))


def _strip_non_code_fragments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    text = re.sub(r"--[^\n]*", " ", text)
    text = re.sub(r"'(?:''|[^'])*'", "''", text)
    return text


def _qualified_call_pattern(
    *parts: str,
    allow_member_access: bool = False,
) -> re.Pattern[str]:
    escaped = r"\s*\.\s*".join(re.escape(part) for part in parts if part)
    lookahead = r"(?:\.|\(|;)" if allow_member_access else r"(?:\(|;)"
    return re.compile(
        rf"(?<![A-Z0-9_$#]){escaped}(?![A-Z0-9_$#])(?=\s*{lookahead})",
        re.IGNORECASE,
    )


def _call_match_patterns(
    dep_child: DependencyNode,
    parent_dep: DependencyNode,
    object_subprogram_counts: dict[tuple[str, str], int],
    subprogram_counts: dict[str, int],
    object_counts: dict[str, int],
) -> list[tuple[int, re.Pattern[str]]]:
    patterns: list[tuple[int, re.Pattern[str]]] = []
    child_schema = dep_child.schema_name.upper()
    child_object = dep_child.object_name.upper()
    child_subprogram = (dep_child.subprogram or "").upper()
    parent_schema = parent_dep.schema_name.upper()
    parent_object = parent_dep.object_name.upper()

    if dep_child.subprogram:
        patterns.append((3, _qualified_call_pattern(child_schema, child_object, child_subprogram)))

        if child_schema == parent_schema or object_subprogram_counts[(child_object, child_subprogram)] == 1:
            patterns.append((2, _qualified_call_pattern(child_object, child_subprogram)))

        if (
            child_schema == parent_schema
            and child_object == parent_object
            and subprogram_counts[child_subprogram] == 1
        ):
            patterns.append((1, _qualified_call_pattern(child_subprogram)))
        return patterns

    patterns.append((3, _qualified_call_pattern(child_schema, child_object, allow_member_access=True)))
    if child_schema == parent_schema or object_counts[child_object] == 1:
        patterns.append((2, _qualified_call_pattern(child_object, allow_member_access=True)))
    return patterns


def _find_callees_in_source(
    source_text: str,
    dep_node: DependencyNode,
) -> list[DependencyNode]:
    """Find callees referenced in source_text using the most specific match available."""
    match_source = _strip_non_code_fragments(source_text)

    object_subprogram_counts: dict[tuple[str, str], int] = {}
    subprogram_counts: dict[str, int] = {}
    object_counts: dict[str, int] = {}
    for child in dep_node.children:
        if child.status not in ("ok", "wrapped", "error", "unindexed", "missing", "cycle"):
            continue
        child_object = child.object_name.upper()
        child_subprogram = (child.subprogram or "").upper()
        object_counts[child_object] = object_counts.get(child_object, 0) + 1
        if child_subprogram:
            key = (child_object, child_subprogram)
            object_subprogram_counts[key] = object_subprogram_counts.get(key, 0) + 1
            subprogram_counts[child_subprogram] = subprogram_counts.get(child_subprogram, 0) + 1

    matches: list[tuple[int, int, str, str, str, DependencyNode]] = []
    for child in dep_node.children:
        if child.status not in ("ok", "wrapped", "error", "unindexed", "missing", "cycle"):
            continue

        best_match: tuple[int, int] | None = None
        for specificity, pattern in _call_match_patterns(
            child,
            dep_node,
            object_subprogram_counts,
            subprogram_counts,
            object_counts,
        ):
            match = pattern.search(match_source)
            if match is None:
                continue
            candidate = (match.start(), -specificity)
            if best_match is None or candidate < best_match:
                best_match = candidate

        if best_match is not None:
            matches.append(
                (
                    best_match[0],
                    best_match[1],
                    child.schema_name.upper(),
                    child.object_name.upper(),
                    (child.subprogram or "").upper(),
                    child,
                )
            )

    matches.sort()
    return [child for *_ignore, child in matches]


def _substatement_to_desc_node(
    node: SubstatementNode,
    prefix: str,
    schema_name: str,
    object_name: str,
    subprogram: str,
) -> DescriptionNode:
    """Convert a SubstatementNode to a DescriptionNode (without call expansion)."""
    rendered = render_substatement(node)
    children = [
        _substatement_to_desc_node(child, prefix, schema_name, object_name, subprogram)
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
        schema_name=schema_name,
        object_name=object_name,
        subprogram=subprogram,
        source_hash=node.source_hash,
        prompt_context=_substatement_prompt_context(node, rendered),
    )


def _make_stub_node(
    dep_child: DependencyNode,
    parent_prefix: str,
    position: int,
    current_schema: Optional[str] = None,
) -> DescriptionNode:
    callee_name = _display_dependency_name(dep_child, current_schema=current_schema)

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


def _rebase_node_id(node_id: str, old_prefix: str, new_prefix: str) -> str:
    if node_id.startswith(old_prefix + "/"):
        return new_prefix + node_id[len(old_prefix):]
    if node_id == old_prefix:
        return new_prefix
    return f"{new_prefix}/{node_id}"


def _clone_with_rebased_ids(
    node: DescriptionNode,
    old_prefix: str,
    new_prefix: str,
) -> DescriptionNode:
    return DescriptionNode(
        node_id=_rebase_node_id(node.node_id, old_prefix, new_prefix),
        node_kind=node.node_kind,
        statement_type=node.statement_type,
        title=node.title,
        source_text=node.source_text,
        start_line=node.start_line,
        end_line=node.end_line,
        description=node.description,
        children=[
            _clone_with_rebased_ids(child, old_prefix, new_prefix)
            for child in node.children
        ],
        schema_name=node.schema_name,
        object_name=node.object_name,
        subprogram=node.subprogram,
        source_hash=node.source_hash,
        prompt_context=node.prompt_context,
    )


def build_description_tree(
    conn: sqlite3.Connection,
    dep_node: DependencyNode,
    in_stack: Optional[set[tuple[str, str, str]]] = None,
    max_depth: Optional[int] = None,
    _depth: int = 0,
    expand_calls: bool = True,
    prefix_override: Optional[str] = None,
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
    prefix = prefix_override or _node_prefix(schema, obj_name, sub or None)

    key = (schema.upper(), obj_name.upper(), sub.upper())

    # Non-ok statuses produce stub nodes
    if dep_node.status != "ok":
        return _make_stub_node(dep_node, prefix, 0, current_schema=schema)

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
        _substatement_to_desc_node(root, prefix, schema, obj_name, sub) for root in roots
    ]

    if expand_calls:
        # Expand calls in leaf nodes
        in_stack.add(key)
        _expand_calls(conn, desc_children, dep_node, in_stack, max_depth, _depth)
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
    max_depth: Optional[int],
    depth: int,
) -> None:
    """Recursively expand call sites in leaf nodes."""
    for node in nodes:
        if node.children:
            # Recurse into children first
            _expand_calls(conn, node.children, dep_node, in_stack, max_depth, depth)
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
                    node.node_id,
                    i,
                    current_schema=dep_node.schema_name,
                ))
                continue

            if max_depth is not None and depth + 1 >= max_depth:
                node.children.append(
                    _make_stub_node(
                        callee_dep,
                        node.node_id,
                        i,
                        current_schema=dep_node.schema_name,
                    )
                )
                continue

            if callee_dep.status != "ok":
                node.children.append(
                    _make_stub_node(
                        callee_dep,
                        node.node_id,
                        i,
                        current_schema=dep_node.schema_name,
                    )
                )
                continue

            # Build callee tree
            callee_tree = build_description_tree(
                conn, callee_dep, in_stack, max_depth, depth + 1,
            )

            # Wrap as a call node
            callee_name = _display_dependency_name(
                callee_dep,
                current_schema=dep_node.schema_name,
            )
            call_node_id = f"{node.node_id}/call:{_dependency_slug(callee_dep)}:{i}"
            callee_prefix = _node_prefix(
                callee_dep.schema_name,
                callee_dep.object_name,
                callee_dep.subprogram or None,
            )
            if callee_tree.node_kind == "method_root":
                rebased_children = [
                    _clone_with_rebased_ids(child, callee_prefix, call_node_id)
                    for child in callee_tree.children
                ]
            else:
                rebased_children = [
                    _clone_with_rebased_ids(callee_tree, callee_prefix, call_node_id)
                ]

            call_node = DescriptionNode(
                node_id=call_node_id,
                node_kind="call",
                statement_type="CALL",
                title=f"CALL -> {callee_name}",
                source_text="",
                start_line=callee_tree.start_line,
                end_line=callee_tree.end_line,
                description="",
                children=rebased_children,
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


def _substatement_prompt_context(node: SubstatementNode, rendered: str) -> str:
    """
    Return prompt context for a substatement without embedding full child source.

    Leaf nodes can use the fully rendered fragment. For interior nodes we keep
    only local context (for example IF/LOOP headers) and let child descriptions
    carry the nested behavior.
    """
    if not node.children:
        return rendered

    source = node.source_text.strip()
    statement_type = node.statement_type.upper()

    if statement_type in {
        "BEGIN_END",
        "IF",
        "IF_ELSIF",
        "IF_ELSE",
        "LOOP_BASIC",
        "LOOP_FOR",
        "LOOP_WHILE",
    }:
        return source

    if statement_type == "EXCEPTION_HANDLER":
        if source:
            return f"EXCEPTION\n{source}"
        return "EXCEPTION"

    if statement_type == "IF_THEN":
        return "THEN"

    if statement_type == "CASE":
        header = _extract_prefix_through_keyword(source, "WHEN")
        return header or "CASE"

    if statement_type == "CASE_WHEN":
        header = _extract_prefix_through_keyword(source, "THEN")
        return header or "WHEN ... THEN"

    if statement_type == "CASE_ELSE":
        return "ELSE"

    return source or statement_type


def _extract_prefix_through_keyword(text: str, keyword: str) -> str:
    match = re.search(rf"^(.*?\b{re.escape(keyword)}\b)", text, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else ""


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
