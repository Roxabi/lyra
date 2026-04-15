"""Build dep-graph HTML from layout.json + gh.json.

Layout schema (label-driven):
  meta{}                   — title, date, repo, label_prefix, issue, category, …
  lanes[].order[]          — issue numbers in display order
  lanes[].par_groups{}     — { group_id: [issue, ...] }
  lanes[].bands[]          — [{ before: N, text: "..." }]
  overrides{}              — per-issue keyed by str(number)
  extra_deps{}             — extra_blocked_by / extra_blocking maps
  standalone.order[]       — standalone issue numbers
  cross_deps[]             — cross-lane notes
  title_rules[]            — sequential regex rules for title normalization

Defer status: driven by gh.issues[N].defer (from <prefix>defer label).
Label-drift warnings: emitted to stderr when layout order lane ≠ GH label lane.
Untriaged section: labeled issues not in any lane order.
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
from dataclasses import dataclass
from html import escape
from pathlib import Path

from .keys import format_key, repo_slug
from .schema import LayoutValidationError, validate_layout
from .titles import normalize_title

# ---------------------------------------------------------------------------
# Context dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CardContext:
    repo: str
    issue_num: int
    lane_code: str
    lane_of: dict[tuple[str, int], str]
    ovr: dict
    gh_entry: dict | None
    extra_blocked_by: list[int]
    extra_blocking: list[int]
    gh_issues: dict
    title_rules: list[dict]
    primary_repo: str = ""


@dataclass(frozen=True, slots=True)
class FlatLaneContext:
    fl: dict
    lane_of: dict[tuple[str, int], str]
    gh_issues: dict
    overrides: dict
    extra_deps: dict
    title_rules: list[dict]
    primary_repo: str = ""


@dataclass(frozen=True, slots=True)
class BuildPaths:
    layout_path: Path
    cache_path: Path
    out_path: Path
    bak_path: Path | None = None


# ---------------------------------------------------------------------------
# Status derivation
# ---------------------------------------------------------------------------


def _has_active_blockers(
    gh_entry: dict,
    extra_blocked_by: list[int],
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
    for n in extra_blocked_by:
        key = format_key(own_repo, n) if own_repo else str(n)
        if gh_issues.get(key, {}).get("state") != "closed":
            return True
    return False


def derive_status(
    ovr: dict,
    gh_entry: dict | None,
    extra_blocked_by: list[int],
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
    extra_blocked_by: list[int],
    extra_blocking: list[int],
    own_repo: str = "",
) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """Merge GH deps with extra deps.

    Returns (blocked_by, blocking) as repo/issue tuples.
    """
    if gh_entry is None:
        return (
            [(own_repo, n) for n in extra_blocked_by],
            [(own_repo, n) for n in extra_blocking],
        )

    raw_bb = gh_entry.get("blocked_by", [])
    blocked_by: list[tuple[str, int]] = [_ref_to_tuple(x, own_repo) for x in raw_bb]
    seen_bb: set[tuple[str, int]] = set(blocked_by)
    for n in extra_blocked_by:
        t = (own_repo, n)
        if t not in seen_bb:
            blocked_by.append(t)

    raw_bl = gh_entry.get("blocking", [])
    blocking: list[tuple[str, int]] = [_ref_to_tuple(x, own_repo) for x in raw_bl]
    seen_bl: set[tuple[str, int]] = set(blocking)
    for n in extra_blocking:
        t = (own_repo, n)
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
    title_rules: list[dict],
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
        ctx.ovr, ctx.gh_entry, ctx.extra_blocked_by, ctx.gh_issues,
        ctx.repo,
    )
    size = ctx.ovr.get("size", "")
    title = display_title(ctx.issue_num, ctx.ovr, ctx.gh_entry, ctx.title_rules)
    size_html = f'<span class="size">{escape(size)}</span>' if size else ""
    deps_html = render_deps(ctx)
    repo_badge_html = _render_repo_badge(ctx.repo, ctx.primary_repo)
    card_id = f"card-{repo_slug(ctx.repo)}-{ctx.issue_num}"
    top_inner = (
        f'<span class="num">#{ctx.issue_num}</span>{repo_badge_html}{size_html}'
    )
    return (
        f'<div class="card {status}" id="{card_id}"{anchor_attr}>'
        f'<div class="top">{top_inner}</div>'
        f'<div class="title">{title}</div>'
        f'<div class="deps">{deps_html}</div>'
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Anchor / spacer computation
# ---------------------------------------------------------------------------


def compute_slot_index(flat_rows: list[dict], target_issue: int) -> int:
    """1-based slot index of target_issue in a flat row list (bands + cards)."""
    idx = 0
    for row in flat_rows:
        if row.get("band"):
            idx += 1
        elif "issue" in row:
            idx += 1
            if row["issue"] == target_issue:
                return idx
    return -1


def _collect_anchors(flat_lanes: list[dict]) -> dict[str, tuple[str, int]]:
    """Pass 1: collect anchor positions from all lanes."""
    anchors: dict[str, tuple[str, int]] = {}
    for fl in flat_lanes:
        code = fl["code"]
        for row in fl["flat_rows"]:
            if "issue" not in row or "anchor" not in row:
                continue
            slot = compute_slot_index(fl["flat_rows"], row["issue"])
            if slot == -1:
                print(
                    f"WARN: anchor '{row['anchor']}' issue #{row['issue']} "
                    f"not found in lane {code}",
                    file=sys.stderr,
                )
                continue
            anchors[row["anchor"]] = (code, slot)
    return anchors


def _compute_lane_insertions(
    fl: dict,
    anchors: dict[str, tuple[str, int]],
) -> list[tuple[int, int]]:
    """Compute (insert_before_index, count) pairs for one lane."""
    code = fl["code"]
    rows = fl["flat_rows"]
    insertions: list[tuple[int, int]] = []

    for i, row in enumerate(rows):
        if "issue" not in row or "anchor_after" not in row:
            continue
        anchor_id = row["anchor_after"]
        if anchor_id not in anchors:
            print(
                f"WARN: anchor_after '{anchor_id}' in lane {code}: unknown anchor",
                file=sys.stderr,
            )
            continue
        _, ref_slot = anchors[anchor_id]
        target_slot = compute_slot_index(rows, row["issue"])
        if target_slot == -1:
            continue

        pg = row.get("par_group")
        insert_before = i
        if pg is not None:
            for j in range(i - 1, -1, -1):
                if rows[j].get("par_group") == pg:
                    insert_before = j
                else:
                    break

        needed = (ref_slot + 1) - target_slot
        if needed <= 0:
            print(
                f"WARN: anchor_after '{anchor_id}' lane {code}: "
                f"slot {target_slot} >= {ref_slot}+1",
                file=sys.stderr,
            )
            continue
        insertions.append((insert_before, needed))
    return insertions


def inject_spacers(flat_lanes: list[dict]) -> list[dict]:
    """Insert synthetic spacer rows for anchor-based cross-lane alignment."""
    flat_lanes = copy.deepcopy(flat_lanes)

    anchors = _collect_anchors(flat_lanes)
    if not anchors:
        return flat_lanes

    # Pass 2: insert spacers
    for fl in flat_lanes:
        rows = fl["flat_rows"]
        insertions = _compute_lane_insertions(fl, anchors)
        for insert_before, count in sorted(
            insertions, key=lambda x: x[0], reverse=True
        ):
            rows[insert_before:insert_before] = [{"spacer": True}] * count

    return flat_lanes


# ---------------------------------------------------------------------------
# Lane flattening (new schema → flat_rows)
# ---------------------------------------------------------------------------


def _check_drift(repo: str, n: int, code: str, gh_issues: dict) -> None:
    """Emit stderr warning if GH lane label differs from layout lane."""
    gh_entry = gh_issues.get(format_key(repo, n))
    if not gh_entry:
        return
    gh_lane = gh_entry.get("lane_label")
    if gh_lane and gh_lane != code:
        print(
            f"WARN drift: layout says {format_key(repo, n)} → lane {code},"
            f" gh label says {gh_lane}",
            file=sys.stderr,
        )


def _build_issue_to_pg(pg_map: dict) -> dict[tuple[str, int], str]:
    """Build (repo, issue) → par_group_id map from lane par_groups."""
    issue_to_pg: dict[tuple[str, int], str] = {}
    for gid, members in pg_map.items():
        for ref in members:
            if isinstance(ref, dict):
                issue_to_pg[(ref["repo"], ref["issue"])] = gid
            else:
                issue_to_pg[("", int(ref))] = gid
    return issue_to_pg


def _build_band_before(bands: list) -> dict[tuple[str, int] | int, str]:
    """Build ref → band text map from lane bands list."""
    band_before: dict[tuple[str, int] | int, str] = {}
    for b in bands:
        bef = b["before"]
        if isinstance(bef, dict):
            band_before[(bef["repo"], bef["issue"])] = b["text"]
        else:
            band_before[int(bef)] = b["text"]
    return band_before


def _flatten_order_item(
    item: dict | int,
    overrides: dict,
    issue_to_pg: dict[tuple[str, int], str],
    band_before: dict[tuple[str, int] | int, str],
) -> tuple[dict | None, dict]:
    """Convert one order item to an optional band row + an issue row."""
    if isinstance(item, dict):
        repo: str = item["repo"]
        n: int = item["issue"]
        ref_key: tuple[str, int] | int = (repo, n)
    else:
        repo = ""
        n = int(item)
        ref_key = n

    band_row: dict | None = None
    if ref_key in band_before:
        band_row = {"band": band_before[ref_key]}

    ovr_key = f"{repo}#{n}" if repo else str(n)
    ovr = overrides.get(ovr_key, {})
    row: dict = {"issue": n, "repo": repo}
    pg = issue_to_pg.get((repo, n)) or issue_to_pg.get(("", n))
    if pg is not None:
        row["par_group"] = pg
    if "anchor" in ovr:
        row["anchor"] = ovr["anchor"]
    if "anchor_after" in ovr:
        row["anchor_after"] = ovr["anchor_after"]
    return band_row, row


def flatten_lane(
    lane: dict,
    overrides: dict,
    label_drift_check: bool,
    gh_issues: dict,
) -> dict:
    """Convert new-schema lane into flat_rows consumed by render/inject."""
    code = lane["code"]
    order = lane.get("order", [])
    issue_to_pg = _build_issue_to_pg(lane.get("par_groups", {}))
    band_before = _build_band_before(lane.get("bands", []))

    flat_rows: list[dict] = []
    for item in order:
        band_row, row = _flatten_order_item(item, overrides, issue_to_pg, band_before)
        if band_row is not None:
            flat_rows.append(band_row)
        flat_rows.append(row)
        if label_drift_check and row.get("repo"):
            _check_drift(row["repo"], row["issue"], code, gh_issues)

    return {
        "code": code,
        "name": lane["name"],
        "color": lane["color"],
        "epic": lane.get("epic"),
        "flat_rows": flat_rows,
    }


# ---------------------------------------------------------------------------
# Lane HTML rendering
# ---------------------------------------------------------------------------


def _close_par(row_htmls: list[str], current_par: str | None) -> None:
    """Close an open par-group div if one is active."""
    if current_par is not None:
        row_htmls.append("    </div>")


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
    extra_bb = extra_blocked_by_map.get(str(n), [])
    extra_bl = extra_blocking_map.get(str(n), [])
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


def render_flat_lane(ctx: FlatLaneContext) -> str:
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


# ---------------------------------------------------------------------------
# Untriaged section
# ---------------------------------------------------------------------------


def render_untriaged(
    untriaged: list[tuple[str, int]],
    gh_issues: dict,
    primary_repo: str = "",
) -> str:
    if not untriaged:
        return ""
    cards = []
    for repo, n in untriaged:
        gh_entry = gh_issues.get(format_key(repo, n))
        title = escape(gh_entry["title"]) if gh_entry else f"#{n}"
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
    title_rules: list[dict],
    primary_repo: str = "",
) -> str:
    cards = []
    for repo, n in order:
        ovr_key = format_key(repo, n) if repo else str(n)
        ovr = overrides.get(ovr_key, {})
        gh_entry = gh_issues.get(format_key(repo, n) if repo else str(n))
        status = derive_status(ovr, gh_entry, [], gh_issues, repo)
        size = ovr.get("size", "")
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
    items = []
    for cd in cross_deps:
        kind = escape(cd["kind"])
        text = escape(cd["text"])
        items.append(f'    <li><span class="kind">{kind}</span>{text}</li>')
    return "\n".join(items)


# ---------------------------------------------------------------------------
# CSS (parameterized — lane color vars injected from layout)
# ---------------------------------------------------------------------------

CSS_BASE = """\
:root {
  --slot-h: 56px;
}
:root, [data-theme="dark"] {
  --bg:          #0d1117;
  --bg-panel:    #13191f;
  --bg-card:     #161b22;
  --bg-cell:     #0f141a;
  --bg-epic:     rgba(232,93,4,0.05);
  --border:      #21262d;
  --border-hi:   #30363d;
  --text:        #fafafa;
  --text-muted:  #8b93a1;
  --text-dim:    #6b7280;
  --accent:      #e85d04;
  --accent-hi:   #f97316;
  --lane-a:      #22c55e;
  --lane-b:      #3b82f6;
  --lane-c1:     #a855f7;
  --lane-c2:     #d946ef;
  --lane-c3:     #ec4899;
  --lane-d:      #06b6d4;
  --lane-e:      #eab308;
  --lane-f:      #10b981;
  --lane-g:      #f97316;
  --lane-h:      #f59e0b;
  --lane-i:      #14b8a6;
  --lane-indep:  #94a3b8;
  --status-done: #16a34a;
  --status-ready:#facc15;
  --status-blocked:#fb923c;
  --status-defer:#64748b;
  --arrow:       #4b5563;
  --dep-in:      #94a3b8;
  --dep-out:     #94a3b8;
  --dep-ext:     #f97316;
{extra_vars}
[data-theme="light"] {
  --bg:          #fafaf9;
  --bg-panel:    #ffffff;
  --bg-card:     #f4f4f0;
  --bg-cell:     #ffffff;
  --bg-epic:     rgba(232,93,4,0.04);
  --border:      #d6d3d1;
  --border-hi:   #a8a29e;
  --text:        #1c1917;
  --text-muted:  #57534e;
  --text-dim:    #78716c;
  --accent:      #c2410c;
  --accent-hi:   #e85d04;
  --arrow:       #a8a29e;
  --dep-in:      #6b7280;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: 'Inter', system-ui, sans-serif;
  padding: 24px;
  min-height: 100vh;
  font-size: 12px;
  line-height: 1.4;
}
header {
  max-width: 1750px;
  margin: 0 auto 18px;
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 24px;
  flex-wrap: wrap;
}
h1 {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.02em;
}
h1 span { color: var(--accent); }
.subtitle {
  color: var(--text-muted);
  font-size: 12px;
  margin-top: 4px;
}
.legend {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  font-size: 10px;
  color: var(--text-muted);
}
.legend .pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 7px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 4px;
  font-family: 'JetBrains Mono', monospace;
}
.dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}
.dot.done    { background: var(--status-done); }
.dot.ready   { background: var(--status-ready); }
.dot.blocked { background: var(--status-blocked); }
.dot.defer   { background: var(--status-defer); }

/* Flex-column lanes layout */
.lanes {
  max-width: 1750px;
  margin: 0 auto;
  display: flex;
  gap: 8px;
  align-items: stretch;
  background: var(--bg-panel);
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
}
.lane {
  flex: 1 1 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.lane-head {
  padding: 8px 6px;
  text-align: center;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-top: 3px solid;
  border-radius: 5px 5px 0 0;
}
.lane-head .code {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
  font-size: 13px;
}
.lane-head .name {
  display: block;
  font-size: 10px;
  color: var(--text-muted);
  margin-top: 2px;
  font-weight: 500;
}
.lane[data-lane="a"]     > .lane-head { border-top-color: var(--lane-a); }
.lane[data-lane="b"]     > .lane-head { border-top-color: var(--lane-b); }
.lane[data-lane="c1"]    > .lane-head { border-top-color: var(--lane-c1); }
.lane[data-lane="c2"]    > .lane-head { border-top-color: var(--lane-c2); }
.lane[data-lane="c3"]    > .lane-head { border-top-color: var(--lane-c3); }
.lane[data-lane="d"]     > .lane-head { border-top-color: var(--lane-d); }
.lane[data-lane="e"]     > .lane-head { border-top-color: var(--lane-e); }
.lane[data-lane="f"]     > .lane-head { border-top-color: var(--lane-f); }
.lane[data-lane="g"]     > .lane-head { border-top-color: var(--lane-g); }
.lane[data-lane="h"]     > .lane-head { border-top-color: var(--lane-h); }
.lane[data-lane="i"]     > .lane-head { border-top-color: var(--lane-i); }
.lane[data-lane="indep"] > .lane-head { border-top-color: var(--lane-indep); }
{extra_selectors}
/* Epic wrap */
.epic-wrap {
  background: var(--bg-epic);
  border: 1px solid var(--accent);
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 0 5px 6px;
  flex: 1 1 auto;
}
.epic-wrap.defer { opacity: 0.7; }
.epic-banner {
  background: var(--accent);
  color: #fff;
  padding: 3px 8px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  font-weight: 700;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 6px;
  border-radius: 5px 5px 0 0;
  overflow: hidden;
  white-space: nowrap;
  margin: 0 -5px;
}
.epic-banner .tag {
  font-size: 8px;
  opacity: 0.85;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  flex-shrink: 0;
}

/* Section band (milestone label) */
.ms-band {
  display: flex;
  align-items: center;
  height: 18px;
  line-height: 18px;
  font-size: 9px;
  font-family: 'JetBrains Mono', monospace;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.1em;
}
.ms-band::after {
  content: "";
  flex: 1;
  height: 1px;
  background: var(--border);
  margin-left: 6px;
}

/* Spacer — invisible slot placeholder */
.spacer {
  height: var(--slot-h);
}

/* Parallel siblings group */
.par {
  display: flex;
  flex-direction: column;
  gap: 4px;
  border-left: 2px solid var(--text-dim);
  padding-left: 6px;
  margin-left: 2px;
}

/* Card — fixed height for slot uniformity */
.card {
  background: var(--bg-cell);
  border: 1px solid var(--border);
  border-left: 3px solid var(--status-defer);
  border-radius: 4px;
  padding: 5px 7px;
  font-size: 11px;
  min-width: 0;
  height: var(--slot-h);
  min-height: var(--slot-h);
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 0 0 auto;
}
.card.done     { border-left-color: var(--status-done); opacity: 0.55; }
.card.ready    { border-left-color: var(--status-ready); }
.card.blocked  { border-left-color: var(--status-blocked); }
.card.defer    { border-left-color: var(--status-defer); opacity: 0.65; }

.card .top {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 4px;
}
.card .num {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
  font-size: 11px;
}
.card.done .num::after { content: " \u2713"; color: var(--status-done); }
.card .size {
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px;
  color: var(--text-dim);
}
.card .title {
  font-size: 10px;
  color: var(--text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card .deps {
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px;
  display: flex;
  flex-wrap: nowrap;
  gap: 4px 6px;
  margin-top: 2px;
  color: var(--dep-in);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card .deps .ext { color: var(--dep-ext); font-weight: 600; }
.card .deps .none { color: var(--text-dim); opacity: 0.6; }

.rbadge {
  display: inline-block;
  padding: 1px 6px;
  margin-left: 6px;
  font-size: 0.7em;
  border-radius: 4px;
  background: var(--border-hi, #30363d);
  color: var(--text-dim, #6b7280);
  font-weight: 500;
}

.card--missing {
  border: 2px dashed var(--status-blocked, #fb923c);
  background: rgba(251,146,60,0.06);
  color: var(--text-muted, #8b93a1);
  padding: 8px;
  font-style: italic;
}

.standalone {
  max-width: 1750px;
  margin: 10px auto 0;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
}
.standalone-head {
  padding: 8px 14px;
  background: var(--bg-card);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: baseline;
  gap: 10px;
  font-family: 'JetBrains Mono', monospace;
}
.standalone-head .label {
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--text);
}
.standalone-head .hint {
  font-size: 10px;
  color: var(--text-muted);
}
.standalone-grid {
  padding: 10px;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 6px;
}

.cross-deps {
  max-width: 1750px;
  margin: 18px auto 0;
  padding: 14px 18px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 10px;
}
.cross-deps h3 {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}
.cross-deps ul {
  list-style: none;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 4px 18px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: var(--text-muted);
}
.cross-deps li::before { content: "\u21b3 "; color: var(--accent); }
.cross-deps .kind {
  display: inline-block;
  padding: 1px 5px;
  border-radius: 2px;
  font-size: 9px;
  background: rgba(232,93,4,0.1);
  color: var(--accent);
  margin-right: 4px;
}

.theme-btn {
  position: fixed;
  top: 14px;
  right: 14px;
  padding: 6px 10px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  color: var(--text-muted);
  font-family: inherit;
  font-size: 11px;
  border-radius: 4px;
  cursor: pointer;
}

footer {
  max-width: 1750px;
  margin: 16px auto 0;
  text-align: center;
  font-size: 11px;
  color: var(--text-dim);
  font-family: 'JetBrains Mono', monospace;
}
footer a { color: var(--accent); text-decoration: none; }
"""

THEME_SCRIPT = """\
<script>
(function() {
  const btn = document.getElementById('theme-toggle');
  const KEY = 'lyra-v2-graph-theme';
  const saved = localStorage.getItem(KEY);
  if (saved) document.documentElement.setAttribute('data-theme', saved);
  function update() {
    const cur = document.documentElement.getAttribute('data-theme');
    btn.textContent = cur === 'dark' ? '\u25d1 light' : '\u25d0 dark';
  }
  update();
  btn.addEventListener('click', () => {
    const cur = document.documentElement.getAttribute('data-theme');
    const next = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem(KEY, next);
    update();
  });
})();
</script>"""


def build_html(layout: dict, gh_issues: dict) -> str:
    meta = layout["meta"]
    lanes = layout["lanes"]
    overrides = layout.get("overrides", {})
    extra_deps = layout.get("extra_deps", {})
    standalone = layout.get("standalone", {})
    cross_deps = layout.get("cross_deps", [])
    title_rules: list[dict] = layout.get("title_rules", [])

    _repos_list: list[str] = meta.get("repos", [])
    primary_repo: str = _repos_list[0] if _repos_list else meta.get("repo", "")

    # Build lane_of map: (repo, issue) → lane_code
    lane_of: dict[tuple[str, int], str] = {}
    for lane in lanes:
        for item in lane.get("order", []):
            if isinstance(item, dict):
                lane_of[(item["repo"], item["issue"])] = lane["code"]
            else:
                lane_of[(primary_repo, int(item))] = lane["code"]

    flat_lanes = [flatten_lane(lane, overrides, True, gh_issues) for lane in lanes]
    flat_lanes = inject_spacers(flat_lanes)

    # Untriaged detection — use (repo, issue) set
    all_ordered: set[tuple[str, int]] = set(lane_of.keys())
    raw_standalone_order = standalone.get("order", [])
    standalone_order: list[tuple[str, int]] = []
    for item in raw_standalone_order:
        if isinstance(item, dict):
            standalone_order.append((item["repo"], item["issue"]))
        else:
            standalone_order.append((primary_repo, int(item)))
    all_ordered.update(standalone_order)
    epic_issues: set[tuple[str, int]] = set()
    for lane in layout["lanes"]:
        if lane.get("epic"):
            epic = lane["epic"]
            epic_repo = epic.get("repo", primary_repo)
            epic_issues.add((epic_repo, epic["issue"]))

    untriaged: list[tuple[str, int]] = sorted(
        (
            (entry.get("repo", primary_repo), entry["number"])
            for _, entry in gh_issues.items()
            if entry
            and entry.get("lane_label") is not None
            and not entry.get("hidden")
            and (entry.get("repo", primary_repo), entry["number"]) not in all_ordered
            and (entry.get("repo", primary_repo), entry["number"]) not in epic_issues
            and not entry.get("standalone")
            and "number" in entry
        ),
        key=lambda x: (x[0], x[1]),
    )

    lanes_html = "\n\n".join(
        render_flat_lane(
            FlatLaneContext(
                fl=fl,
                lane_of=lane_of,
                gh_issues=gh_issues,
                overrides=overrides,
                extra_deps=extra_deps,
                title_rules=title_rules,
                primary_repo=primary_repo,
            )
        )
        for fl in flat_lanes
    )
    untriaged_html = render_untriaged(untriaged, gh_issues, primary_repo)
    standalone_html = render_standalone(
        standalone_order, gh_issues, overrides, title_rules, primary_repo
    )
    cross_html = render_cross_deps(cross_deps)

    # CSS — str.replace avoids .format() colliding with raw CSS braces
    css = CSS_BASE.replace("{extra_vars}", "").replace("{extra_selectors}", "")

    title = escape(meta["title"])
    date = escape(meta["date"])
    issue = meta.get("issue", "")
    category = escape(meta.get("category", ""))
    cat_label = escape(meta.get("cat_label", ""))
    color = escape(meta.get("color", ""))
    repo = meta.get("repo", primary_repo)
    repo_url = f"https://github.com/{repo}/issues" if repo else "#"
    lane_count = len(lanes)

    subtitle = (
        f"{lane_count} lanes \u00b7 1 card per row \u00b7"
        f" <strong>\u2190</strong> = blocked by"
        f" \u00b7 <strong>\u2192</strong> = unblocks"
        f' \u00b7 <span class="ext"'
        f' style="color:var(--dep-ext);font-family:monospace;font-weight:600;">'
        f"X:#N</span> = cross-lane"
    )
    footer_line = (
        f"Lyra v2 plan \u00b7 refreshed {date}"
        f' \u00b7 <a href="{repo_url}">{repo_url}</a>'
        f' \u00b7 <a href="nats-arch-roadmap.html">NATS arch roadmap</a>'
    )
    standalone_comment = (
        "<!-- Dedicated row: standalone items (no chain, no deps, ship anytime) -->"
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
        "B:#609</span> = cross-lane ref</span>"
    )

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{title}">
<meta property="og:type" content="article">
<meta property="og:url" content="https://forge.roxabi.dev/lyra/visuals/lyra-v2-dependency-graph.html">
<meta property="og:image" content="https://forge.roxabi.dev/og-image.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:site_name" content="Roxabi Forge">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{title}">
<meta name="twitter:image" content="https://forge.roxabi.dev/og-image.png">
<!-- diagram-meta:start -->
<meta name="diagram:title"     content="{title}">
<meta name="diagram:date"      content="{date}">
<meta name="diagram:category"  content="{category}">
<meta name="diagram:cat-label" content="{cat_label}">
<meta name="diagram:color"     content="{color}">
<meta name="diagram:badges"    content="latest">
<meta name="diagram:issue"     content="{issue}">
<!-- diagram-meta:end -->
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="{fonts_url}" rel="stylesheet">
<style>
{css}
</style>
</head>
<body>

<header>
  <div>
    <h1>Lyra <span>v2</span> \u2014 Dependency Graph</h1>
    <div class="subtitle">{subtitle}</div>
  </div>
  <div class="legend">
    <span class="pill"><span class="dot done"></span> done</span>
    <span class="pill"><span class="dot ready"></span> ready</span>
    <span class="pill"><span class="dot blocked"></span> blocked</span>
    <span class="pill"><span class="dot defer"></span> deferred</span>
    {legend_epic}
    {legend_xref}
  </div>
</header>

<div class="lanes">

{lanes_html}

</div>

{untriaged_html}{standalone_comment}
<div class="standalone">
  <div class="standalone-head">
    <span class="label">Standalone</span>
    <span class="hint">\u2014 no chain, no deps, ship anytime</span>
  </div>
  <div class="standalone-grid">
{standalone_html}
  </div>
</div>

<div class="cross-deps">
  <h3>Cross-lane critical path (to M3 observability live)</h3>
  <ul>
{cross_html}
  </ul>
</div>

<button class="theme-btn" id="theme-toggle">&#9681; light</button>

<footer>
  <p>{footer_line}</p>
</footer>

{THEME_SCRIPT}

</body>
</html>"""


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
