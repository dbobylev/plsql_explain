from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

from rag import sync
from summarizer.tree_prompts import PROMPT_VERSION


def _insert_analysis_run(
    conn: sqlite3.Connection,
    run_id: str,
    schema_name: str = "S",
    object_name: str = "PKG_A",
    object_type: str = "PACKAGE BODY",
    subprogram: str = "PROC_MAIN",
) -> None:
    conn.execute(
        """
        INSERT INTO analysis_run
            (run_id, schema_name, object_name, object_type, subprogram,
             prompt_version, status, started_at, finished_at, error_message)
        VALUES (?, ?, ?, ?, ?, ?, 'completed', '2026-01-01T00:00:00+00:00',
                '2026-01-01T00:01:00+00:00', NULL)
        """,
        (run_id, schema_name, object_name, object_type, subprogram, PROMPT_VERSION),
    )


def _insert_node(
    conn: sqlite3.Connection,
    run_id: str,
    node_id: str,
    node_kind: str,
    statement_type: str,
    title: str,
    description: str,
    source_text: str,
    parent_node_id: str | None,
    position: int,
) -> None:
    conn.execute(
        """
        INSERT INTO node_description
            (run_id, schema_name, object_name, object_type, subprogram,
             node_id, node_kind, statement_type, title,
             start_line, end_line, source_text, parent_node_id, position,
             source_hash, description, prompt_version, described_at)
        VALUES (?, 'S', 'PKG_A', 'PACKAGE BODY', 'PROC_MAIN',
                ?, ?, ?, ?, 1, 10, ?, ?, ?, ?, ?, ?, '2026-01-01T00:01:00+00:00')
        """,
        (
            run_id,
            node_id,
            node_kind,
            statement_type,
            title,
            source_text,
            parent_node_id,
            position,
            f"hash:{node_id}",
            description,
            PROMPT_VERSION,
        ),
    )


def test_build_rag_exports_method_and_table_docs(mem_conn: sqlite3.Connection) -> None:
    run_id = "run-1"
    _insert_analysis_run(mem_conn, run_id)
    _insert_node(
        mem_conn,
        run_id,
        "S.PKG_A.PROC_MAIN/root",
        "method_root",
        "METHOD",
        "PKG_A.PROC_MAIN",
        "Обрабатывает заказ и готовит данные.",
        "BEGIN v_status := c.get('ORDER_STATUS'); pkg_b.do_work; SELECT status FROM orders; END;",
        None,
        0,
    )
    _insert_node(
        mem_conn,
        run_id,
        "S.PKG_A.PROC_MAIN/seq:0",
        "statement",
        "SQL_SELECT",
        "SQL_SELECT",
        "Читает заказ из таблицы orders.",
        "SELECT status FROM orders WHERE id = p_id",
        "S.PKG_A.PROC_MAIN/root",
        0,
    )
    _insert_node(
        mem_conn,
        run_id,
        "S.PKG_A.PROC_MAIN/call:S.PKG_B.DO_WORK:0",
        "call",
        "CALL",
        "CALL -> PKG_B.DO_WORK",
        "Вызывает дочерний расчет.",
        "",
        "S.PKG_A.PROC_MAIN/root",
        1,
    )

    mem_conn.execute(
        """
        INSERT INTO call_edge
            (caller_schema, caller_object, caller_type, caller_subprogram,
             callee_schema, callee_object, callee_subprogram)
        VALUES ('S', 'PKG_A', 'PACKAGE BODY', 'PROC_MAIN', 'S', 'PKG_B', 'DO_WORK')
        """
    )
    mem_conn.execute(
        """
        INSERT INTO table_access
            (schema_name, object_name, object_type, subprogram,
             table_schema, table_name, operation)
        VALUES ('S', 'PKG_A', 'PACKAGE BODY', 'PROC_MAIN', 'S', 'ORDERS', 'SELECT')
        """
    )
    mem_conn.execute(
        """
        INSERT INTO table_metadata
            (schema_name, table_name, object_type, table_comment, refreshed_at)
        VALUES ('S', 'ORDERS', 'TABLE', 'Заказы клиентов', '2026-01-01T00:00:00+00:00')
        """
    )
    mem_conn.execute(
        """
        INSERT INTO column_metadata
            (schema_name, table_name, column_name, column_id,
             data_type, nullable, column_comment, refreshed_at)
        VALUES ('S', 'ORDERS', 'STATUS', 1, 'VARCHAR2', 0, 'Статус заказа',
                '2026-01-01T00:00:00+00:00')
        """
    )
    mem_conn.execute(
        """
        INSERT INTO dict_constant
            (const_name, shortname, fullname, resolved_text, refreshed_at)
        VALUES ('ORDER_STATUS', 'PAID', 'Заказ оплачен', 'Заказ оплачен',
                '2026-01-01T00:00:00+00:00')
        """
    )
    mem_conn.execute(
        """
        INSERT INTO rag_document
            (chunk_id, source_kind, chunk_type, schema_name, object_name, object_type,
             subprogram, title, summary_text, content_text, code_text, parent_chunk_id,
             node_id, run_id, start_line, end_line, source_hash, prompt_version,
             metadata_json, refreshed_at)
        VALUES ('stale', 'analysis_node', 'method_step', 'S', 'PKG_A', 'PACKAGE BODY',
                'PROC_MAIN', 'stale', '', '', '', NULL, '', '', 0, 0, '', '',
                '{}', '2026-01-01T00:00:00+00:00')
        """
    )
    mem_conn.commit()

    with patch("fetcher.sqlite_store.init_db"), patch("fetcher.sqlite_store._connect", return_value=mem_conn):
        sync.run(schema="S", object_name="PKG_A", subprogram="PROC_MAIN")

    rows = mem_conn.execute(
        "SELECT chunk_id, chunk_type, title, content_text, metadata_json FROM rag_document ORDER BY chunk_id"
    ).fetchall()

    assert sorted(row["chunk_type"] for row in rows) == [
        "method_step",
        "method_step",
        "method_summary",
        "table_doc",
    ]
    assert all(row["chunk_id"] != "stale" for row in rows)

    summary_row = next(row for row in rows if row["chunk_type"] == "method_summary")
    assert "S.PKG_A.PROC_MAIN" in summary_row["content_text"]
    assert "S.PKG_B.DO_WORK" in summary_row["content_text"]
    assert "S.ORDERS" in summary_row["content_text"]
    assert "ORDER_STATUS=Заказ оплачен" in summary_row["content_text"]
    summary_meta = json.loads(summary_row["metadata_json"])
    assert summary_meta["table_refs"] == ["S.ORDERS"]
    assert summary_meta["callee_refs"] == ["S.PKG_B.DO_WORK"]

    sql_row = next(row for row in rows if row["title"] == "SQL_SELECT")
    sql_meta = json.loads(sql_row["metadata_json"])
    assert sql_meta["table_refs"] == ["S.ORDERS"]

    table_row = next(row for row in rows if row["chunk_type"] == "table_doc")
    assert "Заказы клиентов" in table_row["content_text"]
    assert "STATUS VARCHAR2 NOT NULL" in table_row["content_text"]
