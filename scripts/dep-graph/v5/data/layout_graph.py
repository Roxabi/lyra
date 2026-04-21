"""DAG positioning math for the graph view.

Ported from build_v4_gitgraph.py + build_v4_5_gitgraph.py — independent
implementation, no imports from those modules.

Positioning rule:
  1. Per milestone, grid size = 2 * max_band_size - 1 cells spanning
     [LANE_X_START, LANE_X_END].
  2. Min 2-cell stride between adjacent nodes in the same band.
  3. Depth 0: uniform spread across the grid.
  4. Depth d > 0: desired cell = mean of parent cells (current or prior ms).
  5. Two-sweep L/R collision resolution, clamped to grid bounds.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .model import COLUMN_GROUPS, MS_CODES

# Default lane ordering (determines within-band tie-break). Computed from the
# module-level defaults; callers that need a non-default order pass `lane_order`
# explicitly into layout_grid / ms_idx.
LANE_ORDER: list[str] = [code for _, _, codes in COLUMN_GROUPS for code in codes]

# ─── Positioning constants ──────────────────────────────────────────────────
LANE_X_START = 4.0
LANE_X_END = 96.0
Y_TOP = 2.5
Y_BOT = 97.5
ROW_HEIGHT_PX = 44
CONTAINER_CHROME_PX = 40
CONTAINER_HEIGHT_SCALE = 1.0

MIN_CELL_GAP = 2          # ≥ 1 blank cell between nodes in a band
MAX_CELL_WIDTH_PCT = 4.0  # cap so small milestones don't stretch to full width


# ─── Pure helpers ───────────────────────────────────────────────────────────

def _lane_idx(lane_code: str, lane_order: list[str] | None = None) -> int:
    order = lane_order if lane_order is not None else LANE_ORDER
    try:
        return order.index(lane_code)
    except ValueError:
        return len(order)


def ms_idx(ms: str | None, ms_codes: list[str] | None = None) -> int:
    codes = ms_codes if ms_codes is not None else MS_CODES
    if ms in codes:
        return codes.index(ms)
    return 99


def edge_path(x1: float, y1: float, x2: float, y2: float) -> str:
    """Cubic bezier edge between two points in the 0..100 stage space."""
    if abs(x1 - x2) < 0.1:
        return f"M {x1:.2f},{y1:.2f} L {x2:.2f},{y2:.2f}"
    ymid = (y1 + y2) / 2
    return (
        f"M {x1:.2f},{y1:.2f} "
        f"C {x1:.2f},{ymid:.2f} {x2:.2f},{ymid:.2f} "
        f"{x2:.2f},{y2:.2f}"
    )


def _ms_bounds(grid_size: int) -> tuple[float, float, float]:
    page_span = LANE_X_END - LANE_X_START
    page_center = (LANE_X_START + LANE_X_END) / 2
    if grid_size <= 1:
        return page_center, page_center, 0.0
    natural = page_span / (grid_size - 1)
    if natural <= MAX_CELL_WIDTH_PCT:
        return LANE_X_START, LANE_X_END, natural
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


def _resolve_cells(desired: list[int], grid_size: int) -> list[int]:
    """Two-sweep placement enforcing ≥ MIN_CELL_GAP, clamped to grid."""
    n = len(desired)
    order = sorted(range(n), key=lambda i: (desired[i], i))
    final = [0] * n
    for k, idx in enumerate(order):
        c = max(desired[idx], 0)
        if k > 0:
            c = max(c, final[order[k - 1]] + MIN_CELL_GAP)
        final[idx] = c
    for k in range(n - 1, -1, -1):
        idx = order[k]
        c = min(final[idx], grid_size - 1)
        if k < n - 1:
            c = min(c, final[order[k + 1]] - MIN_CELL_GAP)
        final[idx] = c
    return final


def _uniform_cells(n: int, grid_size: int) -> list[int]:
    if n <= 0:
        return []
    if n == 1:
        return [grid_size // 2]
    step = (grid_size - 1) / (n - 1)
    return [round(i * step) for i in range(n)]


# ─── Layout ─────────────────────────────────────────────────────────────────

@dataclass
class _Placement:
    all_tasks: list[dict[str, Any]]
    ms: str
    gsize: int
    cell_of: dict[int, int] = field(default_factory=dict)
    x_of: dict[int, float] = field(default_factory=dict)


def _parent_cells(t: dict[str, Any], ctx: _Placement) -> list[int]:
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


def _place_ms_band(band_tasks: list[dict[str, Any]], ctx: _Placement) -> None:
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
    tasks: list[dict[str, Any]],
    *,
    lane_order: list[str] | None = None,
    ms_codes: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Returns (node_records, band_records, grid_size_per_ms).

    node record:  {task, x, y, lane_tone}
    band record:  {ms, depth, y, tasks, count}

    `lane_order` / `ms_codes` override the module-level defaults when the
    caller has layout.json-driven config.
    """
    by_ms: dict[str, dict[int, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list),
    )
    for t in tasks:
        by_ms[t.get("milestone") or "M9"][t.get("depth", 0)].append(t)

    grid_size_per_ms: dict[str, int] = {}
    for ms, depths in by_ms.items():
        max_band = max(len(b) for b in depths.values())
        grid_size_per_ms[ms] = max(MIN_CELL_GAP * max_band - 1, 1)

    cell_of_num: dict[int, int] = {}
    x_of_num: dict[int, float] = {}
    for ms in sorted(by_ms.keys(), key=lambda m: ms_idx(m, ms_codes)):
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
                key=lambda t: (_lane_idx(t["lane"], lane_order), t.get("num", 0)),
            )
            _place_ms_band(band_tasks, ctx)

    sorted_band_keys: list[tuple[str, int]] = sorted(
        {(t.get("milestone") or "M9", t.get("depth", 0)) for t in tasks},
        key=lambda k: (ms_idx(k[0], ms_codes), k[1]),
    )
    n_bands = len(sorted_band_keys)
    step_y = (Y_BOT - Y_TOP) / max(n_bands - 1, 1)

    node_records: list[dict[str, Any]] = []
    band_records: list[dict[str, Any]] = []
    for i, (ms, depth) in enumerate(sorted_band_keys):
        band_y = Y_TOP + i * step_y
        band_tasks = sorted(by_ms[ms][depth], key=lambda t: cell_of_num[t["num"]])
        for t in band_tasks:
            node_records.append({
                "task": t,
                "x": x_of_num[t["num"]],
                "y": band_y,
                "lane_tone": t["lane"],
            })
        band_records.append({
            "ms": ms,
            "depth": depth,
            "y": band_y,
            "tasks": band_tasks,
            "count": len(band_tasks),
        })

    return node_records, band_records, grid_size_per_ms


def ms_vertical_extents(
    bands: list[dict[str, Any]],
) -> dict[str, tuple[float, float]]:
    """For each milestone, return (top_pct, bot_pct) enclosing its bands."""
    ys_by_ms: dict[str, list[float]] = defaultdict(list)
    for b in bands:
        ys_by_ms[b["ms"]].append(b["y"])
    ys_sorted = sorted({b["y"] for b in bands})
    step = (ys_sorted[1] - ys_sorted[0]) if len(ys_sorted) > 1 else 5.0
    out: dict[str, tuple[float, float]] = {}
    for ms, ys in ys_by_ms.items():
        top = min(ys) - step / 2
        bot = max(ys) + step / 2
        out[ms] = (max(top, 1.0), min(bot, 99.0))
    return out


def container_height(bands: list[dict[str, Any]]) -> int:
    """Pixel height for the graph wrap container."""
    base = max(len(bands), 16) * (ROW_HEIGHT_PX * 2)
    return int(base * CONTAINER_HEIGHT_SCALE + CONTAINER_CHROME_PX)
