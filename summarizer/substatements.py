from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Optional


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
    preceding_comment: str = ""
    children: list["SubstatementNode"] = field(default_factory=list)


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
               start_line, end_line, source_text, preceding_comment, source_hash
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
            preceding_comment=r["preceding_comment"],
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


def total_source_length(roots: list[SubstatementNode]) -> int:
    return sum(_tree_source_len(r) for r in roots)
