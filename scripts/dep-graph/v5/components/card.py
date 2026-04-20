"""Issue-card component. Shared by grid and graph views."""
from __future__ import annotations

import html
from typing import Any

from ..data.model import ref_key


def _dep_chip(refs: list[dict[str, Any]], arrow: str, issues: dict[str, dict]) -> str:
    if not refs:
        return ""
    pills: list[str] = []
    for r in refs:
        k = ref_key(r)
        target = issues.get(k)
        num = r["issue"]
        is_external = r["repo"] != "Roxabi/lyra"
        closed = target and target["state"] == "closed"
        cls = "dep-ref"
        if closed:
            cls += " closed"
        if is_external:
            cls += " ext"
        prefix = r["repo"].split("/")[-1][:1].upper() + ":" if is_external else ""
        pills.append(f'<span class="{cls}" data-link="{k}">{prefix}#{num}</span>')
    direction = "in" if arrow == "←" else "out"
    return (
        f'<span class="dep-chip dep-{direction}">'
        f'<span class="dep-arrow">{arrow}</span>'
        f'{"".join(pills)}</span>'
    )


def render_card(  # noqa: PLR0913
    iss: dict[str, Any],
    *,
    epic_tone: str,
    issues: dict[str, dict],
    status: str,
    depth: int,
    style: str = "",
) -> str:
    """Render one issue-card.

    `style` lets the graph view inject absolute positioning.
    """
    num = iss["number"]
    repo = iss["repo"]
    key = f"{repo}#{num}"
    title = html.escape(iss["title"])
    short = title if len(title) <= 52 else title[:51] + "…"
    url = f"https://github.com/{repo}/issues/{num}"
    size = iss.get("size") or ""
    size_pill = (
        f'<span class="card-size">{html.escape(size)}</span>' if size else ""
    )
    blocked_by = iss.get("blocked_by", [])
    blocking = iss.get("blocking", [])
    blocked_keys = ",".join(ref_key(b) for b in blocked_by)
    blocking_keys = ",".join(ref_key(b) for b in blocking)
    dep_chips = (
        _dep_chip(blocked_by, "←", issues)
        + _dep_chip(blocking, "→", issues)
    )
    dep_row = f'<div class="dep-row">{dep_chips}</div>' if dep_chips else ""
    style_attr = f' style="{style}"' if style else ""
    return (
        f'<a class="issue-card {status}" data-tone="{epic_tone}" '
        f'data-iss="{key}" data-depth="{depth}" '
        f'data-blockedby="{blocked_keys}" data-blocking="{blocking_keys}" '
        f'href="{url}" target="_blank" rel="noopener" title="{title}"{style_attr}>'
        f'<div class="card-head">'
        f'<span class="card-dot" aria-hidden="true"></span>'
        f'<span class="card-num">#{num}</span>'
        f'<span class="card-title">{short}</span>'
        f'{size_pill}'
        f'</div>'
        f'{dep_row}'
        f'</a>'
    )
