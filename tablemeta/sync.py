from __future__ import annotations

import logging

from dotenv import load_dotenv

from app_logging import ensure_logging_configured
from fetcher import sqlite_store as fetcher_store
from tablemeta import oracle_client, sqlite_store
from tablemeta.models import TableMetadataRecord

load_dotenv()

_logger = logging.getLogger(__name__)


def run(
    schema: str,
    object_name: str | None = None,
) -> None:
    ensure_logging_configured()
    fetcher_store.init_db()

    with fetcher_store._connect() as conn:
        refs = sqlite_store.list_referenced_tables(conn, schema, object_name)

    if not refs:
        _logger.info("Метаданные таблиц: для scope schema=%s%s ссылки на таблицы не найдены.", schema, f", object={object_name}" if object_name else "")
        return

    _logger.debug(
        "tablemeta.sync.run started: schema=%s, object_name=%s, refs=%d",
        schema,
        object_name,
        len(refs),
    )

    table_records, column_records = oracle_client.fetch_table_metadata(refs)
    table_map = {(record.schema_name, record.table_name): record for record in table_records}
    columns_map: dict[tuple[str, str], list] = {}
    for column in column_records:
        columns_map.setdefault((column.schema_name, column.table_name), []).append(column)

    counts = {"refreshed": 0, "missing": 0}
    with fetcher_store._connect() as conn:
        for ref in refs:
            key = (ref.schema_name, ref.table_name)
            record = table_map.get(
                key,
                TableMetadataRecord(
                    schema_name=ref.schema_name,
                    table_name=ref.table_name,
                    object_type=None,
                    table_comment=None,
                ),
            )
            columns = columns_map.get(key, [])
            with conn:
                sqlite_store.replace_table_metadata(conn, record, columns)
            if key in table_map:
                counts["refreshed"] += 1
            else:
                counts["missing"] += 1
                _logger.warning("  [WARN] metadata not found for %s.%s", ref.schema_name, ref.table_name)

    _logger.info(
        "Метаданные таблиц: %d обновлено, %d не найдено.",
        counts["refreshed"],
        counts["missing"],
    )
