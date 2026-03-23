from __future__ import annotations

import os
from typing import Iterator
import oracledb
from dotenv import load_dotenv

load_dotenv()


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


def fetch_objects(
    schema: str, object_name: str
) -> Iterator[tuple[str, str, str, str]]:
    """
    Yields (schema, name, type, full_source_text) for each PL/SQL object
    matching the given object_name.
    """
    schema = schema.upper()
    object_name = object_name.upper()

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(_QUERY_OBJECT, schema=schema, object_name=object_name)

            current_key = None
            lines: list[str] = []

            for owner, name, obj_type, line_text in cur:
                key = (owner, name, obj_type)

                if key != current_key:
                    if current_key is not None:
                        yield (*current_key, "".join(lines))
                    current_key = key
                    lines = []

                lines.append(line_text or "")

            if current_key is not None and lines:
                yield (*current_key, "".join(lines))
