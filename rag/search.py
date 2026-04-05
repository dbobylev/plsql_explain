from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from rag.sqlite_store import load_all_embeddings


@dataclass
class SearchResult:
    schema_name: str
    object_name: str
    subprogram: str
    node_kind: str
    statement_type: str
    title: str
    description: str
    source_text: str
    start_line: int
    end_line: int
    score: float
    embed_text: str


def search(
    conn: sqlite3.Connection,
    query: str,
    client: object,  # EmbeddingClient (avoid circular import)
    schema: Optional[str] = None,
    object_name: Optional[str] = None,
    top_k: int = 10,
) -> list[SearchResult]:
    """
    Semantic search over embedded nodes.

    Embeds ``query``, then ranks all stored embeddings by cosine similarity
    and returns the top ``top_k`` results.
    """
    import numpy as np

    query_vec = np.array(client.embed([query])[0], dtype=np.float32)
    norm = float(np.linalg.norm(query_vec))
    if norm > 0:
        query_vec /= norm

    entries = load_all_embeddings(conn, client.model, schema=schema, object_name=object_name)
    if not entries:
        return []

    rows = [row for row, _ in entries]
    matrix = np.array([emb for _, emb in entries], dtype=np.float32)

    row_norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    row_norms = np.where(row_norms > 0, row_norms, 1.0)
    matrix /= row_norms

    scores: list[float] = (matrix @ query_vec).tolist()

    top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]

    return [
        SearchResult(
            schema_name=rows[i]["schema_name"],
            object_name=rows[i]["object_name"],
            subprogram=rows[i]["subprogram"] or "",
            node_kind=rows[i]["node_kind"],
            statement_type=rows[i]["statement_type"],
            title=rows[i]["title"],
            description=rows[i]["description"],
            source_text=rows[i]["source_text"] or "",
            start_line=rows[i]["start_line"] or 0,
            end_line=rows[i]["end_line"] or 0,
            score=scores[i],
            embed_text=rows[i]["embed_text"],
        )
        for i in top_indices
    ]
