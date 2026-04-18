"""Build v3 lane-swim dependency graph.

Reads: layout.json + gh.json (same inputs as build.py).
Output: lyra-v2-dependency-graph-v3.html

Shape: columns = lane-groups (NATS, CONTAIN, LLM, ...), rows = milestones.
Each cell stacks compact issue cards. Toggle groups cards by epic sub-lane.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

FORGE = Path.home() / ".roxabi/forge/lyra/visuals"
LAYOUT_PATH = FORGE / "lyra-v2-dependency-graph.layout.json"
CACHE_PATH = FORGE / "lyra-v2-dependency-graph.gh.json"
OUT_PATH = FORGE / "lyra-v2-dependency-graph-v3.html"
FGRAPH_BASE = Path.home() / ".roxabi/forge/_shared/fgraph-base.css"

# Group 13 epic-lanes into 9 high-level columns
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

# Milestones in display order.
# Key = label as stored in gh.json, display = short row label.
MILESTONES = [
    ("M0  NATS hardening",                "M0", "NATS hardening"),
    ("M1  NATS maturity  containerize",   "M1", "NATS maturity / containerize"),
    ("M2  LLM stack modernization",       "M2", "LLM stack modernization"),
    ("M3  Observability",                 "M3", "Observability"),
    ("M4  Hub statelessness",             "M4", "Hub statelessness"),
    ("M5  Plugin layer",                  "M5", "Plugin layer"),
]


def _state_class(issue: dict) -> str:
    return "done" if issue["state"] == "closed" else "open"


def _card(issue: dict, epic_tone: str) -> str:
    num = issue["number"]
    title = html.escape(issue["title"])
    # truncate long titles for card compactness
    short = title if len(title) <= 48 else title[:47] + "…"
    repo = issue["repo"]
    url = f"https://github.com/{repo}/issues/{num}"
    state = _state_class(issue)
    size = issue.get("size") or ""
    size_pill = f'<span class="card-size">{html.escape(size)}</span>' if size else ""
    return (
        f'<a class="issue-card {state}" data-tone="{epic_tone}" href="{url}" '
        f'title="{title}" target="_blank" rel="noopener">'
        f'<span class="card-num">#{num}</span>'
        f'<span class="card-title">{short}</span>'
        f'{size_pill}'
        f'</a>'
    )


def _render_cell(
    cells_by_epic: dict[str, list[str]], lane_codes: list[str], lane_meta: dict
) -> str:
    """Render one cell (milestone×column). cells_by_epic[epic_code] = [card_html]."""
    groups = []
    for code in lane_codes:
        cards = cells_by_epic.get(code, [])
        if not cards:
            continue
        meta = lane_meta[code]
        tag = html.escape(meta.get("epic", {}).get("tag", "") or "")
        epic_num = meta.get("epic", {}).get("issue", "")
        name = html.escape(meta["name"])
        header = (
            f'<div class="epic-header" data-tone="{meta["color"]}">'
            f'<span class="epic-code">{code}</span>'
            f'<span class="epic-name">{name}</span>'
            f'{f"<span class=epic-tag>{tag}</span>" if tag else ""}'
            f'{f"<span class=epic-ref>#{epic_num}</span>" if epic_num else ""}'
            f'</div>'
        )
        groups.append(
            f'<div class="epic-group" data-epic="{code}">'
            f'{header}'
            f'<div class="epic-cards">{"".join(cards)}</div>'
            f'</div>'
        )
    return "".join(groups) if groups else '<div class="cell-empty">·</div>'


FONT_LINKS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link rel="stylesheet" '
    'href="https://fonts.googleapis.com/css2?'
    "family=Inter:wght@400;500;600;700"
    "&family=JetBrains+Mono:wght@400;500;600;700"
    "&family=Outfit:wght@500;600;700"
    '&display=swap">'
)


def _build_matrix(
    issues: dict,
) -> tuple[dict[tuple[str, str], list[dict]], int]:
    """Build (milestone, lane_code) → [issue] map. Returns (matrix, total)."""
    matrix: dict[tuple[str, str], list[dict]] = {}
    total = 0
    for iss in issues.values():
        ms = iss.get("milestone")
        lane = iss.get("lane_label")
        if not ms or not lane:
            continue
        matrix.setdefault((ms, lane), []).append(iss)
        total += 1
    for v in matrix.values():
        v.sort(key=lambda i: (i["state"] == "closed", i["number"]))
    return matrix, total


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
) -> tuple[list[str], int, int]:
    """Render milestone rows. Returns (row_html, open_count, closed_count)."""
    rows = []
    open_count = 0
    closed_count = 0
    for ms_key, ms_code, ms_name in MILESTONES:
        cells = [
            f'<div class="row-header">'
            f'<div class="ms-code">{ms_code}</div>'
            f'<div class="ms-name">{html.escape(ms_name)}</div>'
            f"</div>"
        ]
        for col_label, _, codes in COLUMN_GROUPS:
            cells_by_epic: dict[str, list[str]] = {}
            for code in codes:
                for iss in matrix.get((ms_key, code), []):
                    if iss["state"] == "closed":
                        closed_count += 1
                    else:
                        open_count += 1
                    cells_by_epic.setdefault(code, []).append(_card(iss, code))
            cells.append(
                f'<div class="grid-cell" data-col="{col_label}" data-ms="{ms_code}">'
                f"{_render_cell(cells_by_epic, codes, lane_meta)}"
                f"</div>"
            )
        rows.append(
            f'<div class="grid-row" data-ms="{ms_code}">{"".join(cells)}</div>'
        )
    return rows, open_count, closed_count


def build(layout: dict, gh: dict) -> str:
    lane_meta = {lane["code"]: lane for lane in layout["lanes"]}
    matrix, counts_total = _build_matrix(gh.get("issues", {}))
    col_headers_html = _build_col_headers(lane_meta)
    row_html, open_count, closed_count = _build_grid_rows(matrix, lane_meta)

    fgraph_base = FGRAPH_BASE.read_text() if FGRAPH_BASE.exists() else ""

    meta = layout["meta"]
    title = html.escape(meta["title"] + " — v3 (lane-swim grid)")
    date = html.escape(meta["date"])
    repo = meta["repos"][0]
    repo_url = f"https://github.com/{repo}/issues"
    subtitle = (
        f"{len(MILESTONES)} milestones × {len(COLUMN_GROUPS)} columns · "
        f"{open_count} open · {closed_count} closed · {counts_total} total"
    )

    # Build the HTML
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
/* ══════════════════════════════════════════════════════════════════
   v3 lane-swim dep-graph — tokens, grid, cards
   ══════════════════════════════════════════════════════════════════ */
:root, [data-theme="dark"] {{
  --bg:         #0d1117;
  --bg-panel:   #13191f;
  --bg-card:    #161b22;
  --bg-cell:    #0f141a;
  --bg-epic:    rgba(232,93,4,0.05);
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
  /* lane tones */
  --lane-a1:    #22c55e;
  --lane-a2:    #6366f1;
  --lane-a3:    #8b5cf6;
  --lane-b:     #3b82f6;
  --lane-c1:    #a855f7;
  --lane-c2:    #d946ef;
  --lane-c3:    #ec4899;
  --lane-d:     #06b6d4;
  --lane-e:     #eab308;
  --lane-f:     #10b981;
  --lane-g:     #f97316;
  --lane-h:     #f59e0b;
  --lane-i:     #14b8a6;
  --status-done: #16a34a;
}}

/* ── fgraph-base primitives (for lane-title / phase-line aesthetic) ─ */
{fgraph_base}

/* ── Reset + body ── */
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

/* ── Toolbar ── */
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

/* ══════════════════════════════════════════════════════════════════
   lane-swim grid — columns × rows
   ══════════════════════════════════════════════════════════════════ */
.lane-swim-grid {{
  --row-header-w: 140px;
  --col-min-w:    170px;
  display: grid;
  grid-template-columns:
    var(--row-header-w)
    repeat({len(COLUMN_GROUPS)}, minmax(var(--col-min-w), 1fr));
  gap: 0;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  position: relative;
}}

/* ── Header row (lanes / columns) ── */
.grid-head {{
  display: contents;
}}
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
.col-label[data-tone="a1"]  {{ color: var(--lane-a1); }}
.col-label[data-tone="b"]   {{ color: var(--lane-b); }}
.col-label[data-tone="c1"]  {{ color: var(--lane-c1); }}
.col-label[data-tone="d"]   {{ color: var(--lane-d); }}
.col-label[data-tone="e"]   {{ color: var(--lane-e); }}
.col-label[data-tone="f"]   {{ color: var(--lane-f); }}
.col-label[data-tone="g"]   {{ color: var(--lane-g); }}
.col-label[data-tone="h"]   {{ color: var(--lane-h); }}
.col-label[data-tone="i"]   {{ color: var(--lane-i); }}
.col-epics {{
  display: flex;
  flex-direction: column;
  gap: 2px;
}}
.col-epic {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px;
  color: var(--text-muted);
  opacity: 0.82;
}}

/* ── Grid rows (milestones) ── */
.grid-row {{
  display: contents;
}}
.row-header {{
  padding: 14px 10px;
  background: var(--bg-panel);
  border-top: 1px solid var(--border);
  border-right: 1px dashed var(--border);
  display: flex;
  flex-direction: column;
  gap: 3px;
  position: sticky;
  left: 0;
}}
.ms-code {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  font-weight: 700;
  color: var(--accent);
}}
.ms-name {{
  font-family: 'Inter', sans-serif;
  font-size: 11px;
  color: var(--text-muted);
  line-height: 1.3;
}}

/* ── Cell ── */
.grid-cell {{
  padding: 8px;
  border-top: 1px solid var(--border);
  border-right: 1px dashed var(--border);
  background: var(--bg-cell);
  min-height: 60px;
  display: flex;
  flex-direction: column;
  gap: 6px;
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

/* ── Epic group (within a cell) ── */
.epic-group {{
  display: flex;
  flex-direction: column;
  gap: 3px;
}}
.epic-header {{
  display: none; /* hidden by default; revealed when grouping is on */
  align-items: baseline;
  gap: 5px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px;
  padding: 2px 0 3px;
  border-bottom: 1px dashed var(--border);
  margin-bottom: 2px;
}}
body.group-epic .epic-header {{ display: flex; }}
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
.epic-header .epic-tag {{
  color: var(--accent);
  font-weight: 600;
}}
.epic-header .epic-ref {{
  color: var(--text-dim);
}}
.epic-header[data-tone="a1"]  {{ color: var(--lane-a1); }}
.epic-header[data-tone="a2"]  {{ color: var(--lane-a2); }}
.epic-header[data-tone="a3"]  {{ color: var(--lane-a3); }}
.epic-header[data-tone="b"]   {{ color: var(--lane-b); }}
.epic-header[data-tone="c1"]  {{ color: var(--lane-c1); }}
.epic-header[data-tone="c2"]  {{ color: var(--lane-c2); }}
.epic-header[data-tone="c3"]  {{ color: var(--lane-c3); }}
.epic-header[data-tone="d"]   {{ color: var(--lane-d); }}
.epic-header[data-tone="e"]   {{ color: var(--lane-e); }}
.epic-header[data-tone="f"]   {{ color: var(--lane-f); }}
.epic-header[data-tone="g"]   {{ color: var(--lane-g); }}
.epic-header[data-tone="h"]   {{ color: var(--lane-h); }}
.epic-header[data-tone="i"]   {{ color: var(--lane-i); }}

.epic-cards {{
  display: flex;
  flex-direction: column;
  gap: 4px;
}}

/* ── Issue card ── */
.issue-card {{
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 6px;
  align-items: center;
  padding: 5px 7px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-left: 3px solid currentColor;
  border-radius: 4px;
  font-size: 11px;
  text-decoration: none;
  color: var(--text);
  transition: background 0.15s, border-color 0.15s, transform 0.12s;
}}
.issue-card:hover {{
  background: var(--bg-panel);
  border-color: currentColor;
  transform: translateX(1px);
}}
.issue-card.done {{
  opacity: 0.55;
  text-decoration: line-through;
  text-decoration-color: var(--status-done);
}}
.issue-card.done:hover {{ opacity: 0.9; }}
.issue-card .card-num {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 700;
  color: currentColor;
}}
.issue-card .card-title {{
  color: var(--text);
  font-size: 10.5px;
  line-height: 1.25;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}}
.issue-card .card-size {{
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
/* Tone mapping for card border-left */
.issue-card[data-tone="a1"]  {{ color: var(--lane-a1); }}
.issue-card[data-tone="a2"]  {{ color: var(--lane-a2); }}
.issue-card[data-tone="a3"]  {{ color: var(--lane-a3); }}
.issue-card[data-tone="b"]   {{ color: var(--lane-b); }}
.issue-card[data-tone="c1"]  {{ color: var(--lane-c1); }}
.issue-card[data-tone="c2"]  {{ color: var(--lane-c2); }}
.issue-card[data-tone="c3"]  {{ color: var(--lane-c3); }}
.issue-card[data-tone="d"]   {{ color: var(--lane-d); }}
.issue-card[data-tone="e"]   {{ color: var(--lane-e); }}
.issue-card[data-tone="f"]   {{ color: var(--lane-f); }}
.issue-card[data-tone="g"]   {{ color: var(--lane-g); }}
.issue-card[data-tone="h"]   {{ color: var(--lane-h); }}
.issue-card[data-tone="i"]   {{ color: var(--lane-i); }}

/* ── Footer ── */
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

/* ── Hide-closed filter ── */
body.hide-closed .issue-card.done {{ display: none; }}
body.hide-closed .epic-group:has(.epic-cards:empty) {{ display: none; }}
</style>
</head>
<body>

<header class="page-header">
  <div>
    <h1>
      Lyra <span class="accent">v2</span> —
      Dependency Graph <span class="accent">v3</span>
    </h1>
    <div class="subtitle">{subtitle}</div>
  </div>
</header>

<div class="toolbar">
  <label><input type="checkbox" id="toggle-epic"> group by epic</label>
  <label><input type="checkbox" id="toggle-closed"> hide closed</label>
</div>

<div class="lane-swim-grid">
  <div class="grid-head">
    <div class="spacer"></div>
    {"".join(col_headers_html)}
  </div>
  {"".join(row_html)}
</div>

<footer class="page-footer">
  Lyra v2 plan · refreshed {date} ·
  <a href="{repo_url}">{html.escape(repo_url)}</a> ·
  <a href="lyra-v2-dependency-graph.html">v2 (legacy view)</a>
</footer>

<script>
(function() {{
  const body = document.body;
  document.getElementById('toggle-epic').addEventListener('change', e => {{
    body.classList.toggle('group-epic', e.target.checked);
  }});
  document.getElementById('toggle-closed').addEventListener('change', e => {{
    body.classList.toggle('hide-closed', e.target.checked);
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
