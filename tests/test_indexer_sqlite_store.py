import hashlib
import pytest
from parser.models import CallEdge, SubprogramInfo, SubstatementInfo, TableAccess
from indexer import sqlite_store as store


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


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


# ---------------------------------------------------------------------------
# replace_subprograms
# ---------------------------------------------------------------------------

def _make_subprogram(name="PROC1", src="PROCEDURE PROC1 IS BEGIN NULL; END;"):
    return SubprogramInfo(
        name=name, subprogram_type="PROCEDURE",
        start_line=1, end_line=3, source_text=src,
    )


def test_replace_subprograms_inserts_rows(mem_conn):
    sps = [_make_subprogram("PROC1"), _make_subprogram("PROC2", "PROCEDURE PROC2 IS BEGIN NULL; END;")]
    store.replace_subprograms(mem_conn, "S", "PKG_A", "PACKAGE BODY", sps)
    mem_conn.commit()

    count = mem_conn.execute("SELECT COUNT(*) FROM subprogram").fetchone()[0]
    assert count == 2


def test_replace_subprograms_stores_source_hash(mem_conn):
    src = "PROCEDURE PROC1 IS BEGIN NULL; END;"
    store.replace_subprograms(mem_conn, "S", "PKG_A", "PACKAGE BODY", [_make_subprogram(src=src)])
    mem_conn.commit()

    row = mem_conn.execute("SELECT source_hash FROM subprogram WHERE subprogram_name='PROC1'").fetchone()
    assert row["source_hash"] == _sha256(src)


def test_replace_subprograms_deletes_old_rows(mem_conn):
    store.replace_subprograms(mem_conn, "S", "PKG_A", "PACKAGE BODY", [_make_subprogram("OLD_PROC")])
    mem_conn.commit()
    store.replace_subprograms(mem_conn, "S", "PKG_A", "PACKAGE BODY", [_make_subprogram("NEW_PROC")])
    mem_conn.commit()

    rows = mem_conn.execute("SELECT subprogram_name FROM subprogram").fetchall()
    assert len(rows) == 1
    assert rows[0]["subprogram_name"] == "NEW_PROC"


def test_replace_subprograms_empty_list_clears_rows(mem_conn):
    store.replace_subprograms(mem_conn, "S", "PKG_A", "PACKAGE BODY", [_make_subprogram()])
    mem_conn.commit()
    store.replace_subprograms(mem_conn, "S", "PKG_A", "PACKAGE BODY", [])
    mem_conn.commit()

    count = mem_conn.execute("SELECT COUNT(*) FROM subprogram").fetchone()[0]
    assert count == 0


# ---------------------------------------------------------------------------
# replace_substatements
# ---------------------------------------------------------------------------

def _make_stmt(seq, parent_seq=None, stmt_type="SQL_SELECT", subprogram="PROC1", position=0):
    return SubstatementInfo(
        subprogram=subprogram, seq=seq, parent_seq=parent_seq, position=position,
        statement_type=stmt_type, start_line=10 + seq, end_line=10 + seq,
        source_text=f"SELECT {seq} FROM dual",
    )


def test_replace_substatements_inserts_rows(mem_conn):
    stmts = [_make_stmt(0), _make_stmt(1, position=1)]
    store.replace_substatements(mem_conn, "S", "PKG_A", "PACKAGE BODY", stmts)
    mem_conn.commit()

    count = mem_conn.execute("SELECT COUNT(*) FROM substatement").fetchone()[0]
    assert count == 2


def test_replace_substatements_stores_parent_child_tree(mem_conn):
    stmts = [
        _make_stmt(0, stmt_type="IF"),
        _make_stmt(1, parent_seq=0, stmt_type="IF_THEN"),
        _make_stmt(2, parent_seq=1, stmt_type="SQL_SELECT"),
    ]
    store.replace_substatements(mem_conn, "S", "PKG_A", "PACKAGE BODY", stmts)
    mem_conn.commit()

    rows = mem_conn.execute(
        "SELECT seq, parent_seq FROM substatement ORDER BY seq"
    ).fetchall()
    assert rows[0]["parent_seq"] is None
    assert rows[1]["parent_seq"] == 0
    assert rows[2]["parent_seq"] == 1


def test_replace_substatements_none_subprogram_stored_as_empty_string(mem_conn):
    stmt = SubstatementInfo(
        subprogram=None, seq=0, parent_seq=None, position=0,
        statement_type="SQL_SELECT", start_line=1, end_line=1,
        source_text="SELECT 1 FROM dual",
    )
    store.replace_substatements(mem_conn, "S", "PKG_A", "PACKAGE BODY", [stmt])
    mem_conn.commit()

    row = mem_conn.execute("SELECT subprogram FROM substatement").fetchone()
    assert row["subprogram"] == ""


def test_replace_substatements_stores_source_hash(mem_conn):
    src = "SELECT 42 FROM dual"
    stmt = SubstatementInfo(
        subprogram="PROC1", seq=0, parent_seq=None, position=0,
        statement_type="SQL_SELECT", start_line=5, end_line=5,
        source_text=src,
    )
    store.replace_substatements(mem_conn, "S", "PKG_A", "PACKAGE BODY", [stmt])
    mem_conn.commit()

    row = mem_conn.execute("SELECT source_hash FROM substatement").fetchone()
    assert row["source_hash"] == _sha256(src)


def test_replace_substatements_deletes_old_rows(mem_conn):
    store.replace_substatements(mem_conn, "S", "PKG_A", "PACKAGE BODY", [_make_stmt(0)])
    mem_conn.commit()
    store.replace_substatements(mem_conn, "S", "PKG_A", "PACKAGE BODY", [_make_stmt(0, stmt_type="IF")])
    mem_conn.commit()

    rows = mem_conn.execute("SELECT statement_type FROM substatement").fetchall()
    assert len(rows) == 1
    assert rows[0]["statement_type"] == "IF"


def test_replace_substatements_empty_list_clears_rows(mem_conn):
    store.replace_substatements(mem_conn, "S", "PKG_A", "PACKAGE BODY", [_make_stmt(0)])
    mem_conn.commit()
    store.replace_substatements(mem_conn, "S", "PKG_A", "PACKAGE BODY", [])
    mem_conn.commit()

    count = mem_conn.execute("SELECT COUNT(*) FROM substatement").fetchone()[0]
    assert count == 0
