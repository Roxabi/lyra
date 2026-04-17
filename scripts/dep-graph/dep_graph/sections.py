"""Section rendering for dep-graph.

This module contains functions for rendering standalone sections like
untriaged issues, standalone cards, and cross-lane dependencies.

Extracted from build.py for modularity.
"""

from __future__ import annotations

from html import escape

from .keys import format_key
from .render import derive_status, display_title

# ---------------------------------------------------------------------------
# Untriaged section
# ---------------------------------------------------------------------------


def render_untriaged(
    untriaged: list[tuple[str, int]],
    gh_issues: dict,
    primary_repo: str = "",
) -> str:
    """Render untriaged issues section."""
    if not untriaged:
        return ""
    cards = []
    for repo, n in untriaged:
        gh_entry = gh_issues.get(format_key(repo, n))
        title = escape(gh_entry.get("title", f"#{n}")) if gh_entry else f"#{n}"
        cards.append(
            f'    <div class="card ready">'
            f'<div class="top"><span class="num">#{n}</span></div>'
            f'<div class="title">{title}</div>'
            f'<div class="deps"><span class="none">untriaged</span></div>'
            f"</div>"
        )
    inner = "\n".join(cards)
    return f"""\
<div class="standalone">
  <div class="standalone-head">
    <span class="label">Untriaged</span>
    <span class="hint">\u2014 labeled but not in any lane order</span>
  </div>
  <div class="standalone-grid">
{inner}
  </div>
</div>

"""


# ---------------------------------------------------------------------------
# Standalone section
# ---------------------------------------------------------------------------


def render_standalone(
    order: list[tuple[str, int]],
    gh_issues: dict,
    overrides: dict,
    title_rules: list[dict] | None,
    primary_repo: str = "",
) -> str:
    """Render standalone issues section."""
    cards = []
    for repo, n in order:
        ovr_key = format_key(repo, n) if repo else str(n)
        ovr = overrides.get(ovr_key, {})
        gh_entry = gh_issues.get(format_key(repo, n) if repo else str(n))
        status = derive_status(ovr, gh_entry, [], gh_issues, repo)
        size = ovr.get("size") or (gh_entry.get("size") if gh_entry else "") or ""
        title = display_title(n, ovr, gh_entry, title_rules)
        size_html = f'<span class="size">{escape(size)}</span>' if size else ""
        cards.append(
            f'    <div class="card {status}">'
            f'<div class="top"><span class="num">#{n}</span>{size_html}</div>'
            f'<div class="title">{title}</div>'
            f'<div class="deps"><span class="none">no deps</span></div>'
            f"</div>"
        )
    return "\n".join(cards)


# ---------------------------------------------------------------------------
# Cross-deps section
# ---------------------------------------------------------------------------


def render_cross_deps(cross_deps: list[dict]) -> str:
    """Render cross-lane dependencies section."""
    items = []
    for cd in cross_deps:
        kind = escape(cd["kind"])
        text = escape(cd["text"])
        items.append(f'    <li><span class="kind">{kind}</span>{text}</li>')
    return "\n".join(items)
