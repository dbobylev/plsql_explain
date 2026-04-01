from __future__ import annotations

import logging

from dotenv import load_dotenv

from app_logging import ensure_logging_configured

load_dotenv()

from fetcher import oracle_client, sqlite_store

_logger = logging.getLogger(__name__)


def run(schema: str, object_name: str | None = None) -> None:
    ensure_logging_configured()
    sqlite_store.init_db()

    counts = {"inserted": 0, "updated": 0, "unchanged": 0}
    _logger.debug(
        "fetcher.sync.run started: schema=%s, object_name=%s",
        schema,
        object_name,
    )

    with sqlite_store._connect() as conn:
        for schema_name, name, obj_type, source_text in oracle_client.fetch_objects(schema, object_name):
            result = sqlite_store.upsert_object(conn, schema_name, name, obj_type, source_text)
            counts[result] += 1
            if result != "unchanged":
                _logger.info("  [%s] %s.%s (%s)", result, schema_name, name, obj_type)

    total = sum(counts.values())
    _logger.info(
        f"\nГотово: всего {total} объектов — "
        f"{counts['inserted']} новых, {counts['updated']} обновлено, {counts['unchanged']} без изменений."
    )
