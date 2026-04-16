from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from main import build_parser, build_summary_path, cmd_summarize


def test_fetch_parses_schema():
    args = build_parser().parse_args(["fetch", "--schema", "MYSCHEMA"])
    assert args.schema == "MYSCHEMA"
    assert args.object is None


def test_fetch_parses_object():
    args = build_parser().parse_args(["fetch", "--schema", "S", "--object", "PKG_FOO"])
    assert args.object == "PKG_FOO"


def test_fetch_missing_schema_raises():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["fetch"])


def test_fetch_parse_flag_defaults_false():
    args = build_parser().parse_args(["fetch", "--schema", "S"])
    assert args.parse is False


def test_fetch_parse_flag_set():
    args = build_parser().parse_args(["fetch", "--schema", "S", "--parse"])
    assert args.parse is True


def test_fetch_with_table_meta_flag():
    args = build_parser().parse_args(["fetch", "--schema", "S", "--parse", "--with-table-meta"])
    assert args.with_table_meta is True


def test_fetch_with_dict_const_flag():
    args = build_parser().parse_args(["fetch", "--schema", "S", "--parse", "--with-dict-const"])
    assert args.with_dict_const is True


def test_parse_command_schema():
    args = build_parser().parse_args(["parse", "--schema", "MYSCHEMA"])
    assert args.schema == "MYSCHEMA"
    assert args.object is None
    assert args.force is False
    assert args.log_level == "DEBUG"


def test_parse_command_with_object_and_force():
    args = build_parser().parse_args(["parse", "--schema", "S", "--object", "PKG_A", "--force"])
    assert args.object == "PKG_A"
    assert args.force is True


def test_parse_command_with_table_meta():
    args = build_parser().parse_args(["parse", "--schema", "S", "--with-table-meta"])
    assert args.with_table_meta is True


def test_parse_command_with_dict_const():
    args = build_parser().parse_args(["parse", "--schema", "S", "--with-dict-const"])
    assert args.with_dict_const is True


def test_parse_command_missing_schema_raises():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["parse"])


def test_explain_parses_schema_and_object():
    args = build_parser().parse_args(["explain", "--schema", "MYSCHEMA", "--object", "PKG_A"])
    assert args.schema == "MYSCHEMA"
    assert args.object == "PKG_A"
    assert args.subprogram is None


def test_explain_parses_subprogram():
    args = build_parser().parse_args(
        ["explain", "--schema", "S", "--object", "PKG_A", "--subprogram", "PROC_X"]
    )
    assert args.subprogram == "PROC_X"


def test_explain_missing_schema_raises():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["explain", "--object", "PKG_A"])


def test_explain_missing_object_raises():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["explain", "--schema", "S"])


def test_sync_table_meta_parses_args():
    args = build_parser().parse_args(["sync-table-meta", "--schema", "S", "--object", "PKG_A"])
    assert args.schema == "S"
    assert args.object == "PKG_A"


def test_sync_dict_const_parses_args():
    args = build_parser().parse_args(["sync-dict-const", "--schema", "S", "--object", "PKG_A"])
    assert args.schema == "S"
    assert args.object == "PKG_A"


def test_summarize_parses_required_args():
    args = build_parser().parse_args(["summarize", "--schema", "MYSCHEMA", "--object", "PKG_A"])
    assert args.schema == "MYSCHEMA"
    assert args.object == "PKG_A"
    assert args.subprogram is None
    assert args.force is False


def test_summarize_parses_optional_args():
    args = build_parser().parse_args(
        [
            "summarize",
            "--schema",
            "S",
            "--object",
            "PKG_A",
            "--subprogram",
            "PROC_X",
            "--depth",
            "2",
            "--force",
        ]
    )
    assert args.subprogram == "PROC_X"
    assert args.depth == 2
    assert args.force is True


def test_build_summary_path_uses_expected_pattern():
    args = build_parser().parse_args(
        ["summarize", "--schema", "MYSCHEMA", "--object", "PKG_ORDERS", "--subprogram", "CALCULATE_TOTAL"]
    )
    path = build_summary_path(args, timestamp=datetime(2026, 4, 1, 12, 34, 56))
    assert path == Path("rusult_summary/summary_myschema_pkg_orders_calculate_total_20260401_123456.md")


def test_build_summary_path_uses_root_when_subprogram_missing():
    args = build_parser().parse_args(["summarize", "--schema", "MYSCHEMA", "--object", "PKG_ORDERS"])
    path = build_summary_path(args, timestamp=datetime(2026, 4, 1, 12, 34, 56))
    assert path == Path("rusult_summary/summary_myschema_pkg_orders_root_20260401_123456.md")



def test_cmd_summarize_writes_summary_to_markdown_file(tmp_path, monkeypatch):
    output_path = tmp_path / "rusult_summary" / "summary_s_pkg_a_proc_x_20260401_123456.md"
    output_compact_html_path = tmp_path / "rusult_summary" / "summary_s_pkg_a_proc_x_20260401_123456_compact.html"
    args = build_parser().parse_args(
        ["summarize", "--schema", "S", "--object", "PKG_A", "--subprogram", "PROC_X"]
    )
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))
    rendered_output = "PKG_A.PROC_X — итоговое описание"
    rendered_compact_html = "<html><body>PKG_A.PROC_X итоговое описание</body></html>"

    with patch("main.ensure_logging_configured"), \
         patch("dotenv.load_dotenv"), \
         patch("summarizer.llm_client.LlmClient", return_value=object()), \
         patch("summarizer.tree_describer.describe_tree_run", return_value="run-1"), \
         patch("summarizer.tree_describer.render_tree_from_run", return_value=rendered_output), \
         patch("summarizer.tree_describer.render_tree_compact_html_from_run", return_value=rendered_compact_html), \
         patch("main.build_summary_path", return_value=output_path), \
         patch("main.build_summary_compact_html_path", return_value=output_compact_html_path):
        cmd_summarize(args)

    content = output_path.read_text(encoding="utf-8")
    compact_html_content = output_compact_html_path.read_text(encoding="utf-8")
    assert "PKG_A.PROC_X" in content
    assert "итоговое описание" in content
    assert "<html>" in compact_html_content
    assert "итоговое описание" in compact_html_content


def test_debug_defaults():
    args = build_parser().parse_args(["debug"])
    assert args.schema == "DEBUG"
    assert args.object == "ANONYMOUS"
    assert args.object_type == "PACKAGE BODY"
    assert args.source_file is None
    assert args.source is None
    assert args.output_json is False


def test_debug_schema_override():
    args = build_parser().parse_args(["debug", "--schema", "MYSCHEMA"])
    assert args.schema == "MYSCHEMA"


def test_debug_source_file():
    args = build_parser().parse_args(["debug", "--source-file", "foo.sql"])
    assert args.source_file == "foo.sql"
    assert args.source is None


def test_debug_inline_source():
    args = build_parser().parse_args(["debug", "--source", "BEGIN NULL; END;"])
    assert args.source == "BEGIN NULL; END;"
    assert args.source_file is None


def test_debug_type_override():
    args = build_parser().parse_args(["debug", "--type", "PROCEDURE"])
    assert args.object_type == "PROCEDURE"


def test_debug_json_flag():
    args = build_parser().parse_args(["debug", "--json"])
    assert args.output_json is True


def test_debug_source_and_source_file_mutually_exclusive():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["debug", "--source", "x", "--source-file", "f.sql"])
