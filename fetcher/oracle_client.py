from __future__ import annotations

import os
from typing import Iterator
import oracledb
from dotenv import load_dotenv

load_dotenv()

# Ensure Oracle returns UTF-8 text in thick mode (Oracle Instant Client).
# Without this, Cyrillic characters from CL8MSWIN1251/CL8ISO8859P5 databases
# may be returned as garbled bytes.
os.environ.setdefault("NLS_LANG", ".AL32UTF8")


def _connect() -> oracledb.Connection:
    return oracledb.connect(
        user=os.environ["ORACLE_USER"],
        password=os.environ["ORACLE_PASSWORD"],
        dsn=os.environ["ORACLE_DSN"],
    )


_QUERY_OBJECT = """
    SELECT owner, name, type, text
    FROM dba_source
    WHERE owner = :schema
      AND name = :object_name
    ORDER BY owner, name, type, line
"""

_QUERY_SCHEMA = """
    SELECT owner, name, type, text
    FROM dba_source
    WHERE owner = :schema
    ORDER BY owner, name, type, line
"""


def fetch_objects(
    schema: str, object_name: str | None
) -> Iterator[tuple[str, str, str, str]]:
    """
    Yields (schema, name, type, full_source_text) for each PL/SQL object
    matching the given object_name.
    """
    schema = schema.upper()
    object_name = object_name.upper() if object_name else None

    with _connect() as conn:
        with conn.cursor() as cur:
            if object_name:
                cur.execute(_QUERY_OBJECT, schema=schema, object_name=object_name)
            else:
                cur.execute(_QUERY_SCHEMA, schema=schema)

            current_key = None
            lines: list[str] = []

            for owner, name, obj_type, line_text in cur:
                key = (owner, name, obj_type)

                if key != current_key:
                    if current_key is not None:
                        source = "".join(lines)
                        if not source.lstrip().upper().startswith("CREATE"):
                            source = "CREATE OR REPLACE " + source
                        yield (*current_key, source)
                    current_key = key
                    lines = []

                if isinstance(line_text, bytes):
                    line_text = line_text.decode("utf-8", errors="replace")
                lines.append(line_text or "")

            if current_key is not None and lines:
                source = "".join(lines)
                if not source.lstrip().upper().startswith("CREATE"):
                    source = "CREATE OR REPLACE " + source
                yield (*current_key, source)
