"""Build v4.3 dep-graph — parent-anchored x positioning.

Extends v4.1a (milestone-local x, depth-bands) with a reorder rule:

  For each task in band N, pull its x toward the mean x of its parents
  in earlier bands, but never more than MAX_X_PULL away from the uniform
  default slot. Resolve collisions with MIN_X_GAP.

Preserves v4.1a's parallelism essence: within a band, tasks still spread
across the full width — we only REORDER which task sits in which slot
(and nudge them closer to their parent lineage).

Reads: ~/.roxabi/forge/lyra/visuals/lyra-v2-dependency-graph-v3.1.tasks.json
Emits: ~/.roxabi/forge/lyra/visuals/lyra-v2-dependency-graph-v4.3.html
"""

from __future__ import annotations

import html
import json
import sys
from collections import defaultdict
from pathlib import Path

# Import all shared helpers from v4.1a
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import build_v4_gitgraph as v4  # noqa: E402

FORGE = Path.home() / ".roxabi/forge/lyra/visuals"
TASKS_PATH = FORGE / "lyra-v2-dependency-graph-v3.1.tasks.json"
OUT = FORGE / "lyra-v2-dependency-graph-v4.3.html"

# ─── Tunable constants ─────────────────────────────────────────────────────
# Max distance a task may be pulled from its uniform default slot (% units).
MAX_X_PULL = 12.0
# Minimum horizontal gap between adjacent nodes in the same band (% units).
# Adaptive: never larger than the uniform slot width for that band.
MIN_X_GAP = 3.5


# ─── Parent-anchored layout ────────────────────────────────────────────────

def _default_xs(n: int) -> tuple[list[float], float]:
    if n == 1:
        return [(v4.LANE_X_START + v4.LANE_X_END) / 2], v4.LANE_X_END - v4.LANE_X_START
    step = (v4.LANE_X_END - v4.LANE_X_START) / (n - 1)
    return [v4.LANE_X_START + j * step for j in range(n)], step


def _parent_xs(target_num: int, priors: list[dict]) -> list[float]:
    """x of every already-placed task that unblocks `target_num`."""
    xs = []
    for p in priors:
        if any(u.get("issue") == target_num for u in p["task"].get("unblocks", [])):
            xs.append(p["x"])
    return xs


def _pull_to_parent(default_x: float, parent_xs: list[float]) -> tuple[float, bool]:
    if not parent_xs:
        return default_x, False
    mean_parent = sum(parent_xs) / len(parent_xs)
    diff = mean_parent - default_x
    if diff > MAX_X_PULL:
        diff = MAX_X_PULL
    elif diff < -MAX_X_PULL:
        diff = -MAX_X_PULL
    return default_x + diff, True


def _resolve_collisions(
    desired_xs: list[float], gap: float,
) -> tuple[list[float], list[int]]:
    """Two-sweep placement: honour desired order, enforce min gap + bounds."""
    n = len(desired_xs)
    order = sorted(range(n), key=lambda k: (desired_xs[k], k))
    final = [0.0] * n
    for k, idx in enumerate(order):
        x = max(desired_xs[idx], v4.LANE_X_START)
        if k > 0:
            x = max(x, final[order[k - 1]] + gap)
        final[idx] = x
    for k in range(n - 1, -1, -1):
        idx = order[k]
        x = min(final[idx], v4.LANE_X_END)
        if k < n - 1:
            x = min(x, final[order[k + 1]] - gap)
        final[idx] = x
    return final, order


def _place_band(
    band_tasks: list[dict],
    band_y: float,
    priors: list[dict],
) -> list[dict]:
    ordered = sorted(
        band_tasks,
        key=lambda t: (
            next(k for k, (c, _, _) in enumerate(v4.LANES) if c == t["lane"]),
            t.get("num", 0),
        ),
    )
    n = len(ordered)
    default_xs, uniform_step = _default_xs(n)
    gap = min(MIN_X_GAP, uniform_step * 0.8)

    desired: list[float] = []
    anchor: list[bool] = []
    for j, t in enumerate(ordered):
        x, pulled = _pull_to_parent(default_xs[j], _parent_xs(t["num"], priors))
        desired.append(x)
        anchor.append(pulled)

    final_xs, order = _resolve_collisions(desired, gap)
    out: list[dict] = []
    for idx in order:
        t = ordered[idx]
        tone = v4.lane_tone(t["lane"])
        out.append({
            "task": t,
            "x": final_xs[idx],
            "y": band_y,
            "lane_tone": tone,
            "node_tone": v4.status_tone(t.get("status", "ready"), tone),
            "anchored": anchor[idx],
        })
    return out


def layout_parent_anchored(tasks: list[dict]) -> tuple[list[dict], list[dict]]:
    """v4.3 layout: milestone-local parallelism + parent-anchored reorder.

    Y: depth bands (same as v4.1a).
    X: default uniform slots, then each task pulled toward mean x of its
       parents in earlier bands, clamped to MAX_X_PULL and MIN_X_GAP.
    """
    bands = v4.build_bands(tasks)
    n_bands = len(bands)
    step_y = (v4.Y_BOT - v4.Y_TOP) / max(n_bands - 1, 1)

    node_records: list[dict] = []
    band_records: list[dict] = []

    for i, (ms, depth, band_tasks) in enumerate(bands):
        band_y = v4.Y_TOP + i * step_y
        placed = _place_band(band_tasks, band_y, node_records)
        node_records.extend(placed)

        sep_y = (
            (band_y + (v4.Y_TOP + (i - 1) * step_y)) / 2
            if i > 0
            else max(v4.Y_TOP - 2.0, 3.0)
        )
        band_records.append({
            "ms": ms,
            "depth": depth,
            "y": band_y,
            "tasks": band_tasks,
            "count": len(placed),
            "sep_y": sep_y,
            "label": f"{ms} · depth {depth} · {len(placed)} parallel",
        })

    return node_records, band_records


# ─── Render ────────────────────────────────────────────────────────────────

def render_page(tasks: list[dict]) -> str:
    node_records, bands = layout_parent_anchored(tasks)
    container_height = (
        max(len(bands), 20) * (v4.ROW_HEIGHT_PX * 2) + v4.CONTAINER_CHROME_PX
    )

    counts: dict[str, int] = defaultdict(int)
    for t in tasks:
        counts[t.get("status", "ready")] += 1
    all_nums = {tt["num"] for tt in tasks}
    edge_count = sum(
        1 for t in tasks for ref in t.get("unblocks", [])
        if ref.get("issue") in all_nums
    )
    anchored = sum(1 for rec in node_records if rec.get("anchored"))
    mode_title = "v4.3 · parent-anchored"
    mode_subtitle = (
        f"{len(bands)} depth-bands · {len(node_records)} tasks · "
        f"{anchored} anchored to parent · max pull {MAX_X_PULL:.0f}% · "
        f"same y = parallelizable"
    )
    subtitle = (
        f"{len(tasks)} issues · {edge_count} edges · "
        f"{counts.get('ready',0)} ready · {counts.get('blocked',0)} blocked · "
        f"{counts.get('done',0)} done · 13 lanes · 6 milestones · "
        f"{mode_subtitle}"
    )

    nodes = v4.render_nodes(node_records)
    edges = v4.render_edges(tasks, node_records)
    separators = v4.render_milestone_separators(bands)
    legend = v4.render_color_legend()

    graph_inner = f"{separators}\n{edges}\n{nodes}"
    graph_block = (
        f'<div class="gitgraph-wrap" style="height:{container_height}px;" '
        f'role="img" aria-label="Lyra dependency graph — parent-anchored lane view.">'
        f"{graph_inner}</div>"
    )

    return v4.PAGE_TEMPLATE.format(
        subtitle=html.escape(subtitle),
        container_height=container_height,
        graph_block=graph_block,
        labels_wrap="",
        legend=legend,
        title_suffix=mode_title,
    )


# ─── Main ──────────────────────────────────────────────────────────────────

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
