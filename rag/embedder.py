from __future__ import annotations

import logging
import os
import sqlite3
from typing import Optional

from rag.sqlite_store import (
    get_parent_chain,
    get_table_accesses_for_object,
    get_unembedded_nodes,
    upsert_embedding,
)

_logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
EMBED_BATCH_SIZE = 32


def _default_model() -> str:
    return os.environ.get("LLM_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)


class EmbeddingClient:
    """OpenAI-compatible embedding client (same endpoint as LLM)."""

    def __init__(self) -> None:
        import openai  # lazy import so tests can mock without installing

        base_url = os.environ.get("LLM_BASE_URL")
        api_key = os.environ.get("LLM_API_KEY", "")
        self._model = _default_model()
        self._client = openai.OpenAI(base_url=base_url, api_key=api_key)

    @property
    def model(self) -> str:
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        _logger.debug("Embedding %d texts with model=%s", len(texts), self._model)
        response = self._client.embeddings.create(model=self._model, input=texts)
        return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]


def build_embed_text(
    node: sqlite3.Row,
    parent_titles: list[str],
    table_accesses: list[sqlite3.Row],
) -> str:
    """
    Build the rich text that will be embedded for a node.

    Format:
        [SCHEMA.OBJECT.SUBPROGRAM]
        Parent title → Parent title
        Node title (lines X–Y)

        LLM description

        Таблицы: TABLE1 (SELECT), TABLE2 (INSERT)

        Код:
        source_text
    """
    schema = node["schema_name"]
    obj = node["object_name"]
    sub = node["subprogram"] or ""

    identifier_parts = [schema, obj]
    if sub:
        identifier_parts.append(sub)
    identifier = ".".join(identifier_parts)

    parts: list[str] = [f"[{identifier}]"]

    # Breadcrumb from parent titles
    filtered_parents = [t for t in parent_titles if t and t.strip()]
    if filtered_parents:
        parts.append(" → ".join(filtered_parents))

    # Title with optional line range
    title = (node["title"] or "").strip()
    start_line = node["start_line"] or 0
    end_line = node["end_line"] or 0
    if start_line and end_line and start_line != end_line:
        title_line = f"{title} (строки {start_line}–{end_line})"
    elif start_line:
        title_line = f"{title} (строка {start_line})"
    else:
        title_line = title
    if title_line:
        parts.append(title_line)

    # Description
    description = (node["description"] or "").strip()
    if description:
        parts.append("")
        parts.append(description)

    # Table accesses
    if table_accesses:
        table_parts = [f"{row['table_name']} ({row['operation']})" for row in table_accesses]
        parts.append("")
        parts.append("Таблицы: " + ", ".join(table_parts))

    # Source code
    source_text = (node["source_text"] or "").strip()
    if source_text:
        parts.append("")
        parts.append("Код:")
        parts.append(source_text)

    return "\n".join(parts)


def run_embed(
    conn: sqlite3.Connection,
    client: EmbeddingClient,
    schema: Optional[str] = None,
    object_name: Optional[str] = None,
) -> int:
    """
    Embed all unembedded canonical nodes.  Returns the count of newly embedded nodes.
    """
    nodes = get_unembedded_nodes(conn, client.model, schema=schema, object_name=object_name)

    if not nodes:
        _logger.info("Все узлы уже проиндексированы.")
        return 0

    _logger.info("Узлов для индексации: %d", len(nodes))

    # Build (id, embed_text) pairs
    texts_and_ids: list[tuple[int, str]] = []
    for node in nodes:
        parents = get_parent_chain(conn, node["run_id"], node["node_id"])
        parent_titles = [p["title"] for p in parents]
        table_accesses = get_table_accesses_for_object(
            conn,
            node["schema_name"],
            node["object_name"],
            node["subprogram"] or "",
        )
        embed_text = build_embed_text(node, parent_titles, table_accesses)
        texts_and_ids.append((node["id"], embed_text))

    # Batch embed and store
    total = 0
    for batch_start in range(0, len(texts_and_ids), EMBED_BATCH_SIZE):
        batch = texts_and_ids[batch_start : batch_start + EMBED_BATCH_SIZE]
        ids = [item[0] for item in batch]
        texts = [item[1] for item in batch]

        embeddings = client.embed(texts)

        for node_id, embed_text, embedding in zip(ids, texts, embeddings):
            upsert_embedding(conn, node_id, client.model, embed_text, embedding)

        total += len(batch)
        _logger.info("Проиндексировано: %d / %d", total, len(texts_and_ids))

    return total
