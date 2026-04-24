from __future__ import annotations

import json
import logging
import hashlib
from typing import Any, Optional

from app_logging import ensure_logging_configured
from fetcher import sqlite_store as fetcher_store
from rag.embed_client import EmbeddingClient
from rag.qdrant_client import QdrantClient
from rag import sqlite_store

_logger = logging.getLogger(__name__)

_DEFAULT_DISTANCE = "Cosine"


def run_index(
    collection: str,
    schema: Optional[str] = None,
    object_name: Optional[str] = None,
    subprogram: Optional[str] = None,
    chunk_types: Optional[list[str]] = None,
    batch_size: int = 16,
    vector_size: Optional[int] = None,
    distance: str = _DEFAULT_DISTANCE,
) -> int:
    ensure_logging_configured()
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    fetcher_store.init_db()

    with fetcher_store._connect() as conn:
        rows = sqlite_store.list_documents(
            conn,
            schema=schema,
            object_name=object_name,
            subprogram=subprogram,
            chunk_types=chunk_types,
        )

    if not rows:
        _logger.info(
            "RAG-индексация: в SQLite не найдено документов для scope schema=%s%s%s.",
            schema or "*",
            f", object={object_name}" if object_name else "",
            f", subprogram={subprogram}" if subprogram else "",
        )
        return 0

    qdrant = QdrantClient()
    _ensure_collection(qdrant, collection, vector_size=vector_size, distance=distance)
    embedder = EmbeddingClient()

    uploaded = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        vectors = embedder.embed_texts([row["content_text"] for row in batch])
        points = [
            {
                "id": _point_id(row["chunk_id"]),
                "vector": vector,
                "payload": _build_payload(row),
            }
            for row, vector in zip(batch, vectors, strict=True)
        ]
        qdrant.upsert_points(collection, points)
        uploaded += len(points)
        _logger.info(
            "RAG-индексация: загружено %d/%d точек в collection=%s.",
            uploaded,
            len(rows),
            collection,
        )

    return uploaded


def run_search(
    query: str,
    collection: str,
    limit: int = 5,
    schema: Optional[str] = None,
    object_name: Optional[str] = None,
    subprogram: Optional[str] = None,
    chunk_types: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    ensure_logging_configured()
    embedder = EmbeddingClient()
    qdrant = QdrantClient()
    vector = embedder.embed_texts([query])[0]
    query_filter = _build_qdrant_filter(
        schema=schema,
        object_name=object_name,
        subprogram=subprogram,
        chunk_types=chunk_types,
    )
    return qdrant.search(
        collection=collection,
        vector=vector,
        limit=limit,
        query_filter=query_filter,
        with_payload=True,
    )


def _ensure_collection(
    client: QdrantClient,
    collection: str,
    vector_size: Optional[int],
    distance: str,
) -> None:
    existing = client.get_collection(collection)
    if existing is not None:
        existing_size = _extract_vector_size(existing)
        if vector_size is not None and existing_size is not None and existing_size != vector_size:
            raise ValueError(
                f"Collection '{collection}' already exists with vector size {existing_size},"
                f" expected {vector_size}"
            )
        return
    if vector_size is None:
        raise ValueError(
            f"Collection '{collection}' does not exist, so --vector-size is required"
        )
    client.create_collection(collection, vector_size=vector_size, distance=distance)


def _extract_vector_size(response: dict[str, Any]) -> int | None:
    result = response.get("result", response)
    config = result.get("config") if isinstance(result, dict) else None
    params = config.get("params") if isinstance(config, dict) else None
    vectors = params.get("vectors") if isinstance(params, dict) else None
    if isinstance(vectors, dict) and "size" in vectors:
        size = vectors.get("size")
        return int(size) if size is not None else None
    return None


def _point_id(chunk_id: str) -> int:
    digest = hashlib.sha256(chunk_id.encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)
    return value or 1


def _build_payload(row: Any) -> dict[str, Any]:
    metadata = _parse_metadata_json(row["metadata_json"])
    payload = {
        "chunk_id": row["chunk_id"],
        "source_kind": row["source_kind"],
        "chunk_type": row["chunk_type"],
        "schema_name": row["schema_name"],
        "object_name": row["object_name"],
        "object_type": row["object_type"],
        "subprogram": row["subprogram"],
        "title": row["title"],
        "summary_text": row["summary_text"],
        "parent_chunk_id": row["parent_chunk_id"],
        "node_id": row["node_id"],
        "run_id": row["run_id"],
        "start_line": row["start_line"],
        "end_line": row["end_line"],
        "source_hash": row["source_hash"],
        "prompt_version": row["prompt_version"],
    }
    if metadata:
        payload["metadata"] = metadata
    return payload


def _parse_metadata_json(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _build_qdrant_filter(
    schema: Optional[str],
    object_name: Optional[str],
    subprogram: Optional[str],
    chunk_types: Optional[list[str]],
) -> dict[str, Any] | None:
    must: list[dict[str, Any]] = []
    if schema:
        must.append({"key": "schema_name", "match": {"value": schema.upper()}})
    if object_name:
        must.append({"key": "object_name", "match": {"value": object_name.upper()}})
    if subprogram:
        must.append({"key": "subprogram", "match": {"value": subprogram.upper()}})
    if chunk_types:
        if len(chunk_types) == 1:
            must.append({"key": "chunk_type", "match": {"value": chunk_types[0]}})
        else:
            must.append({"key": "chunk_type", "match": {"any": chunk_types}})
    if not must:
        return None
    return {"must": must}
