"""Lane HTML rendering for dep-graph.

This module contains functions for rendering flattened lanes as HTML,
including par-group handling, epic wrapping, and row-level rendering.

Extracted from build.py for modularity.
"""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from .flatten import FlatLaneContext
from .keys import format_key, parse_key
from .render import CardContext, render_card

if TYPE_CHECKING:
    pass  # Avoid circular imports at type-check time


# ---------------------------------------------------------------------------
# Par-group handling
# ---------------------------------------------------------------------------


def _close_par(row_htmls: list[str], current_par: str | None) -> None:
    """Close an open par-group div if one is active."""
    if current_par is not None:
        row_htmls.append("    </div>")


# ---------------------------------------------------------------------------
# Issue row rendering
# ---------------------------------------------------------------------------


def _render_issue_row(
    row: dict,
    ctx: FlatLaneContext,
    row_htmls: list[str],
    current_par: str | None,
) -> str | None:
    """Render an issue row. Returns updated current_par."""
    code = ctx.fl["code"]
    extra_blocked_by_map: dict[str, list[int]] = ctx.extra_deps.get(
        "extra_blocked_by", {}
    )
    extra_blocking_map: dict[str, list[int]] = ctx.extra_deps.get("extra_blocking", {})

    n = row["issue"]
    row_repo: str = row.get("repo", "")
    pg: str | None = row.get("par_group")
    if pg != current_par:
        _close_par(row_htmls, current_par)
        current_par = None
        if pg is not None:
            row_htmls.append('    <div class="par">')
            current_par = pg
    ovr_key = format_key(row_repo, n) if row_repo else str(n)
    ovr = ctx.overrides.get(ovr_key, {})
    gh_key = format_key(row_repo, n) if row_repo else str(n)
    gh_entry = ctx.gh_issues.get(gh_key)
    if gh_entry is None and not row_repo:
        # fallback for legacy int-keyed overrides
        gh_entry = ctx.gh_issues.get(str(n))
    bb_key = format_key(row_repo, n) if row_repo else str(n)
    bl_key = format_key(row_repo, n) if row_repo else str(n)
    _raw_bb = extra_blocked_by_map.get(bb_key, [])
    _raw_bl = extra_blocking_map.get(bl_key, [])

    def _as_ref(k: str | int) -> tuple[str, int]:
        if isinstance(k, str):
            return parse_key(k)
        return (row_repo, int(k))

    extra_bb: list[tuple[str, int]] = [_as_ref(k) for k in _raw_bb]
    extra_bl: list[tuple[str, int]] = [_as_ref(k) for k in _raw_bl]
    anchor_attr = ""
    if "anchor" in row:
        anchor_attr = f' data-anchor="{escape(row["anchor"])}"'
    elif "anchor_after" in row:
        anchor_attr = f' data-anchor-after="{escape(row["anchor_after"])}"'
    card_ctx = CardContext(
        repo=row_repo,
        issue_num=n,
        lane_code=code,
        lane_of=ctx.lane_of,
        ovr=ovr,
        gh_entry=gh_entry,
        extra_blocked_by=extra_bb,
        extra_blocking=extra_bl,
        gh_issues=ctx.gh_issues,
        title_rules=ctx.title_rules,
        primary_repo=ctx.primary_repo,
    )
    card = render_card(card_ctx, anchor_attr)
    indent = "      " if current_par is not None else "    "
    row_htmls.append(f"{indent}{card}")
    return current_par


def _render_row(
    row: dict,
    ctx: FlatLaneContext,
    row_htmls: list[str],
    current_par: str | None,
) -> str | None:
    """Render one row and append to row_htmls. Returns updated current_par."""
    if row.get("spacer"):
        _close_par(row_htmls, current_par)
        current_par = None
        row_htmls.append('    <div class="spacer" aria-hidden="true"></div>')
    elif row.get("band"):
        _close_par(row_htmls, current_par)
        current_par = None
        row_htmls.append(f'    <div class="ms-band">{escape(row["band"])}</div>')
    elif "issue" in row:
        current_par = _render_issue_row(row, ctx, row_htmls, current_par)
    return current_par


# ---------------------------------------------------------------------------
# Lane rendering
# ---------------------------------------------------------------------------


def render_flat_lane(ctx: FlatLaneContext) -> str:
    """Render a flattened lane as HTML."""
    fl = ctx.fl
    code = fl["code"]
    name = fl["name"]
    color = fl["color"]
    epic = fl.get("epic")
    rows = fl["flat_rows"]

    parts: list[str] = []
    parts.append(f'  <div class="lane" data-lane="{escape(color)}">')
    parts.append(
        f'    <div class="lane-head">'
        f'<span class="code">{escape(code.upper())}</span>'
        f'<span class="name">{escape(name)}</span></div>'
    )

    row_htmls: list[str] = []
    current_par: str | None = None

    for row in rows:
        current_par = _render_row(row, ctx, row_htmls, current_par)

    # close any open par group
    _close_par(row_htmls, current_par)

    if epic:
        defer_class = " defer" if epic.get("defer") else ""
        epic_issue = epic["issue"]
        epic_label = escape(epic["label"])
        epic_tag = escape(epic["tag"])
        parts.append(f'    <div class="epic-wrap{defer_class}">')
        parts.append(
            f'      <div class="epic-banner">'
            f"<span>#{epic_issue} \u00b7 {epic_label}</span>"
            f'<span class="tag">{epic_tag}</span></div>'
        )
        for rh in row_htmls:
            parts.append("  " + rh)
        parts.append("    </div>")
    else:
        parts.extend(row_htmls)

    parts.append("  </div>")
    return "\n".join(parts)
