from __future__ import annotations

from html import escape
from typing import Optional

from summarizer.description_tree import DescriptionNode


def render_tree_compact_html(root: DescriptionNode) -> str:
    """
    Render the description tree as a compact minimal HTML page (no CSS/JS).

    The page is a flat list of sections — one per non-leaf node.
    Each section contains a small table showing the node itself (as a header row)
    and its direct children.  Names of non-leaf children are anchor links that
    jump to their own section.
    """
    non_leaf_nodes: list[DescriptionNode] = []
    _collect_non_leaf(root, non_leaf_nodes)

    anchor_map: dict[str, str] = {
        node.node_id: f"n{i}" for i, node in enumerate(non_leaf_nodes)
    }

    sections: list[str] = []
    for node in non_leaf_nodes:
        sections.append(_render_section(node, anchor_map))

    top_nav = _render_top_nav(root, anchor_map)
    body = "\n".join(sections)

    return (
        "<!DOCTYPE html>\n"
        "<html>\n"
        "<head>\n"
        f'<meta charset="utf-8">\n'
        f"<title>{escape(root.title)}</title>\n"
        "</head>\n"
        '<body style="font-family:monospace;font-size:small">\n'
        f"<h2>{escape(root.title)}</h2>\n"
        f"{top_nav}\n"
        f"{body}\n"
        "</body>\n"
        "</html>\n"
    )


def _collect_non_leaf(node: DescriptionNode, result: list[DescriptionNode]) -> None:
    if node.children:
        result.append(node)
        for child in node.children:
            _collect_non_leaf(child, result)


def _render_top_nav(root: DescriptionNode, anchor_map: dict[str, str]) -> str:
    """Top navigation: links to first-level non-leaf children of root."""
    links: list[str] = []
    if root.node_id in anchor_map:
        links.append(f'<a href="#{anchor_map[root.node_id]}">[root]</a>')
    for child in root.children:
        if child.node_id in anchor_map:
            label = escape(_node_label(child))
            links.append(f'<a href="#{anchor_map[child.node_id]}">{label}</a>')
    if not links:
        return ""
    return "<p>" + " &nbsp;|&nbsp; ".join(links) + "</p>"


def _render_section(node: DescriptionNode, anchor_map: dict[str, str]) -> str:
    anchor = anchor_map[node.node_id]
    heading_label = escape(_node_label(node))
    kind_label = escape(node.node_kind)

    rows: list[str] = []

    # Parent row (the node itself)
    rows.append(_render_row(
        name_html=f'<a id="{anchor}"><b>{heading_label}</b></a>',
        kind=node.node_kind,
        statement_type=node.statement_type,
        description=node.description,
        source_text=node.source_text,
        is_header=True,
    ))

    # Child rows
    for child in node.children:
        if child.node_id in anchor_map:
            child_anchor = anchor_map[child.node_id]
            name_html = f'<a href="#{child_anchor}">{escape(_node_label(child))}</a>'
        else:
            name_html = escape(_node_label(child))
        rows.append(_render_row(
            name_html=name_html,
            kind=child.node_kind,
            statement_type=child.statement_type,
            description=child.description,
            source_text=child.source_text,
            is_header=False,
        ))

    rows_html = "\n".join(rows)
    return (
        f'<h3><a id="{anchor}_h">{heading_label}</a> '
        f'<small style="font-weight:normal">({kind_label})</small></h3>\n'
        '<table border="1" cellspacing="0" cellpadding="2" '
        'style="border-collapse:collapse;width:100%">\n'
        "<tr>"
        '<th style="width:18%">Имя</th>'
        '<th style="width:6%">Тип</th>'
        '<th style="width:38%">Описание</th>'
        '<th style="width:38%">Код</th>'
        "</tr>\n"
        f"{rows_html}\n"
        "</table>\n"
    )


def _render_row(
    name_html: str,
    kind: str,
    statement_type: str,
    description: str,
    source_text: str,
    is_header: bool,
) -> str:
    bg = ' style="background:#f0f0f0"' if is_header else ""
    kind_cell = escape(statement_type) if statement_type and statement_type != kind.upper() else escape(kind)
    desc_cell = escape(description) if description else ""
    src_cell = (
        f'<pre style="margin:0;white-space:pre-wrap;font-size:x-small">{escape(source_text)}</pre>'
        if source_text
        else ""
    )
    return (
        f"<tr{bg}>"
        f"<td>{name_html}</td>"
        f"<td>{kind_cell}</td>"
        f"<td>{desc_cell}</td>"
        f"<td>{src_cell}</td>"
        "</tr>"
    )


def _node_label(node: DescriptionNode) -> str:
    """Short display label for a node."""
    if node.title:
        return node.title
    parts = [node.object_name]
    if node.subprogram:
        parts.append(node.subprogram)
    return ".".join(parts) if parts else node.node_id
