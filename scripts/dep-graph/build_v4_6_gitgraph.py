"""Build v4.6 dep-graph — v4.5 grid layout with full issue cards per node.

Positions come from v4.5 (integer grid, parent-anchored, max-cell-width cap).
This variant renders each node as a compact card carrying issue number,
status dot, lane code, and truncated title instead of a bare circle.

Hover expands the card to show the full title and lifts it above neighbours.

Reads: ~/.roxabi/forge/lyra/visuals/lyra-v2-dependency-graph-v3.1.tasks.json
Emits: ~/.roxabi/forge/lyra/visuals/lyra-v2-dependency-graph-v4.6.html
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
OUT = FORGE / "lyra-v2-dependency-graph-v4.6.html"

# Visual tuning
TITLE_CHARS = 26        # truncation for the default (non-hover) title
CONTAINER_HEIGHT_SCALE = 1.35  # bands need more vertical room for cards
ZIGZAG_PCT = 1.4        # offset even/odd cards within a band (0 = no zigzag)
ZIGZAG_MIN_BAND = 4     # only apply zigzag when band has ≥ this many nodes


CARD_CSS = """
/* ─── v4.6 issue cards ─────────────────────────────────────────── */
.gg-card {
  position: absolute;
  transform: translate(-50%, -50%);
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 2px;
  min-width: 52px;
  max-width: 108px;
  padding: 4px 7px 5px;
  background: var(--surface);
  border: 2px solid currentColor;
  border-radius: 7px;
  font-family: 'Inter', sans-serif;
  text-decoration: none;
  z-index: 3;
  cursor: pointer;
  transition: transform 0.15s ease, box-shadow 0.18s ease, max-width 0.18s ease;
  box-shadow: 0 2px 6px rgba(0,0,0,0.35);
}
.gg-card:hover {
  transform: translate(-50%, -50%) scale(1.08);
  z-index: 20;
  box-shadow: 0 8px 24px rgba(0,0,0,0.55), 0 0 0 2px currentColor;
  max-width: 280px;
  min-width: 160px;
}
.gg-card-head {
  display: flex;
  align-items: center;
  gap: 5px;
  line-height: 1;
}
.gg-card-dot {
  width: 6px; height: 6px; border-radius: 50%;
  flex-shrink: 0;
}
.gg-card-dot.ready {
  background: var(--green);
  box-shadow: 0 0 4px rgba(16,185,129,0.6);
}
.gg-card-dot.blocked {
  background: var(--red);
  box-shadow: 0 0 4px rgba(248,113,113,0.6);
}
.gg-card-dot.done { background: var(--text-dim); opacity: 0.6; }
.gg-card-num {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
  font-size: 9.5px;
  color: currentColor;
  white-space: nowrap;
}
.gg-card-lane {
  font-family: 'JetBrains Mono', monospace;
  font-size: 7.5px;
  font-weight: 600;
  color: var(--text-dim);
  letter-spacing: 0.08em;
  margin-left: auto;
  text-transform: uppercase;
}
.gg-card-title {
  font-size: 9px;
  font-weight: 500;
  color: var(--text);
  line-height: 1.22;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.gg-card:hover .gg-card-title {
  white-space: normal;
  overflow: visible;
}
.gg-card.teal   { color: var(--teal); }
.gg-card.green  { color: var(--green); }
.gg-card.amber  { color: var(--amber); }
.gg-card.pink   { color: var(--pink); }
.gg-card.plum   { color: var(--plum); }
.gg-card.cyan   { color: var(--cyan); }
.gg-card.muted {
  color: var(--text-dim);
  background: var(--surface);
  opacity: 0.62;
  box-shadow: none;
}
.gg-card.blocked { border-style: dashed; opacity: 0.85; }
"""


def _apply_zigzag(node_records: list[dict]) -> None:
    """Offset alternate cards in dense bands to ease visual collisions."""
    if ZIGZAG_PCT <= 0:
        return
    by_y: dict[float, list[dict]] = defaultdict(list)
    for n in node_records:
        by_y[round(n["y"], 4)].append(n)
    for y, band in by_y.items():
        if len(band) < ZIGZAG_MIN_BAND:
            continue
        band.sort(key=lambda n: n["x"])
        for i, n in enumerate(band):
            n["y"] = y + (ZIGZAG_PCT if i % 2 == 0 else -ZIGZAG_PCT)


def render_cards(node_records: list[dict]) -> str:
    parts: list[str] = []
    for n in node_records:
        t = n["task"]
        num = t.get("num", 0)
        full_title = t.get("title", "")
        title = v4.truncate(full_title, limit=TITLE_CHARS)
        lane_code = t["lane"]
        status = t.get("status", "ready")
        milestone = t.get("milestone") or ""
        size = t.get("size") or ""
        url = t.get("url") or f"https://github.com/Roxabi/lyra/issues/{num}"
        tip_bits = [f"#{num}", full_title, lane_code, milestone]
        if size:
            tip_bits.append(size)
        tip_bits.append(f"status: {status}")
        tip = " · ".join(tip_bits)
        parts.append(
            f'<a class="gg-card {n["node_tone"]}" '
            f'href="{html.escape(url)}" target="_blank" '
            f'style="left:{n["x"]:.2f}%; top:{n["y"]:.2f}%;" '
            f'title="{html.escape(tip)}">'
            f'<div class="gg-card-head">'
            f'<span class="gg-card-dot {status}"></span>'
            f'<span class="gg-card-num">#{num}</span>'
            f'<span class="gg-card-lane">{html.escape(lane_code)}</span>'
            f'</div>'
            f'<div class="gg-card-title">{html.escape(title)}</div>'
            f'</a>'
        )
    return "\n".join(parts)


def render_page(tasks: list[dict]) -> str:
    node_records, bands, grid_sizes = v45.layout_grid(tasks)
    _apply_zigzag(node_records)

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
    mode_title = "v4.6 · cards"
    mode_subtitle = (
        f"{len(bands)} depth-bands · {len(node_records)} tasks · "
        f"grids ({grid_summary}) · max cell {v45.MAX_CELL_WIDTH_PCT:.1f}% · "
        f"hover a card for full title"
    )
    subtitle = (
        f"{len(tasks)} issues · {edge_count} edges · "
        f"{counts.get('ready',0)} ready · {counts.get('blocked',0)} blocked · "
        f"{counts.get('done',0)} done · 13 lanes · 6 milestones · "
        f"{mode_subtitle}"
    )

    cards = render_cards(node_records)
    edges = v4.render_edges(tasks, node_records)
    separators = v4.render_milestone_separators(bands)
    legend = v4.render_color_legend()

    graph_inner = f"{separators}\n{edges}\n{cards}"
    graph_block = (
        f'<div class="gitgraph-wrap" style="height:{container_height}px;" '
        f'role="img" aria-label="Lyra dependency graph — card view.">'
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
    # Inject card CSS just before </style> without re-formatting braces.
    return page.replace("</style>", CARD_CSS + "\n</style>", 1)


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
