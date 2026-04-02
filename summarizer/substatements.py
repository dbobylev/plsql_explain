from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

PLANNER_VERSION = "2"


@dataclass
class SubstatementNode:
    seq: int
    parent_seq: Optional[int]
    position: int
    statement_type: str
    start_line: int
    end_line: int
    source_text: str
    source_hash: str
    children: list["SubstatementNode"] = field(default_factory=list)


@dataclass
class AnalysisUnit:
    unit_key: str
    unit_kind: str  # method | block | branch | code_chunk
    statement_type: str
    title: str
    path: tuple[str, ...]
    header_context: tuple[str, ...] = ()
    body_nodes: list[SubstatementNode] = field(default_factory=list)
    children: list["AnalysisUnit"] = field(default_factory=list)
    part_no: int = 1
    parts_total: int = 1
    oversized: bool = False
    estimated_chars: int = 0
    unit_hash: str = ""


_BLOCK_HEADER_TYPES = {
    "BEGIN_END",
    "EXCEPTION_HANDLER",
    "IF",
    "IF_ELSIF",
    "IF_ELSE",
    "LOOP_BASIC",
    "LOOP_FOR",
    "LOOP_WHILE",
}
_BODY_ONLY_TYPES = {"IF_THEN"}
_OPAQUE_TYPES = {"CASE", "CASE_ELSE", "CASE_WHEN"}
_SYNTHETIC_CLOSERS = {
    "BEGIN_END": "END;",
    "IF": "END IF;",
    "LOOP_BASIC": "END LOOP;",
    "LOOP_FOR": "END LOOP;",
    "LOOP_WHILE": "END LOOP;",
}
_SEQUENCE_CONTAINER_TYPES = {
    "BEGIN_END",
    "LOOP_BASIC",
    "LOOP_FOR",
    "LOOP_WHILE",
    "EXCEPTION_HANDLER",
    "IF_THEN",
    "IF_ELSIF",
    "IF_ELSE",
    "CASE_WHEN",
    "CASE_ELSE",
}
_BRANCHING_CONTAINER_TYPES = {"IF", "CASE"}
_SCOPE_LABELS = {
    "BEGIN_END": "BEGIN-блок",
    "IF": "IF-блок",
    "IF_THEN": "Ветка THEN",
    "IF_ELSIF": "Ветка ELSIF",
    "IF_ELSE": "Ветка ELSE",
    "LOOP_BASIC": "Цикл LOOP",
    "LOOP_FOR": "Цикл FOR",
    "LOOP_WHILE": "Цикл WHILE",
    "CASE": "CASE-блок",
    "CASE_WHEN": "Ветка WHEN",
    "CASE_ELSE": "Ветка ELSE",
    "EXCEPTION_HANDLER": "Обработчик исключения",
    "DECLARE": "DECLARE-секция",
    "FORALL": "FORALL-блок",
}


def load_substatement_tree(
    conn: sqlite3.Connection,
    schema: str,
    object_name: str,
    object_type: str,
    subprogram: Optional[str],
) -> list[SubstatementNode]:
    """
    Load substatements from DB and assemble into a tree via parent_seq.

    Returns root nodes (parent_seq IS NULL) sorted by position.
    """
    norm_sub = subprogram if subprogram else ""
    rows = conn.execute(
        """
        SELECT seq, parent_seq, position, statement_type,
               start_line, end_line, source_text, source_hash
        FROM substatement
        WHERE schema_name = ? AND object_name = ? AND object_type = ? AND subprogram = ?
        ORDER BY seq
        """,
        (schema.upper(), object_name.upper(), object_type.upper(), norm_sub.upper()),
    ).fetchall()

    if not rows:
        return []

    nodes: dict[int, SubstatementNode] = {}
    for r in rows:
        nodes[r["seq"]] = SubstatementNode(
            seq=r["seq"],
            parent_seq=r["parent_seq"],
            position=r["position"],
            statement_type=r["statement_type"],
            start_line=r["start_line"],
            end_line=r["end_line"],
            source_text=r["source_text"],
            source_hash=r["source_hash"],
        )

    roots: list[SubstatementNode] = []
    for node in nodes.values():
        if node.parent_seq is not None and node.parent_seq in nodes:
            nodes[node.parent_seq].children.append(node)
        else:
            roots.append(node)

    for node in nodes.values():
        node.children.sort(key=lambda n: n.position)
    roots.sort(key=lambda n: n.position)

    return roots


def _tree_source_len(node: SubstatementNode) -> int:
    total = len(node.source_text)
    for child in node.children:
        total += _tree_source_len(child)
    return total


def _tree_source_hashes(node: SubstatementNode) -> list[str]:
    result = [node.source_hash]
    for child in node.children:
        result.extend(_tree_source_hashes(child))
    return result


def render_substatement(node: SubstatementNode) -> str:
    """
    Reconstruct a readable code fragment for LLM analysis.

    Some parser nodes keep only the block header in source_text (for example
    BEGIN/IF/LOOP), while others already contain the full body text
    (for example CASE/CASE_WHEN). This renderer avoids duplicating child
    statements while still keeping compound blocks readable.
    """
    return "\n".join(_render_substatement_parts(node)).strip()


def _render_substatement_parts(node: SubstatementNode) -> list[str]:
    source = node.source_text.strip()

    if node.statement_type in _OPAQUE_TYPES:
        return [source] if source else []

    if node.statement_type == "EXCEPTION_HANDLER":
        parts = ["EXCEPTION"]
        if source:
            parts.append(source)
        for child in node.children:
            child_text = render_substatement(child)
            if child_text:
                parts.append(child_text)
        return parts

    if node.statement_type in _BLOCK_HEADER_TYPES:
        parts: list[str] = [source] if source else []
        for child in node.children:
            child_text = render_substatement(child)
            if child_text:
                parts.append(child_text)
        closer = _SYNTHETIC_CLOSERS.get(node.statement_type)
        if closer:
            parts.append(closer)
        return parts

    if node.statement_type in _BODY_ONLY_TYPES:
        if not node.children:
            return [source] if source else []
        parts: list[str] = []
        for child in node.children:
            child_text = render_substatement(child)
            if child_text:
                parts.append(child_text)
        return parts

    if source:
        return [source]

    parts: list[str] = []
    for child in node.children:
        child_text = render_substatement(child)
        if child_text:
            parts.append(child_text)
    return parts


def render_analysis_unit_source(unit: AnalysisUnit) -> str:
    """Render the body of an analysis unit with type annotations."""
    lines: list[str] = []
    for body_node in unit.body_nodes:
        lines.append(
            f"-- [{body_node.statement_type}] (lines {body_node.start_line}-{body_node.end_line})"
        )
        lines.append(render_substatement(body_node))
        lines.append("")
    return "\n".join(lines).rstrip()


def _analysis_source_len(node: SubstatementNode) -> int:
    return len(render_substatement(node))


def _estimate_nodes_chars(nodes: list[SubstatementNode]) -> int:
    return sum(_analysis_source_len(node) for node in nodes)


def _line_span(nodes: list[SubstatementNode]) -> tuple[int, int]:
    return min(node.start_line for node in nodes), max(node.end_line for node in nodes)


def _scope_label(statement_type: str) -> str:
    return _SCOPE_LABELS.get(statement_type, statement_type)


def _scope_title(node: SubstatementNode) -> str:
    return f"{_scope_label(node.statement_type)} (строки {node.start_line}-{node.end_line})"


def _chunk_title(nodes: list[SubstatementNode]) -> str:
    start_line, end_line = _line_span(nodes)
    return f"Фрагмент строк {start_line}-{end_line}"


def _node_key_segment(node: SubstatementNode) -> str:
    return f"{node.statement_type.lower()}:{node.seq}"


def _slice_key(nodes: list[SubstatementNode]) -> str:
    if len(nodes) == 1:
        return f"node:{nodes[0].seq}"
    return f"slice:{nodes[0].seq}-{nodes[-1].seq}"


def _node_is_splittable(node: SubstatementNode) -> bool:
    return bool(node.children) and (
        node.statement_type in _SEQUENCE_CONTAINER_TYPES
        or node.statement_type in _BRANCHING_CONTAINER_TYPES
    )


def _header_text(node: SubstatementNode) -> str:
    source = node.source_text.strip()
    if node.statement_type == "DECLARE":
        return "DECLARE"
    if node.statement_type == "IF_THEN":
        return "THEN"
    if node.statement_type == "CASE_ELSE":
        return "ELSE"
    if node.statement_type == "CASE_WHEN":
        return _extract_until_keyword(source, "THEN") or "WHEN"
    if node.statement_type == "CASE":
        first_line = next((line.strip() for line in source.splitlines() if line.strip()), "")
        return first_line or "CASE"
    return source or _scope_label(node.statement_type)


def _extract_until_keyword(source: str, keyword: str) -> str:
    if not source:
        return ""
    match = re.search(rf"\b{re.escape(keyword)}\b", source, re.IGNORECASE)
    if match:
        return source[:match.end()].strip()
    first_line = next((line.strip() for line in source.splitlines() if line.strip()), "")
    return first_line


def plan_substatement_analysis(
    roots: list[SubstatementNode],
    max_chunk_tokens: int = 2000,
) -> AnalysisUnit:
    """
    Build a recursive analysis tree for a subprogram.

    - Small nodes stay intact as code chunks.
    - Oversized compound nodes are expanded recursively by logical scope.
    - IF / CASE create branch-level aggregate nodes before block-level aggregation.
    """
    max_chunk_chars = max_chunk_tokens * 4
    method = AnalysisUnit(
        unit_key="method",
        unit_kind="method",
        statement_type="METHOD",
        title="Метод",
        path=("Метод",),
    )
    method.children = [
        _plan_node(root, max_chunk_chars, method.unit_key, method.path, ())
        for root in roots
    ]
    _finalize_analysis_tree(method)
    return method


def _plan_node(
    node: SubstatementNode,
    max_chunk_chars: int,
    parent_key: str,
    parent_path: tuple[str, ...],
    header_context: tuple[str, ...],
) -> AnalysisUnit:
    node_len = _analysis_source_len(node)
    if node_len <= max_chunk_chars:
        return _make_code_chunk(
            [node],
            parent_key,
            parent_path,
            header_context,
            oversized=False,
        )

    if not _node_is_splittable(node):
        return _make_code_chunk(
            [node],
            parent_key,
            parent_path,
            header_context,
            oversized=True,
        )

    if node.statement_type == "IF":
        return _plan_if_block(node, max_chunk_chars, parent_key, parent_path, header_context)
    if node.statement_type == "CASE":
        return _plan_case_block(node, max_chunk_chars, parent_key, parent_path, header_context)
    return _plan_sequence_container(node, max_chunk_chars, parent_key, parent_path, header_context)


def _plan_if_block(
    node: SubstatementNode,
    max_chunk_chars: int,
    parent_key: str,
    parent_path: tuple[str, ...],
    header_context: tuple[str, ...],
) -> AnalysisUnit:
    title = _scope_title(node)
    path = parent_path + (title,)
    unit_key = f"{parent_key}/{_node_key_segment(node)}"
    if_header = _header_text(node)
    children = [
        _plan_branch_container(child, max_chunk_chars, unit_key, path, header_context + ((if_header,) if if_header else ()))
        for child in node.children
    ]
    return AnalysisUnit(
        unit_key=unit_key,
        unit_kind="block",
        statement_type=node.statement_type,
        title=title,
        path=path,
        header_context=header_context + ((if_header,) if if_header else ()),
        children=children,
        estimated_chars=node_len_safe(node),
    )


def _plan_case_block(
    node: SubstatementNode,
    max_chunk_chars: int,
    parent_key: str,
    parent_path: tuple[str, ...],
    header_context: tuple[str, ...],
) -> AnalysisUnit:
    title = _scope_title(node)
    path = parent_path + (title,)
    unit_key = f"{parent_key}/{_node_key_segment(node)}"
    case_header = _header_text(node)
    children = [
        _plan_branch_container(child, max_chunk_chars, unit_key, path, header_context + ((case_header,) if case_header else ()))
        for child in node.children
    ]
    return AnalysisUnit(
        unit_key=unit_key,
        unit_kind="block",
        statement_type=node.statement_type,
        title=title,
        path=path,
        header_context=header_context + ((case_header,) if case_header else ()),
        children=children,
        estimated_chars=node_len_safe(node),
    )


def _plan_branch_container(
    node: SubstatementNode,
    max_chunk_chars: int,
    parent_key: str,
    parent_path: tuple[str, ...],
    header_context: tuple[str, ...],
) -> AnalysisUnit:
    if not node.children:
        return _make_code_chunk(
            [node],
            parent_key,
            parent_path,
            header_context + _branch_header_context(node),
            oversized=_analysis_source_len(node) > max_chunk_chars,
        )

    branch_header = _branch_header_context(node)
    title = _scope_title(node)
    path = parent_path + (title,)
    unit_key = f"{parent_key}/{_node_key_segment(node)}"
    children = _plan_sequence_children(
        node.children,
        max_chunk_chars,
        unit_key,
        path,
        header_context + branch_header,
    )
    return AnalysisUnit(
        unit_key=unit_key,
        unit_kind="branch",
        statement_type=node.statement_type,
        title=title,
        path=path,
        header_context=header_context + branch_header,
        children=children,
        estimated_chars=node_len_safe(node),
    )


def _branch_header_context(node: SubstatementNode) -> tuple[str, ...]:
    if node.statement_type == "EXCEPTION_HANDLER":
        header = _header_text(node)
        return ("EXCEPTION", header) if header else ("EXCEPTION",)
    header = _header_text(node)
    return (header,) if header else ()


def _plan_sequence_container(
    node: SubstatementNode,
    max_chunk_chars: int,
    parent_key: str,
    parent_path: tuple[str, ...],
    header_context: tuple[str, ...],
) -> AnalysisUnit:
    if not node.children:
        return _make_code_chunk(
            [node],
            parent_key,
            parent_path,
            header_context + _sequence_scope_header(node),
            oversized=_analysis_source_len(node) > max_chunk_chars,
        )

    title = _scope_title(node)
    path = parent_path + (title,)
    unit_key = f"{parent_key}/{_node_key_segment(node)}"
    scope_header = _sequence_scope_header(node)
    children = _plan_sequence_children(
        node.children,
        max_chunk_chars,
        unit_key,
        path,
        header_context + scope_header,
    )
    return AnalysisUnit(
        unit_key=unit_key,
        unit_kind="branch" if node.statement_type == "EXCEPTION_HANDLER" else "block",
        statement_type=node.statement_type,
        title=title,
        path=path,
        header_context=header_context + scope_header,
        children=children,
        estimated_chars=node_len_safe(node),
    )


def _sequence_scope_header(node: SubstatementNode) -> tuple[str, ...]:
    if node.statement_type == "EXCEPTION_HANDLER":
        header = _header_text(node)
        return ("EXCEPTION", header) if header else ("EXCEPTION",)
    header = _header_text(node)
    return (header,) if header else ()


def _plan_sequence_children(
    nodes: list[SubstatementNode],
    max_chunk_chars: int,
    parent_key: str,
    parent_path: tuple[str, ...],
    header_context: tuple[str, ...],
) -> list[AnalysisUnit]:
    children: list[AnalysisUnit] = []
    pending_nodes: list[SubstatementNode] = []
    pending_chars = 0

    def flush_pending() -> None:
        nonlocal pending_nodes, pending_chars
        if not pending_nodes:
            return
        children.append(
            _make_code_chunk(
                pending_nodes,
                parent_key,
                parent_path,
                header_context,
                oversized=pending_chars > max_chunk_chars,
            )
        )
        pending_nodes = []
        pending_chars = 0

    for child in nodes:
        if child.statement_type == "EXCEPTION_HANDLER":
            flush_pending()
            children.append(_plan_node(child, max_chunk_chars, parent_key, parent_path, header_context))
            continue

        child_chars = _analysis_source_len(child)
        if child_chars <= max_chunk_chars:
            if pending_nodes and pending_chars + child_chars > max_chunk_chars:
                flush_pending()
            pending_nodes.append(child)
            pending_chars += child_chars
            continue

        flush_pending()
        children.append(_plan_node(child, max_chunk_chars, parent_key, parent_path, header_context))

    flush_pending()
    return children


def _make_code_chunk(
    nodes: list[SubstatementNode],
    parent_key: str,
    parent_path: tuple[str, ...],
    header_context: tuple[str, ...],
    *,
    oversized: bool,
) -> AnalysisUnit:
    title = _chunk_title(nodes)
    return AnalysisUnit(
        unit_key=f"{parent_key}/{_slice_key(nodes)}",
        unit_kind="code_chunk",
        statement_type=nodes[0].statement_type if len(nodes) == 1 else "SEQUENCE",
        title=title,
        path=parent_path + (title,),
        header_context=header_context,
        body_nodes=list(nodes),
        oversized=oversized,
        estimated_chars=_estimate_nodes_chars(nodes),
    )


def _finalize_analysis_tree(unit: AnalysisUnit) -> None:
    _assign_part_numbers(unit)
    _compute_unit_hash(unit)


def _assign_part_numbers(unit: AnalysisUnit) -> None:
    code_children = [child for child in unit.children if child.unit_kind == "code_chunk"]
    total = len(code_children)
    for index, child in enumerate(code_children, 1):
        child.part_no = index
        child.parts_total = total
    for child in unit.children:
        _assign_part_numbers(child)


def _compute_unit_hash(unit: AnalysisUnit) -> str:
    payload = [
        PLANNER_VERSION,
        unit.unit_kind,
        unit.statement_type,
        unit.title,
        *unit.header_context,
        str(unit.part_no),
        str(unit.parts_total),
        str(unit.oversized),
    ]
    for body_node in unit.body_nodes:
        payload.extend(_tree_source_hashes(body_node))
    for child in unit.children:
        payload.append(_compute_unit_hash(child))
    unit.unit_hash = hashlib.sha256("|".join(payload).encode()).hexdigest()
    return unit.unit_hash


def iter_code_units(unit: AnalysisUnit) -> list[AnalysisUnit]:
    result: list[AnalysisUnit] = []
    if unit.unit_kind == "code_chunk":
        result.append(unit)
    for child in unit.children:
        result.extend(iter_code_units(child))
    return result


def chunk_substatements(
    roots: list[SubstatementNode],
    max_chunk_tokens: int = 2000,
) -> list[list[SubstatementNode]]:
    """
    Compatibility wrapper over the recursive planner.

    Returns only leaf code chunks in execution order so older tests and tools
    can still inspect how the method was split.
    """
    if not roots:
        return []
    max_chunk_chars = max_chunk_tokens * 4
    units: list[SubstatementNode] = []
    for root in roots:
        units.extend(
            _compat_chunk_units_for_node(
                root,
                max_chunk_chars,
                expand_begin_blocks=root.statement_type == "BEGIN_END",
            )
        )

    chunks: list[list[SubstatementNode]] = []
    current_chunk: list[SubstatementNode] = []
    current_chars = 0

    for unit in units:
        unit_chars = _analysis_source_len(unit)
        starts_exception = unit.statement_type == "EXCEPTION_HANDLER"

        if starts_exception and current_chunk:
            chunks.append(current_chunk)
            current_chunk = []
            current_chars = 0

        if current_chunk and current_chars + unit_chars > max_chunk_chars:
            chunks.append(current_chunk)
            current_chunk = []
            current_chars = 0

        current_chunk.append(unit)
        current_chars += unit_chars

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def _compat_chunk_units_for_node(
    node: SubstatementNode,
    max_chunk_chars: int,
    *,
    expand_begin_blocks: bool = False,
) -> list[SubstatementNode]:
    if (
        node.statement_type == "BEGIN_END"
        and node.children
        and (expand_begin_blocks or _analysis_source_len(node) > max_chunk_chars)
    ):
        units: list[SubstatementNode] = []
        for child in node.children:
            units.extend(_compat_chunk_units_for_node(child, max_chunk_chars))
        if units:
            return units
    return [node]


def compute_chunk_hash(chunk: list[SubstatementNode]) -> str:
    all_hashes: list[str] = []
    for root in chunk:
        all_hashes.extend(_tree_source_hashes(root))
    combined = "|".join(all_hashes)
    return hashlib.sha256(combined.encode()).hexdigest()


def total_source_length(roots: list[SubstatementNode]) -> int:
    return sum(_tree_source_len(r) for r in roots)


def node_len_safe(node: SubstatementNode) -> int:
    return _analysis_source_len(node)
