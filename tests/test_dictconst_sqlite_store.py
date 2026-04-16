from dictconst import sqlite_store
from dictconst.models import DictConstantRecord


def test_extract_const_names_deduplicates_and_normalizes() -> None:
    text = """
    v_a := c.get('foo');
    v_b := C . GET ( 'Bar' );
    v_c := c.get('foo');
    """

    assert sqlite_store.extract_const_names(text) == ["FOO", "BAR"]


def test_list_referenced_constants_reads_from_object_source(mem_conn) -> None:
    mem_conn.execute(
        """
        INSERT INTO object_source
            (schema_name, object_name, object_type, source_text, source_hash, fetched_at)
        VALUES
            ('S', 'PKG_A', 'PACKAGE BODY', 'BEGIN v_x := c.get(''foo''); END;', 'h1', datetime('now')),
            ('S', 'PKG_B', 'PACKAGE BODY', 'BEGIN v_y := c.get(''bar''); END;', 'h2', datetime('now'))
        """
    )
    mem_conn.commit()

    refs = sqlite_store.list_referenced_constants(mem_conn, "S")

    assert [ref.const_name for ref in refs] == ["FOO", "BAR"]


def test_load_constant_usages_returns_found_and_missing_constants(mem_conn) -> None:
    sqlite_store.replace_dict_constant(
        mem_conn,
        DictConstantRecord(
            const_name="FOO",
            shortname="Коротко",
            fullname="Полное имя",
        ),
    )
    mem_conn.commit()

    usages = sqlite_store.load_constant_usages(
        mem_conn,
        "v_a := c.get('foo'); v_b := c.get('bar');",
    )

    assert [(usage.const_name, usage.resolved_text) for usage in usages] == [
        ("FOO", "Полное имя"),
        ("BAR", None),
    ]

