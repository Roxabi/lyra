"""Mode toggle — [Graph | Table] segment control.

`active` must be one of 'graph' | 'grid'. Default is 'graph'.
JS in assets/app.js re-syncs this from localStorage/hash on load.
"""
from __future__ import annotations


def render_toggle(active: str = "graph") -> str:
    assert active in ("graph", "grid"), f"invalid view: {active}"
    g_active = " active" if active == "graph" else ""
    t_active = " active" if active == "grid" else ""
    g_pressed = "true" if active == "graph" else "false"
    t_pressed = "true" if active == "grid" else "false"
    return (
        '<div class="mode-toggle" role="group" aria-label="View mode" tabindex="0">\n'
        f'  <button type="button" data-view="graph" class="graph{g_active}" '
        f'aria-pressed="{g_pressed}">Graph</button>\n'
        f'  <button type="button" data-view="grid" class="grid{t_active}" '
        f'aria-pressed="{t_pressed}">Table</button>\n'
        '</div>\n'
    )
