from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

from fetcher import sqlite_store as fetcher_store
from indexer import sqlite_store as indexer_store
from parser import runner
from parser.runner import ParserError


def run(
    schema: str,
    object_name: str | None = None,
    force: bool = False,
) -> None:
    fetcher_store.init_db()

    counts = {"parsed": 0, "wrapped": 0, "error": 0, "unchanged": 0}

    with fetcher_store._connect() as conn:
        query = "SELECT schema_name, object_name, object_type, source_text, source_hash FROM object_source WHERE schema_name=?"
        params: list = [schema.upper()]
        if object_name:
            query += " AND object_name=?"
            params.append(object_name.upper())

        rows = conn.execute(query, params).fetchall()

    with fetcher_store._connect() as conn:
        for row in rows:
            schema_name = row["schema_name"]
            name = row["object_name"]
            obj_type = row["object_type"]
            source_text = row["source_text"]
            current_hash = row["source_hash"]

            if not force:
                parsed_hash = indexer_store.get_parse_hash(conn, schema_name, name, obj_type)
                if parsed_hash == current_hash:
                    counts["unchanged"] += 1
                    continue

            try:
                output = runner.parse_object(schema_name, name, obj_type, source_text)
            except ParserError as e:
                print(f"  [ERROR] {schema_name}.{name}: {e}")
                counts["error"] += 1
                continue

            with conn:
                indexer_store.replace_call_edges(conn, schema_name, name, obj_type, output.call_edges)
                indexer_store.replace_table_accesses(conn, schema_name, name, obj_type, output.table_accesses)
                indexer_store.replace_subprograms(conn, schema_name, name, obj_type, output.subprograms)
                indexer_store.replace_substatements(conn, schema_name, name, obj_type, output.substatements)
                indexer_store.upsert_parse_result(
                    conn, schema_name, name, obj_type, current_hash, output.status, output.error_message
                )

            if output.status == "ok":
                counts["parsed"] += 1
                print(f"  [ok] {schema_name}.{name} ({obj_type})")
            elif output.status == "wrapped":
                counts["wrapped"] += 1
            else:
                counts["error"] += 1
                print(f"  [WARN] {schema_name}.{name}: {output.error_message}")

    total = sum(counts.values())
    print(
        f"\nГотово: всего {total} объектов — "
        f"{counts['parsed']} распарсено, {counts['wrapped']} wrapped, "
        f"{counts['error']} ошибок, {counts['unchanged']} без изменений."
    )
