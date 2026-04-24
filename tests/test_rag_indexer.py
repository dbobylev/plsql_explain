from __future__ import annotations

import json
from unittest.mock import Mock, patch

from rag import indexer


def _insert_rag_document(
    conn,
    chunk_id: str,
    chunk_type: str,
    title: str,
    content_text: str,
    summary_text: str = "",
    schema_name: str = "S",
    object_name: str = "PKG_A",
    subprogram: str = "PROC_MAIN",
) -> None:
    conn.execute(
        """
        INSERT INTO rag_document
            (chunk_id, source_kind, chunk_type, schema_name, object_name, object_type,
             subprogram, title, summary_text, content_text, code_text, parent_chunk_id,
             node_id, run_id, start_line, end_line, source_hash, prompt_version,
             metadata_json, refreshed_at)
        VALUES (?, 'analysis_node', ?, ?, ?, 'PACKAGE BODY',
                ?, ?, ?, ?, '', NULL, '', 'run-1', 1, 3, 'hash', 'pv1',
                ?, '2026-01-01T00:00:00+00:00')
        """,
        (
            chunk_id,
            chunk_type,
            schema_name,
            object_name,
            subprogram,
            title,
            summary_text,
            content_text,
            json.dumps({"method_ref": "S.PKG_A.PROC_MAIN"}, ensure_ascii=False),
        ),
    )


def test_run_index_uploads_documents_to_qdrant(mem_conn) -> None:
    _insert_rag_document(
        mem_conn,
        chunk_id="method:S.PKG_A.PROC_MAIN:root",
        chunk_type="method_summary",
        title="PKG_A.PROC_MAIN",
        summary_text="Обрабатывает заказ.",
        content_text="Метод: S.PKG_A.PROC_MAIN\nНазначение: Обрабатывает заказ.",
    )
    _insert_rag_document(
        mem_conn,
        chunk_id="method:S.PKG_A.PROC_MAIN:step1",
        chunk_type="method_step",
        title="SQL_SELECT",
        summary_text="Читает заказ.",
        content_text="Метод: S.PKG_A.PROC_MAIN\nШаг: SQL_SELECT\nСмысл: Читает заказ.",
    )
    mem_conn.commit()

    embedder = Mock()
    embedder.embed_texts.return_value = [[0.1, 0.2], [0.3, 0.4]]
    qdrant = Mock()
    qdrant.get_collection.return_value = None

    with patch("fetcher.sqlite_store.init_db"), \
         patch("fetcher.sqlite_store._connect", return_value=mem_conn), \
         patch("rag.indexer.EmbeddingClient", return_value=embedder), \
         patch("rag.indexer.QdrantClient", return_value=qdrant):
        uploaded = indexer.run_index(
            collection="plsql_rag",
            schema="S",
            object_name="PKG_A",
            subprogram="PROC_MAIN",
            batch_size=2,
            vector_size=1536,
        )

    assert uploaded == 2
    qdrant.create_collection.assert_called_once_with("plsql_rag", vector_size=1536, distance="Cosine")
    embedder.embed_texts.assert_called_once()
    qdrant.upsert_points.assert_called_once()
    points = qdrant.upsert_points.call_args.args[1]
    assert points[0]["payload"]["chunk_id"] == "method:S.PKG_A.PROC_MAIN:root"
    assert points[0]["payload"]["chunk_type"] == "method_summary"
    assert points[0]["payload"]["summary_text"] == "Обрабатывает заказ."
    assert points[0]["payload"]["metadata"] == {"method_ref": "S.PKG_A.PROC_MAIN"}
    assert isinstance(points[0]["id"], int)


def test_run_search_builds_query_filter_and_returns_hits() -> None:
    embedder = Mock()
    embedder.embed_texts.return_value = [[0.5, 0.6]]
    qdrant = Mock()
    qdrant.search.return_value = [{"score": 0.99, "payload": {"chunk_id": "method:1"}}]

    with patch("rag.indexer.EmbeddingClient", return_value=embedder), \
         patch("rag.indexer.QdrantClient", return_value=qdrant):
        hits = indexer.run_search(
            query="Как считается сумма заказа?",
            collection="plsql_rag",
            limit=3,
            schema="S",
            object_name="PKG_A",
            subprogram="PROC_MAIN",
            chunk_types=["method_summary", "method_step"],
        )

    assert hits == [{"score": 0.99, "payload": {"chunk_id": "method:1"}}]
    qdrant.search.assert_called_once()
    kwargs = qdrant.search.call_args.kwargs
    assert kwargs["collection"] == "plsql_rag"
    assert kwargs["limit"] == 3
    assert kwargs["vector"] == [0.5, 0.6]
    assert kwargs["query_filter"] == {
        "must": [
            {"key": "schema_name", "match": {"value": "S"}},
            {"key": "object_name", "match": {"value": "PKG_A"}},
            {"key": "subprogram", "match": {"value": "PROC_MAIN"}},
            {"key": "chunk_type", "match": {"any": ["method_summary", "method_step"]}},
        ]
    }


def test_run_index_requires_vector_size_for_missing_collection(mem_conn) -> None:
    _insert_rag_document(
        mem_conn,
        chunk_id="method:S.PKG_A.PROC_MAIN:root",
        chunk_type="method_summary",
        title="PKG_A.PROC_MAIN",
        summary_text="Обрабатывает заказ.",
        content_text="Метод: S.PKG_A.PROC_MAIN\nНазначение: Обрабатывает заказ.",
    )
    mem_conn.commit()

    qdrant = Mock()
    qdrant.get_collection.return_value = None

    with patch("fetcher.sqlite_store.init_db"), \
         patch("fetcher.sqlite_store._connect", return_value=mem_conn), \
         patch("rag.indexer.QdrantClient", return_value=qdrant), \
         patch("rag.indexer.EmbeddingClient"):
        try:
            indexer.run_index(collection="plsql_rag", schema="S")
        except ValueError as exc:
            assert "--vector-size" in str(exc)
        else:
            raise AssertionError("Expected ValueError")


def test_run_index_validates_existing_collection_vector_size(mem_conn) -> None:
    _insert_rag_document(
        mem_conn,
        chunk_id="method:S.PKG_A.PROC_MAIN:root",
        chunk_type="method_summary",
        title="PKG_A.PROC_MAIN",
        summary_text="Обрабатывает заказ.",
        content_text="Метод: S.PKG_A.PROC_MAIN\nНазначение: Обрабатывает заказ.",
    )
    mem_conn.commit()

    qdrant = Mock()
    qdrant.get_collection.return_value = {
        "result": {
            "config": {
                "params": {
                    "vectors": {
                        "size": 768,
                    }
                }
            }
        }
    }

    with patch("fetcher.sqlite_store.init_db"), \
         patch("fetcher.sqlite_store._connect", return_value=mem_conn), \
         patch("rag.indexer.QdrantClient", return_value=qdrant), \
         patch("rag.indexer.EmbeddingClient"):
        try:
            indexer.run_index(
                collection="plsql_rag",
                schema="S",
                vector_size=1536,
            )
        except ValueError as exc:
            assert "vector size 768" in str(exc)
        else:
            raise AssertionError("Expected ValueError")
