"""Graph view — DAG with dots + pill labels (v4.8-style).

Nodes are positioned absolutely inside .graph-stage using percentage coords
from layout_graph.layout_grid. Each task produces one .gg-node (dot) and one
.gg-ilabel (pill) offset below the dot. Edges are SVG paths in a
viewBox="0 0 100 100" stretched to fill. Milestone row-headers on the left
gutter are positioned by pixel.
"""
from __future__ import annotations

import html
from typing import Any

from ..data import layout_graph as lg
from ..data.derive import tasks_for_graph
from ..data.model import GraphData

# Title truncation inside the pill before hover-expand.
TITLE_CHARS = 28


def _truncate(s: str, limit: int = TITLE_CHARS) -> str:
    s = s.strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _chain_attrs(t: dict[str, Any]) -> str:
    """data-iss / data-blockedby / data-blocking attribute string."""
    key = t["key"]
    blockers = ",".join(
        f"{b['repo']}#{b['issue']}" for b in t.get("blockers", [])
    )
    unblocks = ",".join(
        f"{u['repo']}#{u['issue']}" for u in t.get("unblocks", [])
    )
    return (
        f'data-iss="{key}" '
        f'data-blockedby="{blockers}" '
        f'data-blocking="{unblocks}"'
    )


def _status_cls(status: str) -> str:
    if status == "done":
        return " done"
    if status == "blocked":
        return " blocked"
    return ""


def _render_nodes(node_records: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for n in node_records:
        t = n["task"]
        num = t["num"]
        url = t["url"]
        status_cls = _status_cls(t["status"])
        attrs = _chain_attrs(t)
        tone = n["lane_tone"]
        title = _truncate(t["title"], limit=58)
        parts.append(
            f'<a class="gg-node{status_cls}" data-tone="{tone}" '
            f'href="{html.escape(url)}" target="_blank" rel="noopener" '
            f'style="left:{n["x"]:.2f}%; top:{n["y"]:.2f}%;" '
            f'title="#{num} — {html.escape(title)}" {attrs}></a>'
        )
    return "\n    ".join(parts)


def _render_ilabels(node_records: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for n in node_records:
        t = n["task"]
        num = t["num"]
        url = t["url"]
        full_title = t["title"]
        title = _truncate(full_title)
        status_cls = _status_cls(t["status"])
        attrs = _chain_attrs(t)
        tone = n["lane_tone"]
        tip = f"#{num} — {full_title}"
        parts.append(
            f'<a class="gg-ilabel{status_cls}" data-tone="{tone}" '
            f'href="{html.escape(url)}" target="_blank" rel="noopener" '
            f'style="left:{n["x"]:.2f}%; top:{n["y"]:.2f}%;" '
            f'title="{html.escape(tip)}" {attrs}>'
            f'<span class="gg-ldot" aria-hidden="true"></span>'
            f'<span class="gg-ilabel-num">#{num}</span>'
            f'<span class="gg-ilabel-title">{html.escape(title)}</span>'
            f'</a>'
        )
    return "\n    ".join(parts)


def _render_edges(
    tasks: list[dict[str, Any]], node_records: list[dict[str, Any]]
) -> str:
    by_num = {n["task"]["num"]: n for n in node_records}
    paths: list[str] = []
    for t in tasks:
        src_num = t["num"]
        src = by_num.get(src_num)
        if not src:
            continue
        for ref in t.get("unblocks", []):
            tgt_num = ref.get("issue")
            tgt = by_num.get(tgt_num)
            if not tgt:
                continue
            tone = src["lane_tone"]
            dashed = " blocked" if t.get("status") == "blocked" else ""
            d = lg.edge_path(src["x"], src["y"], tgt["x"], tgt["y"])
            src_key = t["key"]
            tgt_key = tgt["task"]["key"]
            paths.append(
                f'<path class="gg-edge{dashed}" data-tone="{tone}" '
                f'd="{d}" data-src="{src_key}" data-tgt="{tgt_key}" '
                f'vector-effect="non-scaling-stroke"/>'
            )
    inner = "\n      ".join(paths)
    return (
        '<svg class="graph-svg" viewBox="0 0 100 100" '
        'preserveAspectRatio="none" aria-hidden="true">\n      '
        f'{inner}\n    </svg>'
    )


def _render_msrows(
    bands: list[dict[str, Any]],
    container_h: int,
    data: GraphData,
) -> str:
    extents = lg.ms_vertical_extents(bands)
    ms_codes = data.ms_codes
    ms_name_by_code = data.ms_name_by_code
    ordered = sorted(extents.items(), key=lambda kv: lg.ms_idx(kv[0], ms_codes))
    rows: list[str] = []
    seps: list[str] = []
    for ms, (top_pct, bot_pct) in ordered:
        name = ms_name_by_code.get(ms, "")
        top_px = round(top_pct / 100 * container_h)
        height_px = round((bot_pct - top_pct) / 100 * container_h)
        rows.append(
            f'<div class="gg-msrow" style="top:{top_px}px; height:{height_px}px;">'
            f'<div class="gg-msrow-code">{html.escape(ms)}</div>'
            f'<div class="gg-msrow-name">{html.escape(name)}</div>'
            f'</div>'
        )
    for i in range(1, len(ordered)):
        prev_bot = ordered[i - 1][1][1]
        cur_top = ordered[i][1][0]
        y_pct = (prev_bot + cur_top) / 2
        y_px = round(y_pct / 100 * container_h)
        seps.append(f'<div class="gg-msrow-sep" style="top:{y_px}px;"></div>')
    return "\n  ".join(rows + seps)


def render(data: GraphData, *, active: bool = True) -> str:
    tasks = tasks_for_graph(data)
    lane_order = [code for _, _, codes in data.column_groups for code in codes]
    node_records, bands, _ = lg.layout_grid(
        tasks, lane_order=lane_order, ms_codes=data.ms_codes,
    )
    container_h = lg.container_height(bands)

    msrows = _render_msrows(bands, container_h, data)
    edges = _render_edges(tasks, node_records)
    nodes = _render_nodes(node_records)
    labels = _render_ilabels(node_records)

    active_cls = " view-active" if active else ""
    return (
        f'<section class="view view-graph{active_cls}" data-view="graph">\n'
        f'<div class="graph-wrap" style="height:{container_h}px;" '
        f'role="img" aria-label="Lyra v2 dependency graph — DAG view">\n'
        f'  {msrows}\n'
        f'  <div class="graph-stage">\n'
        f'    {edges}\n'
        f'    {nodes}\n'
        f'    {labels}\n'
        '  </div>\n'
        '</div>\n'
        '</section>\n'
    )
