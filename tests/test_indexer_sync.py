import hashlib
from unittest.mock import patch, MagicMock
import pytest

import indexer.sync as sync
from parser.models import CallEdge, ParseOutput, SubprogramInfo, SubstatementInfo, TableAccess


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _make_output(status: str = "ok", name: str = "PKG_A") -> ParseOutput:
    return ParseOutput(
        schema_name="S", object_name=name, object_type="PACKAGE BODY",
        status=status, error_message="oops" if status == "error" else None,
        call_edges=[CallEdge(caller_subprogram="P", callee_schema=None, callee_object="PKG_B", callee_subprogram=None)],
        table_accesses=[TableAccess(subprogram="P", table_schema=None, table_name="TBL", operation="SELECT")],
    )


def _seed_object(conn, name: str, source: str = "source text"):
    h = _hash(source)
    conn.execute(
        "INSERT OR REPLACE INTO object_source "
        "(schema_name, object_name, object_type, source_text, source_hash, fetched_at) "
        "VALUES (?,?,?,?,?,?)",
        ("S", name, "PACKAGE BODY", source, h, "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()
    return h


def run_sync(mem_conn, schema="S", object_name=None, force=False, parse_output=None):
    if parse_output is None:
        parse_output = _make_output()
    with patch("fetcher.sqlite_store.init_db"), \
         patch("fetcher.sqlite_store._connect", return_value=mem_conn), \
         patch("parser.runner.parse_object", return_value=parse_output):
        sync.run(schema=schema, object_name=object_name, force=force)


def test_unchanged_object_is_skipped(mem_conn):
    h = _seed_object(mem_conn, "PKG_A")
    mem_conn.execute(
        "INSERT INTO parse_result (schema_name, object_name, object_type, parsed_at, source_hash, status) "
        "VALUES (?,?,?,?,?,?)",
        ("S", "PKG_A", "PACKAGE BODY", "2026-01-01", h, "ok"),
    )
    mem_conn.commit()

    with patch("fetcher.sqlite_store.init_db"), \
         patch("fetcher.sqlite_store._connect", return_value=mem_conn), \
         patch("parser.runner.parse_object") as mock_parse:
        sync.run(schema="S")

    mock_parse.assert_not_called()


def test_new_object_is_parsed_and_stored(mem_conn):
    _seed_object(mem_conn, "PKG_A")

    run_sync(mem_conn)

    row = mem_conn.execute(
        "SELECT status FROM parse_result WHERE object_name='PKG_A'"
    ).fetchone()
    assert row["status"] == "ok"

    edge_count = mem_conn.execute("SELECT COUNT(*) FROM call_edge").fetchone()[0]
    assert edge_count == 1

    access_count = mem_conn.execute("SELECT COUNT(*) FROM table_access").fetchone()[0]
    assert access_count == 1


def test_force_reparses_unchanged_object(mem_conn):
    h = _seed_object(mem_conn, "PKG_A")
    mem_conn.execute(
        "INSERT INTO parse_result (schema_name, object_name, object_type, parsed_at, source_hash, status) "
        "VALUES (?,?,?,?,?,?)",
        ("S", "PKG_A", "PACKAGE BODY", "2026-01-01", h, "ok"),
    )
    mem_conn.commit()

    with patch("fetcher.sqlite_store.init_db"), \
         patch("fetcher.sqlite_store._connect", return_value=mem_conn), \
         patch("parser.runner.parse_object", return_value=_make_output()) as mock_parse:
        sync.run(schema="S", force=True)

    mock_parse.assert_called_once()


def test_wrapped_status_is_stored(mem_conn):
    _seed_object(mem_conn, "PKG_W")
    run_sync(mem_conn, parse_output=_make_output(status="wrapped", name="PKG_W"))

    row = mem_conn.execute(
        "SELECT status FROM parse_result WHERE object_name='PKG_W'"
    ).fetchone()
    assert row["status"] == "wrapped"


def test_error_status_is_stored(mem_conn):
    _seed_object(mem_conn, "PKG_E")
    run_sync(mem_conn, parse_output=_make_output(status="error", name="PKG_E"))

    row = mem_conn.execute(
        "SELECT status FROM parse_result WHERE object_name='PKG_E'"
    ).fetchone()
    assert row["status"] == "error"


def test_summary_output_contains_counts(mem_conn, capsys):
    _seed_object(mem_conn, "PKG_A")
    run_sync(mem_conn)
    captured = capsys.readouterr()
    assert "1" in captured.out


def test_sync_stores_subprograms_and_substatements(mem_conn):
    _seed_object(mem_conn, "PKG_A")
    output = ParseOutput(
        schema_name="S", object_name="PKG_A", object_type="PACKAGE BODY",
        status="ok", error_message=None,
        call_edges=[],
        table_accesses=[],
        subprograms=[
            SubprogramInfo(name="PROC1", subprogram_type="PROCEDURE",
                           start_line=2, end_line=8, source_text="PROCEDURE PROC1 IS BEGIN NULL; END;"),
        ],
        substatements=[
            SubstatementInfo(subprogram="PROC1", seq=0, parent_seq=None, position=0,
                             statement_type="SQL_SELECT", start_line=4, end_line=4,
                             source_text="SELECT 1 FROM dual"),
            SubstatementInfo(subprogram="PROC1", seq=1, parent_seq=None, position=1,
                             statement_type="IF", start_line=5, end_line=7,
                             source_text="IF TRUE THEN NULL; END IF;"),
        ],
    )
    run_sync(mem_conn, parse_output=output)

    sp_count = mem_conn.execute("SELECT COUNT(*) FROM subprogram").fetchone()[0]
    assert sp_count == 1

    st_count = mem_conn.execute("SELECT COUNT(*) FROM substatement").fetchone()[0]
    assert st_count == 2

    row = mem_conn.execute("SELECT subprogram_name FROM subprogram").fetchone()
    assert row["subprogram_name"] == "PROC1"

    types = {r["statement_type"] for r in mem_conn.execute("SELECT statement_type FROM substatement")}
    assert types == {"SQL_SELECT", "IF"}


def test_object_name_filter(mem_conn):
    _seed_object(mem_conn, "PKG_A")
    _seed_object(mem_conn, "PKG_B")

    with patch("fetcher.sqlite_store.init_db"), \
         patch("fetcher.sqlite_store._connect", return_value=mem_conn), \
         patch("parser.runner.parse_object", return_value=_make_output()) as mock_parse:
        sync.run(schema="S", object_name="PKG_A")

    assert mock_parse.call_count == 1
    assert mock_parse.call_args[0][1] == "PKG_A"
