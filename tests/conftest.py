import sqlite3
import pytest
from pathlib import Path

SCHEMA_SQL = (Path(__file__).parent.parent / "db" / "schema.sql").read_text()


@pytest.fixture
def mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    yield conn
    conn.close()
