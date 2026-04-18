"""Build v4.7 dep-graph — v4.5 grid layout + pill labels next to each node.

Same positioning as v4.5 (integer grid, parent-anchored, capped cell width).
Each circle node gets a compact label pill rendered directly below it with:

  [ ● #NUM  truncated title ]

Matches v4.1a's `.gg-chip` visual family minus the lane code. Labels expand
on hover to show the full title.

Reads: ~/.roxabi/forge/lyra/visuals/lyra-v2-dependency-graph-v3.1.tasks.json
Emits: ~/.roxabi/forge/lyra/visuals/lyra-v2-dependency-graph-v4.7.html
"""

from __future__ import annotations

import html
import json
import sys
from collections import defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import build_v4_5_gitgraph as v45  # noqa: E402
import build_v4_gitgraph as v4  # noqa: E402

FORGE = Path.home() / ".roxabi/forge/lyra/visuals"
TASKS_PATH = FORGE / "lyra-v2-dependency-graph-v3.1.tasks.json"
OUT = FORGE / "lyra-v2-dependency-graph-v4.7.html"

# Visual tuning
TITLE_CHARS = 28          # default (collapsed) title truncation
CONTAINER_HEIGHT_SCALE = 1.3  # make bands taller so labels fit below dots


LABEL_CSS = """
/* ─── v4.7 inline labels (pills next to nodes) ─────────────────── */
.gg-ilabel {
  position: absolute;
  transform: translate(-50%, 14px);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 9px;
  min-width: 46px;
  max-width: 104px;
  border-radius: 99px;
  background: var(--surface2);
  border: 1px solid var(--border-bright);
  color: var(--text);
  font-family: 'Inter', sans-serif;
  font-size: 10.5px;
  font-weight: 500;
  line-height: 1.2;
  text-decoration: none;
  white-space: nowrap;
  z-index: 2;
  cursor: pointer;
  transition: transform 0.12s ease, border-color 0.12s ease,
              box-shadow 0.14s ease, max-width 0.18s ease;
}
.gg-ilabel:hover {
  transform: translate(-50%, 14px) scale(1.04);
  max-width: 280px;
  border-color: currentColor;
  box-shadow: 0 6px 18px rgba(0,0,0,0.45), 0 0 0 1px currentColor;
  z-index: 12;
}
.gg-ilabel .gg-dot {
  width: 6px; height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
  box-shadow: none;
}
.gg-ilabel-num {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
  font-size: 10px;
  color: currentColor;
  flex-shrink: 0;
}
.gg-ilabel-title {
  color: var(--text);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
}
.gg-ilabel:hover .gg-ilabel-title {
  white-space: normal;
  overflow: visible;
}
.gg-ilabel.teal   { color: var(--teal); }
.gg-ilabel.green  { color: var(--green); }
.gg-ilabel.amber  { color: var(--amber); }
.gg-ilabel.pink   { color: var(--pink); }
.gg-ilabel.plum   { color: var(--plum); }
.gg-ilabel.cyan   { color: var(--cyan); }
.gg-ilabel.muted  { color: var(--text-dim); opacity: 0.6; }
.gg-ilabel.blocked { border-style: dashed; }
"""


def render_ilabels(node_records: list[dict]) -> str:
    parts: list[str] = []
    for n in node_records:
        t = n["task"]
        num = t.get("num", 0)
        full_title = t.get("title", "")
        title = v4.truncate(full_title, limit=TITLE_CHARS)
        status = t.get("status", "ready")
        url = t.get("url") or f"https://github.com/Roxabi/lyra/issues/{num}"
        tip = f"#{num} — {full_title}"
        parts.append(
            f'<a class="gg-ilabel {n["node_tone"]}" '
            f'href="{html.escape(url)}" target="_blank" '
            f'style="left:{n["x"]:.2f}%; top:{n["y"]:.2f}%;" '
            f'title="{html.escape(tip)}">'
            f'<span class="gg-dot {status}"></span>'
            f'<span class="gg-ilabel-num">#{num}</span>'
            f'<span class="gg-ilabel-title">{html.escape(title)}</span>'
            f'</a>'
        )
    return "\n".join(parts)


def render_page(tasks: list[dict]) -> str:
    node_records, bands, grid_sizes = v45.layout_grid(tasks)

    base_height = max(len(bands), 20) * (v4.ROW_HEIGHT_PX * 2)
    container_height = int(
        base_height * CONTAINER_HEIGHT_SCALE + v4.CONTAINER_CHROME_PX
    )

    counts: dict[str, int] = defaultdict(int)
    for t in tasks:
        counts[t.get("status", "ready")] += 1
    all_nums = {tt["num"] for tt in tasks}
    edge_count = sum(
        1 for t in tasks for ref in t.get("unblocks", [])
        if ref.get("issue") in all_nums
    )
    ordered_gs = sorted(grid_sizes.items(), key=lambda kv: v4.ms_idx(kv[0]))
    grid_summary = ", ".join(f"{ms}:{n}c" for ms, n in ordered_gs)
    mode_title = "v4.7 · labels"
    mode_subtitle = (
        f"{len(bands)} depth-bands · {len(node_records)} tasks · "
        f"grids ({grid_summary}) · max cell {v45.MAX_CELL_WIDTH_PCT:.1f}% · "
        f"hover a label for full title"
    )
    subtitle = (
        f"{len(tasks)} issues · {edge_count} edges · "
        f"{counts.get('ready',0)} ready · {counts.get('blocked',0)} blocked · "
        f"{counts.get('done',0)} done · 13 lanes · 6 milestones · "
        f"{mode_subtitle}"
    )

    dots = v4.render_nodes(node_records)
    labels = render_ilabels(node_records)
    edges = v4.render_edges(tasks, node_records)
    separators = v4.render_milestone_separators(bands)
    legend = v4.render_color_legend()

    graph_inner = f"{separators}\n{edges}\n{dots}\n{labels}"
    graph_block = (
        f'<div class="gitgraph-wrap" style="height:{container_height}px;" '
        f'role="img" aria-label="Lyra dependency graph — labeled nodes.">'
        f"{graph_inner}</div>"
    )

    page = v4.PAGE_TEMPLATE.format(
        subtitle=html.escape(subtitle),
        container_height=container_height,
        graph_block=graph_block,
        labels_wrap="",
        legend=legend,
        title_suffix=mode_title,
    )
    return page.replace("</style>", LABEL_CSS + "\n</style>", 1)


def main() -> int:
    if not TASKS_PATH.exists():
        print(f"ERROR: tasks file not found: {TASKS_PATH}")
        return 1
    raw = json.loads(TASKS_PATH.read_text())
    tasks = v4.sort_tasks(raw)
    page = render_page(tasks)
    OUT.write_text(page)
    print(f"wrote {OUT} ({len(page):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
