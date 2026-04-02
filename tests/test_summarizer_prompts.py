from __future__ import annotations

import hashlib

from summarizer.prompts import (
    build_aggregate_unit_prompt,
    build_analysis_unit_prompt,
    build_prompt,
)
from summarizer.substatements import AnalysisUnit, SubstatementNode
from traversal.models import ColumnMetadataInfo, DependencyNode, TableAccessInfo


def _node(
    *,
    object_name: str = "PKG_MAIN",
    subprogram: str | None = "PROC1",
    table_accesses: list[TableAccessInfo] | None = None,
) -> DependencyNode:
    return DependencyNode(
        schema_name="S",
        object_name=object_name,
        object_type="PACKAGE BODY",
        subprogram=subprogram,
        status="ok",
        error_message=None,
        table_accesses=table_accesses or [],
        children=[],
    )


def _substatement(statement_type: str, source_text: str) -> SubstatementNode:
    return SubstatementNode(
        seq=1,
        parent_seq=None,
        position=0,
        statement_type=statement_type,
        start_line=1,
        end_line=1,
        source_text=source_text,
        source_hash=hashlib.sha256(source_text.encode()).hexdigest(),
        children=[],
    )


def test_build_prompt_filters_dependencies_by_source_fragment_case_insensitively() -> None:
    node = _node(
        table_accesses=[
            TableAccessInfo(table_schema="HR", table_name="ORDERS", operation="SELECT"),
            TableAccessInfo(table_schema="HR", table_name="CUSTOMERS", operation="UPDATE"),
        ]
    )
    child_summaries = {
        ("PKG_UTIL", "RUN_JOB"): "utility summary",
        ("PKG_MAIN", "LOCAL_HELPER"): "local summary",
        ("PKG_OTHER", "IGNORE_ME"): "other summary",
    }
    source_fragment = """
    begin
        select count(*) into v_cnt from hr.orders;
        corp.pkg_util.run_job();
        local_helper();
    end;
    """

    _, user = build_prompt(node, source_fragment, child_summaries)

    assert "HR.ORDERS (SELECT)" in user
    assert "CUSTOMERS" not in user
    assert "PKG_UTIL.RUN_JOB: utility summary" in user
    assert "PKG_MAIN.LOCAL_HELPER: local summary" in user
    assert "IGNORE_ME" not in user


def test_build_analysis_unit_prompt_skips_unreferenced_dependencies_for_declare_section() -> None:
    node = _node(
        table_accesses=[TableAccessInfo(table_schema="HR", table_name="ORDERS", operation="SELECT")]
    )
    unit = AnalysisUnit(
        unit_key="method/node:1",
        unit_kind="code_chunk",
        statement_type="DECLARE",
        title="DECLARE-секция",
        path=("Метод", "DECLARE-секция"),
        body_nodes=[_substatement("DECLARE", "v_total NUMBER;")],
    )
    child_summaries = {
        ("PKG_MAIN", "LOCAL_HELPER"): "local summary",
    }

    _, user = build_analysis_unit_prompt(node, unit, child_summaries)

    assert "Обращения к таблицам:" not in user
    assert "Вызываемые объекты и их описания:" not in user


def test_build_prompt_respects_identifier_boundaries() -> None:
    node = _node(
        table_accesses=[TableAccessInfo(table_schema="HR", table_name="ORDERS", operation="SELECT")]
    )

    _, user = build_prompt(node, "BEGIN SELECT * FROM hr.orders_tmp; END;", {})

    assert "ORDERS (SELECT)" not in user


def test_build_aggregate_unit_prompt_filters_method_children_by_method_source_text() -> None:
    node = _node()
    unit = AnalysisUnit(
        unit_key="method",
        unit_kind="method",
        statement_type="METHOD",
        title="Метод",
        path=("Метод",),
    )
    child_analyses = [
        (
            AnalysisUnit(
                unit_key="method/node:1",
                unit_kind="code_chunk",
                statement_type="OTHER",
                title="Фрагмент строк 1-1",
                path=("Метод", "Фрагмент строк 1-1"),
            ),
            "Анализ первого фрагмента",
        )
    ]
    child_summaries = {
        ("PKG_UTIL", "RUN_JOB"): "utility summary",
        ("PKG_MAIN", "LOCAL_HELPER"): "local summary",
    }
    method_source = """
    begin
        corp.pkg_util.run_job();
    end;
    """

    _, user = build_aggregate_unit_prompt(
        node,
        unit,
        child_analyses,
        child_summaries,
        "brief",
        reference_text=method_source,
    )

    assert "PKG_UTIL.RUN_JOB: utility summary" in user
    assert "LOCAL_HELPER" not in user


def test_build_prompt_includes_table_comment_and_matching_columns() -> None:
    node = _node(
        table_accesses=[
            TableAccessInfo(
                table_schema="HR",
                table_name="ORDERS",
                operation="SELECT",
                table_comment="Заказы клиентов",
                columns=[
                    ColumnMetadataInfo(column_name="ORDER_ID", data_type="NUMBER", column_comment="Идентификатор"),
                    ColumnMetadataInfo(column_name="STATUS", data_type="VARCHAR2", column_comment="Статус заказа"),
                    ColumnMetadataInfo(column_name="INTERNAL_FLAG", data_type="CHAR", column_comment="Служебный признак"),
                ],
            ),
        ]
    )

    _, user = build_prompt(
        node,
        """
        begin
            select o.order_id, o.status
              into v_id, v_status
              from hr.orders o;
        end;
        """,
        {},
    )

    assert "HR.ORDERS (SELECT) — Заказы клиентов" in user
    assert "ORDER_ID (NUMBER) — Идентификатор" in user
    assert "STATUS (VARCHAR2) — Статус заказа" in user
    assert "INTERNAL_FLAG" not in user
