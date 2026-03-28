from __future__ import annotations

import hashlib
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
    children: list[SubstatementNode] = field(default_factory=list)


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

    # Sort children by position at every level
    for node in nodes.values():
        node.children.sort(key=lambda n: n.position)
    roots.sort(key=lambda n: n.position)

    return roots


def _tree_source_len(node: SubstatementNode) -> int:
    """Total source_text length of a node and all its descendants."""
    total = len(node.source_text)
    for child in node.children:
        total += _tree_source_len(child)
    return total


def _tree_source_hashes(node: SubstatementNode) -> list[str]:
    """Collect source_hash values from node and all descendants in DFS order."""
    result = [node.source_hash]
    for child in node.children:
        result.extend(_tree_source_hashes(child))
    return result


def chunk_substatements(
    roots: list[SubstatementNode],
    max_chunk_tokens: int = 2000,
) -> list[list[SubstatementNode]]:
    """
    Group root-level substatements into chunks for LLM analysis.

    Each root node (with all its descendants) stays intact — compound
    structures like IF + THEN/ELSIF/ELSE are never split.

    EXCEPTION_HANDLER always starts a new chunk.
    """
    if not roots:
        return []

    max_chunk_chars = max_chunk_tokens * 4  # rough token estimate

    chunks: list[list[SubstatementNode]] = []
    current_chunk: list[SubstatementNode] = []
    current_chars = 0

    for root in roots:
        root_chars = _tree_source_len(root)

        # EXCEPTION_HANDLER always starts a new chunk
        if root.statement_type == "EXCEPTION_HANDLER" and current_chunk:
            chunks.append(current_chunk)
            current_chunk = []
            current_chars = 0

        # If adding this root exceeds budget and chunk is non-empty, flush
        if current_chunk and current_chars + root_chars > max_chunk_chars:
            chunks.append(current_chunk)
            current_chunk = []
            current_chars = 0

        current_chunk.append(root)
        current_chars += root_chars

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def compute_chunk_hash(chunk: list[SubstatementNode]) -> str:
    """Compute a hash for a chunk based on all substatement source hashes."""
    all_hashes: list[str] = []
    for root in chunk:
        all_hashes.extend(_tree_source_hashes(root))
    combined = "|".join(all_hashes)
    return hashlib.sha256(combined.encode()).hexdigest()


def total_source_length(roots: list[SubstatementNode]) -> int:
    """Total source_text length across all root nodes and their descendants."""
    return sum(_tree_source_len(r) for r in roots)
