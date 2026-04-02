from __future__ import annotations

import argparse
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from app_logging import (
    LOG_LEVEL_NAMES,
    configure_logging,
    default_log_level,
    ensure_logging_configured,
    shutdown_logging,
)

_logger = logging.getLogger(__name__)
SUMMARY_OUTPUT_DIR = "rusult_summary"
SUMMARY_ROOT_LABEL = "root"


def _sanitize_filename_component(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z_.-]+", "_", value.strip().lower())
    return normalized.strip("._") or "unknown"


def build_summary_filename(args: argparse.Namespace, timestamp: datetime | None = None) -> str:
    timestamp = timestamp or datetime.now().astimezone()
    program_name = args.subprogram or SUMMARY_ROOT_LABEL
    parts = [
        "summary",
        _sanitize_filename_component(args.schema),
        _sanitize_filename_component(args.object),
        _sanitize_filename_component(program_name),
        timestamp.strftime("%Y%m%d_%H%M%S"),
    ]
    return "_".join(parts) + ".md"


def build_summary_path(
    args: argparse.Namespace,
    output_dir: str | Path = SUMMARY_OUTPUT_DIR,
    timestamp: datetime | None = None,
) -> Path:
    return Path(output_dir) / build_summary_filename(args, timestamp=timestamp)


def write_summary_output(
    args: argparse.Namespace,
    summary: str,
    output_dir: str | Path = SUMMARY_OUTPUT_DIR,
    timestamp: datetime | None = None,
) -> Path:
    summary_path = build_summary_path(args, output_dir=output_dir, timestamp=timestamp)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(summary, encoding="utf-8")
    _logger.debug("Summary written to %s", summary_path)
    return summary_path


def cmd_summarize(args: argparse.Namespace) -> None:
    ensure_logging_configured(getattr(args, "log_level", None))
    from dotenv import load_dotenv
    load_dotenv()
    import sqlite3
    from fetcher.sqlite_store import init_db
    from summarizer.llm_client import LlmClient
    from summarizer.tree_describer import describe_tree_run, render_tree_from_run

    db_path = os.environ.get("SQLITE_PATH", "./data/plsql.db")
    _logger.debug(
        "Суммаризация объекта: schema=%s, object=%s%s, force=%s",
        args.schema,
        args.object,
        f", subprogram={args.subprogram}" if args.subprogram else "",
        args.force,
    )
    init_db()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        client = LlmClient()
        run_id = describe_tree_run(
            conn,
            args.schema,
            args.object,
            args.subprogram or None,
            client,
            force=args.force,
            max_depth=args.depth,
        )
        output = render_tree_from_run(conn, run_id)
    finally:
        conn.close()
    write_summary_output(args, output)
    _logger.info(output)


def cmd_explain(args: argparse.Namespace) -> None:
    ensure_logging_configured(getattr(args, "log_level", None))
    from dotenv import load_dotenv
    load_dotenv()
    import sqlite3
    from traversal.graph import build_tree, print_tree, print_tree_verbose

    db_path = os.environ.get("SQLITE_PATH", "./data/plsql.db")
    _logger.debug(
        "Построение дерева зависимостей: schema=%s, object=%s%s, verbose=%s",
        args.schema,
        args.object,
        f", subprogram={args.subprogram}" if args.subprogram else "",
        args.verbose,
    )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        node = build_tree(conn, args.schema, args.object, args.subprogram or None, max_depth=args.depth)
    finally:
        conn.close()
    if args.verbose:
        print_tree_verbose(node)
    else:
        print_tree(node)


def cmd_fetch(args: argparse.Namespace) -> None:
    ensure_logging_configured(getattr(args, "log_level", None))
    from fetcher.sync import run

    _logger.info(
        "Загрузка исходников: schema=%s%s",
        args.schema,
        f", object={args.object}" if args.object else "",
    )
    run(schema=args.schema, object_name=args.object)
    if args.parse:
        from indexer.sync import run as parse_run

        _logger.info("")
        _logger.info("Запуск парсинга...")
        parse_run(
            schema=args.schema,
            object_name=args.object,
            with_table_meta=args.with_table_meta,
        )


def cmd_parse(args: argparse.Namespace) -> None:
    ensure_logging_configured(getattr(args, "log_level", None))
    from indexer.sync import run

    _logger.info(
        "Парсинг объектов: schema=%s%s%s",
        args.schema,
        f", object={args.object}" if args.object else "",
        " [force]" if args.force else "",
    )
    run(
        schema=args.schema,
        object_name=args.object,
        force=args.force,
        with_table_meta=args.with_table_meta,
    )


def cmd_sync_table_meta(args: argparse.Namespace) -> None:
    ensure_logging_configured(getattr(args, "log_level", None))
    from tablemeta.sync import run

    _logger.info(
        "Синхронизация метаданных таблиц: schema=%s%s",
        args.schema,
        f", object={args.object}" if args.object else "",
    )
    run(schema=args.schema, object_name=args.object)


def cmd_debug(args: argparse.Namespace) -> None:
    ensure_logging_configured(getattr(args, "log_level", None))
    from dotenv import load_dotenv
    load_dotenv()
    from parser.debug import run
    run(args)


def build_parser() -> argparse.ArgumentParser:
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "--log-level",
        type=str.upper,
        choices=LOG_LEVEL_NAMES,
        default=default_log_level(),
        help=f"Уровень логирования (по умолчанию: {default_log_level()})",
    )
    parser = argparse.ArgumentParser(
        prog="plsql_explain",
        description="Инструмент для анализа PL/SQL кода Oracle",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser(
        "fetch",
        parents=[common_parser],
        help="Загрузить исходники из Oracle в SQLite",
    )
    fetch_parser.add_argument("--schema", required=True, help="Имя схемы Oracle (например: MYSCHEMA)")
    fetch_parser.add_argument("--object", default=None, help="Имя конкретного объекта (опционально)")
    fetch_parser.add_argument("--parse", action="store_true", help="После загрузки сразу запустить парсинг")
    fetch_parser.add_argument(
        "--with-table-meta",
        action="store_true",
        help="После парсинга синхронизировать описания таблиц и колонок",
    )
    fetch_parser.set_defaults(func=cmd_fetch)

    parse_parser = subparsers.add_parser(
        "parse",
        parents=[common_parser],
        help="Парсить PL/SQL объекты, обновить граф зависимостей",
    )
    parse_parser.add_argument("--schema", required=True, help="Имя схемы Oracle")
    parse_parser.add_argument("--object", default=None, help="Имя конкретного объекта (опционально)")
    parse_parser.add_argument("--force", action="store_true", help="Перепарсить даже неизменённые объекты")
    parse_parser.add_argument(
        "--with-table-meta",
        action="store_true",
        help="После парсинга синхронизировать описания таблиц и колонок",
    )
    parse_parser.set_defaults(func=cmd_parse)

    table_meta_parser = subparsers.add_parser(
        "sync-table-meta",
        parents=[common_parser],
        help="Загрузить описания таблиц и колонок для уже найденных table_access",
    )
    table_meta_parser.add_argument("--schema", required=True, help="Имя схемы Oracle")
    table_meta_parser.add_argument("--object", default=None, help="Имя конкретного объекта (опционально)")
    table_meta_parser.set_defaults(func=cmd_sync_table_meta)

    summarize_parser = subparsers.add_parser(
        "summarize",
        parents=[common_parser],
        help="Иерархическая LLM-суммаризация объекта",
    )
    summarize_parser.add_argument("--schema", required=True, help="Имя схемы Oracle")
    summarize_parser.add_argument("--object", required=True, help="Имя объекта")
    summarize_parser.add_argument("--subprogram", default=None, help="Имя подпрограммы внутри пакета (опционально)")
    summarize_parser.add_argument("--depth", type=int, default=None, help="Максимальная глубина обхода зависимостей (по умолчанию: без ограничения)")
    summarize_parser.add_argument("--force", action="store_true", help="Игнорировать кэш описаний")
    summarize_parser.set_defaults(func=cmd_summarize)

    explain_parser = subparsers.add_parser(
        "explain",
        parents=[common_parser],
        help="Обход графа зависимостей и вывод дерева",
    )
    explain_parser.add_argument("--schema", required=True, help="Имя схемы Oracle")
    explain_parser.add_argument("--object", required=True, help="Имя объекта (пакет, процедура, функция)")
    explain_parser.add_argument("--subprogram", default=None, help="Имя подпрограммы внутри пакета (опционально)")
    explain_parser.add_argument("--depth", type=int, default=None, help="Максимальная глубина обхода зависимостей")
    explain_parser.add_argument("--verbose", "-v", action="store_true", help="Подробный вывод: схема, тип, ошибки, обращения к таблицам")
    explain_parser.set_defaults(func=cmd_explain)

    debug_parser = subparsers.add_parser(
        "debug",
        parents=[common_parser],
        help="Запустить C# парсер на произвольном PL/SQL и изучить результат",
    )
    debug_parser.add_argument("--schema", default="DEBUG", help="Имя схемы (по умолчанию: DEBUG)")
    debug_parser.add_argument("--object", default="ANONYMOUS", help="Имя объекта (по умолчанию: ANONYMOUS)")
    debug_parser.add_argument("--type", dest="object_type", default="PACKAGE BODY", help='Тип объекта (по умолчанию: "PACKAGE BODY")')
    source_group = debug_parser.add_mutually_exclusive_group()
    source_group.add_argument("--source-file", metavar="FILE", help="Путь к .sql файлу")
    source_group.add_argument("--source", help="PL/SQL текст inline")
    debug_parser.add_argument("--json", dest="output_json", action="store_true", help="Вывод в формате JSON")
    debug_parser.add_argument("--output", dest="output_file", metavar="FILE", help="Записать результат в файл (UTF-8) вместо stdout")
    debug_parser.set_defaults(func=cmd_debug)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    session = configure_logging(args)
    _logger.debug("Logging initialized: file=%s, level=%s", session.log_path, session.log_level)
    try:
        args.func(args)
    except Exception:
        _logger.exception("Команда завершилась ошибкой: %s", args.command)
        raise
    finally:
        shutdown_logging()


if __name__ == "__main__":
    main()
