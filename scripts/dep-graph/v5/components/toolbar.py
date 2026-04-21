"""Filter toolbar: group/hide-closed/only-ready + legend pills."""

from __future__ import annotations


def render_toolbar() -> str:
    return (
        '<div class="toolbar">\n'
        '  <div class="filters">\n'
        '    <label><input type="checkbox" id="toggle-epic" checked>'
        " group by epic</label>\n"
        '    <label><input type="checkbox" id="toggle-closed"> hide closed</label>\n'
        '    <label><input type="checkbox" id="toggle-ready"> only ready</label>\n'
        "  </div>\n"
        '  <label class="search">'
        '<input type="search" id="issue-search" inputmode="numeric" '
        'placeholder="# issue" autocomplete="off" spellcheck="false">'
        "</label>\n"
        '  <span class="legend-pills">\n'
        '    <span class="lp ready"><span class="lpd"></span>ready</span>\n'
        '    <span class="lp blocked"><span class="lpd"></span>blocked</span>\n'
        '    <span class="lp done"><span class="lpd"></span>done</span>\n'
        "  </span>\n"
        "</div>\n"
    )
