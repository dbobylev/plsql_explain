from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from parser.models import ParseOutput, SubstatementInfo


def _print_table(headers: list[str], rows: list[tuple]) -> None:
    if not rows:
        print("  (нет данных)")
        return
    widths = [
        max(len(h), max(len(str(r[i])) for r in rows))
        for i, h in enumerate(headers)
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt.format(*[str(c) if c is not None else "" for c in row]))


def _render_substatement_node(
    node: SubstatementInfo,
    children_map: dict,
    indent: str,
    is_last: bool,
) -> None:
    branch = "└── " if is_last else "├── "
    print(f"{indent}{branch}[{node.seq}] {node.statement_type}  L{node.start_line}-L{node.end_line}")
    child_indent = indent + ("    " if is_last else "│   ")
    children = children_map.get(node.seq, [])
    for i, child in enumerate(children):
        _render_substatement_node(child, children_map, child_indent, i == len(children) - 1)


def _print_substatement_tree(substatements: list) -> None:
    if not substatements:
        print("  (нет данных)")
        return

    # Group by subprogram
    groups: dict[str | None, list] = {}
    for s in substatements:
        groups.setdefault(s.subprogram, []).append(s)

    for subprogram, stmts in groups.items():
        label = subprogram if subprogram is not None else "(package-level)"
        print(f"\n  Subprogram: {label}")
        children_map: dict[int | None, list] = {}
        for s in stmts:
            children_map.setdefault(s.parent_seq, []).append(s)
        roots = children_map.get(None, [])
        for i, root in enumerate(roots):
            _render_substatement_node(root, children_map, "  ", i == len(roots) - 1)


def _print_debug_result(result: ParseOutput) -> None:
    print(f"\n=== Parse Result: {result.schema_name}.{result.object_name} ({result.object_type}) ===")
    print(f"Status: {result.status}")
    if result.error_message:
        print(f"Error:  {result.error_message}")

    print(f"\n--- Call edges ({len(result.call_edges)}) ---")
    _print_table(
        ["CALLER", "CALLEE_SCHEMA", "CALLEE_OBJECT", "CALLEE_SUBPROGRAM"],
        [(e.caller_subprogram, e.callee_schema, e.callee_object, e.callee_subprogram)
         for e in result.call_edges],
    )

    print(f"\n--- Table accesses ({len(result.table_accesses)}) ---")
    _print_table(
        ["SUBPROGRAM", "TABLE_SCHEMA", "TABLE_NAME", "OPERATION"],
        [(a.subprogram, a.table_schema, a.table_name, a.operation)
         for a in result.table_accesses],
    )

    print(f"\n--- Subprograms ({len(result.subprograms)}) ---")
    _print_table(
        ["NAME", "TYPE", "START_LINE", "END_LINE"],
        [(sp.name, sp.subprogram_type, sp.start_line, sp.end_line)
         for sp in result.subprograms],
    )

    print(f"\n--- Substatements ({len(result.substatements)}) ---")
    _print_substatement_tree(result.substatements)
    print()


def cmd_debug(args: argparse.Namespace) -> None:
    import dataclasses
    import json as _json
    from parser.runner import parse_object, ParserError

    if args.source_file:
        try:
            with open(args.source_file, encoding="utf-8") as f:
                source_text = f.read()
        except OSError as exc:
            print(f"Cannot read file: {exc}", file=sys.stderr)
            sys.exit(1)
    elif args.source:
        source_text = args.source
    else:
        if sys.stdin.isatty():
            print("Reading PL/SQL from stdin (Ctrl-D to finish)...", file=sys.stderr)
        source_text = sys.stdin.read()

    try:
        result = parse_object(args.schema, args.object, args.object_type, source_text)
    except ParserError as exc:
        print(f"Parser error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.output_json:
        print(_json.dumps(dataclasses.asdict(result), indent=2, ensure_ascii=False))
        return

    _print_debug_result(result)


def cmd_summarize(args: argparse.Namespace) -> None:
    from dotenv import load_dotenv
    load_dotenv()
    import sqlite3
    import os
    from traversal.graph import build_tree
    from summarizer.llm_client import LlmClient
    from summarizer.engine import summarize_node

    db_path = os.environ.get("SQLITE_PATH", "./data/plsql.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    node = build_tree(conn, args.schema, args.object, args.subprogram or None)
    client = LlmClient()
    summary = summarize_node(conn, node, client, force=args.force)
    conn.close()
    print(summary)


def cmd_explain(args: argparse.Namespace) -> None:
    from dotenv import load_dotenv
    load_dotenv()
    import sqlite3
    import os
    from traversal.graph import build_tree, print_tree

    db_path = os.environ.get("SQLITE_PATH", "./data/plsql.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    node = build_tree(conn, args.schema, args.object, args.subprogram or None)
    conn.close()
    print_tree(node)


def cmd_fetch(args: argparse.Namespace) -> None:
    from fetcher.sync import run
    print(f"Загрузка исходников: schema={args.schema}" + (f", object={args.object}" if args.object else ""))
    run(schema=args.schema, object_name=args.object)
    if args.parse:
        from indexer.sync import run as parse_run
        print()
        print("Запуск парсинга...")
        parse_run(schema=args.schema, object_name=args.object)


def cmd_parse(args: argparse.Namespace) -> None:
    from indexer.sync import run
    print(
        f"Парсинг объектов: schema={args.schema}"
        + (f", object={args.object}" if args.object else "")
        + (" [force]" if args.force else "")
    )
    run(schema=args.schema, object_name=args.object, force=args.force)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plsql_explain",
        description="Инструмент для анализа PL/SQL кода Oracle",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser("fetch", help="Загрузить исходники из Oracle в SQLite")
    fetch_parser.add_argument("--schema", required=True, help="Имя схемы Oracle (например: MYSCHEMA)")
    fetch_parser.add_argument("--object", default=None, help="Имя конкретного объекта (опционально)")
    fetch_parser.add_argument("--parse", action="store_true", help="После загрузки сразу запустить парсинг")
    fetch_parser.set_defaults(func=cmd_fetch)

    parse_parser = subparsers.add_parser("parse", help="Парсить PL/SQL объекты, обновить граф зависимостей")
    parse_parser.add_argument("--schema", required=True, help="Имя схемы Oracle")
    parse_parser.add_argument("--object", default=None, help="Имя конкретного объекта (опционально)")
    parse_parser.add_argument("--force", action="store_true", help="Перепарсить даже неизменённые объекты")
    parse_parser.set_defaults(func=cmd_parse)

    summarize_parser = subparsers.add_parser("summarize", help="Иерархическая LLM-суммаризация объекта")
    summarize_parser.add_argument("--schema", required=True, help="Имя схемы Oracle")
    summarize_parser.add_argument("--object", required=True, help="Имя объекта")
    summarize_parser.add_argument("--subprogram", default=None, help="Имя подпрограммы внутри пакета (опционально)")
    summarize_parser.add_argument("--force", action="store_true", help="Игнорировать кэш суммари")
    summarize_parser.set_defaults(func=cmd_summarize)

    explain_parser = subparsers.add_parser("explain", help="Обход графа зависимостей и вывод дерева")
    explain_parser.add_argument("--schema", required=True, help="Имя схемы Oracle")
    explain_parser.add_argument("--object", required=True, help="Имя объекта (пакет, процедура, функция)")
    explain_parser.add_argument("--subprogram", default=None, help="Имя подпрограммы внутри пакета (опционально)")
    explain_parser.set_defaults(func=cmd_explain)

    debug_parser = subparsers.add_parser("debug", help="Запустить C# парсер на произвольном PL/SQL и изучить результат")
    debug_parser.add_argument("--schema", default="DEBUG", help="Имя схемы (по умолчанию: DEBUG)")
    debug_parser.add_argument("--object", default="ANONYMOUS", help="Имя объекта (по умолчанию: ANONYMOUS)")
    debug_parser.add_argument("--type", dest="object_type", default="PACKAGE BODY", help='Тип объекта (по умолчанию: "PACKAGE BODY")')
    source_group = debug_parser.add_mutually_exclusive_group()
    source_group.add_argument("--source-file", metavar="FILE", help="Путь к .sql файлу")
    source_group.add_argument("--source", help="PL/SQL текст inline")
    debug_parser.add_argument("--json", dest="output_json", action="store_true", help="Вывод в формате JSON")
    debug_parser.set_defaults(func=cmd_debug)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
