"""Tests for summarizer.engine.summarize_node."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import MagicMock, call, patch

import pytest

from summarizer.engine import summarize_node
from traversal.models import DependencyNode, TableAccessInfo

NOW = datetime.now(timezone.utc).isoformat()

# ── Helpers ───────────────────────────────────────────────────────────────────


def _ok_node(
    name: str,
    schema: str = "S",
    subprogram: Optional[str] = None,
    children: Optional[list[DependencyNode]] = None,
    table_accesses: Optional[list[TableAccessInfo]] = None,
) -> DependencyNode:
    return DependencyNode(
        schema_name=schema,
        object_name=name,
        object_type="PACKAGE BODY",
        subprogram=subprogram,
        status="ok",
        error_message=None,
        table_accesses=table_accesses or [],
        children=children or [],
    )


def _stub_node(name: str, status: str) -> DependencyNode:
    return DependencyNode(
        schema_name="S",
        object_name=name,
        object_type=None,
        subprogram=None,
        status=status,
        error_message=None,
    )


def _insert_source(conn: sqlite3.Connection, name: str, source: str = "-- code") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO object_source "
        "(schema_name, object_name, object_type, source_text, source_hash, fetched_at) "
        "VALUES ('S', ?, 'PACKAGE BODY', ?, 'hash_' || ?, ?)",
        (name, source, name, NOW),
    )
    conn.commit()


def _insert_parse_result(conn: sqlite3.Connection, name: str, source_hash: str = None) -> None:
    h = source_hash or f"hash_{name}"
    conn.execute(
        "INSERT OR IGNORE INTO parse_result "
        "(schema_name, object_name, object_type, parsed_at, source_hash, status) "
        "VALUES ('S', ?, 'PACKAGE BODY', ?, ?, 'ok')",
        (name, NOW, h),
    )
    conn.commit()


def _make_client(return_value: str = "суммари") -> MagicMock:
    client = MagicMock()
    client.complete.return_value = return_value
    return client


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_leaf_node_calls_llm_once(mem_conn: sqlite3.Connection) -> None:
    _insert_source(mem_conn, "PKG_A")
    _insert_parse_result(mem_conn, "PKG_A")

    node = _ok_node("PKG_A")
    client = _make_client("описание PKG_A")

    result = summarize_node(mem_conn, node, client)

    assert result == "описание PKG_A"
    assert client.complete.call_count == 1


def test_cache_hit_skips_llm(mem_conn: sqlite3.Connection) -> None:
    _insert_source(mem_conn, "PKG_A")
    _insert_parse_result(mem_conn, "PKG_A")

    # Pre-populate summary cache with matching hash
    mem_conn.execute(
        "INSERT INTO summary (schema_name, object_name, object_type, subprogram, "
        "source_hash, summary_text, summarized_at) VALUES ('S', 'PKG_A', 'PACKAGE BODY', '', 'hash_PKG_A', 'кэш', ?)",
        (NOW,),
    )
    mem_conn.commit()

    node = _ok_node("PKG_A")
    client = _make_client()

    result = summarize_node(mem_conn, node, client)

    assert result == "кэш"
    client.complete.assert_not_called()


def test_force_ignores_cache(mem_conn: sqlite3.Connection) -> None:
    _insert_source(mem_conn, "PKG_A")
    _insert_parse_result(mem_conn, "PKG_A")

    mem_conn.execute(
        "INSERT INTO summary (schema_name, object_name, object_type, subprogram, "
        "source_hash, summary_text, summarized_at) VALUES ('S', 'PKG_A', 'PACKAGE BODY', '', 'hash_PKG_A', 'кэш', ?)",
        (NOW,),
    )
    mem_conn.commit()

    node = _ok_node("PKG_A")
    client = _make_client("новое суммари")

    result = summarize_node(mem_conn, node, client, force=True)

    assert result == "новое суммари"
    client.complete.assert_called_once()


def test_child_summary_included_in_parent_prompt(mem_conn: sqlite3.Connection) -> None:
    _insert_source(mem_conn, "PKG_A")
    _insert_parse_result(mem_conn, "PKG_A")
    _insert_source(mem_conn, "PKG_B")
    _insert_parse_result(mem_conn, "PKG_B")

    child = _ok_node("PKG_B")
    parent = _ok_node("PKG_A", children=[child])

    call_order: list[str] = []

    def fake_complete(system: str, user: str) -> str:
        if "PKG_B" in user and "Объект: S.PKG_B" in user:
            call_order.append("child")
            return "суммари PKG_B"
        call_order.append("parent")
        # Verify child summary is in parent prompt
        assert "суммари PKG_B" in user
        return "суммари PKG_A"

    client = MagicMock()
    client.complete.side_effect = fake_complete

    result = summarize_node(mem_conn, parent, client)

    assert result == "суммари PKG_A"
    assert call_order == ["child", "parent"]


def test_diamond_llm_called_once_per_unique_node(mem_conn: sqlite3.Connection) -> None:
    """A→B→D, A→C→D: D should be summarized only once despite appearing twice."""
    for name in ("PKG_A", "PKG_B", "PKG_C", "PKG_D"):
        _insert_source(mem_conn, name)
        _insert_parse_result(mem_conn, name)

    d1 = _ok_node("PKG_D")
    d2 = _ok_node("PKG_D")
    b = _ok_node("PKG_B", children=[d1])
    c = _ok_node("PKG_C", children=[d2])
    a = _ok_node("PKG_A", children=[b, c])

    call_counts: dict[str, int] = {}

    def fake_complete(system: str, user: str) -> str:
        # Detect which object is being summarized from the prompt header
        for name in ("PKG_D", "PKG_C", "PKG_B", "PKG_A"):
            if f"S.{name}" in user:
                call_counts[name] = call_counts.get(name, 0) + 1
                return f"суммари {name}"
        return "суммари unknown"

    client = MagicMock()
    client.complete.side_effect = fake_complete

    summarize_node(mem_conn, a, client)

    assert call_counts.get("PKG_D", 0) == 1, "PKG_D must be summarized exactly once"


def test_wrapped_node_returns_stub(mem_conn: sqlite3.Connection) -> None:
    node = _stub_node("PKG_WRAPPED", "wrapped")
    client = _make_client()

    result = summarize_node(mem_conn, node, client)

    assert "[wrapped]" in result
    assert "PKG_WRAPPED" in result
    client.complete.assert_not_called()


def test_missing_node_returns_stub(mem_conn: sqlite3.Connection) -> None:
    node = _stub_node("PKG_MISSING", "missing")
    client = _make_client()

    result = summarize_node(mem_conn, node, client)

    assert "[missing]" in result
    client.complete.assert_not_called()


def test_bottom_up_order(mem_conn: sqlite3.Connection) -> None:
    """Children must be summarized before their parent."""
    for name in ("PKG_A", "PKG_B", "PKG_C"):
        _insert_source(mem_conn, name)
        _insert_parse_result(mem_conn, name)

    c = _ok_node("PKG_C")
    b = _ok_node("PKG_B", children=[c])
    a = _ok_node("PKG_A", children=[b])

    order: list[str] = []

    def fake_complete(system: str, user: str) -> str:
        for name in ("PKG_C", "PKG_B", "PKG_A"):
            if f"S.{name}" in user:
                order.append(name)
                return f"суммари {name}"
        return "суммари unknown"

    client = MagicMock()
    client.complete.side_effect = fake_complete

    summarize_node(mem_conn, a, client)

    assert order == ["PKG_C", "PKG_B", "PKG_A"]
