"""Build dep-graph HTML from layout.json + gh.json.

Layout schema (label-driven, all fields except meta/lanes optional):
  meta{}                   — title, date, repo, label_prefix, issue, category, …
  lanes[].code/name/color  — required per lane
  lanes[].epic             — optional epic banner
  lanes[].order[]          — optional: if absent, auto-derived from gh_issues
  lanes[].par_groups{}     — optional: if absent, auto-derived from topo depth
  lanes[].bands[]          — optional: if absent, auto-derived from milestones
  overrides{}              — optional per-issue editorial overrides
  extra_deps{}             — deprecated: use GH blocked_by instead
  standalone.order[]       — optional: if absent, auto-derived from labels
  cross_deps[]             — optional cross-lane notes (editorial)
  title_rules[]            — deprecated: built-in rules in titles.py handle this

Defer status: driven by gh.issues[N].defer (from <prefix>defer label).
Label-drift warnings: emitted to stderr when layout order lane ≠ GH label lane.
Untriaged section: labeled issues not in any lane order (only for explicit orders).
Size: read from gh.json entry 'size' field (derived from size:* label in fetch),
      overridden by overrides.<key>.size if present.
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from html import escape
from pathlib import Path

from jinja2 import Environment, PackageLoader

from .milestone import _prepare_milestone_rows, render_milestone_row
from .render import (
    CardContext,
    _render_missing_card,
    _render_repo_badge,
    derive_status,
    display_title,
    render_deps,
)
from .schema import LayoutValidationError, validate_layout
from .sections import render_cross_deps

# ---------------------------------------------------------------------------
# Jinja2 environment
# ---------------------------------------------------------------------------

_jinja_env = Environment(loader=PackageLoader("dep_graph", "templates"))


# ---------------------------------------------------------------------------
# Build paths dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BuildPaths:
    layout_path: Path
    cache_path: Path
    out_path: Path
    bak_path: Path | None = None


# ---------------------------------------------------------------------------
# Meta escaping
# ---------------------------------------------------------------------------


def _escape_meta(meta: dict, primary_repo: str) -> dict[str, str]:
    """Escape user-provided meta fields for safe HTML rendering."""
    raw_issue = meta.get("issue", "")
    issue_display = (
        str(raw_issue["issue"]) if isinstance(raw_issue, dict) else str(raw_issue)
    )
    repo_url = (
        f"https://github.com/{escape(primary_repo)}/issues" if primary_repo else "#"
    )
    return {
        "title": escape(meta["title"]),
        "date": escape(meta["date"]),
        "issue": escape(issue_display),
        "category": escape(meta.get("category", "")),
        "cat_label": escape(meta.get("cat_label", "")),
        "color": escape(meta.get("color", "")),
        "repo_url": repo_url,
    }


# ---------------------------------------------------------------------------
# Main HTML builder
# ---------------------------------------------------------------------------


def build_html(layout: dict, gh_issues: dict) -> str:
    meta = layout["meta"]
    overrides = layout.get("overrides", {})
    cross_deps = layout.get("cross_deps", [])

    _repos_list: list[str] = meta.get("repos", [])
    primary_repo: str = _repos_list[0] if _repos_list else meta.get("repo", "")

    # Milestone-first layout
    milestone_rows, lane_of, lane_meta = _prepare_milestone_rows(
        layout, gh_issues, primary_repo, overrides
    )

    milestone_html = "\n\n".join(
        render_milestone_row(
            row,
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
        for row in milestone_rows
    )
    cross_html = render_cross_deps(cross_deps)

    escaped = _escape_meta(meta, primary_repo)
    title = escaped["title"]
    date = escaped["date"]
    issue = escaped["issue"]
    category = escaped["category"]
    cat_label = escaped["cat_label"]
    color = escaped["color"]
    repo_url = escaped["repo_url"]
    ms_count = len(milestone_rows)

    subtitle = (
        f"{ms_count} milestones \u00b7 M0\u2192M1\u2192M2\u2192M3"
        f"\u2192M4\u2192M5 \u00b7"
        f" <strong>\u2190</strong> = blocked by"
        f" \u00b7 <strong>\u2192</strong> = unblocks"
        f' \u00b7 <span class="ext"'
        f' style="color:var(--dep-ext);font-family:monospace;font-weight:600;">'
        f"X:#N</span> = cross-lane"
    )
    footer_line = (
        f"Lyra v2 plan \u00b7 refreshed {date}"
        f' \u00b7 <a href="{repo_url}">{escape(repo_url)}</a>'
        f' \u00b7 <a href="nats-arch-roadmap.html">NATS arch roadmap</a>'
    )
    fonts_url = (
        "https://fonts.googleapis.com/css2?"
        "family=Inter:wght@400;500;600;700"
        "&family=JetBrains+Mono:wght@400;600;700&display=swap"
    )
    legend_epic = (
        '<span class="pill"'
        ' style="background: var(--accent); color: #fff;'
        ' border-color: var(--accent);">epic</span>'
    )
    legend_xref = (
        '<span class="pill">'
        '<span style="color: var(--dep-ext); font-weight: 700;">'
        "B:#727</span> = cross-lane ref</span>"
    )

    template = _jinja_env.get_template("base.html")
    return template.render(
        title=title,
        date=date,
        issue=issue,
        category=category,
        cat_label=cat_label,
        color=color,
        fonts_url=fonts_url,
        subtitle=subtitle,
        legend_epic=legend_epic,
        legend_xref=legend_xref,
        footer_line=footer_line,
        extra_vars="",
        extra_selectors="",
        milestone_html=milestone_html,
        cross_html=cross_html,
    )


def run_build(
    paths: BuildPaths, *, no_validate: bool = False, verbose: bool = False
) -> int:
    """Main build logic. Returns exit code."""
    layout_path = paths.layout_path
    cache_path = paths.cache_path
    out_path = paths.out_path
    bak_path = paths.bak_path

    if not layout_path.exists():
        print(f"ERROR: {layout_path} not found", file=sys.stderr)
        return 1
    if not cache_path.exists():
        print(
            f"ERROR: {cache_path} not found — run dep-graph fetch first",
            file=sys.stderr,
        )
        return 1

    layout = json.loads(layout_path.read_text())
    gh_data = json.loads(cache_path.read_text())
    gh_issues = gh_data.get("issues", {})

    if not no_validate:
        try:
            validate_layout(layout_path)
        except LayoutValidationError as exc:
            print(f"SCHEMA ERROR at {exc.path}: {exc.message}", file=sys.stderr)
            return 1

    if bak_path and out_path.exists():
        shutil.copy2(out_path, bak_path)
        if verbose:
            print(f"Backed up to {bak_path}")

    html = build_html(layout, gh_issues)
    out_path.write_text(html)
    print(f"Written: {out_path} ({out_path.stat().st_size} bytes)")
    return 0
