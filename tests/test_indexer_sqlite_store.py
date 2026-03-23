import pytest
from parser.models import CallEdge, TableAccess
from indexer import sqlite_store as store


def test_get_parse_hash_returns_none_if_not_parsed(mem_conn):
    result = store.get_parse_hash(mem_conn, "S", "PKG_A", "PACKAGE BODY")
    assert result is None


def test_upsert_parse_result_stores_hash(mem_conn):
    store.upsert_parse_result(mem_conn, "S", "PKG_A", "PACKAGE BODY", "abc123", "ok", None)
    mem_conn.commit()
    assert store.get_parse_hash(mem_conn, "S", "PKG_A", "PACKAGE BODY") == "abc123"


def test_upsert_parse_result_updates_on_conflict(mem_conn):
    store.upsert_parse_result(mem_conn, "S", "PKG_A", "PACKAGE BODY", "old_hash", "ok", None)
    mem_conn.commit()
    store.upsert_parse_result(mem_conn, "S", "PKG_A", "PACKAGE BODY", "new_hash", "error", "oops")
    mem_conn.commit()

    row = mem_conn.execute(
        "SELECT source_hash, status, error_message FROM parse_result "
        "WHERE schema_name='S' AND object_name='PKG_A'"
    ).fetchone()
    assert row["source_hash"] == "new_hash"
    assert row["status"] == "error"
    assert row["error_message"] == "oops"


def test_replace_call_edges_inserts_rows(mem_conn):
    edges = [
        CallEdge(caller_subprogram="PROC1", callee_schema=None, callee_object="PKG_B", callee_subprogram="GET"),
        CallEdge(caller_subprogram="PROC1", callee_schema=None, callee_object="PKG_C", callee_subprogram=None),
    ]
    store.replace_call_edges(mem_conn, "S", "PKG_A", "PACKAGE BODY", edges)
    mem_conn.commit()

    count = mem_conn.execute("SELECT COUNT(*) FROM call_edge").fetchone()[0]
    assert count == 2


def test_replace_call_edges_deletes_old_rows(mem_conn):
    edges_v1 = [
        CallEdge(caller_subprogram="P", callee_schema=None, callee_object="PKG_OLD", callee_subprogram=None),
    ]
    store.replace_call_edges(mem_conn, "S", "PKG_A", "PACKAGE BODY", edges_v1)
    mem_conn.commit()

    edges_v2 = [
        CallEdge(caller_subprogram="P", callee_schema=None, callee_object="PKG_NEW", callee_subprogram=None),
    ]
    store.replace_call_edges(mem_conn, "S", "PKG_A", "PACKAGE BODY", edges_v2)
    mem_conn.commit()

    rows = mem_conn.execute("SELECT callee_object FROM call_edge").fetchall()
    assert len(rows) == 1
    assert rows[0]["callee_object"] == "PKG_NEW"


def test_replace_table_accesses_inserts_rows(mem_conn):
    accesses = [
        TableAccess(subprogram="P", table_schema=None, table_name="ORDERS", operation="SELECT"),
        TableAccess(subprogram="P", table_schema=None, table_name="ORDERS", operation="UPDATE"),
    ]
    store.replace_table_accesses(mem_conn, "S", "PKG_A", "PACKAGE BODY", accesses)
    mem_conn.commit()

    count = mem_conn.execute("SELECT COUNT(*) FROM table_access").fetchone()[0]
    assert count == 2


def test_replace_table_accesses_deletes_old_rows(mem_conn):
    accesses_v1 = [TableAccess(subprogram=None, table_schema=None, table_name="OLD_TBL", operation="SELECT")]
    store.replace_table_accesses(mem_conn, "S", "PKG_A", "PACKAGE BODY", accesses_v1)
    mem_conn.commit()

    accesses_v2 = [TableAccess(subprogram=None, table_schema=None, table_name="NEW_TBL", operation="INSERT")]
    store.replace_table_accesses(mem_conn, "S", "PKG_A", "PACKAGE BODY", accesses_v2)
    mem_conn.commit()

    rows = mem_conn.execute("SELECT table_name FROM table_access").fetchall()
    assert len(rows) == 1
    assert rows[0]["table_name"] == "NEW_TBL"


def test_replace_call_edges_empty_list_clears_rows(mem_conn):
    edges = [CallEdge(caller_subprogram="P", callee_schema=None, callee_object="PKG_X", callee_subprogram=None)]
    store.replace_call_edges(mem_conn, "S", "PKG_A", "PACKAGE BODY", edges)
    mem_conn.commit()
    store.replace_call_edges(mem_conn, "S", "PKG_A", "PACKAGE BODY", [])
    mem_conn.commit()

    count = mem_conn.execute("SELECT COUNT(*) FROM call_edge").fetchone()[0]
    assert count == 0
