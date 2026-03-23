import argparse
import sys


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

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
