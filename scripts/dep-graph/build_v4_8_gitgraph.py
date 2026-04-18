"""Build v4.8 dep-graph — v4.7 labels + milestone row-headers + hover-chain.

Same positioning and labels as v4.7. Adds:

  1. Left gutter (160 px) with a row-header card per milestone showing the
     code (e.g. `M1`) in orange and short description below (matches the
     v3.1 visual).
  2. Hover-chain highlight: hovering any node or label dims every other
     element to 18 % opacity and highlights self (orange outline), upstream
     blockers (red tint), and downstream unblocks (teal tint) — same
     behaviour as v3.1's `.issue-card` hover.

Reads: ~/.roxabi/forge/lyra/visuals/lyra-v2-dependency-graph-v3.1.tasks.json
Emits: ~/.roxabi/forge/lyra/visuals/lyra-v2-dependency-graph-v4.8.html
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
OUT = FORGE / "lyra-v2-dependency-graph-v4.8.html"

# Visual tuning
TITLE_CHARS = 28
CONTAINER_HEIGHT_SCALE = 1.3
GUTTER_PX = 160       # left gutter width for milestone row headers
CARD_W_PX = 136       # row-header card width (inside the gutter)

MS_NAMES: dict[str, str] = {
    "M0": "NATS hardening",
    "M1": "NATS maturity / containerize",
    "M2": "LLM stack modernization",
    "M3": "Observability",
    "M4": "Hub statelessness",
    "M5": "Plugin layer",
}


V48_CSS_TMPL = """
/* ─── v4.8 stage + milestone row headers ────────────────────────── */
.gitgraph-stage {
  position: absolute;
  left: __GUTTER__px;
  right: 0; top: 0; bottom: 0;
}
.gg-msrow {
  position: absolute;
  left: 12px;
  width: __CARD_W__px;
  padding: 12px 12px 14px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  z-index: 4;
}
.gg-msrow-code {
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  font-weight: 700;
  color: var(--accent);
  letter-spacing: 0.04em;
}
.gg-msrow-name {
  font-family: 'Inter', sans-serif;
  font-size: 11px;
  color: var(--text-muted);
  line-height: 1.32;
}

/* ─── v4.8 inline pill labels (same as v4.7) ─────────────────────── */
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
              box-shadow 0.14s ease, max-width 0.18s ease, opacity 0.18s ease;
}
.gg-ilabel:hover {
  transform: translate(-50%, 14px) scale(1.04);
  max-width: 280px;
  border-color: currentColor;
  box-shadow: 0 6px 18px rgba(0,0,0,0.45), 0 0 0 1px currentColor;
  z-index: 12;
}
.gg-ilabel .gg-dot {
  width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0;
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

/* ─── v4.8 hover-chain highlight ────────────────────────────────── */
.gitgraph-wrap .gg-node,
.gitgraph-wrap .gg-ilabel,
.gitgraph-wrap .gg-curve {
  transition: opacity 0.18s ease, box-shadow 0.18s ease;
}
.gitgraph-wrap.hl-active .gg-node,
.gitgraph-wrap.hl-active .gg-ilabel { opacity: 0.15; }
.gitgraph-wrap.hl-active .gg-curve  { opacity: 0.08; }

.gitgraph-wrap.hl-active .gg-node.hl-self,
.gitgraph-wrap.hl-active .gg-ilabel.hl-self {
  opacity: 1;
  z-index: 15;
  box-shadow: 0 0 0 2px var(--accent), 0 0 14px rgba(232,93,4,0.55);
}
.gitgraph-wrap.hl-active .gg-node.hl-up,
.gitgraph-wrap.hl-active .gg-ilabel.hl-up {
  opacity: 1;
  box-shadow: 0 0 0 1px var(--red), 0 0 6px rgba(248,113,113,0.35);
}
.gitgraph-wrap.hl-active .gg-node.hl-down,
.gitgraph-wrap.hl-active .gg-ilabel.hl-down {
  opacity: 1;
  box-shadow: 0 0 0 1px var(--teal), 0 0 6px rgba(6,182,212,0.35);
}
.gitgraph-wrap.hl-active .gg-curve.hl-edge {
  opacity: 0.95;
  stroke-width: 2.2;
}
"""


V48_JS = """
<script>
(function () {
  const wrap = document.querySelector('.gitgraph-wrap');
  if (!wrap) return;
  const targets = Array.from(wrap.querySelectorAll('[data-iss]'));
  if (targets.length === 0) return;

  const blockers = new Map();  // iss -> upstream blocker keys
  const blocking = new Map();  // iss -> downstream unblock keys
  const byIss = new Map();
  targets.forEach(el => {
    const k = el.dataset.iss;
    if (!byIss.has(k)) byIss.set(k, []);
    byIss.get(k).push(el);
    if (!blockers.has(k)) {
      blockers.set(k, (el.dataset.blockedby || '').split(',').filter(Boolean));
      blocking.set(k, (el.dataset.blocking  || '').split(',').filter(Boolean));
    }
  });
  const curves = Array.from(wrap.querySelectorAll('.gg-curve[data-src]'));

  function traverse(start, adj) {
    const seen = new Set();
    const stack = [start];
    while (stack.length) {
      const k = stack.pop();
      for (const n of adj.get(k) || []) {
        if (!seen.has(n)) { seen.add(n); stack.push(n); }
      }
    }
    return seen;
  }

  function highlight(iss) {
    const up   = traverse(iss, blockers);
    const down = traverse(iss, blocking);
    wrap.classList.add('hl-active');
    (byIss.get(iss) || []).forEach(el => el.classList.add('hl-self'));
    up.forEach(n => (byIss.get(n) || []).forEach(el => el.classList.add('hl-up')));
    down.forEach(n => (byIss.get(n) || []).forEach(el => el.classList.add('hl-down')));
    const chain = new Set([iss, ...up, ...down]);
    curves.forEach(c => {
      if (chain.has(c.dataset.src) && chain.has(c.dataset.tgt)) {
        c.classList.add('hl-edge');
      }
    });
  }

  function clearAll() {
    wrap.classList.remove('hl-active');
    wrap.querySelectorAll('.hl-self, .hl-up, .hl-down').forEach(el =>
      el.classList.remove('hl-self', 'hl-up', 'hl-down'));
    curves.forEach(c => c.classList.remove('hl-edge'));
  }

  targets.forEach(el => {
    el.addEventListener('mouseenter', () => highlight(el.dataset.iss));
    el.addEventListener('mouseleave', clearAll);
  });
})();
</script>
"""


# ─── Rendering helpers (emit hover-chain data attributes) ──────────────────

def _chain_attrs(t: dict, all_nums: set[int], unblocks_by: dict[int, list[int]]) -> str:
    num = t.get("num", 0)
    blockers = [str(p) for p in unblocks_by.get(num, []) if p in all_nums]
    blocks = [
        str(u.get("issue"))
        for u in t.get("unblocks", [])
        if u.get("issue") in all_nums
    ]
    return (
        f'data-iss="{num}" '
        f'data-blockedby="{",".join(blockers)}" '
        f'data-blocking="{",".join(blocks)}"'
    )


def render_nodes(node_records: list[dict], all_tasks: list[dict]) -> str:
    all_nums = {t["num"] for t in all_tasks}
    unblocks_by: dict[int, list[int]] = defaultdict(list)
    for t in all_tasks:
        for u in t.get("unblocks", []):
            tgt = u.get("issue")
            if tgt in all_nums:
                unblocks_by[tgt].append(t["num"])

    parts: list[str] = []
    for n in node_records:
        t = n["task"]
        num = t.get("num", 0)
        title = v4.truncate(t.get("title", ""))
        url = t.get("url") or f"https://github.com/Roxabi/lyra/issues/{num}"
        attrs = _chain_attrs(t, all_nums, unblocks_by)
        parts.append(
            f'<a class="gg-node {n["node_tone"]}" '
            f'href="{html.escape(url)}" target="_blank" '
            f'style="left:{n["x"]:.2f}%; top:{n["y"]:.2f}%;" '
            f'title="#{num} — {html.escape(title)}" {attrs}></a>'
        )
    return "\n".join(parts)


def render_ilabels(node_records: list[dict], all_tasks: list[dict]) -> str:
    all_nums = {t["num"] for t in all_tasks}
    unblocks_by: dict[int, list[int]] = defaultdict(list)
    for t in all_tasks:
        for u in t.get("unblocks", []):
            tgt = u.get("issue")
            if tgt in all_nums:
                unblocks_by[tgt].append(t["num"])

    parts: list[str] = []
    for n in node_records:
        t = n["task"]
        num = t.get("num", 0)
        full_title = t.get("title", "")
        title = v4.truncate(full_title, limit=TITLE_CHARS)
        status = t.get("status", "ready")
        url = t.get("url") or f"https://github.com/Roxabi/lyra/issues/{num}"
        attrs = _chain_attrs(t, all_nums, unblocks_by)
        tip = f"#{num} — {full_title}"
        parts.append(
            f'<a class="gg-ilabel {n["node_tone"]}" href="{html.escape(url)}" '
            f'target="_blank" '
            f'style="left:{n["x"]:.2f}%; top:{n["y"]:.2f}%;" '
            f'title="{html.escape(tip)}" {attrs}>'
            f'<span class="gg-dot {status}"></span>'
            f'<span class="gg-ilabel-num">#{num}</span>'
            f'<span class="gg-ilabel-title">{html.escape(title)}</span>'
            f'</a>'
        )
    return "\n".join(parts)


def render_edges(tasks: list[dict], node_records: list[dict]) -> str:
    by_num = {n["task"]["num"]: n for n in node_records}
    paths: list[str] = []
    for t in tasks:
        src_num = t.get("num")
        src = by_num.get(src_num)
        if not src:
            continue
        for ref in t.get("unblocks", []):
            tgt_num = ref.get("issue")
            tgt = by_num.get(tgt_num)
            if not tgt:
                continue
            tone = src["lane_tone"]
            dashed = "dashed" if t.get("status") == "blocked" else ""
            d = v4.edge_path(src["x"], src["y"], tgt["x"], tgt["y"])
            paths.append(
                f'<path class="gg-curve {tone} {dashed}" d="{d}" '
                f'data-src="{src_num}" data-tgt="{tgt_num}"/>'
            )
    inner = "\n  ".join(paths)
    return (
        '<svg class="gitgraph-svg" viewBox="0 0 100 100" '
        'preserveAspectRatio="none" aria-hidden="true">\n  '
        f'{inner}\n</svg>'
    )


def _ms_vertical_extents(bands: list[dict]) -> dict[str, tuple[float, float]]:
    """Return {ms: (top_pct, bottom_pct)} covering the bands of each milestone.

    Bounds are expanded by half a band step so the card visually encloses
    the milestone's rows.
    """
    ys_by_ms: dict[str, list[float]] = defaultdict(list)
    for b in bands:
        ys_by_ms[b["ms"]].append(b["y"])
    # Determine band step from neighbouring band y's
    ys_sorted = sorted({b["y"] for b in bands})
    step = (ys_sorted[1] - ys_sorted[0]) if len(ys_sorted) > 1 else 5.0
    out: dict[str, tuple[float, float]] = {}
    for ms, ys in ys_by_ms.items():
        top = min(ys) - step / 2
        bot = max(ys) + step / 2
        out[ms] = (max(top, 1.0), min(bot, 99.0))
    return out


def render_milestone_rows(bands: list[dict], container_h: int) -> str:
    extents = _ms_vertical_extents(bands)
    parts: list[str] = []
    ordered = sorted(extents.items(), key=lambda kv: v4.ms_idx(kv[0]))
    for ms, (top_pct, bot_pct) in ordered:
        name = MS_NAMES.get(ms, "")
        top_px = round(top_pct / 100 * container_h)
        height_px = round((bot_pct - top_pct) / 100 * container_h)
        parts.append(
            f'<div class="gg-msrow" style="top:{top_px}px; height:{height_px}px;">'
            f'<div class="gg-msrow-code">{html.escape(ms)}</div>'
            f'<div class="gg-msrow-name">{html.escape(name)}</div>'
            f'</div>'
        )
    return "\n".join(parts)


# ─── Page render ───────────────────────────────────────────────────────────

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
    mode_title = "v4.8 · rows + hover chain"
    mode_subtitle = (
        f"{len(bands)} depth-bands · {len(node_records)} tasks · "
        f"grids ({grid_summary}) · hover a dot/label to trace blockers + unblocks"
    )
    subtitle = (
        f"{len(tasks)} issues · {edge_count} edges · "
        f"{counts.get('ready',0)} ready · {counts.get('blocked',0)} blocked · "
        f"{counts.get('done',0)} done · 13 lanes · 6 milestones · "
        f"{mode_subtitle}"
    )

    dots = render_nodes(node_records, tasks)
    labels = render_ilabels(node_records, tasks)
    edges = render_edges(tasks, node_records)
    separators = v4.render_milestone_separators(bands)
    legend = v4.render_color_legend()
    ms_rows = render_milestone_rows(bands, container_height)

    stage_inner = f"{separators}\n{edges}\n{dots}\n{labels}"
    stage_block = f'<div class="gitgraph-stage">{stage_inner}</div>'
    graph_block = (
        f'<div class="gitgraph-wrap" style="height:{container_height}px;" '
        f'role="img" aria-label="Lyra dependency graph — rows + hover chain.">'
        f"{ms_rows}{stage_block}</div>"
    )

    css = V48_CSS_TMPL.replace("__GUTTER__", str(GUTTER_PX)).replace(
        "__CARD_W__", str(CARD_W_PX),
    )
    page = v4.PAGE_TEMPLATE.format(
        subtitle=html.escape(subtitle),
        container_height=container_height,
        graph_block=graph_block,
        labels_wrap="",
        legend=legend,
        title_suffix=mode_title,
    )
    page = page.replace("</style>", css + "\n</style>", 1)
    page = page.replace("</body>", V48_JS + "\n</body>", 1)
    return page


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
