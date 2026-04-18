"""Build v4.4 dep-graph — grandalf-powered crossing minimization.

Uses grandalf's Sugiyama layer-ordering (barycenter heuristic + crossing
reduction + dummy vertices for multi-band edges) to decide the ORDER of
tasks within each band. Then applies our milestone-local uniform-slot x
assignment in that order, so the parallelism essence (band fills row
width) is preserved.

Separation of concerns:
  grandalf → which task sits where in a band (minimize crossings)
  ours     → x coord assignment (uniform slot spread per milestone)

Reads: ~/.roxabi/forge/lyra/visuals/lyra-v2-dependency-graph-v3.1.tasks.json
Emits: ~/.roxabi/forge/lyra/visuals/lyra-v2-dependency-graph-v4.4.html
"""

from __future__ import annotations

import html
import json
import sys
from collections import defaultdict
from pathlib import Path

from grandalf.graphs import Edge, Graph, Vertex  # type: ignore[import-untyped]
from grandalf.layouts import Layer, SugiyamaLayout  # type: ignore[import-untyped]

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import build_v4_gitgraph as v4  # noqa: E402

FORGE = Path.home() / ".roxabi/forge/lyra/visuals"
TASKS_PATH = FORGE / "lyra-v2-dependency-graph-v3.1.tasks.json"
OUT = FORGE / "lyra-v2-dependency-graph-v4.4.html"

# grandalf expects each Vertex to expose a .view with width/height.
class _View:
    w = 14
    h = 14


def _band_index(t: dict, bands: list[tuple[str, int]]) -> int:
    key = (t.get("milestone") or "M9", t.get("depth", 0))
    return bands.index(key)


def _build_grandalf_layout(
    tasks: list[dict],
) -> tuple[SugiyamaLayout, dict[int, Vertex]]:
    """Build a Sugiyama layout whose layers match our (milestone, depth) bands.

    Returns (sug, vertex_by_num). Caller must run ordering_step() iterations.
    """
    # Canonical band order (same as v4.1a)
    sorted_bands: list[tuple[str, int]] = sorted(
        {(t.get("milestone") or "M9", t.get("depth", 0)) for t in tasks},
        key=lambda k: (v4.ms_idx(k[0]), k[1]),
    )
    n_bands = len(sorted_bands)

    # One Vertex per task
    vertex_by_num: dict[int, Vertex] = {}
    vertices: list[Vertex] = []
    for t in tasks:
        v = Vertex(t)
        v.view = _View()
        vertex_by_num[t["num"]] = v
        vertices.append(v)

    # Build edges from `unblocks` refs that resolve within our dataset
    all_nums = set(vertex_by_num.keys())
    edges: list[Edge] = []
    for t in tasks:
        src = vertex_by_num[t["num"]]
        src_rank = _band_index(t, sorted_bands)
        for ref in t.get("unblocks", []):
            tgt_num = ref.get("issue")
            if tgt_num not in all_nums:
                continue
            tgt_task = next(x for x in tasks if x["num"] == tgt_num)
            tgt_rank = _band_index(tgt_task, sorted_bands)
            # Skip edges that go backward or within same band — grandalf's
            # layered model requires rank(src) < rank(tgt).
            if src_rank >= tgt_rank:
                continue
            edges.append(Edge(src, vertex_by_num[tgt_num]))

    g = Graph(vertices, edges)
    # SugiyamaLayout._edge_inverter reads g.degenerated_edges (self-loops).
    # Graph (multi-component wrapper) lacks this attr — graph_core has it.
    # We have no self-loops, so inject an empty list.
    g.degenerated_edges = []
    sug = SugiyamaLayout(g)

    # Short-circuit grandalf's auto-ranking: we already know the bands.
    sug.dag = True
    sug.alt_e = []
    for t in tasks:
        v = vertex_by_num[t["num"]]
        sug.grx[v].rank = _band_index(t, sorted_bands)

    # Build empty layers, populate with real vertices in deterministic order.
    sug.layers = [Layer([]) for _ in range(n_bands)]
    for t in tasks:
        v = vertex_by_num[t["num"]]
        sug.layers[sug.grx[v].rank].append(v)

    # Insert dummy vertices for long edges (rank span > 1).
    for e in edges:
        sug.setdummies(e)

    # Initialise each layer — must happen AFTER dummies so layer.__x is correct.
    for layer in sug.layers:
        layer.setup(sug)
    sug.initdone = True
    # After init_all grandalf leaves dag=False so the first _edge_inverter
    # call at the start of Layer.order() flips it to True for _neighbors.
    sug.dag = False

    return sug, vertex_by_num


def _run_ordering(sug: SugiyamaLayout, max_iter: int = 24) -> int:
    """Run grandalf's barycenter-based reorder until crossings stop dropping."""
    prev_crossings = sum(layer.ccount or 0 for layer in sug.layers)
    for _ in range(max_iter):
        list(sug.ordering_step())
        total = sum(layer.ccount or 0 for layer in sug.layers)
        if total >= prev_crossings:
            break
        prev_crossings = total
    return prev_crossings


def _order_from_layers(sug: SugiyamaLayout) -> list[list[dict]]:
    """Return ordered lists of real task dicts per band (dummies stripped)."""
    bands: list[list[dict]] = []
    for layer in sug.layers:
        real = []
        for v in layer:
            # Real vertices carry the task dict as .data; dummies have .data = None
            if getattr(v, "data", None) is not None:
                real.append(v.data)
        bands.append(real)
    return bands


def layout_grandalf(tasks: list[dict]) -> tuple[list[dict], list[dict], int]:
    """v4.4 layout — grandalf for within-band ordering, ours for x assignment."""
    sug, _ = _build_grandalf_layout(tasks)
    crossings = _run_ordering(sug)
    ordered_bands = _order_from_layers(sug)

    sorted_band_keys: list[tuple[str, int]] = sorted(
        {(t.get("milestone") or "M9", t.get("depth", 0)) for t in tasks},
        key=lambda k: (v4.ms_idx(k[0]), k[1]),
    )
    n_bands = len(sorted_band_keys)
    step_y = (v4.Y_BOT - v4.Y_TOP) / max(n_bands - 1, 1)

    node_records: list[dict] = []
    band_records: list[dict] = []
    for i, ((ms, depth), tasks_in_order) in enumerate(
        zip(sorted_band_keys, ordered_bands, strict=True),
    ):
        band_y = v4.Y_TOP + i * step_y
        n = len(tasks_in_order)
        if n == 1:
            xs = [(v4.LANE_X_START + v4.LANE_X_END) / 2]
        else:
            step = (v4.LANE_X_END - v4.LANE_X_START) / (n - 1)
            xs = [v4.LANE_X_START + j * step for j in range(n)]
        for t, x in zip(tasks_in_order, xs, strict=True):
            tone = v4.lane_tone(t["lane"])
            node_records.append({
                "task": t,
                "x": x,
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
            "tasks": tasks_in_order,
            "count": n,
            "sep_y": sep_y,
            "label": f"{ms} · depth {depth} · {n} parallel",
        })

    return node_records, band_records, crossings


def render_page(tasks: list[dict]) -> str:
    node_records, bands, crossings = layout_grandalf(tasks)
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
    mode_title = "v4.4 · grandalf crossing-min"
    mode_subtitle = (
        f"{len(bands)} depth-bands · {len(node_records)} tasks · "
        f"{crossings} final crossings (barycenter + dummy vertices) · "
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
        f'role="img" aria-label="Lyra dependency graph — grandalf-ordered.">'
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
