from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional


def _norm(subprogram: Optional[str]) -> str:
    """Normalize subprogram to empty string for storage (NULL-safe UNIQUE key)."""
    return subprogram.upper() if subprogram else ""


def get_source_text(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    obj_type: Optional[str] = None,
) -> Optional[str]:
    if obj_type is None:
        row = conn.execute(
            """
            SELECT source_text
            FROM object_source
            WHERE schema_name=? AND object_name=?
            ORDER BY CASE
                         WHEN object_type LIKE '% BODY' THEN 0
                         WHEN object_type IN ('PACKAGE', 'TYPE') THEN 1
                         ELSE 2
                     END,
                     object_type
            LIMIT 1
            """,
            (schema.upper(), name.upper()),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT source_text
            FROM object_source
            WHERE schema_name=? AND object_name=? AND object_type=?
            """,
            (schema.upper(), name.upper(), obj_type.upper()),
        ).fetchone()
    return row[0] if row else None


def get_source_hash(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    obj_type: str,
) -> Optional[str]:
    row = conn.execute(
        "SELECT source_hash FROM parse_result "
        "WHERE schema_name=? AND object_name=? AND object_type=?",
        (schema.upper(), name.upper(), obj_type),
    ).fetchone()
    return row[0] if row else None


def get_summary(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    obj_type: str,
    subprogram: Optional[str],
    summary_kind: str = "brief",
) -> Optional[tuple[str, str]]:
    """
    Returns (source_hash, summary_text) if a cached summary exists, else None.
    """
    row = conn.execute(
        "SELECT source_hash, summary_text FROM summary "
        "WHERE schema_name=? AND object_name=? AND object_type=? AND subprogram=? AND summary_kind=?",
        (schema.upper(), name.upper(), obj_type, _norm(subprogram), summary_kind),
    ).fetchone()
    return (row[0], row[1]) if row else None


def upsert_summary(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    obj_type: str,
    subprogram: Optional[str],
    source_hash: str,
    summary_text: str,
    summary_kind: str = "brief",
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            """
            INSERT INTO summary
                (schema_name, object_name, object_type, subprogram, summary_kind,
                 source_hash, summary_text, summarized_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(schema_name, object_name, object_type, subprogram, summary_kind)
            DO UPDATE SET source_hash=excluded.source_hash,
                          summary_text=excluded.summary_text,
                          summarized_at=excluded.summarized_at
            """,
            (schema.upper(), name.upper(), obj_type, _norm(subprogram),
             summary_kind, source_hash, summary_text, now),
        )


def get_analysis_cache(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    obj_type: str,
    subprogram: Optional[str],
    unit_key: str,
    unit_kind: str,
    summary_kind: str,
    planner_version: str,
    prompt_version: str,
) -> Optional[tuple[str, str]]:
    """
    Returns (unit_hash, analysis_text) if a cached recursive analysis exists.
    """
    row = conn.execute(
        """
        SELECT unit_hash, analysis_text
        FROM analysis_cache
        WHERE schema_name=? AND object_name=? AND object_type=? AND subprogram=?
          AND unit_key=? AND unit_kind=? AND summary_kind=?
          AND planner_version=? AND prompt_version=?
        """,
        (
            schema.upper(),
            name.upper(),
            obj_type,
            _norm(subprogram),
            unit_key,
            unit_kind,
            summary_kind,
            planner_version,
            prompt_version,
        ),
    ).fetchone()
    return (row[0], row[1]) if row else None


def upsert_analysis_cache(
    conn: sqlite3.Connection,
    schema: str,
    name: str,
    obj_type: str,
    subprogram: Optional[str],
    unit_key: str,
    unit_kind: str,
    summary_kind: str,
    planner_version: str,
    prompt_version: str,
    unit_hash: str,
    analysis_text: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            """
            INSERT INTO analysis_cache
                (schema_name, object_name, object_type, subprogram,
                 unit_key, unit_kind, summary_kind,
                 planner_version, prompt_version,
                 unit_hash, analysis_text, analyzed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(schema_name, object_name, object_type, subprogram,
                        unit_key, unit_kind, summary_kind, planner_version, prompt_version)
            DO UPDATE SET unit_hash=excluded.unit_hash,
                          analysis_text=excluded.analysis_text,
                          analyzed_at=excluded.analyzed_at
            """,
            (
                schema.upper(),
                name.upper(),
                obj_type,
                _norm(subprogram),
                unit_key,
                unit_kind,
                summary_kind,
                planner_version,
                prompt_version,
                unit_hash,
                analysis_text,
                now,
            ),
        )
