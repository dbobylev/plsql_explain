from __future__ import annotations

import argparse
import logging
import sys
from typing import TYPE_CHECKING

from app_logging import ensure_logging_configured

if TYPE_CHECKING:
    from parser.models import ParseOutput, SubstatementInfo

_logger = logging.getLogger(__name__)


def _format_table(headers: list[str], rows: list[tuple]) -> str:
    if not rows:
        return "  (нет данных)"
    widths = [
        max(len(h), max(len(str(r[i])) for r in rows))
        for i, h in enumerate(headers)
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines = [fmt.format(*headers), "  ".join("-" * w for w in widths)]
    for row in rows:
        lines.append(fmt.format(*[str(c) if c is not None else "" for c in row]))
    return "\n".join(lines)


def _render_substatement_node(
    node: SubstatementInfo,
    children_map: dict,
    indent: str,
    is_last: bool,
) -> list[str]:
    branch = "└── " if is_last else "├── "
    lines = [f"{indent}{branch}[{node.seq}] {node.statement_type}  L{node.start_line}-L{node.end_line}"]
    child_indent = indent + ("    " if is_last else "│   ")
    children = children_map.get(node.seq, [])
    for i, child in enumerate(children):
        lines.extend(_render_substatement_node(child, children_map, child_indent, i == len(children) - 1))
    return lines


def _format_substatement_tree(substatements: list) -> str:
    if not substatements:
        return "  (нет данных)"

    groups: dict[str | None, list] = {}
    for s in substatements:
        groups.setdefault(s.subprogram, []).append(s)

    lines: list[str] = []
    for subprogram, stmts in groups.items():
        label = subprogram if subprogram is not None else "(package-level)"
        lines.append(f"\n  Subprogram: {label}")
        children_map: dict[int | None, list] = {}
        for s in stmts:
            children_map.setdefault(s.parent_seq, []).append(s)
        roots = children_map.get(None, [])
        for i, root in enumerate(roots):
            lines.extend(_render_substatement_node(root, children_map, "  ", i == len(roots) - 1))
    return "\n".join(lines)


def _render_result(result: ParseOutput) -> str:
    lines = [f"\n=== Parse Result: {result.schema_name}.{result.object_name} ({result.object_type}) ==="]
    lines.append(f"Status: {result.status}")
    if result.error_message:
        lines.append(f"Error:  {result.error_message}")

    lines.append(f"\n--- Call edges ({len(result.call_edges)}) ---")
    lines.append(
        _format_table(
            ["CALLER", "CALLEE_SCHEMA", "CALLEE_OBJECT", "CALLEE_SUBPROGRAM"],
            [(e.caller_subprogram, e.callee_schema, e.callee_object, e.callee_subprogram)
             for e in result.call_edges],
        )
    )

    lines.append(f"\n--- Table accesses ({len(result.table_accesses)}) ---")
    lines.append(
        _format_table(
            ["SUBPROGRAM", "TABLE_SCHEMA", "TABLE_NAME", "OPERATION"],
            [(a.subprogram, a.table_schema, a.table_name, a.operation)
             for a in result.table_accesses],
        )
    )

    lines.append(f"\n--- Subprograms ({len(result.subprograms)}) ---")
    lines.append(
        _format_table(
            ["NAME", "TYPE", "START_LINE", "END_LINE"],
            [(sp.name, sp.subprogram_type, sp.start_line, sp.end_line)
             for sp in result.subprograms],
        )
    )

    lines.append(f"\n--- Substatements ({len(result.substatements)}) ---")
    lines.append(_format_substatement_tree(result.substatements))
    lines.append("")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> None:
    ensure_logging_configured(getattr(args, "log_level", None))
    import dataclasses
    import json as _json
    from parser.runner import parse_object, ParserError

    if args.source_file:
        try:
            with open(args.source_file, encoding="utf-8") as f:
                source_text = f.read()
        except OSError as exc:
            _logger.error("Cannot read file: %s", exc)
            sys.exit(1)
    elif args.source:
        source_text = args.source
    else:
        if sys.stdin.isatty():
            _logger.info("Reading PL/SQL from stdin (Ctrl-D to finish)...")
        source_text = sys.stdin.read()

    try:
        result = parse_object(args.schema, args.object, args.object_type, source_text)
    except ParserError as exc:
        _logger.error("Parser error: %s", exc)
        sys.exit(1)

    if args.output_json:
        text = _json.dumps(dataclasses.asdict(result), indent=2, ensure_ascii=False)
    else:
        text = _render_result(result)

    if args.output_file:
        try:
            with open(args.output_file, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as exc:
            _logger.error("Cannot write file: %s", exc)
            sys.exit(1)
    else:
        _logger.info("%s", text)
