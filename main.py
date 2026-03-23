import argparse
import sys


def cmd_fetch(args: argparse.Namespace) -> None:
    from fetcher.sync import run
    print(f"Загрузка исходников: schema={args.schema}" + (f", object={args.object}" if args.object else ""))
    run(schema=args.schema, object_name=args.object)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plsql_explain",
        description="Инструмент для анализа PL/SQL кода Oracle",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser("fetch", help="Загрузить исходники из Oracle в SQLite")
    fetch_parser.add_argument("--schema", required=True, help="Имя схемы Oracle (например: MYSCHEMA)")
    fetch_parser.add_argument("--object", default=None, help="Имя конкретного объекта (опционально)")
    fetch_parser.set_defaults(func=cmd_fetch)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
