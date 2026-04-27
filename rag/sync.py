from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from typing import Optional

from app_logging import ensure_logging_configured
from dictconst.sqlite_store import load_constant_usages
from fetcher import sqlite_store as fetcher_store
from rag import sqlite_store
from rag.models import RagDocument
from summarizer.tree_prompts import PROMPT_VERSION
from traversal import sqlite_store as traversal_store

_logger = logging.getLogger(__name__)

_CALL_TITLE_PREFIX = "CALL -> "


def run(
    schema: str,
    object_name: Optional[str] = None,
    subprogram: Optional[str] = None,
) -> None:
    ensure_logging_configured()
    fetcher_store.init_db()

    with fetcher_store._connect() as conn:
        runs = sqlite_store.list_latest_completed_runs(
            conn,
            schema=schema,
            object_name=object_name,
            subprogram=subprogram,
            prompt_version=PROMPT_VERSION,
        )

        if not runs:
            _logger.info(
                "RAG-экспорт: для scope schema=%s%s%s completed summarize-run не найден.",
                schema,
                f", object={object_name}" if object_name else "",
                f", subprogram={subprogram}" if subprogram else "",
            )
            return

        counts = {"method_summary": 0, "method_step": 0, "table_doc": 0}
        table_keys: set[tuple[str, str]] = set()

        for run_row in runs:
            docs, doc_table_keys = _build_method_documents(conn, run_row)
            sqlite_store.delete_method_documents(
                conn,
                run_row["schema_name"],
                run_row["object_name"],
                run_row["object_type"],
                run_row["subprogram"],
            )
            for doc in docs:
                sqlite_store.upsert_document(conn, doc)
                counts[doc.chunk_type] += 1
            table_keys.update(doc_table_keys)

        for table_doc in _build_table_documents(conn, table_keys):
            sqlite_store.upsert_document(conn, table_doc)
            counts[table_doc.chunk_type] += 1

    _logger.info(
        "RAG-экспорт: %d methods, %d steps, %d table docs.",
        counts["method_summary"],
        counts["method_step"],
        counts["table_doc"],
    )


def _build_method_documents(
    conn: sqlite3.Connection,
    run_row: sqlite3.Row,
) -> tuple[list[RagDocument], set[tuple[str, str]]]:
    rows = conn.execute(
        """
        SELECT node_id, node_kind, statement_type, title, start_line, end_line,
               source_text, parent_node_id, position, source_hash, description
        FROM node_description
        WHERE run_id = ?
        ORDER BY CASE WHEN parent_node_id IS NULL THEN 0 ELSE 1 END,
                 parent_node_id,
                 position,
                 node_id
        """,
        (run_row["run_id"],),
    ).fetchall()
    if not rows:
        return [], set()

    children_by_parent: dict[str, list[sqlite3.Row]] = {}
    root_row: sqlite3.Row | None = None
    for row in rows:
        parent_node_id = row["parent_node_id"]
        if parent_node_id is None:
            root_row = row
            continue
        children_by_parent.setdefault(parent_node_id, []).append(row)
    if root_row is None:
        return [], set()

    schema_name = run_row["schema_name"]
    object_name = run_row["object_name"]
    object_type = run_row["object_type"]
    subprogram = run_row["subprogram"]
    method_ref = _method_ref(schema_name, object_name, subprogram)
    root_chunk_id = _method_chunk_id(schema_name, object_name, subprogram, root_row["node_id"])

    table_accesses = traversal_store.get_table_accesses(
        conn,
        schema_name,
        object_name,
        object_type,
        _as_optional_subprogram(subprogram),
    )
    table_refs = [_table_ref(access.table_schema, access.table_name) for access in table_accesses]
    call_edges = traversal_store.get_call_edges(
        conn,
        schema_name,
        object_name,
        object_type,
        _as_optional_subprogram(subprogram),
    )
    callee_refs = [
        _method_ref(callee_schema or schema_name, callee_object, callee_subprogram)
        for callee_schema, callee_object, callee_subprogram in call_edges
    ]
    root_constants = _constant_metadata(conn, root_row["source_text"])

    documents = [
        RagDocument(
            chunk_id=root_chunk_id,
            source_kind="analysis_node",
            chunk_type="method_summary",
            schema_name=schema_name,
            object_name=object_name,
            object_type=object_type,
            subprogram=subprogram,
            title=root_row["title"] or method_ref,
            summary_text=root_row["description"] or "",
            content_text=_build_method_summary_text(
                method_ref=method_ref,
                object_type=object_type,
                summary_text=root_row["description"] or "",
                table_refs=table_refs,
                callee_refs=callee_refs,
                dict_constants=root_constants,
            ),
            code_text=root_row["source_text"] or "",
            parent_chunk_id=None,
            node_id=root_row["node_id"],
            run_id=run_row["run_id"],
            start_line=root_row["start_line"],
            end_line=root_row["end_line"],
            source_hash=root_row["source_hash"] or "",
            prompt_version=run_row["prompt_version"],
            metadata_json=_json(
                {
                    "method_ref": method_ref,
                    "node_kind": root_row["node_kind"],
                    "statement_type": root_row["statement_type"],
                    "table_refs": table_refs,
                    "callee_refs": callee_refs,
                    "dict_constants": root_constants,
                    "child_chunk_ids": [
                        _method_chunk_id(schema_name, object_name, subprogram, child["node_id"])
                        for child in children_by_parent.get(root_row["node_id"], [])
                    ],
                    "code_char_count": len(root_row["source_text"] or ""),
                }
            ),
        )
    ]

    for row in rows:
        if row["node_id"] == root_row["node_id"]:
            continue

        parent_chunk_id = root_chunk_id
        if row["parent_node_id"]:
            parent_chunk_id = _method_chunk_id(
                schema_name,
                object_name,
                subprogram,
                row["parent_node_id"],
            )
        chunk_id = _method_chunk_id(schema_name, object_name, subprogram, row["node_id"])
        step_table_refs = _match_table_refs(row["source_text"], table_refs)
        child_callees = [
            callee
            for child in children_by_parent.get(row["node_id"], [])
            for callee in _callee_refs_from_row(child)
        ]
        node_callees = _callee_refs_from_row(row)
        dict_constants = _constant_metadata(conn, row["source_text"])

        documents.append(
            RagDocument(
                chunk_id=chunk_id,
                source_kind="analysis_node",
                chunk_type="method_step",
                schema_name=schema_name,
                object_name=object_name,
                object_type=object_type,
                subprogram=subprogram,
                title=row["title"] or row["statement_type"],
                summary_text=row["description"] or "",
                content_text=_build_method_step_text(
                    method_ref=method_ref,
                    title=row["title"] or row["statement_type"],
                    node_kind=row["node_kind"],
                    statement_type=row["statement_type"],
                    summary_text=row["description"] or "",
                    table_refs=step_table_refs,
                    callee_refs=node_callees or child_callees,
                    dict_constants=dict_constants,
                    code_text=row["source_text"] or "",
                ),
                code_text=row["source_text"] or "",
                parent_chunk_id=parent_chunk_id,
                node_id=row["node_id"],
                run_id=run_row["run_id"],
                start_line=row["start_line"],
                end_line=row["end_line"],
                source_hash=row["source_hash"] or "",
                prompt_version=run_row["prompt_version"],
                metadata_json=_json(
                    {
                        "method_ref": method_ref,
                        "node_kind": row["node_kind"],
                        "statement_type": row["statement_type"],
                        "table_refs": step_table_refs,
                        "callee_refs": node_callees or child_callees,
                        "dict_constants": dict_constants,
                        "parent_node_id": row["parent_node_id"],
                        "child_chunk_ids": [
                            _method_chunk_id(schema_name, object_name, subprogram, child["node_id"])
                            for child in children_by_parent.get(row["node_id"], [])
                        ],
                    }
                ),
            )
        )

    return documents, {(access.table_schema or schema_name, access.table_name) for access in table_accesses}


def _build_table_documents(
    conn: sqlite3.Connection,
    table_keys: set[tuple[str, str]],
) -> list[RagDocument]:
    docs: list[RagDocument] = []
    for schema_name, table_name in sorted(table_keys):
        table_row = sqlite_store.load_table_row(conn, schema_name, table_name)
        column_rows = sqlite_store.load_table_columns(conn, schema_name, table_name)
        object_type = table_row["object_type"] if table_row else ""
        table_comment = table_row["table_comment"] if table_row else None
        title = _table_ref(schema_name, table_name)
        content_text = _build_table_text(
            title=title,
            object_type=object_type,
            table_comment=table_comment,
            column_rows=column_rows,
        )
        docs.append(
            RagDocument(
                chunk_id=f"table:{title}",
                source_kind="table_metadata",
                chunk_type="table_doc",
                schema_name=schema_name.upper(),
                object_name=table_name.upper(),
                object_type=object_type or "",
                title=title,
                summary_text=table_comment or "",
                content_text=content_text,
                source_hash=_hash_text(content_text),
                metadata_json=_json(
                    {
                        "table_ref": title,
                        "column_count": len(column_rows),
                        "columns": [
                            {
                                "name": row["column_name"],
                                "type": row["data_type"],
                                "nullable": None if row["nullable"] is None else bool(row["nullable"]),
                                "comment": row["column_comment"],
                            }
                            for row in column_rows
                        ],
                    }
                ),
            )
        )
    return docs


def _build_method_summary_text(
    method_ref: str,
    object_type: str,
    summary_text: str,
    table_refs: list[str],
    callee_refs: list[str],
    dict_constants: list[dict[str, str | None]],
) -> str:
    lines = [
        f"Метод: {method_ref}",
        f"Тип объекта: {object_type}",
        f"Назначение: {summary_text or 'Описание отсутствует.'}",
    ]
    if callee_refs:
        lines.append(f"Прямые вызовы: {', '.join(callee_refs)}")
    if table_refs:
        lines.append(f"Таблицы: {', '.join(table_refs)}")
    if dict_constants:
        lines.append(
            "Словарные константы: "
            + ", ".join(
                _render_constant_item(item["const_name"], item.get("resolved_text"))
                for item in dict_constants
            )
        )
    return "\n".join(lines)


def _build_method_step_text(
    method_ref: str,
    title: str,
    node_kind: str,
    statement_type: str,
    summary_text: str,
    table_refs: list[str],
    callee_refs: list[str],
    dict_constants: list[dict[str, str | None]],
    code_text: str,
) -> str:
    lines = [
        f"Метод: {method_ref}",
        f"Шаг: {title}",
        f"Тип узла: {node_kind}",
        f"Тип оператора: {statement_type}",
        f"Смысл: {summary_text or 'Описание отсутствует.'}",
    ]
    if callee_refs:
        lines.append(f"Связанные вызовы: {', '.join(callee_refs)}")
    if table_refs:
        lines.append(f"Связанные таблицы: {', '.join(table_refs)}")
    if dict_constants:
        lines.append(
            "Словарные константы: "
            + ", ".join(
                _render_constant_item(item["const_name"], item.get("resolved_text"))
                for item in dict_constants
            )
        )
    if code_text:
        lines.append("Код:")
        lines.append(code_text.strip())
    return "\n".join(lines)


def _build_table_text(
    title: str,
    object_type: Optional[str],
    table_comment: Optional[str],
    column_rows: list[sqlite3.Row],
) -> str:
    lines = [
        f"Таблица: {title}",
        f"Тип: {object_type or 'UNKNOWN'}",
        f"Описание: {table_comment or 'Описание отсутствует.'}",
    ]
    if column_rows:
        lines.append("Колонки:")
        for row in column_rows:
            nullable = ""
            if row["nullable"] is not None:
                nullable = " NULL" if bool(row["nullable"]) else " NOT NULL"
            comment = f" — {row['column_comment']}" if row["column_comment"] else ""
            lines.append(f"- {row['column_name']} {row['data_type'] or 'UNKNOWN'}{nullable}{comment}")
    return "\n".join(lines)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _as_optional_subprogram(value: str) -> Optional[str]:
    return value or None


def _method_ref(schema_name: str, object_name: str, subprogram: Optional[str]) -> str:
    parts = [schema_name.upper(), object_name.upper()]
    if subprogram:
        parts.append(subprogram.upper())
    return ".".join(parts)


def _table_ref(schema_name: Optional[str], table_name: str) -> str:
    if schema_name:
        return f"{schema_name.upper()}.{table_name.upper()}"
    return table_name.upper()


def _method_chunk_id(
    schema_name: str,
    object_name: str,
    subprogram: Optional[str],
    node_id: str,
) -> str:
    return f"method:{_method_ref(schema_name, object_name, subprogram)}:{node_id}"


def _match_table_refs(source_text: str, table_refs: list[str]) -> list[str]:
    if not source_text:
        return []
    return [
        table_ref
        for table_ref in table_refs
        if _contains_identifier(source_text, table_ref.split(".")[-1])
    ]


def _contains_identifier(text: str, identifier: str) -> bool:
    return bool(
        re.search(
            rf"(?<![A-Z0-9_$#]){re.escape(identifier)}(?![A-Z0-9_$#])",
            text or "",
            re.IGNORECASE,
        )
    )


def _callee_refs_from_row(row: sqlite3.Row) -> list[str]:
    title = row["title"] or ""
    if not title.startswith(_CALL_TITLE_PREFIX):
        return []
    return [title[len(_CALL_TITLE_PREFIX):].strip().upper()]


def _constant_metadata(
    conn: sqlite3.Connection,
    source_text: str,
) -> list[dict[str, str | None]]:
    return [
        {
            "const_name": usage.const_name,
            "shortname": usage.shortname,
            "fullname": usage.fullname,
            "resolved_text": usage.resolved_text,
        }
        for usage in load_constant_usages(conn, source_text or "")
    ]


def _render_constant_item(const_name: str, resolved_text: Optional[str]) -> str:
    if resolved_text:
        return f"{const_name}={resolved_text}"
    return const_name
