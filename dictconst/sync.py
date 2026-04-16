from __future__ import annotations

import logging

from dotenv import load_dotenv

from app_logging import ensure_logging_configured
from dictconst import oracle_client, sqlite_store
from dictconst.models import DictConstantRecord
from fetcher import sqlite_store as fetcher_store

load_dotenv()

_logger = logging.getLogger(__name__)


def run(
    schema: str,
    object_name: str | None = None,
) -> None:
    ensure_logging_configured()
    fetcher_store.init_db()

    with fetcher_store._connect() as conn:
        refs = sqlite_store.list_referenced_constants(conn, schema, object_name)

    if not refs:
        _logger.info(
            "Словарные константы: для scope schema=%s%s вызовы c.get(...) не найдены.",
            schema,
            f", object={object_name}" if object_name else "",
        )
        return

    _logger.debug(
        "dictconst.sync.run started: schema=%s, object_name=%s, refs=%d",
        schema,
        object_name,
        len(refs),
    )

    records = oracle_client.fetch_dict_constants(refs)
    record_map = {record.const_name: record for record in records}

    counts = {"refreshed": 0, "missing": 0}
    with fetcher_store._connect() as conn:
        for ref in refs:
            record = record_map.get(
                ref.const_name,
                DictConstantRecord(
                    const_name=ref.const_name,
                    shortname=None,
                    fullname=None,
                ),
            )
            with conn:
                sqlite_store.replace_dict_constant(conn, record)
            if ref.const_name in record_map:
                counts["refreshed"] += 1
            else:
                counts["missing"] += 1
                _logger.warning("  [WARN] dict constant not found for %s", ref.const_name)

    _logger.info(
        "Словарные константы: %d обновлено, %d не найдено.",
        counts["refreshed"],
        counts["missing"],
    )
