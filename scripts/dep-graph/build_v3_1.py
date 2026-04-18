"""Build v3.1 lane-swim dependency graph.

Differences from v3:
  - Cards are topo-sorted within each cell (ready first, then by depth).
  - Each card shows a status dot (ready / blocked / done).
  - Each card shows dep chips: ← #N (blockers) and → #N (unblocks).
  - Hover on a card dims all others and highlights the full dep chain
    across the grid (transitive blockers + unblocks).
"""

from __future__ import annotations

import html
import json
from collections import defaultdict
from pathlib import Path

FORGE = Path.home() / ".roxabi/forge/lyra/visuals"
LAYOUT_PATH = FORGE / "lyra-v2-dependency-graph.layout.json"
CACHE_PATH = FORGE / "lyra-v2-dependency-graph.gh.json"
OUT_PATH = FORGE / "lyra-v2-dependency-graph-v3.1.html"
TASKS_PATH = FORGE / "lyra-v2-dependency-graph-v3.1.tasks.json"
FGRAPH_BASE = Path.home() / ".roxabi/forge/_shared/fgraph-base.css"

COLUMN_GROUPS = [
    ("NATS",       "a1",   ["a1", "a2", "a3"]),
    ("CONTAINER",  "b",    ["b"]),
    ("LLM",        "c1",   ["c1", "c2", "c3"]),
    ("OBS",        "d",    ["d"]),
    ("HUB",        "e",    ["e"]),
    ("PLUGINS",    "f",    ["f"]),
    ("VOICE",      "g",    ["g"]),
    ("DEPLOY",     "h",    ["h"]),
    ("VAULT",      "i",    ["i"]),
]

MILESTONES = [
    ("M0  NATS hardening",                "M0", "NATS hardening"),
    ("M1  NATS maturity  containerize",   "M1", "NATS maturity / containerize"),
    ("M2  LLM stack modernization",       "M2", "LLM stack modernization"),
    ("M3  Observability",                 "M3", "Observability"),
    ("M4  Hub statelessness",             "M4", "Hub statelessness"),
    ("M5  Plugin layer",                  "M5", "Plugin layer"),
]


def _ref_key(ref: dict) -> str:
    return f"{ref['repo']}#{ref['issue']}"


def _compute_depth(issues: dict[str, dict]) -> dict[str, int]:
    """Topological execution depth: counts ALL blockers (open + closed).

    Done tasks sit at their natural position in the chain, before the tasks
    they unblock. 0 = no blockers at all; N = 1 + max(depth(blocker)).
    """
    depth: dict[str, int] = {}

    def resolve(key: str, stack: set[str]) -> int:
        if key in depth:
            return depth[key]
        if key in stack:  # cycle guard
            return 0
        iss = issues.get(key)
        if not iss:
            return 0
        blockers = [_ref_key(b) for b in iss.get("blocked_by", [])]
        if not blockers:
            d = 0
        else:
            stack = stack | {key}
            d = 1 + max(
                (resolve(b, stack) for b in blockers if b in issues),
                default=0,
            )
        depth[key] = d
        return d

    for k in issues:
        resolve(k, set())
    return depth


def _status(iss: dict, issues: dict[str, dict]) -> str:
    if iss["state"] == "closed":
        return "done"
    open_blockers = [
        b for b in iss.get("blocked_by", [])
        if issues.get(_ref_key(b), {}).get("state") != "closed"
    ]
    return "blocked" if open_blockers else "ready"


def _dep_chip(refs: list[dict], arrow: str, issues: dict[str, dict]) -> str:
    """Compact chip listing referenced issue numbers."""
    if not refs:
        return ""
    pills = []
    for r in refs:
        k = _ref_key(r)
        target = issues.get(k)
        num = r["issue"]
        is_external = r["repo"] != "Roxabi/lyra"
        closed = target and target["state"] == "closed"
        cls = "dep-ref"
        if closed:
            cls += " closed"
        if is_external:
            cls += " ext"
        prefix = ""
        if is_external:
            prefix = r["repo"].split("/")[-1][:1].upper() + ":"
        pills.append(
            f'<span class="{cls}" data-link="{k}">{prefix}#{num}</span>'
        )
    return (
        f'<span class="dep-chip dep-{"in" if arrow == "←" else "out"}">'
        f'<span class="dep-arrow">{arrow}</span>'
        f'{"".join(pills)}</span>'
    )


def _card(
    iss: dict, epic_tone: str, issues: dict[str, dict], status: str, depth: int
) -> str:
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
    blocked_keys = ",".join(_ref_key(b) for b in blocked_by)
    blocking_keys = ",".join(_ref_key(b) for b in blocking)
    dep_chips = _dep_chip(blocked_by, "←", issues) + _dep_chip(
        blocking, "→", issues
    )
    dep_row = (
        f'<div class="dep-row">{dep_chips}</div>' if dep_chips else ""
    )
    return (
        f'<a class="issue-card {status}" data-tone="{epic_tone}" '
        f'data-iss="{key}" data-depth="{depth}" '
        f'data-blockedby="{blocked_keys}" data-blocking="{blocking_keys}" '
        f'href="{url}" target="_blank" rel="noopener" title="{title}">'
        f'<div class="card-head">'
        f'<span class="card-dot" aria-hidden="true"></span>'
        f'<span class="card-num">#{num}</span>'
        f'<span class="card-title">{short}</span>'
        f'{size_pill}'
        f'</div>'
        f'{dep_row}'
        f'</a>'
    )


def _render_cell(
    issues_here_by_epic: dict[str, list[dict]],
    lane_codes: list[str],
    lane_meta: dict,
    issues: dict[str, dict],
    depth_by_key: dict[str, int],
) -> str:
    groups_html = []
    for code in lane_codes:
        issues_here = issues_here_by_epic.get(code, [])
        if not issues_here:
            continue
        meta = lane_meta[code]
        tag = html.escape(meta.get("epic", {}).get("tag", "") or "")
        epic_num = meta.get("epic", {}).get("issue", "")
        name = html.escape(meta["name"])
        epic_url = (
            f"https://github.com/Roxabi/lyra/issues/{epic_num}" if epic_num else "#"
        )
        header = (
            f'<a class="epic-header" data-tone="{meta["color"]}" '
            f'data-epic="{code}" href="{epic_url}" '
            f'target="_blank" rel="noopener" '
            f'title="Open epic #{epic_num} on GitHub">'
            f'<span class="epic-code">{code}</span>'
            f'<span class="epic-name">{name}</span>'
            f'{f"<span class=epic-tag>{tag}</span>" if tag else ""}'
            f'{f"<span class=epic-ref>#{epic_num}</span>" if epic_num else ""}'
            f'</a>'
        )
        # Execution order: topo depth first (counts done blockers too),
        # then issue number. Done tasks sit upstream of what they unblocked.
        issues_here.sort(
            key=lambda i: (
                depth_by_key.get(f"{i['repo']}#{i['number']}", 0),
                i["number"],
            )
        )
        cards_html = []
        for iss in issues_here:
            st = _status(iss, issues)
            d = depth_by_key.get(f"{iss['repo']}#{iss['number']}", 0)
            cards_html.append(_card(iss, code, issues, st, d))
        groups_html.append(
            f'<div class="epic-group" data-epic="{code}">'
            f'{header}'
            f'<div class="epic-cards">{"".join(cards_html)}</div>'
            f'</div>'
        )
    return (
        "".join(groups_html) if groups_html else '<div class="cell-empty">·</div>'
    )


FONT_LINKS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link rel="stylesheet" '
    'href="https://fonts.googleapis.com/css2?'
    "family=Inter:wght@400;500;600;700"
    "&family=JetBrains+Mono:wght@400;500;600;700"
    "&family=Outfit:wght@500;600;700"
    '&display=swap">'
)


def _epic_keys(layout: dict) -> set[str]:
    primary_repo = layout["meta"]["repos"][0]
    keys: set[str] = set()
    for lane in layout["lanes"]:
        epic = lane.get("epic", {})
        if epic.get("issue"):
            keys.add(f"{primary_repo}#{epic['issue']}")
    return keys


def _build_col_headers(lane_meta: dict) -> list[str]:
    headers = []
    for col_label, col_tone, codes in COLUMN_GROUPS:
        epics = []
        for c in codes:
            m = lane_meta[c]
            epics.append(
                f'<span class="col-epic" data-tone="{c}">'
                f'{c} · {html.escape(m["name"])}'
                f"</span>"
            )
        headers.append(
            f'<div class="col-header">'
            f'<div class="col-label" data-tone="{col_tone}">{col_label}</div>'
            f'<div class="col-epics">{" ".join(epics)}</div>'
            f"</div>"
        )
    return headers


def _build_grid_rows(
    matrix: dict[tuple[str, str], list[dict]],
    lane_meta: dict,
    issues: dict[str, dict],
    depth_by_key: dict[str, int],
) -> tuple[list[str], dict[str, int]]:
    """Render milestone rows. Returns (row_html, status_counts)."""
    rows = []
    counts = {"ready": 0, "blocked": 0, "done": 0}
    for ms_key, ms_code, ms_name in MILESTONES:
        cells = [
            f'<div class="row-header">'
            f'<div class="ms-code">{ms_code}</div>'
            f'<div class="ms-name">{html.escape(ms_name)}</div>'
            f"</div>"
        ]
        for col_label, _, codes in COLUMN_GROUPS:
            by_epic: dict[str, list[dict]] = defaultdict(list)
            for code in codes:
                for iss in matrix.get((ms_key, code), []):
                    counts[_status(iss, issues)] += 1
                    by_epic[code].append(iss)
            cells.append(
                f'<div class="grid-cell" data-col="{col_label}" data-ms="{ms_code}">'
                f"{_render_cell(by_epic, codes, lane_meta, issues, depth_by_key)}"
                f"</div>"
            )
        rows.append(
            f'<div class="grid-row" data-ms="{ms_code}">{"".join(cells)}</div>'
        )
    return rows, counts


def build_tasks_json(layout: dict, gh: dict) -> list[dict]:
    """Emit a flat task list mirroring the HTML grid semantics.

    One entry per non-epic issue that has both milestone and lane.
    Consumers filter via `.status == "ready"` (or blocked / done).
    """
    issues = gh.get("issues", {})
    lane_meta = {lane["code"]: lane for lane in layout["lanes"]}
    epic_keys = _epic_keys(layout)

    ms_short = {k: short for k, short, _ in MILESTONES}
    ms_name = {k: name for k, _, name in MILESTONES}
    col_of_lane = {
        code: label
        for label, _, codes in COLUMN_GROUPS
        for code in codes
    }

    depth_by_key = _compute_depth(issues)

    tasks: list[dict] = []
    for key, iss in issues.items():
        ms = iss.get("milestone")
        lane = iss.get("lane_label")
        if not ms or not lane:
            continue
        if key in epic_keys:
            continue
        lmeta = lane_meta.get(lane, {})
        tasks.append(
            {
                "key": key,
                "repo": iss["repo"],
                "num": iss["number"],
                "title": iss["title"],
                "url": f"https://github.com/{iss['repo']}/issues/{iss['number']}",
                "state": iss["state"],
                "status": _status(iss, issues),
                "milestone": ms_short.get(ms, ms),
                "milestone_name": ms_name.get(ms, ms),
                "lane": lane,
                "lane_name": lmeta.get("name", ""),
                "column": col_of_lane.get(lane, ""),
                "epic": lmeta.get("epic", {}).get("issue"),
                "size": iss.get("size") or None,
                "depth": depth_by_key.get(key, 0),
                "blockers": iss.get("blocked_by", []),
                "unblocks": iss.get("blocking", []),
                "labels": iss.get("labels", []),
            }
        )
    tasks.sort(
        key=lambda t: (
            t["milestone"],
            t["column"],
            t["depth"],
            t["num"],
        )
    )
    return tasks


def build(layout: dict, gh: dict) -> str:
    issues = gh.get("issues", {})
    lane_meta = {lane["code"]: lane for lane in layout["lanes"]}
    epic_keys = _epic_keys(layout)

    matrix: dict[tuple[str, str], list[dict]] = defaultdict(list)
    total = 0
    for key, iss in issues.items():
        ms = iss.get("milestone")
        lane = iss.get("lane_label")
        if not ms or not lane or key in epic_keys:
            continue
        matrix[(ms, lane)].append(iss)
        total += 1

    depth_by_key = _compute_depth(issues)
    col_headers = _build_col_headers(lane_meta)
    rows, counts = _build_grid_rows(matrix, lane_meta, issues, depth_by_key)

    fgraph_base = FGRAPH_BASE.read_text() if FGRAPH_BASE.exists() else ""
    meta = layout["meta"]
    title = html.escape(meta["title"] + " — v3.1 (sequence + deps)")
    date = html.escape(meta["date"])
    repo = meta["repos"][0]
    repo_url = f"https://github.com/{repo}/issues"
    subtitle = (
        f"{len(MILESTONES)} milestones × {len(COLUMN_GROUPS)} columns · "
        f"{counts['ready']} ready · {counts['blocked']} blocked · "
        f"{counts['done']} done · {total} total · "
        f"hover a card to trace its dep chain"
    )

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<!-- diagram-meta:start -->
<meta name="diagram:title"     content="{title}">
<meta name="diagram:date"      content="{date}">
<meta name="diagram:category"  content="plan">
<meta name="diagram:cat-label" content="Plan">
<meta name="diagram:color"     content="amber">
<meta name="diagram:badges"    content="latest">
<meta name="diagram:issue"     content="{html.escape(str(meta['issue']['issue']))}">
<!-- diagram-meta:end -->
<title>{title}</title>
{FONT_LINKS}
<style>
:root, [data-theme="dark"] {{
  --bg:         #0d1117;
  --bg-panel:   #13191f;
  --bg-card:    #161b22;
  --bg-cell:    #0f141a;
  --border:     #21262d;
  --border-hi:  #30363d;
  --text:       #fafafa;
  --text-muted: #8b93a1;
  --text-dim:   #6b7280;
  --accent:     #e85d04;
  --accent-dim: rgba(232,93,4,0.12);
  --accent-glow:rgba(232,93,4,0.4);
  --teal:       #06b6d4;
  --amber:      #f59e0b;
  --green:      #10b981;
  --cyan:       #22d3ee;
  --purple:     #c084fc;
  --red:        #ef4444;
  --status-ready:   #22c55e;
  --status-blocked: #f97316;
  --status-done:    #475569;
  --lane-a1: #22c55e;
  --lane-a2: #6366f1;
  --lane-a3: #8b5cf6;
  --lane-b:  #3b82f6;
  --lane-c1: #a855f7;
  --lane-c2: #d946ef;
  --lane-c3: #ec4899;
  --lane-d:  #06b6d4;
  --lane-e:  #eab308;
  --lane-f:  #10b981;
  --lane-g:  #f97316;
  --lane-h:  #f59e0b;
  --lane-i:  #14b8a6;
}}

{fgraph_base}

* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  background: var(--bg);
  color: var(--text);
  font-family: 'Inter', system-ui, sans-serif;
  padding: 24px;
  min-height: 100vh;
  font-size: 12px;
  line-height: 1.4;
}}
header.page-header {{
  max-width: 100%;
  margin: 0 auto 20px;
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 24px;
  flex-wrap: wrap;
}}
h1 {{
  font-family: 'Outfit', sans-serif;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.02em;
}}
h1 .accent {{ color: var(--accent); }}
.subtitle {{
  color: var(--text-muted);
  font-size: 12px;
  margin-top: 6px;
  font-family: 'JetBrains Mono', monospace;
}}

/* toolbar */
.toolbar {{
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 10px 0;
  flex-wrap: wrap;
}}
.toolbar label {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: var(--text-muted);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  user-select: none;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-card);
}}
.toolbar label:hover {{ border-color: var(--border-hi); color: var(--text); }}
.toolbar input[type="checkbox"] {{ accent-color: var(--accent); }}
.toolbar .legend-pills {{
  display: inline-flex;
  gap: 8px;
  align-items: center;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: var(--text-muted);
  margin-left: auto;
}}
.toolbar .lp {{
  display: inline-flex;
  gap: 5px;
  align-items: center;
}}
.toolbar .lp .lpd {{
  width: 8px; height: 8px; border-radius: 50%;
  box-shadow: 0 0 6px currentColor;
}}
.lp.ready    {{ color: var(--status-ready); }}
.lp.blocked  {{ color: var(--status-blocked); }}
.lp.done     {{ color: var(--status-done); }}

/* grid */
.lane-swim-grid {{
  --row-header-w: 140px;
  --col-min-w:    190px;
  display: grid;
  grid-template-columns:
    var(--row-header-w)
    repeat({len(COLUMN_GROUPS)}, minmax(var(--col-min-w), 1fr));
  gap: 0;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}}

.grid-head {{ display: contents; }}
.grid-head > .spacer {{
  background: var(--bg-panel);
  border-bottom: 1px solid var(--border-hi);
  border-right: 1px dashed var(--border);
}}
.col-header {{
  padding: 12px 10px;
  background: linear-gradient(180deg, rgba(255,255,255,0.03), transparent);
  border-bottom: 1px solid var(--border-hi);
  border-right: 1px dashed var(--border);
}}
.col-header:last-child {{ border-right: none; }}
.col-label {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  margin-bottom: 6px;
}}
.col-label[data-tone="a1"] {{ color: var(--lane-a1); }}
.col-label[data-tone="b"]  {{ color: var(--lane-b); }}
.col-label[data-tone="c1"] {{ color: var(--lane-c1); }}
.col-label[data-tone="d"]  {{ color: var(--lane-d); }}
.col-label[data-tone="e"]  {{ color: var(--lane-e); }}
.col-label[data-tone="f"]  {{ color: var(--lane-f); }}
.col-label[data-tone="g"]  {{ color: var(--lane-g); }}
.col-label[data-tone="h"]  {{ color: var(--lane-h); }}
.col-label[data-tone="i"]  {{ color: var(--lane-i); }}
.col-epics {{ display: flex; flex-direction: column; gap: 2px; }}
.col-epic {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px;
  color: var(--text-muted);
  opacity: 0.82;
}}

.grid-row {{ display: contents; }}
.row-header {{
  padding: 14px 10px;
  background: var(--bg-panel);
  border-top: 1px solid var(--border);
  border-right: 1px dashed var(--border);
  display: flex;
  flex-direction: column;
  gap: 3px;
}}
.ms-code {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  font-weight: 700;
  color: var(--accent);
}}
.ms-name {{
  font-size: 11px;
  color: var(--text-muted);
  line-height: 1.3;
}}

.grid-cell {{
  padding: 8px;
  border-top: 1px solid var(--border);
  border-right: 1px dashed var(--border);
  background: var(--bg-cell);
  min-height: 60px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}}
.grid-cell:last-child {{ border-right: none; }}
.cell-empty {{
  color: var(--text-dim);
  font-family: 'JetBrains Mono', monospace;
  font-size: 14px;
  text-align: center;
  opacity: 0.4;
  padding-top: 18px;
}}

/* epic-group (revealed when .group-epic) */
.epic-group {{ display: flex; flex-direction: column; gap: 4px; }}
.epic-header {{
  display: none;
  align-items: baseline;
  gap: 5px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px;
  padding: 3px 4px 4px;
  border-bottom: 1px dashed var(--border);
  margin-bottom: 2px;
  text-decoration: none;
  border-radius: 3px;
  transition: background 0.15s;
  cursor: pointer;
}}
body.group-epic .epic-header {{ display: flex; }}
.epic-header:hover {{
  background: rgba(255,255,255,0.04);
  border-bottom-style: solid;
}}
.epic-header .epic-code {{
  padding: 1px 5px;
  border-radius: 3px;
  background: var(--bg-card);
  border: 1px solid currentColor;
  font-weight: 700;
  letter-spacing: 0.06em;
}}
.epic-header .epic-name {{
  color: var(--text-muted);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}}
.epic-header .epic-tag {{ color: var(--accent); font-weight: 600; }}
.epic-header .epic-ref {{ color: var(--text-dim); }}
.epic-header[data-tone="a1"] {{ color: var(--lane-a1); }}
.epic-header[data-tone="a2"] {{ color: var(--lane-a2); }}
.epic-header[data-tone="a3"] {{ color: var(--lane-a3); }}
.epic-header[data-tone="b"]  {{ color: var(--lane-b); }}
.epic-header[data-tone="c1"] {{ color: var(--lane-c1); }}
.epic-header[data-tone="c2"] {{ color: var(--lane-c2); }}
.epic-header[data-tone="c3"] {{ color: var(--lane-c3); }}
.epic-header[data-tone="d"]  {{ color: var(--lane-d); }}
.epic-header[data-tone="e"]  {{ color: var(--lane-e); }}
.epic-header[data-tone="f"]  {{ color: var(--lane-f); }}
.epic-header[data-tone="g"]  {{ color: var(--lane-g); }}
.epic-header[data-tone="h"]  {{ color: var(--lane-h); }}
.epic-header[data-tone="i"]  {{ color: var(--lane-i); }}

.epic-cards {{ display: flex; flex-direction: column; gap: 4px; }}

/* issue card */
.issue-card {{
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 6px 7px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-left: 3px solid currentColor;
  border-radius: 4px;
  font-size: 11px;
  text-decoration: none;
  color: var(--text);
  transition: background 0.15s, border-color 0.15s, transform 0.12s, opacity 0.2s;
  position: relative;
}}
.issue-card:hover {{
  background: var(--bg-panel);
  border-color: currentColor;
  transform: translateX(1px);
}}
/* Status tint on the border-left */
.issue-card.ready   {{ }}
.issue-card.blocked {{ }}
.issue-card.done {{
  opacity: 0.45;
  background: transparent;
}}
.issue-card.done .card-title {{
  text-decoration: line-through;
  text-decoration-color: var(--status-done);
  color: var(--text-muted);
}}
.issue-card.done:hover {{ opacity: 0.85; }}

.card-head {{
  display: grid;
  grid-template-columns: auto auto 1fr auto;
  gap: 6px;
  align-items: center;
}}
.card-dot {{
  width: 7px; height: 7px; border-radius: 50%;
  flex-shrink: 0;
  background: var(--status-done);
  box-shadow: 0 0 4px transparent;
}}
.issue-card.ready .card-dot {{
  background: var(--status-ready);
  box-shadow: 0 0 5px var(--status-ready);
}}
.issue-card.blocked .card-dot {{
  background: var(--status-blocked);
  box-shadow: 0 0 5px var(--status-blocked);
}}
.issue-card.done .card-dot {{ background: var(--status-done); }}

.card-num {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 700;
  color: currentColor;
}}
.card-title {{
  color: var(--text);
  font-size: 10.5px;
  line-height: 1.25;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}}
.card-size {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 8.5px;
  font-weight: 600;
  padding: 1px 4px;
  border-radius: 3px;
  background: var(--bg);
  border: 1px solid var(--border);
  color: var(--text-muted);
  letter-spacing: 0.05em;
}}

/* dep row */
.dep-row {{
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding-left: 13px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px;
  line-height: 1;
}}
.dep-chip {{
  display: inline-flex;
  gap: 3px;
  align-items: center;
  color: var(--text-muted);
}}
.dep-chip.dep-in  .dep-arrow {{ color: var(--status-blocked); font-weight: 700; }}
.dep-chip.dep-out .dep-arrow {{ color: var(--teal);           font-weight: 700; }}
.dep-ref {{
  padding: 1px 3px;
  border-radius: 2px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-muted);
}}
.dep-ref.closed {{
  opacity: 0.5;
  text-decoration: line-through;
  text-decoration-color: var(--status-done);
}}
.dep-ref.ext {{
  color: var(--accent);
  border-color: var(--accent);
  background: var(--accent-dim);
}}

/* tone mapping for card border-left */
.issue-card[data-tone="a1"] {{ color: var(--lane-a1); }}
.issue-card[data-tone="a2"] {{ color: var(--lane-a2); }}
.issue-card[data-tone="a3"] {{ color: var(--lane-a3); }}
.issue-card[data-tone="b"]  {{ color: var(--lane-b); }}
.issue-card[data-tone="c1"] {{ color: var(--lane-c1); }}
.issue-card[data-tone="c2"] {{ color: var(--lane-c2); }}
.issue-card[data-tone="c3"] {{ color: var(--lane-c3); }}
.issue-card[data-tone="d"]  {{ color: var(--lane-d); }}
.issue-card[data-tone="e"]  {{ color: var(--lane-e); }}
.issue-card[data-tone="f"]  {{ color: var(--lane-f); }}
.issue-card[data-tone="g"]  {{ color: var(--lane-g); }}
.issue-card[data-tone="h"]  {{ color: var(--lane-h); }}
.issue-card[data-tone="i"]  {{ color: var(--lane-i); }}

/* hover-chain highlight */
body.hl-active .issue-card {{ opacity: 0.18; }}
body.hl-active .issue-card.hl-self {{
  opacity: 1;
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  z-index: 2;
}}
body.hl-active .issue-card.hl-upstream {{
  opacity: 1;
  border-left-width: 4px;
  box-shadow: inset 0 0 0 1px var(--status-blocked);
}}
body.hl-active .issue-card.hl-downstream {{
  opacity: 1;
  border-left-width: 4px;
  box-shadow: inset 0 0 0 1px var(--teal);
}}

/* dep-ref hover hint (not click) */
.dep-ref {{ cursor: default; }}

/* footer */
footer.page-footer {{
  margin-top: 18px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
  color: var(--text-muted);
  font-size: 11px;
  font-family: 'JetBrains Mono', monospace;
}}
footer.page-footer a {{ color: var(--accent); text-decoration: none; }}
footer.page-footer a:hover {{ text-decoration: underline; }}

/* filters */
body.hide-closed .issue-card.done {{ display: none; }}
body.hide-closed .epic-group:has(.epic-cards:empty) {{ display: none; }}
body.only-ready .issue-card.blocked,
body.only-ready .issue-card.done {{ display: none; }}
</style>
</head>
<body class="group-epic">

<header class="page-header">
  <div>
    <h1>
      Lyra <span class="accent">v2</span> —
      Dep Graph <span class="accent">v3.1</span>
    </h1>
    <div class="subtitle">{subtitle}</div>
  </div>
</header>

<div class="toolbar">
  <label><input type="checkbox" id="toggle-epic" checked> group by epic</label>
  <label><input type="checkbox" id="toggle-closed"> hide closed</label>
  <label><input type="checkbox" id="toggle-ready"> only ready</label>
  <span class="legend-pills">
    <span class="lp ready"><span class="lpd"></span>ready</span>
    <span class="lp blocked"><span class="lpd"></span>blocked</span>
    <span class="lp done"><span class="lpd"></span>done</span>
  </span>
</div>

<div class="lane-swim-grid">
  <div class="grid-head">
    <div class="spacer"></div>
    {"".join(col_headers)}
  </div>
  {"".join(rows)}
</div>

<footer class="page-footer">
  Lyra v2 plan · refreshed {date} ·
  <a href="{repo_url}">{html.escape(repo_url)}</a> ·
  <a href="lyra-v2-dependency-graph-v3.html">v3</a> ·
  <a href="lyra-v2-dependency-graph.html">v2 (legacy)</a>
</footer>

<script>
(function() {{
  const body = document.body;
  const cards = Array.from(document.querySelectorAll('.issue-card'));

  // Index cards by "repo#N"
  const byKey = new Map();
  cards.forEach(c => byKey.set(c.dataset.iss, c));

  // Adjacency (outgoing unblocks + incoming blockers)
  const blockers    = new Map(); // key → [keys]
  const unblocks    = new Map();
  cards.forEach(c => {{
    const k = c.dataset.iss;
    blockers.set(k, (c.dataset.blockedby || '').split(',').filter(Boolean));
    unblocks.set(k, (c.dataset.blocking  || '').split(',').filter(Boolean));
  }});

  function traverse(startKey, adj) {{
    const seen = new Set();
    const stack = [startKey];
    while (stack.length) {{
      const k = stack.pop();
      for (const n of adj.get(k) || []) {{
        if (!seen.has(n)) {{ seen.add(n); stack.push(n); }}
      }}
    }}
    return seen;
  }}

  function highlight(card) {{
    const k = card.dataset.iss;
    const up   = traverse(k, blockers);  // transitive blockers
    const down = traverse(k, unblocks);  // transitive unblocks
    body.classList.add('hl-active');
    card.classList.add('hl-self');
    up.forEach(n => byKey.get(n)?.classList.add('hl-upstream'));
    down.forEach(n => byKey.get(n)?.classList.add('hl-downstream'));
  }}

  function clearHighlight() {{
    body.classList.remove('hl-active');
    cards.forEach(c => c.classList.remove('hl-self', 'hl-upstream', 'hl-downstream'));
  }}

  cards.forEach(c => {{
    c.addEventListener('mouseenter', () => highlight(c));
    c.addEventListener('mouseleave', clearHighlight);
  }});

  // Hover on epic-header → highlight only the epic's own cards
  document.querySelectorAll('.epic-header').forEach(h => {{
    h.addEventListener('mouseenter', () => {{
      body.classList.add('hl-active');
      const group = h.parentElement;  // .epic-group
      group.querySelectorAll('.issue-card').forEach(c => c.classList.add('hl-self'));
    }});
    h.addEventListener('mouseleave', clearHighlight);
  }});

  // toolbar toggles
  document.getElementById('toggle-epic').addEventListener('change', e => {{
    body.classList.toggle('group-epic', e.target.checked);
  }});
  document.getElementById('toggle-closed').addEventListener('change', e => {{
    body.classList.toggle('hide-closed', e.target.checked);
  }});
  document.getElementById('toggle-ready').addEventListener('change', e => {{
    body.classList.toggle('only-ready', e.target.checked);
  }});
}})();
</script>

</body>
</html>
"""


def main() -> int:
    if not LAYOUT_PATH.exists():
        print(f"ERROR: {LAYOUT_PATH} not found")
        return 1
    if not CACHE_PATH.exists():
        print(f"ERROR: {CACHE_PATH} not found — run `make dep-graph fetch` first")
        return 1
    layout = json.loads(LAYOUT_PATH.read_text())
    gh = json.loads(CACHE_PATH.read_text())
    html_out = build(layout, gh)
    OUT_PATH.write_text(html_out)
    print(f"Written: {OUT_PATH} ({OUT_PATH.stat().st_size:,} bytes)")

    tasks = build_tasks_json(layout, gh)
    TASKS_PATH.write_text(json.dumps(tasks, indent=2, ensure_ascii=False))
    ready = sum(1 for t in tasks if t["status"] == "ready")
    blocked = sum(1 for t in tasks if t["status"] == "blocked")
    done = sum(1 for t in tasks if t["status"] == "done")
    print(
        f"Written: {TASKS_PATH} ({TASKS_PATH.stat().st_size:,} bytes) · "
        f"{len(tasks)} tasks ({ready} ready · {blocked} blocked · {done} done)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
