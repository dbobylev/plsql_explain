import argparse
import sys


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

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
