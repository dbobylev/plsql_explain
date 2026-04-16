from __future__ import annotations

from collections.abc import Iterable, Sequence

from dictconst.models import DictConstantRecord, DictConstantRef
from fetcher import oracle_client as source_oracle_client

_QUERY_TEMPLATE = """
    SELECT constname, shortname, fullname
    FROM ais.dicti
    WHERE {conditions}
    ORDER BY constname
"""


def fetch_dict_constants(
    refs: Iterable[DictConstantRef],
    chunk_size: int = 200,
) -> list[DictConstantRecord]:
    unique_refs = sorted(set(refs), key=lambda ref: ref.const_name)
    if not unique_refs:
        return []

    records: list[DictConstantRecord] = []
    with source_oracle_client._connect() as conn:
        with conn.cursor() as cur:
            for chunk in _chunked(unique_refs, chunk_size):
                records.extend(_fetch_chunk(cur, chunk))

    return records


def _fetch_chunk(cur, refs: Sequence[DictConstantRef]) -> list[DictConstantRecord]:
    conditions: list[str] = []
    params: dict[str, str] = {}
    for idx, ref in enumerate(refs):
        key = f"name_{idx}"
        conditions.append(f"constname = UPPER(:{key})")
        params[key] = ref.const_name

    cur.execute(_QUERY_TEMPLATE.format(conditions=" OR ".join(conditions)), params)
    return [
        DictConstantRecord(
            const_name=_to_text(const_name),
            shortname=_to_optional_text(shortname),
            fullname=_to_optional_text(fullname),
        )
        for const_name, shortname, fullname in cur
    ]


def _chunked(items: Sequence[DictConstantRef], chunk_size: int) -> Iterable[Sequence[DictConstantRef]]:
    for idx in range(0, len(items), chunk_size):
        yield items[idx:idx + chunk_size]


def _to_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _to_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = _to_text(value)
    return text if text != "" else None

