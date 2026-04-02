from __future__ import annotations

from collections.abc import Iterable, Sequence

from fetcher import oracle_client as source_oracle_client
from tablemeta.models import ColumnMetadataRecord, TableMetadataRecord, TableRef

_TABLE_QUERY_TEMPLATE = """
    SELECT owner, table_name, table_type, comments
    FROM all_tab_comments
    WHERE {conditions}
    ORDER BY owner, table_name
"""

_COLUMN_QUERY_TEMPLATE = """
    SELECT c.owner,
           c.table_name,
           c.column_name,
           c.column_id,
           c.data_type,
           c.nullable,
           cc.comments
    FROM all_tab_columns c
    LEFT JOIN all_col_comments cc
           ON cc.owner = c.owner
          AND cc.table_name = c.table_name
          AND cc.column_name = c.column_name
    WHERE {conditions}
    ORDER BY c.owner, c.table_name, c.column_id, c.column_name
"""


def fetch_table_metadata(
    refs: Iterable[TableRef],
    chunk_size: int = 200,
) -> tuple[list[TableMetadataRecord], list[ColumnMetadataRecord]]:
    unique_refs = sorted(set(refs), key=lambda ref: (ref.schema_name, ref.table_name))
    if not unique_refs:
        return [], []

    tables: list[TableMetadataRecord] = []
    columns: list[ColumnMetadataRecord] = []

    with source_oracle_client._connect() as conn:
        with conn.cursor() as cur:
            for chunk in _chunked(unique_refs, chunk_size):
                tables.extend(_fetch_table_chunk(cur, chunk))
                columns.extend(_fetch_column_chunk(cur, chunk))

    return tables, columns


def _fetch_table_chunk(cur, refs: Sequence[TableRef]) -> list[TableMetadataRecord]:
    conditions, params = _build_ref_conditions(refs, owner_expr="owner", table_expr="table_name")
    cur.execute(_TABLE_QUERY_TEMPLATE.format(conditions=conditions), params)
    return [
        TableMetadataRecord(
            schema_name=_to_text(owner),
            table_name=_to_text(table_name),
            object_type=_to_optional_text(table_type),
            table_comment=_to_optional_text(comment),
        )
        for owner, table_name, table_type, comment in cur
    ]


def _fetch_column_chunk(cur, refs: Sequence[TableRef]) -> list[ColumnMetadataRecord]:
    conditions, params = _build_ref_conditions(refs, owner_expr="c.owner", table_expr="c.table_name")
    cur.execute(_COLUMN_QUERY_TEMPLATE.format(conditions=conditions), params)
    return [
        ColumnMetadataRecord(
            schema_name=_to_text(owner),
            table_name=_to_text(table_name),
            column_name=_to_text(column_name),
            column_id=column_id,
            data_type=_to_optional_text(data_type),
            nullable=_nullable_to_bool(nullable),
            column_comment=_to_optional_text(comment),
        )
        for owner, table_name, column_name, column_id, data_type, nullable, comment in cur
    ]


def _build_ref_conditions(
    refs: Sequence[TableRef],
    *,
    owner_expr: str,
    table_expr: str,
) -> tuple[str, dict[str, str]]:
    conditions: list[str] = []
    params: dict[str, str] = {}
    for idx, ref in enumerate(refs):
        owner_key = f"owner_{idx}"
        table_key = f"table_{idx}"
        conditions.append(f"({owner_expr} = :{owner_key} AND {table_expr} = :{table_key})")
        params[owner_key] = ref.schema_name
        params[table_key] = ref.table_name
    return " OR ".join(conditions), params


def _chunked(items: Sequence[TableRef], chunk_size: int) -> Iterable[Sequence[TableRef]]:
    for idx in range(0, len(items), chunk_size):
        yield items[idx:idx + chunk_size]


def _nullable_to_bool(value: object) -> Optional[bool]:
    text = _to_optional_text(value)
    if text is None:
        return None
    return text.upper() == "Y"


def _to_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _to_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = _to_text(value)
    return text if text != "" else None

