"""Compose the final v5 HTML page.

Concatenates assets/*.css into a single <style> and assets/*.js into a
single <script>. Imports views and components to build the body.
"""
from __future__ import annotations

import html
from pathlib import Path

from .components.header import render_footer
from .components.toggle import render_toggle
from .components.toolbar import render_toolbar
from .data.model import COLUMN_GROUPS, MILESTONES, GraphData
from .views import graph as graph_view
from .views import grid as grid_view

ASSETS = Path(__file__).resolve().parent / "assets"

CSS_FILES = (
    "tokens.css", "base.css", "toggle.css",
    "card.css", "grid.css", "graph.css",
)
JS_FILES = ("hover.js", "app.js")

FONT_LINKS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link rel="stylesheet" '
    'href="https://fonts.googleapis.com/css2?'
    "family=Inter:wght@400;500;600;700"
    "&family=JetBrains+Mono:wght@400;500;600;700"
    "&family=Outfit:wght@500;600;700"
    '&display=swap">'
)


def _read_assets(names: tuple[str, ...]) -> str:
    return "\n\n".join((ASSETS / n).read_text() for n in names)


def _subtitle(data: GraphData) -> str:
    c = data.counts
    return (
        f"{len(MILESTONES)} milestones × {len(COLUMN_GROUPS)} columns · "
        f"{c.get('ready', 0)} ready · {c.get('blocked', 0)} blocked · "
        f"{c.get('done', 0)} done · {data.total} total · "
        f"toggle Graph/Table · hover a card to trace its dep chain"
    )


def _title_html() -> str:
    return (
        'Lyra <span class="accent">v2</span> — '
        'Dep Graph <span class="accent">v5.1</span>'
    )


def build_html(data: GraphData, active: str = "graph") -> str:
    """Assemble the full <!DOCTYPE html> ... </html> page."""
    assert active in ("graph", "grid")
    meta = data.meta
    plain_title = html.escape(f"{meta['title']} — v5.1")
    date = meta.get("date", "")
    issue_num = meta.get("issue", {}).get("issue", "")

    css = _read_assets(CSS_FILES)
    js = _read_assets(JS_FILES)
    header = (
        '<header class="page-header">\n'
        f'  <div>\n'
        f'    <h1>{_title_html()}</h1>\n'
        f'    <div class="subtitle">{html.escape(_subtitle(data))}</div>\n'
        '  </div>\n'
        f'  {render_toggle(active)}'
        '</header>\n'
    )
    toolbar = render_toolbar()
    grid_html = grid_view.render(data, active=(active == "grid"))
    graph_html = graph_view.render(data, active=(active == "graph"))
    footer = render_footer(data.primary_repo, date)

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<!-- diagram-meta:start -->
<meta name="diagram:title"     content="{plain_title}">
<meta name="diagram:date"      content="{html.escape(date)}">
<meta name="diagram:category"  content="plan">
<meta name="diagram:cat-label" content="Plan">
<meta name="diagram:color"     content="amber">
<meta name="diagram:badges"    content="latest">
<meta name="diagram:issue"     content="{html.escape(str(issue_num))}">
<!-- diagram-meta:end -->
<title>{plain_title}</title>
{FONT_LINKS}
<style>
{css}
</style>
</head>
<body class="group-epic view-{active}-active">

<div class="sticky-head">
{header}
{toolbar}
</div>

{graph_html}
{grid_html}
{footer}

<script>
{js}
</script>

</body>
</html>
"""


def write(out_path: Path, data: GraphData, active: str = "graph") -> int:
    html_out = build_html(data, active=active)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_out)
    return len(html_out)
