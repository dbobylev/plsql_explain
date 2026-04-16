from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from dictconst.models import DictConstantRecord, DictConstantRef, DictConstantUsage

_CONST_CALL_PATTERN = re.compile(
    r"\bc\s*\.\s*get\s*\(\s*'((?:''|[^'])+)'\s*\)",
    re.IGNORECASE,
)


def list_referenced_constants(
    conn: sqlite3.Connection,
    schema: str,
    object_name: Optional[str] = None,
) -> list[DictConstantRef]:
    query = """
        SELECT source_text
        FROM object_source
        WHERE schema_name = ?
    """
    params: list[str] = [schema.upper()]
    if object_name:
        query += " AND object_name = ?"
        params.append(object_name.upper())
    query += " ORDER BY object_name, object_type"

    rows = conn.execute(query, params).fetchall()
    seen: set[str] = set()
    refs: list[DictConstantRef] = []
    for row in rows:
        for const_name in extract_const_names(row["source_text"]):
            if const_name in seen:
                continue
            seen.add(const_name)
            refs.append(DictConstantRef(const_name=const_name))
    return refs


def extract_const_names(text: str) -> list[str]:
    seen: set[str] = set()
    names: list[str] = []
    for raw_name in _CONST_CALL_PATTERN.findall(text or ""):
        normalized = raw_name.replace("''", "'").strip().upper()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        names.append(normalized)
    return names


def replace_dict_constant(
    conn: sqlite3.Connection,
    record: DictConstantRecord,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    resolved_text = _resolve_text(record.shortname, record.fullname)
    conn.execute(
        """
        INSERT INTO dict_constant (const_name, shortname, fullname, resolved_text, refreshed_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(const_name)
        DO UPDATE SET shortname=excluded.shortname,
                      fullname=excluded.fullname,
                      resolved_text=excluded.resolved_text,
                      refreshed_at=excluded.refreshed_at
        """,
        (
            record.const_name,
            record.shortname,
            record.fullname,
            resolved_text,
            now,
        ),
    )


def load_constant_usages(
    conn: sqlite3.Connection,
    text: str,
) -> list[DictConstantUsage]:
    names = extract_const_names(text)
    if not names:
        return []

    placeholders = ", ".join("?" for _ in names)
    rows = conn.execute(
        f"""
        SELECT const_name, shortname, fullname, resolved_text
        FROM dict_constant
        WHERE const_name IN ({placeholders})
        """,
        names,
    ).fetchall()
    by_name = {
        row["const_name"]: DictConstantUsage(
            const_name=row["const_name"],
            shortname=row["shortname"],
            fullname=row["fullname"],
            resolved_text=row["resolved_text"],
        )
        for row in rows
    }

    usages: list[DictConstantUsage] = []
    for name in names:
        usage = by_name.get(name)
        if usage is None:
            usages.append(
                DictConstantUsage(
                    const_name=name,
                    shortname=None,
                    fullname=None,
                    resolved_text=None,
                )
            )
            continue
        usages.append(usage)
    return usages


def _resolve_text(shortname: Optional[str], fullname: Optional[str]) -> str | None:
    return fullname or shortname

