"""Card and dependency rendering for dep-graph HTML output.

This module contains all HTML rendering logic for individual cards,
dependency arrows, and related helper functions. Extracted from build.py
for better separation of concerns.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from html import escape

from .keys import format_key, repo_slug
from .titles import normalize_title

# ---------------------------------------------------------------------------
# Context dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CardContext:
    repo: str
    issue_num: int
    lane_code: str
    lane_of: dict[tuple[str, int], str]
    ovr: dict
    gh_entry: dict | None
    extra_blocked_by: list[tuple[str, int]]
    extra_blocking: list[tuple[str, int]]
    gh_issues: dict
    title_rules: list[dict] | None
    primary_repo: str = ""


# ---------------------------------------------------------------------------
# Status derivation
# ---------------------------------------------------------------------------


def _has_active_blockers(
    gh_entry: dict,
    extra_blocked_by: list[tuple[str, int]],
    gh_issues: dict,
    own_repo: str,
) -> bool:
    """Return True if any blocker is still open."""
    for item in gh_entry.get("blocked_by", []):
        if isinstance(item, dict):
            key = format_key(item["repo"], item["issue"])
        else:
            key = format_key(own_repo, item) if own_repo else str(item)
        if gh_issues.get(key, {}).get("state") != "closed":
            return True
    for dep_repo, dep_num in extra_blocked_by:
        key = format_key(dep_repo, dep_num)
        if gh_issues.get(key, {}).get("state") != "closed":
            return True
    return False


def derive_status(
    ovr: dict,
    gh_entry: dict | None,
    extra_blocked_by: list[tuple[str, int]],
    gh_issues: dict,
    repo: str = "",
) -> str:
    if "status" in ovr:
        return ovr["status"]
    if gh_entry is None:
        return "ready"
    if gh_entry.get("defer"):
        return "defer"
    if gh_entry.get("state") == "closed":
        return "done"
    if _has_active_blockers(gh_entry, extra_blocked_by, gh_issues, repo):
        return "blocked"
    return "ready"


# ---------------------------------------------------------------------------
# Deps rendering
# ---------------------------------------------------------------------------


def _ref_to_tuple(item: dict | int, own_repo: str) -> tuple[str, int]:
    """Normalise a blocked_by/blocking item to (repo, issue_num)."""
    if isinstance(item, dict):
        return item["repo"], item["issue"]
    # legacy plain int — belongs to card's own repo
    return own_repo, int(item)


def _collect_dep_lists(
    gh_entry: dict | None,
    extra_blocked_by: list[tuple[str, int]],
    extra_blocking: list[tuple[str, int]],
    own_repo: str = "",
) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """Merge GH deps with extra deps.

    Returns (blocked_by, blocking) as repo/issue tuples.
    """
    if gh_entry is None:
        return list(extra_blocked_by), list(extra_blocking)

    raw_bb = gh_entry.get("blocked_by", [])
    blocked_by: list[tuple[str, int]] = [_ref_to_tuple(x, own_repo) for x in raw_bb]
    seen_bb: set[tuple[str, int]] = set(blocked_by)
    for t in extra_blocked_by:
        if t not in seen_bb:
            blocked_by.append(t)

    raw_bl = gh_entry.get("blocking", [])
    blocking: list[tuple[str, int]] = [_ref_to_tuple(x, own_repo) for x in raw_bl]
    seen_bl: set[tuple[str, int]] = set(blocking)
    for t in extra_blocking:
        if t not in seen_bl:
            blocking.append(t)

    return blocked_by, blocking


def _format_dep_parts(
    blocked_by: list[tuple[str, int]],
    blocking: list[tuple[str, int]],
    lane_code: str,
    lane_of: dict[tuple[str, int], str],
    own_repo: str = "",
) -> tuple[list[str], list[str]]:
    """Split deps into plain (same-lane) and ext (cross-lane) parts."""
    plain_parts: list[str] = []
    ext_parts: list[str] = []

    for ref_repo, n in blocked_by:
        dep_lane = lane_of.get((ref_repo, n))
        is_foreign = ref_repo != own_repo
        if dep_lane == lane_code and not is_foreign:
            plain_parts.append(f"\u2190#{n}")
        elif is_foreign:
            # Show owner/repo#N for cross-repo deps
            ext_parts.append(f"\u2190{ref_repo}#{n}")
        else:
            ext_parts.append(f"\u2190{dep_lane.upper() if dep_lane else '?'}:#{n}")

    for ref_repo, n in blocking:
        dep_lane = lane_of.get((ref_repo, n))
        is_foreign = ref_repo != own_repo
        if dep_lane == lane_code and not is_foreign:
            plain_parts.append(f"\u2192#{n}")
        elif is_foreign:
            ext_parts.append(f"\u2192{ref_repo}#{n}")
        else:
            ext_parts.append(f"\u2192{dep_lane.upper() if dep_lane else '?'}:#{n}")

    return plain_parts, ext_parts


def render_deps(ctx: CardContext) -> str:
    extra_deps_ext: list[str] = ctx.ovr.get("extra_deps_ext", [])

    blocked_by, blocking = _collect_dep_lists(
        ctx.gh_entry, ctx.extra_blocked_by, ctx.extra_blocking, ctx.repo
    )
    plain_parts, ext_parts = _format_dep_parts(
        blocked_by, blocking, ctx.lane_code, ctx.lane_of, ctx.repo
    )
    ext_parts = ext_parts + extra_deps_ext

    if not plain_parts and not ext_parts:
        return '<span class="none">no deps</span>'

    result = escape(" ".join(plain_parts)) if plain_parts else ""
    if ext_parts:
        ext_html = f'<span class="ext">{" ".join(escape(x) for x in ext_parts)}</span>'
        result = (result + " " + ext_html).strip()
    return result


# ---------------------------------------------------------------------------
# Card HTML
# ---------------------------------------------------------------------------


def display_title(
    issue_num: int,
    ovr: dict,
    gh_entry: dict | None,
    title_rules: list[dict] | None,
) -> str:
    # Per-issue override wins
    if "title" in ovr:
        return escape(ovr["title"])
    if gh_entry:
        raw = gh_entry["title"]
        normalized = normalize_title(raw, title_rules)
        return escape(normalized if normalized else raw)
    return f"#{issue_num}"


def _render_repo_badge(repo: str, primary_repo: str) -> str:
    """Return HTML for a repo badge on foreign cards, or empty string if native."""
    if repo == primary_repo:
        return ""
    name = repo.split("/", 1)[1] if "/" in repo else repo
    # data-repo-badge carries the marker string; CSS targets .rbadge.
    return (
        f'<span class="rbadge" data-repo-badge title="{escape(repo)}">'
        f"{escape(name)}</span>"
    )


def _render_missing_card(ref_repo: str, issue_num: int, anchor_attr: str = "") -> str:
    """Return placeholder HTML for an IssueRef absent from gh.json."""
    key = f"{ref_repo}#{issue_num}"
    print(f"WARN: missing {key} in gh.json", file=sys.stderr)
    card_id = f"card-{repo_slug(ref_repo)}-{issue_num}"
    missing_attrs = f' id="{card_id}"{anchor_attr} data-missing="true"'
    return (
        f'<div class="card card--missing"{missing_attrs}>'
        f'<span class="card-missing-label">not-found</span>'
        f'<span class="card-issue">#{issue_num}</span>'
        f'<span class="card-missing-repo">{escape(ref_repo)}</span>'
        f"</div>"
    )


def render_card(ctx: CardContext, anchor_attr: str = "") -> str:
    if ctx.gh_entry is None and ctx.repo:
        return _render_missing_card(ctx.repo, ctx.issue_num, anchor_attr)
    status = derive_status(
        ctx.ovr,
        ctx.gh_entry,
        ctx.extra_blocked_by,
        ctx.gh_issues,
        ctx.repo,
    )
    # size: override wins, then gh.json 'size' field (from size:* label), then empty
    size = (
        ctx.ovr.get("size") or (ctx.gh_entry.get("size") if ctx.gh_entry else "") or ""
    )
    title = display_title(ctx.issue_num, ctx.ovr, ctx.gh_entry, ctx.title_rules)
    size_html = f'<span class="size">{escape(size)}</span>' if size else ""
    deps_html = render_deps(ctx)
    repo_badge_html = _render_repo_badge(ctx.repo, ctx.primary_repo)
    card_id = f"card-{repo_slug(ctx.repo)}-{ctx.issue_num}"
    top_inner = f'<span class="num">#{ctx.issue_num}</span>{repo_badge_html}{size_html}'
    return (
        f'<div class="card {status}" id="{card_id}"{anchor_attr}>'
        f'<div class="top">{top_inner}</div>'
        f'<div class="title">{title}</div>'
        f'<div class="deps">{deps_html}</div>'
        f"</div>"
    )
