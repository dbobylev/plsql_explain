from tablemeta import sqlite_store
from tablemeta.models import ColumnMetadataRecord, TableMetadataRecord


def test_list_referenced_tables_defaults_missing_schema_to_current_schema(mem_conn):
    mem_conn.execute(
        """
        INSERT INTO table_access
            (schema_name, object_name, object_type, subprogram, table_schema, table_name, operation)
        VALUES ('S', 'PKG_A', 'PACKAGE BODY', NULL, NULL, 'ORDERS', 'SELECT')
        """
    )
    mem_conn.commit()

    refs = sqlite_store.list_referenced_tables(mem_conn, "S")

    assert refs == [sqlite_store.TableRef(schema_name="S", table_name="ORDERS")]


def test_replace_table_metadata_replaces_columns(mem_conn):
    sqlite_store.replace_table_metadata(
        mem_conn,
        TableMetadataRecord(schema_name="S", table_name="ORDERS", object_type="TABLE", table_comment="Заказы"),
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
    )
    mem_conn.commit()
    sqlite_store.replace_table_metadata(
        mem_conn,
        TableMetadataRecord(schema_name="S", table_name="ORDERS", object_type="TABLE", table_comment="Заказы v2"),
        [
            ColumnMetadataRecord(
                schema_name="S",
                table_name="ORDERS",
                column_name="STATUS",
                column_id=1,
                data_type="VARCHAR2",
                nullable=True,
                column_comment="Статус",
            ),
        ],
    )
    mem_conn.commit()

    table_row = mem_conn.execute("SELECT table_comment FROM table_metadata WHERE schema_name='S' AND table_name='ORDERS'").fetchone()
    column_rows = mem_conn.execute(
        "SELECT column_name, nullable FROM column_metadata WHERE schema_name='S' AND table_name='ORDERS' ORDER BY column_id"
    ).fetchall()

    assert table_row["table_comment"] == "Заказы v2"
    assert [(row["column_name"], row["nullable"]) for row in column_rows] == [("STATUS", 1)]
