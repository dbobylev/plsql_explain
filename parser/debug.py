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


def _print_result(result: ParseOutput) -> None:
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


def run(args: argparse.Namespace) -> None:
    import dataclasses
    import io
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
        text = _json.dumps(dataclasses.asdict(result), indent=2, ensure_ascii=False)
    else:
        buf = io.StringIO()
        _stdout = sys.stdout
        sys.stdout = buf
        try:
            _print_result(result)
        finally:
            sys.stdout = _stdout
        text = buf.getvalue()

    if args.output_file:
        try:
            with open(args.output_file, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as exc:
            print(f"Cannot write file: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        sys.stdout.write(text)
