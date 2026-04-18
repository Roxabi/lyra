"""Build v4.5 dep-graph — integer-cell grid, parent-anchored children.

Positioning rule (per user spec):

  1. Per milestone, compute grid size = 2 * max_band_size_in_ms - 1 cells
     spanning the full page width [LANE_X_START, LANE_X_END]
     (preserves milestone-local spread = parallelism essence).
  2. At least 1 blank cell between any two adjacent nodes in the same band
     → min stride of 2 cells.
  3. Depth 0: tasks spread uniformly across the grid on a 2-cell stride.
  4. Depth d > 0: each task's desired cell = mean of its parents' cells.
     Parents may be in an earlier band of the same milestone OR in a prior
     milestone (then we convert parent x → nearest cell in this grid).
  5. Collision resolution: two-sweep L/R enforcing min 2-cell gap, clamped
     to [0, grid_size - 1].

  A0                    A0
   |          or       /  \\
  A1                 A1a   A1b

Reads: ~/.roxabi/forge/lyra/visuals/lyra-v2-dependency-graph-v3.1.tasks.json
Emits: ~/.roxabi/forge/lyra/visuals/lyra-v2-dependency-graph-v4.5.html
"""

from __future__ import annotations

import html
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import build_v4_gitgraph as v4  # noqa: E402

FORGE = Path.home() / ".roxabi/forge/lyra/visuals"
TASKS_PATH = FORGE / "lyra-v2-dependency-graph-v3.1.tasks.json"
OUT = FORGE / "lyra-v2-dependency-graph-v4.5.html"

# Minimum cell stride between any two adjacent nodes in a band (1 blank cell).
MIN_CELL_GAP = 2
# Max cell width in percent of page. Milestones whose natural cell width would
# exceed this cap shrink their grid span and center it — keeps node spacing
# uniform across milestones instead of stretching every row to full width.
MAX_CELL_WIDTH_PCT = 4.0


# ─── Helpers ───────────────────────────────────────────────────────────────

def _lane_idx(lane_code: str) -> int:
    return next(i for i, (c, _, _) in enumerate(v4.LANES) if c == lane_code)


def _ms_bounds(grid_size: int) -> tuple[float, float, float]:
    """Return (x_start, x_end, cell_step) for a milestone's grid.

    Natural step = full page width / (grid_size - 1). If that exceeds
    MAX_CELL_WIDTH_PCT, shrink the span and center it on the page.
    """
    page_span = v4.LANE_X_END - v4.LANE_X_START
    page_center = (v4.LANE_X_START + v4.LANE_X_END) / 2
    if grid_size <= 1:
        return page_center, page_center, 0.0
    natural = page_span / (grid_size - 1)
    if natural <= MAX_CELL_WIDTH_PCT:
        return v4.LANE_X_START, v4.LANE_X_END, natural
    step = MAX_CELL_WIDTH_PCT
    span = step * (grid_size - 1)
    x_start = page_center - span / 2
    return x_start, x_start + span, step


def _x_from_cell(cell: int, grid_size: int) -> float:
    x_start, _, step = _ms_bounds(grid_size)
    return x_start + cell * step


def _cell_from_x(x: float, grid_size: int) -> int:
    x_start, _, step = _ms_bounds(grid_size)
    if step == 0:
        return 0
    raw = round((x - x_start) / step)
    return max(0, min(grid_size - 1, int(raw)))


def _resolve_cells(
    desired: list[int], grid_size: int,
) -> list[int]:
    """Two-sweep placement respecting min gap + grid bounds."""
    n = len(desired)
    order = sorted(range(n), key=lambda i: (desired[i], i))
    final = [0] * n
    # L→R: enforce lower bound + min gap vs left neighbour
    for k, idx in enumerate(order):
        c = max(desired[idx], 0)
        if k > 0:
            c = max(c, final[order[k - 1]] + MIN_CELL_GAP)
        final[idx] = c
    # R→L: enforce upper bound + min gap vs right neighbour
    for k in range(n - 1, -1, -1):
        idx = order[k]
        c = min(final[idx], grid_size - 1)
        if k < n - 1:
            c = min(c, final[order[k + 1]] - MIN_CELL_GAP)
        final[idx] = c
    return final


def _uniform_cells(n: int, grid_size: int) -> list[int]:
    """Evenly spread n items across the grid with ≥ MIN_CELL_GAP stride."""
    if n <= 0:
        return []
    if n == 1:
        return [grid_size // 2]
    step = (grid_size - 1) / (n - 1)
    return [round(i * step) for i in range(n)]


# ─── Layout ────────────────────────────────────────────────────────────────

@dataclass
class _Placement:
    all_tasks: list[dict]
    ms: str
    gsize: int
    cell_of: dict[int, int] = field(default_factory=dict)
    x_of: dict[int, float] = field(default_factory=dict)


def _parent_cells(t: dict, ctx: _Placement) -> list[int]:
    """Cells (in ctx's grid) of every parent of task t."""
    out: list[int] = []
    for parent in ctx.all_tasks:
        if parent["num"] == t["num"]:
            continue
        if not any(u.get("issue") == t["num"] for u in parent.get("unblocks", [])):
            continue
        pnum = parent["num"]
        if parent.get("milestone") == ctx.ms and pnum in ctx.cell_of:
            out.append(ctx.cell_of[pnum])
        elif pnum in ctx.x_of:
            out.append(_cell_from_x(ctx.x_of[pnum], ctx.gsize))
    return out


def _place_ms_band(band_tasks: list[dict], ctx: _Placement) -> None:
    n = len(band_tasks)
    desired: list[int] = []
    for j, t in enumerate(band_tasks):
        pc = _parent_cells(t, ctx)
        if pc:
            desired.append(round(sum(pc) / len(pc)))
        else:
            desired.append(_uniform_cells(n, ctx.gsize)[j])
    final = _resolve_cells(desired, ctx.gsize)
    for t, c in zip(band_tasks, final, strict=True):
        ctx.cell_of[t["num"]] = c
        ctx.x_of[t["num"]] = _x_from_cell(c, ctx.gsize)


def layout_grid(
    tasks: list[dict],
) -> tuple[list[dict], list[dict], dict[str, int]]:
    """v4.5 layout. Returns (node_records, band_records, grid_size_per_ms)."""
    by_ms: dict[str, dict[int, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for t in tasks:
        by_ms[t.get("milestone") or "M9"][t.get("depth", 0)].append(t)

    grid_size_per_ms: dict[str, int] = {}
    for ms, depths in by_ms.items():
        max_band = max(len(b) for b in depths.values())
        grid_size_per_ms[ms] = max(MIN_CELL_GAP * max_band - 1, 1)

    cell_of_num: dict[int, int] = {}
    x_of_num: dict[int, float] = {}

    for ms in sorted(by_ms.keys(), key=v4.ms_idx):
        ctx = _Placement(
            all_tasks=tasks,
            ms=ms,
            gsize=grid_size_per_ms[ms],
            cell_of=cell_of_num,
            x_of=x_of_num,
        )
        for depth in sorted(by_ms[ms].keys()):
            band_tasks = sorted(
                by_ms[ms][depth],
                key=lambda t: (_lane_idx(t["lane"]), t.get("num", 0)),
            )
            _place_ms_band(band_tasks, ctx)

    # Build node + band records in band (y) order
    sorted_band_keys: list[tuple[str, int]] = sorted(
        {(t.get("milestone") or "M9", t.get("depth", 0)) for t in tasks},
        key=lambda k: (v4.ms_idx(k[0]), k[1]),
    )
    n_bands = len(sorted_band_keys)
    step_y = (v4.Y_BOT - v4.Y_TOP) / max(n_bands - 1, 1)

    node_records: list[dict] = []
    band_records: list[dict] = []
    for i, (ms, depth) in enumerate(sorted_band_keys):
        band_y = v4.Y_TOP + i * step_y
        band_tasks = sorted(
            by_ms[ms][depth],
            key=lambda t: cell_of_num[t["num"]],
        )
        for t in band_tasks:
            tone = v4.lane_tone(t["lane"])
            node_records.append({
                "task": t,
                "x": x_of_num[t["num"]],
                "y": band_y,
                "lane_tone": tone,
                "node_tone": v4.status_tone(t.get("status", "ready"), tone),
            })
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
            "count": len(band_tasks),
            "sep_y": sep_y,
            "label": f"{ms} · depth {depth} · {len(band_tasks)} parallel",
        })

    return node_records, band_records, grid_size_per_ms


# ─── Render ────────────────────────────────────────────────────────────────

def render_page(tasks: list[dict]) -> str:
    node_records, bands, grid_sizes = layout_grid(tasks)
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
    ordered_gs = sorted(grid_sizes.items(), key=lambda kv: v4.ms_idx(kv[0]))
    grid_summary = ", ".join(f"{ms}:{n}c" for ms, n in ordered_gs)
    mode_title = "v4.5 · grid + parent anchor"
    mode_subtitle = (
        f"{len(bands)} depth-bands · {len(node_records)} tasks · "
        f"grids ({grid_summary}) · ≥1 blank cell between nodes · "
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
        f'role="img" aria-label="Lyra dependency graph — grid-anchored.">'
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
