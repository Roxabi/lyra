"""Build v4 git-graph lane-view dependency graph.

Two layout modes for comparing parallelism readability:

  v4.1a  Hard bands (20 rows).
         Tasks with same (milestone, depth) share one y band.
         Within-lane collisions spread via small x-jitter.
         Right column: one compact row per band listing all tasks in it.

  v4.1b  Band + sub-stack (~35 rows).
         Tasks with same (milestone, depth) share a band.
         Inside a band, each lane with N tasks stacks them vertically.
         Right column: one label per task, y-aligned to its node.

Reads: ~/.roxabi/forge/lyra/visuals/lyra-v2-dependency-graph-v3.1.tasks.json
Emits: ~/.roxabi/forge/lyra/visuals/lyra-v2-dependency-graph-v4.1a.html
       ~/.roxabi/forge/lyra/visuals/lyra-v2-dependency-graph-v4.1b.html
"""

from __future__ import annotations

import html
import json
from collections import defaultdict
from pathlib import Path

FORGE = Path.home() / ".roxabi/forge/lyra/visuals"
TASKS_PATH = FORGE / "lyra-v2-dependency-graph-v3.1.tasks.json"
OUT_A = FORGE / "lyra-v2-dependency-graph-v4.1a.html"
OUT_B = FORGE / "lyra-v2-dependency-graph-v4.1b.html"

# ─── Lane definitions ──────────────────────────────────────────────────────
LANES: list[tuple[str, str, str]] = [
    ("a1", "NATS maturity",          "green"),
    ("a2", "roxabi-nats",            "plum"),
    ("a3", "roxabi-contracts",       "plum"),
    ("b",  "Containerize",           "cyan"),
    ("c1", "LiteLLM",                "pink"),
    ("c2", "lyra_harness",           "pink"),
    ("c3", "lyra_cli",               "pink"),
    ("d",  "Observability",          "teal"),
    ("e",  "Hub stateless",          "amber"),
    ("f",  "Plugins",                "green"),
    ("g",  "Voice",                  "amber"),
    ("h",  "Deploy ops",             "amber"),
    ("i",  "Vault ingest",           "teal"),
]

MILESTONES_ORDER = ["M0", "M1", "M2", "M3", "M4", "M5"]
MILESTONE_LABELS = {
    "M0": "M0 · NATS hardening",
    "M1": "M1 · NATS maturity + containerize",
    "M2": "M2 · LLM stack modernization",
    "M3": "M3 · Observability",
    "M4": "M4 · Hub statelessness",
    "M5": "M5 · Plugin layer",
}

# X-axis: 13 lanes spread across the full page width.
LANE_X_START = 4.0
LANE_X_END = 96.0
LABEL_X = 48.0  # (unused in mode A graph-only view, kept for mode B)

# Y bounds.
Y_TOP = 6.5
Y_BOT = 98.5

# Container chrome.
ROW_HEIGHT_PX = 48
CONTAINER_CHROME_PX = 120


# ─── Basic helpers ─────────────────────────────────────────────────────────

def lane_x(lane_code: str) -> float:
    """Global x for a lane (used by mode B). Mode A uses milestone-local x."""
    idx = next(i for i, (c, _, _) in enumerate(LANES) if c == lane_code)
    if len(LANES) == 1:
        return (LANE_X_START + LANE_X_END) / 2
    step = (LANE_X_END - LANE_X_START) / (len(LANES) - 1)
    return LANE_X_START + idx * step


def build_milestone_lane_x(tasks: list[dict]) -> dict[str, dict[str, float]]:
    """For each milestone, spread its active lanes across the full page width.

    Returns {milestone: {lane_code: x_percent}}. Lanes within a milestone are
    ordered by their global LANES index (preserves color adjacency).
    """
    active: dict[str, set[str]] = defaultdict(set)
    for t in tasks:
        ms = t.get("milestone") or "M9"
        active[ms].add(t["lane"])

    result: dict[str, dict[str, float]] = {}
    for ms, lane_set in active.items():
        ordered = [c for c, _, _ in LANES if c in lane_set]
        n = len(ordered)
        if n == 1:
            # Single lane → center it
            result[ms] = {ordered[0]: (LANE_X_START + LANE_X_END) / 2}
        else:
            step = (LANE_X_END - LANE_X_START) / (n - 1)
            result[ms] = {
                lane: LANE_X_START + i * step
                for i, lane in enumerate(ordered)
            }
    return result


def lane_tone(lane_code: str) -> str:
    return next(t for c, _, t in LANES if c == lane_code)


def truncate(s: str, limit: int = 58) -> str:
    s = s.strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def status_tone(status: str, lane_tone_class: str) -> str:
    if status == "done":
        return "muted"
    if status == "blocked":
        return f"{lane_tone_class} blocked"
    return lane_tone_class


def edge_path(x1: float, y1: float, x2: float, y2: float) -> str:
    if abs(x1 - x2) < 0.1:
        return f"M {x1:.2f},{y1:.2f} L {x2:.2f},{y2:.2f}"
    ymid = (y1 + y2) / 2
    return f"M {x1:.2f},{y1:.2f} C {x1:.2f},{ymid:.2f} {x2:.2f},{ymid:.2f} {x2:.2f},{y2:.2f}"


def ms_idx(ms: str | None) -> int:
    if ms in MILESTONES_ORDER:
        return MILESTONES_ORDER.index(ms)
    return 99


# ─── Band computation (shared) ─────────────────────────────────────────────

def build_bands(tasks: list[dict]) -> list[tuple[str, int, list[dict]]]:
    """Return sorted list of (milestone, depth, tasks_in_band)."""
    groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for t in tasks:
        key = (t.get("milestone") or "M9", t.get("depth", 0))
        groups[key].append(t)
    sorted_keys = sorted(groups.keys(), key=lambda k: (ms_idx(k[0]), k[1]))
    return [(ms, d, sorted(groups[(ms, d)], key=lambda t: (
        next(i for i, (c, _, _) in enumerate(LANES) if c == t["lane"]),
        t.get("num", 0)))) for ms, d in sorted_keys]


# ─── Mode A layout (hard bands, 20 y positions) ────────────────────────────

def layout_a(tasks: list[dict]) -> tuple[list[dict], list[dict]]:
    """Compute positions for v4.1a.

    X: **uniform per-band** — each band's tasks are evenly spaced across the
       full width. Sort order = (lane_idx, num) so same-color tasks stay
       visually adjacent while every adjacent pair has identical spacing.
    Y: depth band order (same as before) for dependency flow.
    """
    bands = build_bands(tasks)
    n_bands = len(bands)
    step_y = (Y_BOT - Y_TOP) / max(n_bands - 1, 1)

    node_records: list[dict] = []
    band_records: list[dict] = []

    for i, (ms, depth, band_tasks) in enumerate(bands):
        band_y = Y_TOP + i * step_y
        # Sort by (global lane index, issue num) for deterministic color adjacency
        ordered = sorted(
            band_tasks,
            key=lambda t: (
                next(k for k, (c, _, _) in enumerate(LANES) if c == t["lane"]),
                t.get("num", 0),
            ),
        )
        n = len(ordered)
        if n == 1:
            xs = [(LANE_X_START + LANE_X_END) / 2]
        else:
            step_x = (LANE_X_END - LANE_X_START) / (n - 1)
            xs = [LANE_X_START + j * step_x for j in range(n)]
        for t, x in zip(ordered, xs, strict=True):
            tone = lane_tone(t["lane"])
            node_records.append({
                "task": t,
                "x": x,
                "y": band_y,
                "lane_tone": tone,
                "node_tone": status_tone(t.get("status", "ready"), tone),
            })
        sep_y = (band_y + (Y_TOP + (i - 1) * step_y)) / 2 if i > 0 else max(Y_TOP - 2.0, 3.0)
        band_records.append({
            "ms": ms,
            "depth": depth,
            "y": band_y,
            "tasks": band_tasks,
            "count": n,
            "sep_y": sep_y,
            "label": f"{ms} · depth {depth} · {n} parallel",
        })

    return node_records, band_records


# ─── Mode B layout (band + sub-stack) ──────────────────────────────────────

def layout_b(tasks: list[dict]) -> tuple[list[dict], list[dict]]:
    """Compute positions for v4.1b.

    Each band has height = max intra-lane stack. Tasks stack vertically
    at their lane_x within the band.
    """
    bands = build_bands(tasks)

    # Compute sub-row count per band
    band_stacks: list[int] = []
    for _, _, band_tasks in bands:
        lane_counts: dict[str, int] = defaultdict(int)
        for t in band_tasks:
            lane_counts[t["lane"]] += 1
        band_stacks.append(max(lane_counts.values()) if lane_counts else 1)

    total_sub = sum(band_stacks)
    sub_step = (Y_BOT - Y_TOP) / max(total_sub - 1, 1)

    node_records: list[dict] = []
    band_records: list[dict] = []

    cursor = 0
    for i, ((ms, depth, band_tasks), stack_h) in enumerate(zip(bands, band_stacks, strict=True)):
        band_y_top = Y_TOP + cursor * sub_step
        band_y_bot = Y_TOP + (cursor + stack_h - 1) * sub_step
        band_y_mid = (band_y_top + band_y_bot) / 2

        # Assign per-lane sub-index
        per_lane_seen: dict[str, int] = defaultdict(int)
        per_lane_total: dict[str, int] = defaultdict(int)
        for t in band_tasks:
            per_lane_total[t["lane"]] += 1

        for t in band_tasks:
            lane_code = t["lane"]
            n = per_lane_total[lane_code]
            k = per_lane_seen[lane_code]
            per_lane_seen[lane_code] += 1
            # Center within the band if fewer than stack_h
            if n == 1:
                sub_y = band_y_mid
            else:
                # Distribute N items evenly across the band height
                if n == stack_h:
                    sub_y = band_y_top + k * sub_step
                else:
                    # Pack near top with spacing sub_step
                    sub_y = band_y_top + (k + (stack_h - n) / 2) * sub_step
            tone = lane_tone(lane_code)
            node_records.append({
                "task": t,
                "x": lane_x(lane_code),
                "y": sub_y,
                "lane_tone": tone,
                "node_tone": status_tone(t.get("status", "ready"), tone),
            })
        sep_y = max(band_y_top - sub_step / 2, Y_TOP - 1.5) if i > 0 else max(Y_TOP - 2.0, 3.0)
        band_records.append({
            "ms": ms,
            "depth": depth,
            "y": band_y_top,
            "y_mid": band_y_mid,
            "y_bot": band_y_bot,
            "tasks": band_tasks,
            "count": len(band_tasks),
            "sep_y": sep_y,
            "label": f"{ms} · depth {depth} · {len(band_tasks)} parallel",
        })
        cursor += stack_h

    return node_records, band_records


# ─── Render helpers ────────────────────────────────────────────────────────

def render_color_legend() -> str:
    """Lane → color mapping strip shown at top of page."""
    parts = ['<div class="gg-legend">']
    for code, name, tone in LANES:
        parts.append(
            f'<span class="gg-legend-item {tone}" title="{html.escape(name)}">'
            f'<span class="gg-legend-swatch"></span>'
            f'<span class="gg-legend-code">{html.escape(code)}</span>'
            f'<span class="gg-legend-name">{html.escape(name)}</span>'
            f'</span>'
        )
    parts.append("</div>")
    return "\n".join(parts)


def render_milestone_separators(bands: list[dict]) -> str:
    """Draw one separator + label per milestone transition (not per depth)."""
    parts = []
    seen: set[str] = set()
    for b in bands:
        ms = b["ms"]
        if ms in seen:
            continue
        seen.add(ms)
        label = str(MILESTONE_LABELS.get(ms, ms))
        parts.append(
            f'<div class="gg-phase-line" style="top:{b["sep_y"]:.2f}%;"></div>'
            f'<div class="gg-phase-lbl" style="top:{b["sep_y"]:.2f}%;">'
            f'{html.escape(label)}</div>'
        )
    return "\n".join(parts)


def render_nodes(node_records: list[dict]) -> str:
    parts = []
    for n in node_records:
        t = n["task"]
        num = t.get("num", 0)
        title = truncate(t.get("title", ""))
        url = t.get("url") or f"https://github.com/Roxabi/lyra/issues/{num}"
        parts.append(
            f'<a class="gg-node {n["node_tone"]}" href="{html.escape(url)}" target="_blank" '
            f'style="left:{n["x"]:.2f}%; top:{n["y"]:.2f}%;" '
            f'title="#{num} — {html.escape(title)}"></a>'
        )
    return "\n".join(parts)


def render_edges(tasks: list[dict], node_records: list[dict]) -> str:
    # Build lookup: num → (x, y, tone)
    by_num: dict[int, dict] = {n["task"]["num"]: n for n in node_records}
    paths = []
    for t in tasks:
        src_num = t.get("num")
        if src_num is None:
            continue
        src = by_num.get(src_num)
        if not src:
            continue
        for ref in t.get("unblocks", []):
            tgt_num = ref.get("issue")
            if tgt_num is None:
                continue
            tgt = by_num.get(tgt_num)
            if not tgt:
                continue
            tone = src["lane_tone"]
            dashed = "dashed" if t.get("status") == "blocked" else ""
            d = edge_path(src["x"], src["y"], tgt["x"], tgt["y"])
            paths.append(f'<path class="gg-curve {tone} {dashed}" d="{d}"/>')
    svg_inner = "\n  ".join(paths)
    return (
        '<svg class="gitgraph-svg" viewBox="0 0 100 100" '
        'preserveAspectRatio="none" aria-hidden="true">\n  '
        f'{svg_inner}\n</svg>'
    )


def render_labels_per_task(node_records: list[dict]) -> str:
    """Mode B: one label per task, aligned to node y."""
    parts = []
    for n in node_records:
        t = n["task"]
        tone = n["lane_tone"]
        num = t.get("num", 0)
        title = truncate(t.get("title", ""))
        size = t.get("size") or ""
        milestone = t.get("milestone") or ""
        lane_code = t["lane"]
        status = t.get("status", "ready")
        meta = " · ".join(x for x in [lane_code, size, milestone] if x)
        size_html = (
            f'<span class="gg-size">{html.escape(size)}</span>' if size else ""
        )
        parts.append(
            f'<div class="gg-label" style="left:{LABEL_X:.2f}%; top:{n["y"]:.2f}%;">'
            f'<div class="gg-label-name {tone}">'
            f'<span class="gg-dot {status}"></span>'
            f'<span class="gg-num">#{num}</span> '
            f'<span class="gg-title-text">{html.escape(title)}</span>'
            f'{size_html}'
            f'</div>'
            f'<div class="gg-label-sig">{html.escape(meta)}</div>'
            f'</div>'
        )
    return "\n".join(parts)


def render_labels_per_milestone(tasks: list[dict]) -> str:
    """Mode A: one chip block per milestone. Chips sorted by depth, lane, num."""
    by_ms: dict[str, list[dict]] = defaultdict(list)
    for t in tasks:
        by_ms[t.get("milestone") or "M9"].append(t)

    parts = []
    for ms in sorted(by_ms.keys(), key=ms_idx):
        ms_tasks = sorted(
            by_ms[ms],
            key=lambda t: (
                t.get("depth", 0),
                next(i for i, (c, _, _) in enumerate(LANES) if c == t["lane"]),
                t.get("num", 0),
            ),
        )
        label = MILESTONE_LABELS.get(ms, ms)
        count = len(ms_tasks)
        chips = []
        for t in ms_tasks:
            lane_code = t["lane"]
            tone = lane_tone(lane_code)
            num = t.get("num", 0)
            title = truncate(t.get("title", ""), limit=48)
            status = t.get("status", "ready")
            url = t.get("url") or f"https://github.com/Roxabi/lyra/issues/{num}"
            chips.append(
                f'<a class="gg-chip {tone}" href="{html.escape(url)}" target="_blank" '
                f'title="{html.escape(title)}">'
                f'<span class="gg-dot {status}"></span>'
                f'<span class="gg-chip-num">#{num}</span>'
                f'<span class="gg-chip-title">{html.escape(title)}</span>'
                f'<span class="gg-chip-lane">{lane_code}</span>'
                f'</a>'
            )
        parts.append(
            f'<div class="gg-milestone-block">'
            f'<div class="gg-milestone-header">'
            f'<span class="gg-milestone-label">{html.escape(label)}</span>'
            f'<span class="gg-milestone-count">{count} tasks</span>'
            f'</div>'
            f'<div class="gg-milestone-chips">{"".join(chips)}</div>'
            f'</div>'
        )
    return "\n".join(parts)


# ─── Page render ───────────────────────────────────────────────────────────

def render_page(mode: str, tasks: list[dict]) -> str:
    if mode == "a":
        node_records, bands = layout_a(tasks)
        container_height = max(len(bands), 20) * (ROW_HEIGHT_PX * 2) + CONTAINER_CHROME_PX
        labels_block = render_labels_per_milestone(tasks)
        mode_title = "v4.1a · hard bands"
        mode_subtitle = (
            f"{len(bands)} depth-bands · {len(node_records)} tasks · "
            f"same y = parallelizable · grouped by milestone"
        )
    elif mode == "b":
        node_records, bands = layout_b(tasks)
        total_sub = sum(max(1, max(
            defaultdict(int, {
                ln: sum(1 for t in b["tasks"] if t["lane"] == ln)
                for ln in {t["lane"] for t in b["tasks"]}
            }).values()
        )) for b in bands)
        container_height = total_sub * ROW_HEIGHT_PX + CONTAINER_CHROME_PX
        labels_block = render_labels_per_task(node_records)
        mode_title = "v4.1b · band + sub-stack"
        mode_subtitle = (
            f"{len(bands)} bands · {len(node_records)} tasks · "
            f"same band y-range = parallelizable · intra-lane collisions stack vertically"
        )
    else:
        raise ValueError(f"unknown mode {mode!r}")

    counts = defaultdict(int)
    for t in tasks:
        counts[t.get("status", "ready")] += 1
    edge_count = sum(
        1 for t in tasks for ref in t.get("unblocks", [])
        if ref.get("issue") in {tt["num"] for tt in tasks}
    )
    subtitle = (
        f"{len(tasks)} issues · {edge_count} edges · "
        f"{counts.get('ready',0)} ready · {counts.get('blocked',0)} blocked · "
        f"{counts.get('done',0)} done · 13 lanes · 6 milestones · "
        f"{mode_subtitle}"
    )

    nodes = render_nodes(node_records)
    edges = render_edges(tasks, node_records)

    if mode == "a":
        # Graph-only view — nodes, edges, milestone separators. No chip list.
        separators = render_milestone_separators(bands)
        legend = render_color_legend()
        graph_inner_a = f"{separators}\n{edges}\n{nodes}"
        graph_block = (
            f'<div class="gitgraph-wrap" style="height:{container_height}px;" '
            f'role="img" aria-label="Lyra dependency graph — git-graph lane view.">'
            f"{graph_inner_a}</div>"
        )
        labels_wrap = ""  # no chip list
        return PAGE_TEMPLATE.format(
            subtitle=html.escape(subtitle),
            container_height=container_height,
            graph_block=graph_block,
            labels_wrap=labels_wrap,
            legend=legend,
            title_suffix=mode_title,
        )
    else:
        # Mode B keeps the old column chrome + depth separators.
        header_strip_b = '<div class="gg-lane-header" aria-hidden="true">'
        for code, name, tone in LANES:
            x = lane_x(code)
            header_strip_b += (
                f'<span class="gg-lane-title {tone}" style="left:{x:.2f}%" '
                f'title="{html.escape(name)}">{html.escape(code)}</span>'
            )
        header_strip_b += "</div>"
        for code, _, _ in LANES:
            x = lane_x(code)
            header_strip_b += f'<div class="gg-rule" style="left:{x:.2f}%;"></div>'
        sep_b = "\n".join(
            f'<div class="gg-phase-line" style="top:{b["sep_y"]:.2f}%;"></div>'
            f'<div class="gg-phase-lbl" style="top:{b["sep_y"]:.2f}%;">'
            f'{html.escape(b["label"])}</div>'
            for b in bands
        )
        legend = ""
        graph_inner_b = f"{header_strip_b}\n{sep_b}\n{edges}\n{nodes}\n{labels_block}"
        graph_block = (
            f'<div class="gitgraph-wrap" role="img" aria-label="Lyra dependency graph.">'
            f"{graph_inner_b}</div>"
        )
        labels_wrap = ""

    return PAGE_TEMPLATE.format(
        subtitle=html.escape(subtitle),
        container_height=container_height,
        graph_block=graph_block,
        labels_wrap=labels_wrap,
        legend=legend,
        title_suffix=mode_title,
    )


# ─── Template ──────────────────────────────────────────────────────────────

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lyra v2 — Dep Graph · {title_suffix}</title>
<meta property="og:title" content="Lyra v2 — Dep Graph · {title_suffix}">
<meta property="og:type" content="article">
<!-- diagram-meta:start -->
<meta name="diagram:title"     content="Lyra v2 — Dep Graph · {title_suffix}">
<meta name="diagram:category"  content="roadmap">
<meta name="diagram:cat-label" content="ROADMAP">
<meta name="diagram:color"     content="orange">
<meta name="diagram:badges"    content="latest">
<!-- diagram-meta:end -->
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@500;600;700&family=Outfit:wght@300;400;500;600;700;800&family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;700&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
html {{ -webkit-font-smoothing: antialiased; }}

:root, [data-theme="dark"] {{
  color-scheme: dark;
  --bg: #0a0a0f; --surface: #18181f; --surface2: #1f2937;
  --border: #2a2a35; --border-bright: #3a3a48;
  --text: #fafafa; --text-muted: #9ca3af; --text-dim: #6b7280;
  --accent: #e85d04; --accent-2: #f97316;
  --accent-dim: rgba(232,93,4,0.08); --accent-glow: rgba(232,93,4,0.22);
  --teal:  #06b6d4;  --green: #10b981;  --amber: #f59e0b;
  --pink:  #ec4899;  --plum:  #a855f7;  --red:   #f87171;
  --cyan:  #22d3ee;  --muted: #6b7280;
}}

body {{
  background: var(--bg); color: var(--text);
  font-family: 'Inter', system-ui, sans-serif;
  line-height: 1.5; min-height: 100vh;
}}
body::before {{
  content:''; position:fixed; inset:0; pointer-events:none; z-index:-2;
  background:
    radial-gradient(ellipse 60% 40% at 50% -5%, var(--accent-glow) 0%, transparent 60%),
    radial-gradient(ellipse 80% 50% at 50% 110%, rgba(249,115,22,0.08) 0%, transparent 55%);
}}
body::after {{
  content:''; position:fixed; inset:0; pointer-events:none; z-index:-1;
  background-image:
    linear-gradient(rgba(232,93,4,0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(232,93,4,0.035) 1px, transparent 1px);
  background-size: 42px 42px;
  mask-image: radial-gradient(ellipse 70% 70% at 50% 50%, black 30%, transparent 80%);
}}

/* ── Header ── */
header {{
  padding: 1.25rem 1.5rem 1rem;
  background: linear-gradient(180deg, var(--surface) 0%, transparent 100%);
  border-bottom: 1px solid var(--border);
}}
header h1 {{
  font-family: 'Chakra Petch', sans-serif; font-weight: 700;
  font-size: clamp(1.4rem, 4vw, 2.1rem);
  letter-spacing: -0.01em; line-height: 1.05;
}}
header h1 .accent {{
  background: linear-gradient(135deg, var(--accent) 0%, var(--accent-2) 100%);
  -webkit-background-clip: text; background-clip: text; color: transparent;
}}
header .eyebrow {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.62rem; font-weight: 500;
  text-transform: uppercase; letter-spacing: 0.22em;
  color: var(--accent); margin-bottom: 0.3rem;
}}
header .subtitle {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem; color: var(--text-muted); margin-top: 0.3rem;
}}

main {{
  max-width: none; margin: 0; padding: 1rem 1.25rem 2rem;
}}

.section {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 1rem 1.25rem 1.5rem;
  position: relative;
  overflow: hidden;
}}
.section::before {{
  content:''; position: absolute; top: 0; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, transparent, var(--accent), transparent);
  opacity: 0.4;
}}

/* ══════════════════════════════════════════════════════════════════
   Git-graph lane diagram
   ══════════════════════════════════════════════════════════════════ */
.gitgraph-wrap {{
  position: relative;
  width: 100%;
  height: {container_height}px;
}}
.gitgraph-svg {{
  position: absolute;
  top: 0; left: 0; width: 100%; height: 100%;
  pointer-events: none; overflow: visible;
}}

/* Lane header strip */
.gg-lane-header {{
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 5%;
  border-bottom: 1px solid var(--border);
  background: linear-gradient(180deg, rgba(255,255,255,0.03), transparent);
  z-index: 4;
}}
.gg-lane-title {{
  position: absolute;
  top: 50%; transform: translate(-50%, -50%);
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px; font-weight: 700;
  letter-spacing: 0.10em;
  text-transform: uppercase;
  white-space: nowrap; line-height: 1;
  padding: 2px 5px;
  border-radius: 3px;
  background: var(--bg);
  border: 1px solid currentColor;
}}
.gg-lane-title.teal   {{ color: var(--teal); }}
.gg-lane-title.green  {{ color: var(--green); }}
.gg-lane-title.amber  {{ color: var(--amber); }}
.gg-lane-title.pink   {{ color: var(--pink); }}
.gg-lane-title.plum   {{ color: var(--plum); }}
.gg-lane-title.cyan   {{ color: var(--cyan); }}

.gg-rule {{
  position: absolute;
  top: 6%; bottom: 1%;
  width: 0;
  border-left: 1px dashed var(--border);
  opacity: 0.25;
  pointer-events: none;
  z-index: 0;
}}

.gg-phase-line {{
  position: absolute; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, rgba(232,93,4,0.30), transparent);
  pointer-events: none; z-index: 1;
}}
.gg-phase-lbl {{
  position: absolute;
  left: 3px; transform: translateY(-120%);
  font-family: 'JetBrains Mono', monospace;
  font-size: 8.5px; font-weight: 600;
  letter-spacing: 0.14em; text-transform: uppercase;
  color: var(--accent); opacity: 0.85;
  white-space: nowrap; line-height: 1;
  padding: 2px 6px;
  background: var(--bg);
  border: 1px solid var(--accent);
  border-radius: 3px;
  z-index: 5;
  pointer-events: none;
}}

.gg-node {{
  position: absolute;
  transform: translate(-50%, -50%);
  width: 14px; height: 14px;
  border-radius: 50%;
  border: 2px solid currentColor;
  background: var(--bg);
  z-index: 3;
  cursor: pointer;
  text-decoration: none;
  transition: box-shadow 0.18s, transform 0.15s;
}}
.gg-node:hover {{
  transform: translate(-50%, -50%) scale(1.6);
  z-index: 6;
}}
.gg-node.teal   {{ color: var(--teal);   box-shadow: 0 0 6px rgba(6,182,212,0.5); }}
.gg-node.green  {{ color: var(--green);  box-shadow: 0 0 6px rgba(16,185,129,0.5); }}
.gg-node.amber  {{ color: var(--amber);  box-shadow: 0 0 6px rgba(245,158,11,0.5); }}
.gg-node.pink   {{ color: var(--pink);   box-shadow: 0 0 6px rgba(236,72,153,0.5); }}
.gg-node.plum   {{ color: var(--plum);   box-shadow: 0 0 6px rgba(168,85,247,0.5); }}
.gg-node.cyan   {{ color: var(--cyan);   box-shadow: 0 0 6px rgba(34,211,238,0.5); }}
.gg-node.muted  {{ color: var(--text-dim); background: var(--surface); opacity: 0.55; box-shadow: none; }}
.gg-node.blocked {{ border-style: dashed; opacity: 0.75; }}

/* Labels — mode B (one per task, aligned to node y) */
.gg-label {{
  position: absolute;
  transform: translateY(-50%);
  z-index: 2;
  pointer-events: none;
  line-height: 1.2;
  right: 1%;
  padding-left: 2px;
}}
.gg-label-name {{
  font-family: 'Outfit', sans-serif;
  font-size: 11.5px; font-weight: 600; line-height: 1.1;
  display: flex; align-items: center; gap: 6px;
  color: var(--text);
}}
.gg-label-name .gg-num {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 10.5px; font-weight: 700; opacity: 0.95;
}}
.gg-label-name.teal   .gg-num {{ color: var(--teal); }}
.gg-label-name.green  .gg-num {{ color: var(--green); }}
.gg-label-name.amber  .gg-num {{ color: var(--amber); }}
.gg-label-name.pink   .gg-num {{ color: var(--pink); }}
.gg-label-name.plum   .gg-num {{ color: var(--plum); }}
.gg-label-name.cyan   .gg-num {{ color: var(--cyan); }}
.gg-title-text {{
  color: var(--text); font-weight: 500;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}}
.gg-label-sig {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 8.5px; color: var(--text-muted);
  margin-top: 2px; letter-spacing: 0.04em;
}}

.gg-dot {{
  display: inline-block;
  width: 7px; height: 7px; border-radius: 50%;
  flex-shrink: 0;
}}
.gg-dot.ready   {{ background: var(--green); box-shadow: 0 0 4px rgba(16,185,129,0.6); }}
.gg-dot.blocked {{ background: var(--red);   box-shadow: 0 0 4px rgba(248,113,113,0.6); }}
.gg-dot.done    {{ background: var(--text-dim); opacity: 0.6; }}
.gg-dot.defer   {{ background: var(--amber); opacity: 0.6; }}

.gg-size {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 8.5px;
  padding: 1px 5px;
  border-radius: 99px;
  background: var(--surface2);
  border: 1px solid var(--border-bright);
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: 600;
  flex-shrink: 0;
}}

/* Color legend — lane → color strip shown below header */
.gg-legend {{
  display: flex;
  flex-wrap: wrap;
  gap: 10px 14px;
  padding: 1rem 1.5rem;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.72rem;
}}
.gg-legend-item {{
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  border-radius: 99px;
  background: var(--surface2);
  border: 1px solid var(--border-bright);
}}
.gg-legend-swatch {{
  display: inline-block;
  width: 10px; height: 10px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 6px currentColor;
  flex-shrink: 0;
}}
.gg-legend-code {{
  font-weight: 700;
  color: currentColor;
  min-width: 1.4em;
  text-align: center;
}}
.gg-legend-name {{
  color: var(--text-muted);
  font-weight: 500;
}}
.gg-legend-item.teal   {{ color: var(--teal); }}
.gg-legend-item.green  {{ color: var(--green); }}
.gg-legend-item.amber  {{ color: var(--amber); }}
.gg-legend-item.pink   {{ color: var(--pink); }}
.gg-legend-item.plum   {{ color: var(--plum); }}
.gg-legend-item.cyan   {{ color: var(--cyan); }}

/* Milestone chip list — one block per milestone, chips wrap */
.gg-milestone-list {{
  display: flex;
  flex-direction: column;
  gap: 1rem;
  margin-top: 1.25rem;
}}
.gg-milestone-block {{
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0.9rem 1rem 1rem;
  position: relative;
}}
.gg-milestone-block::before {{
  content:''; position: absolute; top: 0; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, var(--accent), transparent 70%);
  opacity: 0.45;
  border-top-left-radius: 10px;
  border-top-right-radius: 10px;
}}
.gg-milestone-header {{
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 0.65rem;
}}
.gg-milestone-label {{
  font-family: 'Chakra Petch', 'Outfit', sans-serif;
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--accent);
  letter-spacing: 0.02em;
}}
.gg-milestone-count {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.68rem;
  color: var(--text-muted);
  padding: 2px 8px;
  border-radius: 99px;
  background: var(--surface2);
  border: 1px solid var(--border);
}}
.gg-milestone-chips {{
  display: flex;
  flex-wrap: wrap;
  gap: 14px 18px;  /* row-gap column-gap */
}}

.gg-chip {{
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 7px 14px;
  border-radius: 8px;
  background: var(--surface2);
  border: 1px solid var(--border-bright);
  color: var(--text);
  text-decoration: none;
  font-family: 'Inter', sans-serif;
  font-size: 11.5px;
  font-weight: 500;
  line-height: 1.2;
  transition: transform 0.12s, border-color 0.12s, box-shadow 0.12s;
  max-width: 380px;
}}
.gg-chip:hover {{
  transform: translateY(-1px);
  border-color: currentColor;
  box-shadow: 0 0 8px currentColor;
}}
.gg-chip.teal   {{ color: var(--teal); }}
.gg-chip.green  {{ color: var(--green); }}
.gg-chip.amber  {{ color: var(--amber); }}
.gg-chip.pink   {{ color: var(--pink); }}
.gg-chip.plum   {{ color: var(--plum); }}
.gg-chip.cyan   {{ color: var(--cyan); }}
.gg-chip-num {{
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
  font-size: 11px;
}}
.gg-chip-title {{
  color: var(--text);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}}
.gg-chip-lane {{
  opacity: 0.65;
  font-size: 9px;
  text-transform: uppercase;
  letter-spacing: 0.10em;
  padding-left: 8px;
  border-left: 1px solid var(--border);
  font-weight: 600;
}}

.gg-curve {{
  fill: none;
  stroke-width: 1.5;
  opacity: 0.70;
  vector-effect: non-scaling-stroke;
}}
.gg-curve.teal   {{ stroke: var(--teal); }}
.gg-curve.green  {{ stroke: var(--green); }}
.gg-curve.amber  {{ stroke: var(--amber); }}
.gg-curve.pink   {{ stroke: var(--pink); }}
.gg-curve.plum   {{ stroke: var(--plum); }}
.gg-curve.cyan   {{ stroke: var(--cyan); }}
.gg-curve.dashed {{ stroke-dasharray: 3 3; opacity: 0.50; }}

.legend-row {{
  display: flex; flex-wrap: wrap; gap: 10px;
  margin-top: 1rem;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
  color: var(--text-muted);
}}
.legend-row .lp {{
  display: inline-flex; align-items: center; gap: 5px;
  padding: 3px 8px;
  border-radius: 99px;
  background: var(--surface2);
  border: 1px solid var(--border);
}}
.legend-row .lp .lpd {{
  width: 8px; height: 8px; border-radius: 50%;
}}
.legend-row .lp.ready   .lpd {{ background: var(--green); }}
.legend-row .lp.blocked .lpd {{ background: var(--red); }}
.legend-row .lp.done    .lpd {{ background: var(--text-dim); }}
</style>
</head>
<body>

<header>
  <div class="eyebrow">Lyra · Forge · Roadmap · {title_suffix}</div>
  <h1>Lyra <span class="accent">v2</span> — Dep Graph <span class="accent">{title_suffix}</span></h1>
  <div class="subtitle">{subtitle}</div>
</header>

{legend}

<main>
  <section class="section">
    {graph_block}
    {labels_wrap}
    <div class="legend-row">
      <span class="lp ready"><span class="lpd"></span>ready</span>
      <span class="lp blocked"><span class="lpd"></span>blocked</span>
      <span class="lp done"><span class="lpd"></span>done</span>
    </div>
  </section>
</main>

</body>
</html>
"""


# ─── Sort + main ───────────────────────────────────────────────────────────

def sort_tasks(tasks: list[dict]) -> list[dict]:
    def key(t: dict) -> tuple:
        ms = t.get("milestone") or "M9"
        lane_idx = next(
            (i for i, (c, _, _) in enumerate(LANES) if c == t["lane"]), 99
        )
        return (ms_idx(ms), t.get("depth", 0), lane_idx, t.get("num", 0))
    return sorted(tasks, key=key)


def main() -> int:
    if not TASKS_PATH.exists():
        print(f"ERROR: tasks file not found: {TASKS_PATH}")
        return 1
    raw = json.loads(TASKS_PATH.read_text())
    tasks = sort_tasks(raw)

    for mode, out in [("a", OUT_A), ("b", OUT_B)]:
        page = render_page(mode, tasks)
        out.write_text(page)
        print(f"wrote {out} ({len(page):,} bytes, mode={mode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
