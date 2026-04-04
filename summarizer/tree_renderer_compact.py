from __future__ import annotations

from html import escape

from summarizer.description_tree import DescriptionNode

_BEGIN_END_TYPES = {"BEGIN_END"}


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
        '<meta charset="utf-8">\n'
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
    subprogram_line = _subprogram_path(node)

    rows: list[str] = []

    # Parent row (the node itself)
    rows.append(_render_row(
        name_html=f'<a id="{anchor}"><b>{heading_label}</b></a>',
        start_line=node.start_line,
        end_line=node.end_line,
        description=node.description,
        source_text=_source_text_for_display(node),
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
            start_line=child.start_line,
            end_line=child.end_line,
            description=child.description,
            source_text=_source_text_for_display(child),
            is_header=False,
        ))

    rows_html = "\n".join(rows)
    return (
        f'<h3><a id="{anchor}_h">{heading_label}</a></h3>\n'
        f'<p style="margin:0 0 4px 0"><small>{escape(subprogram_line)}</small></p>\n'
        '<table border="1" cellspacing="0" cellpadding="2" '
        'style="border-collapse:collapse;width:100%">\n'
        "<tr>"
        '<th style="width:22%">Имя</th>'
        '<th style="width:8%">Строки</th>'
        '<th style="width:35%">Описание</th>'
        '<th style="width:35%">Код</th>'
        "</tr>\n"
        f"{rows_html}\n"
        "</table>\n"
    )


def _render_row(
    name_html: str,
    start_line: int,
    end_line: int,
    description: str,
    source_text: str,
    is_header: bool,
) -> str:
    bg = ' style="background:#f0f0f0"' if is_header else ""
    lines_cell = _format_lines(start_line, end_line)
    desc_cell = escape(description) if description else ""
    src_cell = (
        f'<pre style="margin:0;white-space:pre-wrap;font-size:x-small">{escape(source_text)}</pre>'
        if source_text
        else ""
    )
    return (
        f"<tr{bg}>"
        f"<td>{name_html}</td>"
        f"<td>{lines_cell}</td>"
        f"<td>{desc_cell}</td>"
        f"<td>{src_cell}</td>"
        "</tr>"
    )


def _source_text_for_display(node: DescriptionNode) -> str:
    if node.statement_type.upper() in _BEGIN_END_TYPES:
        return ""
    return node.source_text


def _format_lines(start_line: int, end_line: int) -> str:
    if not start_line and not end_line:
        return ""
    if start_line == end_line:
        return str(start_line)
    return f"{start_line}–{end_line}"


def _subprogram_path(node: DescriptionNode) -> str:
    parts = []
    if node.schema_name:
        parts.append(node.schema_name.upper())
    if node.object_name:
        parts.append(node.object_name.upper())
    if node.subprogram:
        parts.append(node.subprogram.upper())
    return ".".join(parts)


def _node_label(node: DescriptionNode) -> str:
    """Short display label for a node."""
    if node.title:
        return node.title
    parts = [node.object_name]
    if node.subprogram:
        parts.append(node.subprogram)
    return ".".join(parts) if parts else node.node_id
