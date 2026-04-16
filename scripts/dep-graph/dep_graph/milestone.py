"""Milestone-first layout rendering for dep-graph.

This module contains constants and functions for grouping issues by milestone
and rendering them in a column-based lane layout.

Extracted from build.py for modularity.
"""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from .keys import format_key, repo_slug

if TYPE_CHECKING:
    pass  # Avoid circular imports at type-check time

# ---------------------------------------------------------------------------
# Milestone ordering constants
# ---------------------------------------------------------------------------

# Milestone display order: NO MILESTONE first, then M0-M5
MILESTONE_ORDER = [
    None,  # "No milestone" anchor at top
    "M0 — NATS hardening",
    "M1 — NATS maturity + containerize",
    "M2 — LLM stack modernization",
    "M3 — Observability",
    "M4 — Hub statelessness",
    "M5 — Plugin layer",
]

# Lane groups for column display (merge sub-lanes into major lanes)
LANE_GROUPS = {
    "A": ["a1", "a2"],  # NATS maturity + SDK
    "B": ["b"],  # Containerize
    "C": ["c1", "c2", "c3"],  # LLM stack (LiteLLM, harness, CLI)
    "D": ["d"],  # Observability
    "E": ["e"],  # Hub stateless
    "F": ["f"],  # Plugins
    "G": ["g"],  # Voice
    "H": ["h"],  # Deploy ops
    "I": ["i"],  # Vault ingest
}

# Lane group metadata
LANE_GROUP_META = {
    "A": {"name": "NATS", "color": "a1"},
    "B": {"name": "Containerize", "color": "b"},
    "C": {"name": "LLM Stack", "color": "c1"},
    "D": {"name": "Observability", "color": "d"},
    "E": {"name": "Stateless", "color": "e"},
    "F": {"name": "Plugins", "color": "f"},
    "G": {"name": "Voice", "color": "g"},
    "H": {"name": "Deploy", "color": "h"},
    "I": {"name": "Vault", "color": "i"},
}


# ---------------------------------------------------------------------------
# Lane code utilities
# ---------------------------------------------------------------------------


def _lane_code_to_group(lane_code: str | None) -> str | None:
    """Map a lane code (a1, a2, c1, etc.) to its group (A, C, etc.)."""
    if lane_code is None:
        return None
    for group, codes in LANE_GROUPS.items():
        if lane_code in codes:
            return group
    return None


# ---------------------------------------------------------------------------
# Issue grouping by milestone
# ---------------------------------------------------------------------------


def _group_issues_by_milestone(
    gh_issues: dict,
    primary_repo: str,
) -> dict[str | None, list[tuple[str, int, str | None, str | None]]]:
    """Group issues by milestone.

    Returns milestone -> [(repo, issue_num, lane_code, lane_group)].
    Issues with no milestone get key None. Lane_code is from lane_label field.
    Lane_group is the major lane group (A, B, C, etc.).
    """
    groups: dict[str | None, list[tuple[str, int, str | None, str | None]]] = {}
    for _key, entry in gh_issues.items():
        if not entry:
            continue
        repo = entry.get("repo", primary_repo)
        num = entry.get("number")
        if num is None:
            continue
        lane_code = entry.get("lane_label")
        lane_group = _lane_code_to_group(lane_code)
        milestone = entry.get("milestone")
        if milestone not in groups:
            groups[milestone] = []
        groups[milestone].append((repo, num, lane_code, lane_group))
    return groups


# ---------------------------------------------------------------------------
# Lane metadata preparation
# ---------------------------------------------------------------------------


def _prepare_lane_metadata(layout: dict) -> dict[str, dict]:
    """Build lane_code -> {name, color, epic} lookup from layout."""
    lane_meta: dict[str, dict] = {}
    for lane in layout.get("lanes", []):
        code = lane["code"]
        lane_meta[code] = {
            "name": lane.get("name", code.upper()),
            "color": lane.get("color", code),
            "epic": lane.get("epic"),
        }
    return lane_meta


# ---------------------------------------------------------------------------
# Milestone row preparation
# ---------------------------------------------------------------------------


def _prepare_milestone_rows(  # noqa: C901
    layout: dict,
    gh_issues: dict,
    primary_repo: str,
    overrides: dict,
) -> tuple[list[dict], dict[tuple[str, int], str], dict[str, dict]]:
    """Prepare milestone-first row structure.

    Returns (milestone_rows, lane_of, lane_meta) where:
      - milestone_rows: list of {milestone, issues: [(repo, num, ...)]}
      - lane_of: maps (repo, issue) -> lane_code for cross-lane arrows
      - lane_meta: lane_code -> {name, color, epic}
    """
    lane_meta = _prepare_lane_metadata(layout)
    groups = _group_issues_by_milestone(gh_issues, primary_repo)

    # Also include issues from layout lanes that aren't in gh.json (render as missing)
    seen: set[tuple[str, int]] = set()
    for _ms, issues in groups.items():
        for repo, num, _lane, _group in issues:
            seen.add((repo, num))

    for lane in layout.get("lanes", []):
        lane_code = lane["code"]
        lane_group = _lane_code_to_group(lane_code)
        for item in lane.get("order", []):
            if isinstance(item, dict):
                repo = item["repo"]
                num = item["issue"]
            else:
                repo = primary_repo
                num = int(item)
            if (repo, num) not in seen:
                # Issue not in gh.json - add to "No milestone" group
                # for missing placeholder rendering
                if None not in groups:
                    groups[None] = []
                groups[None].append((repo, num, lane_code, lane_group))
                seen.add((repo, num))

    # Build lane_of mapping for cross-lane dependency arrows
    lane_of: dict[tuple[str, int], str] = {}
    for _ms, issues in groups.items():
        for repo, num, lane_code, _lane_group in issues:
            if lane_code:
                lane_of[(repo, num)] = lane_code

    # Sort issues within each milestone by lane group, then lane code, then issue
    def sort_key(item: tuple[str, int, str | None, str | None]) -> tuple[int, int, int]:
        _repo, num, lane, group = item
        # Sort by group (A=0, B=1, ...), then lane code, then issue number
        group_idx = ord(group) - ord("A") if group else 99
        lane_idx = (
            0
            if lane is None
            else ord(lane[0]) * 100 + (int(lane[1:]) if lane[1:].isdigit() else 0)
        )
        return (group_idx, lane_idx, num)

    milestone_rows: list[dict] = []
    for ms in MILESTONE_ORDER:
        issues = groups.get(ms, [])
        if not issues:
            continue
        sorted_issues = sorted(issues, key=sort_key)
        milestone_rows.append(
            {
                "milestone": ms,
                "issues": sorted_issues,
            }
        )

    # Add any milestones not in MILESTONE_ORDER (e.g., legacy phases)
    for ms in sorted(str(m) if m else "" for m in groups.keys()):
        ms_key = None if ms == "" else ms
        if ms_key not in MILESTONE_ORDER:
            sorted_issues = sorted(groups[ms_key], key=sort_key)
            milestone_rows.append(
                {
                    "milestone": ms,
                    "issues": sorted_issues,
                }
            )

    return milestone_rows, lane_of, lane_meta


# ---------------------------------------------------------------------------
# Milestone card rendering (helper for render_milestone_row)
# ---------------------------------------------------------------------------


def _render_milestone_card(  # noqa: PLR0913
    repo: str,
    issue_num: int,
    lane_code: str | None,
    lane_meta: dict[str, dict],
    lane_of: dict[tuple[str, int], str],
    gh_issues: dict,
    overrides: dict,
    primary_repo: str,
    *,
    derive_status,
    display_title,
    render_deps,
    CardContext,
    _render_missing_card,
    _render_repo_badge,
) -> str:
    """Render a single card within a milestone row.

    Card rendering utilities are passed as keyword-only arguments to avoid
    circular imports with build.py.
    """
    gh_key = format_key(repo, issue_num)
    gh_entry = gh_issues.get(gh_key)

    # If issue is missing from gh.json, render a placeholder
    if gh_entry is None:
        return _render_missing_card(repo, issue_num)

    ovr_key = gh_key
    ovr = overrides.get(ovr_key, {})

    status = derive_status(ovr, gh_entry, [], gh_issues, repo)
    title = display_title(issue_num, ovr, gh_entry, None)
    deps_html = render_deps(
        CardContext(
            repo=repo,
            issue_num=issue_num,
            lane_code=lane_code or "",
            lane_of=lane_of,
            ovr=ovr,
            gh_entry=gh_entry,
            extra_blocked_by=[],
            extra_blocking=[],
            gh_issues=gh_issues,
            title_rules=None,
            primary_repo=primary_repo,
        )
    )

    # Repo badge for foreign repos (top right of card)
    repo_badge_html = _render_repo_badge(repo, primary_repo)

    card_id = f"card-{repo_slug(repo)}-{issue_num}"
    top_inner = f'<span class="num">#{issue_num}</span>{repo_badge_html}'
    return (
        f'<div class="card {status}" id="{card_id}">'
        f'<div class="top">{top_inner}</div>'
        f'<div class="title">{title}</div>'
        f'<div class="deps">{deps_html}</div>'
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Milestone row rendering
# ---------------------------------------------------------------------------


def render_milestone_row(  # noqa: C901, PLR0913
    row: dict,
    lane_meta: dict[str, dict],
    lane_of: dict[tuple[str, int], str],
    gh_issues: dict,
    overrides: dict,
    primary_repo: str,
    *,
    derive_status,
    display_title,
    render_deps,
    CardContext,
    _render_missing_card,
    _render_repo_badge,
) -> str:
    """Render one milestone row with header and cards in lane columns.

    Card rendering utilities are passed as keyword-only arguments to avoid
    circular imports with build.py.
    """
    milestone = row["milestone"]
    issues = row["issues"]  # [(repo, num, lane_code, lane_group)]

    if not issues:
        return ""

    # Header: "No milestone" or milestone title
    if milestone is None:
        header_html = (
            '<div class="milestone-row-header" data-milestone="none">'
            '<span class="ms-anchor">No milestone</span>'
            '<span class="ms-hint">— independent lanes, standalone, closed</span>'
            "</div>"
        )
    else:
        header_html = (
            f'<div class="milestone-row-header">'
            f'<span class="ms-label">{escape(milestone)}</span>'
            f"</div>"
        )

    # Group issues by lane_group (column), then by lane_code within each group
    by_group: dict[str | None, dict[str | None, list[tuple[str, int]]]] = {}
    for repo, num, lane_code, lane_group in issues:
        if lane_group not in by_group:
            by_group[lane_group] = {}
        if lane_code not in by_group[lane_group]:
            by_group[lane_group][lane_code] = []
        by_group[lane_group][lane_code].append((repo, num))

    # Render columns (lane groups A-I)
    columns_html: list[str] = []
    for group in ["A", "B", "C", "D", "E", "F", "G", "H", "I", None]:
        if group not in by_group:
            continue

        group_meta = (
            LANE_GROUP_META[group] if group else {"name": "Other", "color": "indep"}
        )
        by_lane = by_group[group]
        lane_codes = sorted(by_lane.keys(), key=lambda x: (x or "zzz",))

        # Column header (spans all sub-lanes if any)
        col_header = (
            f'<div class="lane-col-head" data-lane="{group_meta["color"]}">'
            f'<span class="code">{group or "?"}</span>'
            f'<span class="name">{escape(group_meta["name"])}</span></div>'
        )

        # If multiple sub-lanes, render as nested sub-columns
        if len(lane_codes) > 1:
            sub_cols_html: list[str] = []
            for lane_code in lane_codes:
                if lane_code is None:
                    continue
                lane_issues = by_lane[lane_code]
                meta = lane_meta.get(
                    lane_code, {"name": lane_code.upper(), "color": "indep"}
                )

                # Sub-column header
                sub_header = (
                    f'<div class="lane-sub-head" data-lane="{meta["color"]}">'
                    f'<span class="code">{escape(lane_code.upper())}</span></div>'
                )

                # Render cards for this sub-lane
                cards_html = [
                    _render_milestone_card(
                        repo,
                        num,
                        lane_code,
                        lane_meta,
                        lane_of,
                        gh_issues,
                        overrides,
                        primary_repo,
                        derive_status=derive_status,
                        display_title=display_title,
                        render_deps=render_deps,
                        CardContext=CardContext,
                        _render_missing_card=_render_missing_card,
                        _render_repo_badge=_render_repo_badge,
                    )
                    for repo, num in lane_issues
                ]

                inner = "\n".join(cards_html)
                sub_cols_html.append(
                    f'<div class="lane-sub-col">\n'
                    f"{sub_header}\n"
                    f'<div class="lane-sub-cards">\n{inner}\n</div>\n'
                    f"</div>"
                )

            sub_cols = "\n".join(sub_cols_html)
            columns_html.append(
                f'<div class="lane-col lane-col--split">\n'
                f"{col_header}\n"
                f'<div class="lane-sub-cols">\n{sub_cols}\n</div>\n'
                f"</div>"
            )
        else:
            # Single lane or no lane: render cards directly
            lane_code = lane_codes[0] if lane_codes else None
            lane_issues = by_lane.get(lane_code, [])

            cards_html = [
                _render_milestone_card(
                    repo,
                    num,
                    lane_code,
                    lane_meta,
                    lane_of,
                    gh_issues,
                    overrides,
                    primary_repo,
                    derive_status=derive_status,
                    display_title=display_title,
                    render_deps=render_deps,
                    CardContext=CardContext,
                    _render_missing_card=_render_missing_card,
                    _render_repo_badge=_render_repo_badge,
                )
                for repo, num in lane_issues
            ]

            inner = "\n".join(cards_html)
            columns_html.append(
                f'<div class="lane-col">\n'
                f"{col_header}\n"
                f'<div class="lane-col-cards">\n{inner}\n</div>\n'
                f"</div>"
            )

    columns = "\n".join(columns_html)
    return (
        f'<div class="milestone-row">\n'
        f"{header_html}\n"
        f'<div class="milestone-cols">\n{columns}\n</div>\n'
        f"</div>"
    )
