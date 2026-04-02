from unittest.mock import patch

import tablemeta.sync as sync
from tablemeta.models import ColumnMetadataRecord, TableMetadataRecord, TableRef


def _insert_table_access(mem_conn, table_schema=None, table_name="ORDERS"):
    mem_conn.execute(
        """
        INSERT INTO table_access
            (schema_name, object_name, object_type, subprogram, table_schema, table_name, operation)
        VALUES ('S', 'PKG_A', 'PACKAGE BODY', NULL, ?, ?, 'SELECT')
        """,
        (table_schema, table_name),
    )
    mem_conn.commit()


def test_sync_loads_metadata_for_referenced_tables(mem_conn):
    _insert_table_access(mem_conn)

    with patch("fetcher.sqlite_store.init_db"), \
         patch("fetcher.sqlite_store._connect", return_value=mem_conn), \
         patch(
             "tablemeta.oracle_client.fetch_table_metadata",
             return_value=(
                 [
                     TableMetadataRecord(
                         schema_name="S",
                         table_name="ORDERS",
                         object_type="TABLE",
                         table_comment="Заказы клиентов",
                     ),
                 ],
                 [
                     ColumnMetadataRecord(
                         schema_name="S",
                         table_name="ORDERS",
                         column_name="ORDER_ID",
                         column_id=1,
                         data_type="NUMBER",
                         nullable=False,
                         column_comment="Идентификатор",
                     ),
                 ],
             ),
         ) as mock_fetch:
        sync.run(schema="S")

    mock_fetch.assert_called_once_with([TableRef(schema_name="S", table_name="ORDERS")])
    table_row = mem_conn.execute("SELECT table_comment FROM table_metadata").fetchone()
    column_row = mem_conn.execute("SELECT column_name FROM column_metadata").fetchone()
    assert table_row["table_comment"] == "Заказы клиентов"
    assert column_row["column_name"] == "ORDER_ID"


def test_sync_creates_placeholder_when_oracle_metadata_missing(mem_conn):
    _insert_table_access(mem_conn, table_schema="HR", table_name="ORDERS")

    with patch("fetcher.sqlite_store.init_db"), \
         patch("fetcher.sqlite_store._connect", return_value=mem_conn), \
         patch("tablemeta.oracle_client.fetch_table_metadata", return_value=([], [])):
        sync.run(schema="S")

    row = mem_conn.execute(
        "SELECT schema_name, table_name, table_comment FROM table_metadata"
    ).fetchone()
    assert row["schema_name"] == "HR"
    assert row["table_name"] == "ORDERS"
    assert row["table_comment"] is None
