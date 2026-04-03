from __future__ import annotations

from collections import Counter
import hashlib
from html import escape
import logging
import sqlite3
import textwrap
from typing import Optional

from summarizer.description_tree import (
    DescriptionNode,
    _compute_tree_hash,
    _dependency_slug,
    _display_dependency_name,
    _find_callees_in_source,
    _make_stub_node,
    build_description_tree,
)
from summarizer.llm_client import LlmClient
from summarizer.tree_prompts import PROMPT_VERSION, build_prompt
from summarizer.tree_store import (
    create_analysis_run,
    get_cached_analysis,
    iter_run_nodes,
    mark_analysis_run_completed,
    mark_analysis_run_failed,
    upsert_cached_analysis,
    upsert_run_node_description,
)
from traversal.graph import resolve_node_shallow
from traversal.models import DependencyNode

_logger = logging.getLogger(__name__)

MARKDOWN_RENDER_WIDTH = 150
OUTLINE_PREVIEW_LIMIT = 5
OUTLINE_DESCRIPTION_INDENT_MIN = 4
HTML_BRANCH_PREVIEW_LIMIT = 16
INDEX_LABEL_DISPLAY_LIMIT = 20
INDEX_LABEL_CUT_MARKER = "...[cut]"
ROOT_SUBPROGRAM_LABEL = "root"


def describe_tree(
    conn: sqlite3.Connection,
    schema: str,
    object_name: str,
    subprogram: Optional[str],
    client: LlmClient,
    force: bool = False,
    max_depth: Optional[int] = None,
) -> DescriptionNode:
    run_id = describe_tree_run(
        conn,
        schema,
        object_name,
        subprogram,
        client,
        force=force,
        max_depth=max_depth,
    )
    tree = load_tree_from_run(conn, run_id)
    tree.analysis_run_id = run_id
    return tree


def describe_tree_run(
    conn: sqlite3.Connection,
    schema: str,
    object_name: str,
    subprogram: Optional[str],
    client: LlmClient,
    force: bool = False,
    max_depth: Optional[int] = None,
) -> str:
    """
    Memory-optimized production path.

    Builds and describes one method tree at a time, persists each finished node
    immediately, and renders final output from SQLite. This avoids holding the
    fully expanded transitive DescriptionNode tree in memory.
    """
    dep_node = resolve_node_shallow(
        conn,
        schema,
        object_name,
        subprogram,
        include_children=(max_depth is None or max_depth > 0),
    )
    obj_type = dep_node.object_type or ""

    _logger.info(
        "Построение дерева описаний: %s.%s%s",
        schema,
        object_name,
        f".{subprogram}" if subprogram else "",
    )

    _logger.info("Дерево построено, генерация описаний...")

    run_id = create_analysis_run(
        conn,
        schema,
        object_name,
        obj_type,
        subprogram,
        PROMPT_VERSION,
    )

    try:
        _run_incremental_analysis(
            conn,
            dep_node,
            client,
            schema,
            object_name,
            obj_type,
            subprogram,
            force,
            run_id,
            max_depth,
        )
    except Exception as exc:
        mark_analysis_run_failed(conn, run_id, str(exc))
        raise

    mark_analysis_run_completed(conn, run_id)

    return run_id


def _run_incremental_analysis(
    conn: sqlite3.Connection,
    dep_node: DependencyNode,
    client: LlmClient,
    root_schema: str,
    root_object_name: str,
    root_obj_type: str,
    root_subprogram: Optional[str],
    force: bool,
    run_id: str,
    max_depth: Optional[int],
) -> None:
    key = (
        dep_node.schema_name.upper(),
        dep_node.object_name.upper(),
        (dep_node.subprogram or "").upper(),
    )
    in_stack = {key}

    tree = build_description_tree(
        conn,
        dep_node,
        max_depth=max_depth,
        expand_calls=False,
    )
    _describe_local_node(
        conn,
        tree,
        dep_node,
        client,
        root_schema,
        root_object_name,
        root_obj_type,
        root_subprogram,
        force,
        run_id,
        parent_node_id=None,
        position=0,
        in_stack=in_stack,
        max_depth=max_depth,
        depth=0,
    )


def _describe_local_node(
    conn: sqlite3.Connection,
    node: DescriptionNode,
    dep_node: DependencyNode,
    client: LlmClient,
    root_schema: str,
    root_object_name: str,
    root_obj_type: str,
    root_subprogram: Optional[str],
    force: bool,
    run_id: str,
    parent_node_id: Optional[str],
    position: int,
    in_stack: set[tuple[str, str, str]],
    max_depth: Optional[int],
    depth: int,
) -> DescriptionNode:
    original_children = list(node.children)
    compact_children: list[DescriptionNode] = []

    for child_position, child in enumerate(original_children):
        compact_children.append(
            _describe_local_node(
                conn,
                child,
                dep_node,
                client,
                root_schema,
                root_object_name,
                root_obj_type,
                root_subprogram,
                force,
                run_id,
                parent_node_id=node.node_id,
                position=child_position,
                in_stack=in_stack,
                max_depth=max_depth,
                depth=depth,
            )
        )

    if not original_children and node.source_text:
        callees = _find_callees_in_source(node.source_text, dep_node)
        for call_position, callee_dep in enumerate(callees):
            compact_children.append(
                _describe_inline_call(
                    conn,
                    callee_dep,
                    caller_dep=dep_node,
                    parent_node=node,
                    position=call_position,
                    client=client,
                    root_schema=root_schema,
                    root_object_name=root_object_name,
                    root_obj_type=root_obj_type,
                    root_subprogram=root_subprogram,
                    force=force,
                    run_id=run_id,
                    in_stack=in_stack,
                    max_depth=max_depth,
                    depth=depth,
                )
            )

    node.children = compact_children
    _compute_tree_hash(node)
    _describe_and_persist_node(
        conn,
        node,
        client,
        root_schema,
        root_object_name,
        root_obj_type,
        root_subprogram,
        force,
        run_id,
        parent_node_id,
        position,
    )
    return _make_summary_ref(node)


def _describe_inline_call(
    conn: sqlite3.Connection,
    callee_dep: DependencyNode,
    caller_dep: DependencyNode,
    parent_node: DescriptionNode,
    position: int,
    client: LlmClient,
    root_schema: str,
    root_object_name: str,
    root_obj_type: str,
    root_subprogram: Optional[str],
    force: bool,
    run_id: str,
    in_stack: set[tuple[str, str, str]],
    max_depth: Optional[int],
    depth: int,
) -> DescriptionNode:
    callee_key = (
        callee_dep.schema_name.upper(),
        callee_dep.object_name.upper(),
        (callee_dep.subprogram or "").upper(),
    )

    if callee_key in in_stack:
        cycle_dep = DependencyNode(
            schema_name=callee_dep.schema_name,
            object_name=callee_dep.object_name,
            object_type=callee_dep.object_type,
            subprogram=callee_dep.subprogram,
            status="cycle",
            error_message=None,
        )
        stub = _make_stub_node(
            cycle_dep,
            parent_node.node_id,
            position,
            current_schema=caller_dep.schema_name,
        )
        _describe_and_persist_node(
            conn,
            stub,
            client,
            root_schema,
            root_object_name,
            root_obj_type,
            root_subprogram,
            force,
            run_id,
            parent_node.node_id,
            position,
        )
        return _make_summary_ref(stub)

    if max_depth is not None and depth + 1 >= max_depth:
        stub = _make_stub_node(
            callee_dep,
            parent_node.node_id,
            position,
            current_schema=caller_dep.schema_name,
        )
        _describe_and_persist_node(
            conn,
            stub,
            client,
            root_schema,
            root_object_name,
            root_obj_type,
            root_subprogram,
            force,
            run_id,
            parent_node.node_id,
            position,
        )
        return _make_summary_ref(stub)

    if callee_dep.status != "ok":
        stub = _make_stub_node(
            callee_dep,
            parent_node.node_id,
            position,
            current_schema=caller_dep.schema_name,
        )
        _describe_and_persist_node(
            conn,
            stub,
            client,
            root_schema,
            root_object_name,
            root_obj_type,
            root_subprogram,
            force,
            run_id,
            parent_node.node_id,
            position,
        )
        return _make_summary_ref(stub)

    resolved_callee = resolve_node_shallow(
        conn,
        callee_dep.schema_name,
        callee_dep.object_name,
        callee_dep.subprogram or None,
        include_children=True,
    )

    call_node_id = f"{parent_node.node_id}/call:{_dependency_slug(resolved_callee)}:{position}"
    local_tree = build_description_tree(
        conn,
        resolved_callee,
        max_depth=max_depth,
        expand_calls=False,
        prefix_override=call_node_id,
    )

    in_stack.add(callee_key)
    try:
        call_children: list[DescriptionNode] = []
        for child_position, child in enumerate(local_tree.children):
            call_children.append(
                _describe_local_node(
                    conn,
                    child,
                    resolved_callee,
                    client,
                    root_schema,
                    root_object_name,
                    root_obj_type,
                    root_subprogram,
                    force,
                    run_id,
                    parent_node_id=call_node_id,
                    position=child_position,
                    in_stack=in_stack,
                    max_depth=max_depth,
                    depth=depth + 1,
                )
            )
    finally:
        in_stack.discard(callee_key)

    local_tree.children = call_children
    _compute_tree_hash(local_tree)

    callee_name = _display_dependency_name(
        resolved_callee,
        current_schema=caller_dep.schema_name,
    )
    call_node = DescriptionNode(
        node_id=call_node_id,
        node_kind="call",
        statement_type="CALL",
        title=f"CALL -> {callee_name}",
        source_text="",
        start_line=local_tree.start_line,
        end_line=local_tree.end_line,
        description="",
        children=call_children,
        schema_name=resolved_callee.schema_name,
        object_name=resolved_callee.object_name,
        subprogram=resolved_callee.subprogram or "",
        source_hash=local_tree.source_hash,
    )
    _describe_and_persist_node(
        conn,
        call_node,
        client,
        root_schema,
        root_object_name,
        root_obj_type,
        root_subprogram,
        force,
        run_id,
        parent_node.node_id,
        position,
    )
    return _make_summary_ref(call_node)


def _describe_and_persist_node(
    conn: sqlite3.Connection,
    node: DescriptionNode,
    client: LlmClient,
    root_schema: str,
    root_object_name: str,
    root_obj_type: str,
    root_subprogram: Optional[str],
    force: bool,
    run_id: str,
    parent_node_id: Optional[str],
    position: int,
) -> None:
    if node.node_kind == "stub":
        upsert_run_node_description(
            conn,
            run_id,
            root_schema,
            root_object_name,
            root_obj_type,
            root_subprogram,
            node,
            parent_node_id,
            position,
            PROMPT_VERSION,
        )
        _release_prompt_payload(node)
        return

    prompt = build_prompt(node)
    if prompt is None:
        upsert_run_node_description(
            conn,
            run_id,
            root_schema,
            root_object_name,
            root_obj_type,
            root_subprogram,
            node,
            parent_node_id,
            position,
            PROMPT_VERSION,
        )
        _release_prompt_payload(node)
        return

    system_msg, user_msg = prompt
    prompt_hash = _prompt_hash(system_msg, user_msg)

    if not force:
        cached_desc = get_cached_analysis(conn, prompt_hash, PROMPT_VERSION)
        if cached_desc is not None:
            node.description = cached_desc
            _logger.debug("Analysis cache hit: %s", node.node_id)
            upsert_run_node_description(
                conn,
                run_id,
                root_schema,
                root_object_name,
                root_obj_type,
                root_subprogram,
                node,
                parent_node_id,
                position,
                PROMPT_VERSION,
            )
            _release_prompt_payload(node)
            return

    _logger.debug("LLM call for node: %s", node.node_id)
    node.description = client.complete(system_msg, user_msg)
    _logger.debug("LLM response for %s: %s", node.node_id, node.description[:100])
    upsert_cached_analysis(
        conn,
        prompt_hash,
        PROMPT_VERSION,
        node.source_hash,
        node.node_kind,
        node.statement_type,
        node.description,
    )
    upsert_run_node_description(
        conn,
        run_id,
        root_schema,
        root_object_name,
        root_obj_type,
        root_subprogram,
        node,
        parent_node_id,
        position,
        PROMPT_VERSION,
    )
    _release_prompt_payload(node)


def _make_summary_ref(node: DescriptionNode) -> DescriptionNode:
    return DescriptionNode(
        node_id=node.node_id,
        node_kind=node.node_kind,
        statement_type=node.statement_type,
        title=node.title,
        source_text="",
        start_line=node.start_line,
        end_line=node.end_line,
        description=node.description,
        schema_name=node.schema_name,
        object_name=node.object_name,
        subprogram=node.subprogram,
        source_hash=node.source_hash,
        analysis_run_id=node.analysis_run_id,
    )


def _populate_descriptions(
    conn: sqlite3.Connection,
    node: DescriptionNode,
    client: LlmClient,
    root_schema: str,
    root_object_name: str,
    root_obj_type: str,
    root_subprogram: Optional[str],
    force: bool,
    run_id: str,
    parent_node_id: Optional[str],
    position: int,
) -> None:
    """Post-order DFS: describe children first, then this node."""
    for child_position, child in enumerate(node.children):
        _populate_descriptions(
            conn,
            child,
            client,
            root_schema,
            root_object_name,
            root_obj_type,
            root_subprogram,
            force,
            run_id=run_id,
            parent_node_id=node.node_id,
            position=child_position,
        )
        _compact_processed_subtree(child)

    _describe_and_persist_node(
        conn,
        node,
        client,
        root_schema,
        root_object_name,
        root_obj_type,
        root_subprogram,
        force,
        run_id,
        parent_node_id,
        position,
    )


def _prompt_hash(system_msg: str, user_msg: str) -> str:
    payload = f"{system_msg}\n---\n{user_msg}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _release_prompt_payload(node: DescriptionNode) -> None:
    # The node description is already persisted at this point. Dropping the raw
    # prompt payload reduces peak memory while keeping the tree shape intact.
    node.source_text = ""
    node.prompt_context = ""


def _compact_processed_subtree(node: DescriptionNode) -> None:
    _release_prompt_payload(node)


def load_tree_from_run(conn: sqlite3.Connection, run_id: str) -> DescriptionNode:
    rows = iter_run_nodes(conn, run_id)
    if not rows:
        raise ValueError(f"No node_description rows found for run_id={run_id}")

    nodes: dict[str, DescriptionNode] = {}
    root: DescriptionNode | None = None
    for row in rows:
        node = DescriptionNode(
            node_id=row["node_id"],
            node_kind=row["node_kind"],
            statement_type=row["statement_type"],
            title=row["title"],
            source_text="",
            start_line=row["start_line"],
            end_line=row["end_line"],
            description=row["description"],
            schema_name=row["schema_name"],
            object_name=row["object_name"],
            subprogram=row["subprogram"],
            source_hash=row["source_hash"],
            analysis_run_id=row["run_id"],
        )
        nodes[node.node_id] = node

    for row in rows:
        node = nodes[row["node_id"]]
        parent_node_id = row["parent_node_id"]
        if parent_node_id is None:
            root = node
            continue
        nodes[parent_node_id].children.append(node)

    if root is None:
        raise ValueError(f"Run {run_id} has no root node")
    return root


def render_tree_from_run(conn: sqlite3.Connection, run_id: str) -> str:
    return render_tree(load_tree_from_run(conn, run_id))


def render_tree_html_from_run(conn: sqlite3.Connection, run_id: str) -> str:
    return render_tree_html(load_tree_from_run(conn, run_id))


def render_tree(
    node: DescriptionNode,
    max_width: int = MARKDOWN_RENDER_WIDTH,
) -> str:
    """Render the description tree as Markdown with overview and numbered outline."""
    width = max(40, max_width)
    overview_lines = _render_markdown_overview(node)
    outline_lines = _render_numbered_outline(node, width)

    lines = [
        f"# {node.title}",
        "",
        "## Overview",
        *overview_lines,
        "",
        "## Numbered Outline",
        "",
        "````text",
        *outline_lines,
        "````",
    ]
    return "\n".join(lines) + "\n"


def render_tree_html(node: DescriptionNode) -> str:
    """Render the description tree as a presentation-focused HTML report."""
    stats = _collect_tree_stats(node)
    rows = _collect_tree_rows(node)
    identity = _display_html_identity(node)
    line_span = _format_line_span(node)
    hero_description_html = _html_text(node.description) if node.description else ""
    branch_links_html = _render_branch_links(node)
    table_rows_html = "\n".join(_render_html_row(row) for row in rows)

    overview_cards = "".join(
        [
            _render_stat_card("Total nodes", str(stats["total_nodes"]), "All persisted nodes in this run"),
            _render_stat_card("Max depth", str(stats["max_depth"]), "Longest branch from root to leaf"),
            _render_stat_card("Statements", str(stats["statement_nodes"]), "Executable statement nodes"),
            _render_stat_card("Calls", str(stats["call_nodes"]), "Expanded callee nodes"),
            _render_stat_card("Stubs", str(stats["stub_nodes"]), "Missing, wrapped or cut-off nodes"),
            _render_stat_card("Top-level", str(len(node.children)), "Direct children of the root"),
        ]
    )

    meta_pills = [
        _render_meta_pill("Root", node.title),
        _render_meta_pill("Path", identity),
    ]
    if line_span:
        meta_pills.append(_render_meta_pill("Lines", line_span))

    hero_blurb_html = ""
    if hero_description_html:
        hero_blurb_html = f"""
      <div class="hero-blurb">{hero_description_html}</div>"""

    branch_section_html = ""
    if branch_links_html:
        branch_section_html = f"""
    <section class="report-panel">
      <div class="section-head">
        <div>
          <p class="section-kicker">Navigation</p>
          <h2>Top-level branches</h2>
        </div>
        <p class="section-note">Jump to the main branches before scanning the full hierarchy table.</p>
      </div>
      <div class="branch-grid">
        {branch_links_html}
      </div>
    </section>"""

    legend_html = _render_html_legend()

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>{escape(node.title)} - PL/SQL Explain Report</title>
  <style>
    :root {{
      --bg-main: #f5efe5;
      --bg-accent: #e8f2ef;
      --ink-main: #173039;
      --ink-soft: #5d6b73;
      --border: #d8cab8;
      --panel: rgba(255, 252, 247, 0.9);
      --panel-strong: #fffdf8;
      --shadow: 0 22px 48px rgba(34, 49, 54, 0.12);
      --accent-warm: #c56c37;
      --accent-cool: #146c7d;
      --accent-root: #244b5a;
      --accent-stub: #8e4b3f;
      --accent-stmt: #5d6874;
      --row-alt: rgba(255, 248, 240, 0.56);
      --row-hover: rgba(20, 108, 125, 0.08);
      --guide: rgba(20, 108, 125, 0.18);
    }}

    * {{
      box-sizing: border-box;
    }}

    html {{
      scroll-behavior: smooth;
    }}

    body {{
      margin: 0;
      color: var(--ink-main);
      font-family: "Trebuchet MS", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(233, 183, 137, 0.28), transparent 32rem),
        radial-gradient(circle at top right, rgba(20, 108, 125, 0.14), transparent 28rem),
        linear-gradient(180deg, var(--bg-main) 0%, var(--bg-accent) 100%);
    }}

    a {{
      color: inherit;
      text-decoration: none;
    }}

    .page-shell {{
      width: 100%;
      margin: 0;
      padding: 32px 20px 56px;
    }}

    .hero {{
      background: linear-gradient(135deg, rgba(255, 252, 247, 0.95), rgba(245, 251, 250, 0.92));
      border: 1px solid rgba(216, 202, 184, 0.9);
      border-radius: 28px;
      box-shadow: var(--shadow);
      padding: 30px 30px 26px;
      position: relative;
      overflow: hidden;
    }}

    .hero::after {{
      content: "";
      position: absolute;
      inset: auto -70px -110px auto;
      width: 260px;
      height: 260px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(197, 108, 55, 0.18), rgba(197, 108, 55, 0));
      pointer-events: none;
    }}

    .eyebrow {{
      margin: 0 0 10px;
      color: var(--accent-cool);
      font-size: 0.78rem;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }}

    .hero h1 {{
      margin: 0;
      font-size: clamp(1.8rem, 2.6vw, 3rem);
      line-height: 1.06;
      letter-spacing: -0.02em;
    }}

    .hero-meta-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 18px;
    }}

    .meta-pill {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border-radius: 999px;
      background: rgba(255, 248, 240, 0.82);
      border: 1px solid rgba(216, 202, 184, 0.86);
      font-size: 0.93rem;
      line-height: 1.2;
    }}

    .meta-label {{
      color: var(--ink-soft);
      font-size: 0.74rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}

    .hero-blurb {{
      margin-top: 20px;
      max-width: 76ch;
      padding: 18px 20px;
      border-radius: 20px;
      background: rgba(255, 253, 248, 0.88);
      border: 1px solid rgba(216, 202, 184, 0.76);
      line-height: 1.68;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }}

    .overview-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 16px;
      margin-top: 22px;
    }}

    .stat-card {{
      padding: 18px 18px 16px;
      border-radius: 22px;
      background: rgba(255, 253, 249, 0.88);
      border: 1px solid rgba(216, 202, 184, 0.82);
      box-shadow: 0 16px 34px rgba(34, 49, 54, 0.08);
    }}

    .stat-label {{
      margin: 0;
      color: var(--ink-soft);
      font-size: 0.76rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      font-weight: 700;
    }}

    .stat-value {{
      margin: 10px 0 8px;
      font-size: 2rem;
      line-height: 1;
      font-weight: 800;
      letter-spacing: -0.03em;
    }}

    .stat-note {{
      margin: 0;
      color: var(--ink-soft);
      font-size: 0.92rem;
      line-height: 1.45;
    }}

    .report-panel {{
      margin-top: 24px;
      padding: 24px;
      border-radius: 26px;
      background: var(--panel);
      border: 1px solid rgba(216, 202, 184, 0.86);
      box-shadow: var(--shadow);
    }}

    .section-head {{
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 18px;
    }}

    .section-kicker {{
      margin: 0 0 8px;
      color: var(--accent-warm);
      font-size: 0.78rem;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }}

    .section-head h2 {{
      margin: 0;
      font-size: 1.45rem;
      line-height: 1.12;
    }}

    .section-note {{
      margin: 0;
      max-width: 48ch;
      color: var(--ink-soft);
      font-size: 0.95rem;
      line-height: 1.5;
      text-align: right;
    }}

    .branch-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
    }}

    .branch-chip {{
      display: flex;
      flex-direction: column;
      gap: 6px;
      padding: 14px 16px;
      border-radius: 18px;
      background: linear-gradient(180deg, rgba(255, 252, 247, 0.94), rgba(247, 252, 251, 0.88));
      border: 1px solid rgba(216, 202, 184, 0.8);
      box-shadow: 0 12px 26px rgba(34, 49, 54, 0.06);
    }}

    .branch-chip:hover {{
      transform: translateY(-1px);
      box-shadow: 0 16px 30px rgba(34, 49, 54, 0.09);
    }}

    .branch-no {{
      color: var(--accent-cool);
      font-size: 0.76rem;
      font-weight: 700;
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }}

    .branch-title {{
      font-size: 1rem;
      line-height: 1.4;
      font-weight: 700;
      overflow-wrap: anywhere;
    }}

    .branch-scope {{
      color: var(--ink-soft);
      font-size: 0.82rem;
      line-height: 1.4;
      overflow-wrap: anywhere;
    }}

    .branch-overflow {{
      padding: 14px 16px;
      border-radius: 18px;
      background: rgba(255, 249, 241, 0.84);
      border: 1px dashed rgba(197, 108, 55, 0.4);
      color: var(--ink-soft);
      line-height: 1.5;
    }}

    .legend-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 8px;
      margin-bottom: 16px;
    }}

    .legend-tag,
    .kind-badge {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 28px;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 0.77rem;
      font-weight: 700;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      white-space: nowrap;
    }}

    .kind-method_root {{
      background: rgba(36, 75, 90, 0.12);
      color: var(--accent-root);
    }}

    .kind-call {{
      background: rgba(20, 108, 125, 0.14);
      color: var(--accent-cool);
    }}

    .kind-stub {{
      background: rgba(142, 75, 63, 0.14);
      color: var(--accent-stub);
    }}

    .kind-statement {{
      background: rgba(93, 104, 116, 0.12);
      color: var(--accent-stmt);
    }}

    .table-wrap {{
      overflow: auto;
      border-radius: 22px;
      border: 1px solid rgba(216, 202, 184, 0.86);
      background: var(--panel-strong);
    }}

    .tree-table {{
      width: 100%;
      border-collapse: separate;
      border-spacing: 0;
      table-layout: fixed;
    }}

    .tree-table col.no-col {{
      width: 12rem;
    }}

    .tree-table col.level-col {{
      width: 5.5rem;
    }}

    .tree-table col.type-col {{
      width: 9rem;
    }}

    .tree-table col.node-col {{
      width: 32rem;
    }}

    .tree-table col.lines-col {{
      width: 8rem;
    }}

    .tree-table th {{
      position: sticky;
      top: 0;
      z-index: 3;
      padding: 15px 16px;
      border-bottom: 1px solid rgba(216, 202, 184, 0.92);
      background: rgba(245, 239, 229, 0.96);
      color: var(--ink-soft);
      font-size: 0.79rem;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      text-align: left;
    }}

    .tree-table td {{
      padding: 14px 16px;
      border-bottom: 1px solid rgba(216, 202, 184, 0.55);
      vertical-align: top;
    }}

    .tree-table tbody tr:nth-child(even) {{
      background: var(--row-alt);
    }}

    .tree-table tbody tr:hover {{
      background: var(--row-hover);
    }}

    .tree-table tbody tr.group-start td {{
      border-top: 2px solid rgba(20, 108, 125, 0.22);
    }}

    .tree-table tbody tr.root-row {{
      background: rgba(36, 75, 90, 0.05);
    }}

    .row-anchor {{
      font-weight: 700;
      color: var(--accent-cool);
      font-family: "Consolas", "SFMono-Regular", monospace;
    }}

    .row-anchor:hover {{
      color: var(--accent-warm);
    }}

    .level-pill,
    .line-pill {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 2.8rem;
      padding: 5px 10px;
      border-radius: 999px;
      background: rgba(255, 248, 240, 0.86);
      border: 1px solid rgba(216, 202, 184, 0.82);
      font-size: 0.82rem;
      font-weight: 700;
      line-height: 1.1;
    }}

    .line-pill.empty {{
      color: var(--ink-soft);
      background: rgba(245, 239, 229, 0.7);
    }}

    .node-shell {{
      min-height: 30px;
    }}

    .node-title {{
      margin: 0;
      font-size: 1rem;
      line-height: 1.45;
      font-weight: 700;
      overflow-wrap: anywhere;
    }}

    .node-scope {{
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 8px;
      margin-top: 6px;
      color: var(--ink-soft);
      line-height: 1.4;
    }}

    .node-scope-label {{
      color: var(--accent-cool);
      font-size: 0.72rem;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}

    .node-scope-value {{
      font-family: "Consolas", "SFMono-Regular", monospace;
      font-size: 0.86rem;
    }}

    .node-caption {{
      margin-top: 6px;
      color: var(--ink-soft);
      font-size: 0.9rem;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }}

    .summary-text {{
      line-height: 1.62;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }}

    .summary-empty {{
      color: var(--ink-soft);
      font-style: italic;
    }}

    .footer-note {{
      margin-top: 18px;
      color: var(--ink-soft);
      font-size: 0.9rem;
      line-height: 1.5;
    }}

    @media (max-width: 980px) {{
      .page-shell {{
        width: 100%;
        padding: 18px 12px 36px;
      }}

      .hero,
      .report-panel {{
        padding: 18px;
        border-radius: 22px;
      }}

      .section-head {{
        flex-direction: column;
        align-items: flex-start;
      }}

      .section-note {{
        text-align: left;
      }}
    }}

    @media print {{
      body {{
        background: #ffffff;
      }}

      .page-shell {{
        width: 100%;
        padding: 0;
      }}

      .hero,
      .report-panel,
      .stat-card {{
        box-shadow: none;
        background: #ffffff;
      }}

      .tree-table th {{
        position: static;
      }}
    }}
  </style>
</head>
<body>
  <main class="page-shell">
    <section class="hero">
      <p class="eyebrow">PL/SQL Explain Report</p>
      <h1>{escape(node.title)}</h1>
      <div class="hero-meta-row">
        {"".join(meta_pills)}
      </div>{hero_blurb_html}
      <div class="overview-grid">
        {overview_cards}
      </div>
    </section>{branch_section_html}
    <section class="report-panel">
      <div class="section-head">
        <div>
          <p class="section-kicker">Hierarchy</p>
          <h2>Presentation table</h2>
        </div>
        <p class="section-note">A flat, scan-friendly view of the full tree with sticky headers, direct anchors and explicit subprogram context for every node.</p>
      </div>
      <div class="legend-row">
        {legend_html}
      </div>
      <div class="table-wrap">
        <table class="tree-table">
          <colgroup>
            <col class="no-col">
            <col class="level-col">
            <col class="type-col">
            <col class="node-col">
            <col class="lines-col">
            <col>
          </colgroup>
          <thead>
            <tr>
              <th>No</th>
              <th>Level</th>
              <th>Type</th>
              <th>Node</th>
              <th>Lines</th>
              <th>Summary</th>
            </tr>
          </thead>
          <tbody>
            {table_rows_html}
          </tbody>
        </table>
      </div>
      <p class="footer-note">This HTML report is static and does not require JavaScript. It works well in a browser and is suitable for manual reuse in HTML-capable environments.</p>
    </section>
  </main>
</body>
</html>
"""


def _render_markdown_overview(node: DescriptionNode) -> list[str]:
    stats = _collect_tree_stats(node)
    lines = [
        f"- Root: `{node.title}`",
        f"- Total nodes: `{stats['total_nodes']}`",
        f"- Max depth: `{stats['max_depth']}`",
        f"- Statements: `{stats['statement_nodes']}`",
        f"- Calls: `{stats['call_nodes']}`",
        f"- Stubs: `{stats['stub_nodes']}`",
    ]

    root_line_span = _format_line_span(node)
    if root_line_span:
        lines.append(f"- Root line span: `{root_line_span}`")

    if node.children:
        preview_nodes = [
            _outline_title(child, include_line_span=False)
            for child in node.children[:OUTLINE_PREVIEW_LIMIT]
        ]
        preview = ", ".join(f"`{item}`" for item in preview_nodes)
        if len(node.children) > OUTLINE_PREVIEW_LIMIT:
            preview += ", ..."
        lines.append(f"- Top-level items: {preview}")

    return lines


def _collect_tree_stats(node: DescriptionNode) -> dict[str, int]:
    total_nodes = 0
    max_depth = 0
    kind_counts: Counter[str] = Counter()
    stack: list[tuple[DescriptionNode, int]] = [(node, 0)]

    while stack:
        current, depth = stack.pop()
        total_nodes += 1
        max_depth = max(max_depth, depth)
        kind_counts[current.node_kind] += 1
        for child in reversed(current.children):
            stack.append((child, depth + 1))

    return {
        "total_nodes": total_nodes,
        "max_depth": max_depth,
        "statement_nodes": kind_counts["statement"],
        "call_nodes": kind_counts["call"],
        "stub_nodes": kind_counts["stub"],
    }


def _collect_tree_rows(node: DescriptionNode) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []

    def visit(current: DescriptionNode, index_path: list[int]) -> None:
        depth = len(index_path) - 1
        full_index_label = ".".join(str(part) for part in index_path)
        rows.append(
            {
                "node": current,
                "depth": depth,
                "index_label": full_index_label,
                "index_display": _truncate_index_label(full_index_label),
                "row_id": "node-" + "-".join(str(part) for part in index_path),
                "line_span": _format_line_span(current),
                "badge_label": _html_badge_label(current),
                "badge_class": f"kind-{current.node_kind}",
                "caption": _html_caption(current),
                "subprogram_label": _display_subprogram_label(current),
            }
        )
        for position, child in enumerate(current.children, start=1):
            visit(child, [*index_path, position])

    visit(node, [1])
    return rows


def _render_numbered_outline(node: DescriptionNode, max_width: int) -> list[str]:
    lines: list[str] = []
    _append_outline_node(lines, node, [1], max_width)
    return lines


def _append_outline_node(
    lines: list[str],
    node: DescriptionNode,
    index_path: list[int],
    max_width: int,
) -> None:
    full_index_label = ".".join(str(part) for part in index_path)
    index_label = _truncate_index_label(full_index_label)
    lines.append(f"{index_label} {_outline_title(node)} [{_markdown_subprogram_label(node)}]")

    if node.description:
        description_indent = " " * max(len(index_label) + 1, OUTLINE_DESCRIPTION_INDENT_MIN)
        lines.extend(
            _wrap_text_block(
                node.description,
                width=max_width,
                initial_indent=description_indent,
                subsequent_indent=description_indent,
            )
        )

    for position, child in enumerate(node.children, start=1):
        _append_outline_node(lines, child, [*index_path, position], max_width)


def _outline_title(node: DescriptionNode, include_line_span: bool = True) -> str:
    if node.node_kind == "method_root":
        base = node.title
    elif node.node_kind == "statement":
        base = node.statement_type or node.title or "statement"
    else:
        base = node.title or node.statement_type or node.node_kind

    if not include_line_span:
        return base

    line_span = _format_line_span(node)
    if not line_span:
        return base
    return f"{base} {line_span}"


def _format_line_span(node: DescriptionNode) -> str:
    start = node.start_line or 0
    end = node.end_line or 0
    if start <= 0 and end <= 0:
        return ""
    if start > 0 and end > 0 and start != end:
        return f"L{start}-L{end}"
    line_no = start or end
    return f"L{line_no}" if line_no > 0 else ""


def _wrap_text_block(
    text: str,
    width: int,
    initial_indent: str = "",
    subsequent_indent: str = "",
) -> list[str]:
    lines: list[str] = []
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    for raw_line in raw_lines:
        normalized = " ".join(raw_line.split())
        if not normalized:
            if lines and lines[-1] != "":
                lines.append("")
            continue

        wrapped = textwrap.wrap(
            normalized,
            width=width,
            initial_indent=initial_indent,
            subsequent_indent=subsequent_indent,
            break_long_words=False,
            break_on_hyphens=False,
        )
        if wrapped:
            lines.extend(wrapped)
        else:
            lines.append(initial_indent.rstrip())

    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _render_branch_links(node: DescriptionNode) -> str:
    if not node.children:
        return ""

    items: list[str] = []
    for position, child in enumerate(node.children[:HTML_BRANCH_PREVIEW_LIMIT], start=1):
        row_id = f"node-1-{position}"
        title = _truncate_text(_outline_title(child, include_line_span=False), 56)
        branch_scope = _display_subprogram_label(child)
        items.append(
            f"""
        <a class="branch-chip" href="#{row_id}">
          <span class="branch-no">1.{position}</span>
          <span class="branch-title">{escape(title)}</span>
          <span class="branch-scope">Subprogram: {escape(branch_scope)}</span>
        </a>"""
        )

    remaining = len(node.children) - HTML_BRANCH_PREVIEW_LIMIT
    if remaining > 0:
        items.append(
            f"""
        <div class="branch-overflow">
          +{remaining} more top-level branches are present in the table below.
        </div>"""
        )
    return "".join(items)


def _render_html_legend() -> str:
    items = [
        ("kind-method_root", "ROOT"),
        ("kind-call", "CALL"),
        ("kind-statement", "STATEMENT"),
        ("kind-stub", "STUB"),
    ]
    return "".join(
        f'<span class="legend-tag {css_class}">{escape(label)}</span>'
        for css_class, label in items
    )


def _render_stat_card(label: str, value: str, note: str) -> str:
    return f"""
        <article class="stat-card">
          <p class="stat-label">{escape(label)}</p>
          <p class="stat-value">{escape(value)}</p>
          <p class="stat-note">{escape(note)}</p>
        </article>"""


def _render_meta_pill(label: str, value: str) -> str:
    return f"""
        <span class="meta-pill">
          <span class="meta-label">{escape(label)}</span>
          <span>{escape(value)}</span>
        </span>"""


def _render_html_row(row: dict[str, object]) -> str:
    node = row["node"]
    assert isinstance(node, DescriptionNode)
    depth = int(row["depth"])
    index_label = str(row["index_label"])
    index_display = str(row["index_display"])
    row_id = str(row["row_id"])
    line_span = str(row["line_span"])
    badge_label = str(row["badge_label"])
    badge_class = str(row["badge_class"])
    caption = str(row["caption"])
    subprogram_label = str(row["subprogram_label"])

    row_classes = []
    if depth == 0:
        row_classes.append("root-row")
    if depth == 1:
        row_classes.append("group-start")

    summary_html = _html_text(node.description) if node.description else '<span class="summary-empty">No description</span>'
    caption_html = f'<div class="node-caption">{escape(caption)}</div>' if caption else ""
    line_html = f'<span class="line-pill">{escape(line_span)}</span>' if line_span else '<span class="line-pill empty">n/a</span>'
    row_class_attr = f' class="{" ".join(row_classes)}"' if row_classes else ""
    index_title_attr = f' title="{escape(index_label)}"' if index_display != index_label else ""

    return f"""
            <tr id="{escape(row_id)}"{row_class_attr}>
              <td><a class="row-anchor" href="#{escape(row_id)}"{index_title_attr}>{escape(index_display)}</a></td>
              <td><span class="level-pill">{depth}</span></td>
              <td><span class="kind-badge {escape(badge_class)}">{escape(badge_label)}</span></td>
              <td>
                <div class="node-shell">
                  <div class="node-title">{escape(_outline_title(node, include_line_span=False))}</div>
                  <div class="node-scope">
                    <span class="node-scope-label">Subprogram</span>
                    <span class="node-scope-value">{escape(subprogram_label)}</span>
                  </div>{caption_html}
                </div>
              </td>
              <td>{line_html}</td>
              <td><div class="summary-text">{summary_html}</div></td>
            </tr>"""


def _display_html_identity(node: DescriptionNode) -> str:
    parts = [part for part in [node.schema_name, node.object_name, node.subprogram] if part]
    return ".".join(parts) if parts else node.title


def _html_badge_label(node: DescriptionNode) -> str:
    if node.node_kind == "method_root":
        return "ROOT"
    if node.node_kind == "call":
        return "CALL"
    if node.node_kind == "stub":
        return "STUB"
    return (node.statement_type or "STATEMENT").replace("_", " ")


def _html_caption(node: DescriptionNode) -> str:
    if node.node_kind == "method_root":
        return _display_html_identity(node)
    if node.node_kind == "call":
        return "Expanded callee subtree"
    if node.node_kind == "stub":
        return "Terminal dependency marker"
    if node.title and node.title != node.statement_type:
        return node.title
    return ""


def _truncate_text(text: str, limit: int) -> str:
    stripped = " ".join(text.split())
    if len(stripped) <= limit:
        return stripped
    return stripped[: max(0, limit - 3)].rstrip() + "..."


def _truncate_index_label(index_label: str, limit: int = INDEX_LABEL_DISPLAY_LIMIT) -> str:
    if len(index_label) <= limit:
        return index_label
    return index_label[:limit].rstrip(".") + INDEX_LABEL_CUT_MARKER


def _display_subprogram_label(node: DescriptionNode) -> str:
    subprogram = " ".join((node.subprogram or "").split())
    return subprogram or ROOT_SUBPROGRAM_LABEL


def _markdown_subprogram_label(node: DescriptionNode) -> str:
    return f"subprogram={_display_subprogram_label(node)}"


def _html_text(text: str) -> str:
    return escape(text)
