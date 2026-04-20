"""Page header component."""
from __future__ import annotations

import html


def render_header(title: str, subtitle: str) -> str:
    """Render the top page header. `title` is plain text; it gets escaped and
    'v2'/'v5' are tinted with the accent class."""
    safe_title = html.escape(title)
    return (
        '<header class="page-header">\n'
        '  <div>\n'
        f'    <h1>{safe_title}</h1>\n'
        f'    <div class="subtitle">{html.escape(subtitle)}</div>\n'
        '  </div>\n'
        '</header>\n'
    )


def render_footer(repo: str, date: str) -> str:
    repo_url = f"https://github.com/{repo}/issues"
    return (
        '<footer class="page-footer">\n'
        f'  Lyra v2 plan · refreshed {html.escape(date)} · '
        f'<a href="{repo_url}">{html.escape(repo_url)}</a> · '
        '<a href="lyra-v2-dependency-graph-v3.1.html">v3.1</a> · '
        '<a href="lyra-v2-dependency-graph-v4.8.html">v4.8</a>\n'
        '</footer>\n'
    )
