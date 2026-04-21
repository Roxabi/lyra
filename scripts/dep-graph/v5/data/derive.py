"""Pure derivations over loaded issue data: depth, status, counts, task list."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .model import (
    NO_LANE,
    NO_MS,
    GraphData,
    Lane,
    ref_key,
)


def compute_depth(issues: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Topological execution depth. 0 = no blockers, N = 1 + max(parent)."""
    depth: dict[str, int] = {}

    def resolve(key: str, stack: set[str]) -> int:
        if key in depth:
            return depth[key]
        if key in stack:
            return 0
        iss = issues.get(key)
        if not iss:
            return 0
        blockers = [ref_key(b) for b in iss.get("blocked_by", [])]
        if not blockers:
            d = 0
        else:
            stack = stack | {key}
            d = 1 + max(
                (resolve(b, stack) for b in blockers if b in issues),
                default=0,
            )
        depth[key] = d
        return d

    for k in issues:
        resolve(k, set())
    return depth


def status_of(iss: dict[str, Any], issues: dict[str, dict[str, Any]]) -> str:
    """Return 'done' | 'blocked' | 'ready' for one issue."""
    if iss["state"] == "closed":
        return "done"
    open_blockers = [
        b for b in iss.get("blocked_by", [])
        if issues.get(ref_key(b), {}).get("state") != "closed"
    ]
    return "blocked" if open_blockers else "ready"


def compute_visible(
    issues: dict[str, dict[str, Any]], primary_repo: str
) -> set[str]:
    """Visibility set per project-level rule.

    · Seed: all open items in primary repo.
    · Forward cascade (blocking), any state, any repo.
    · 1-hop backward (blocked_by) from anything already visible.
    """
    visible: set[str] = {
        k for k, i in issues.items()
        if i.get("repo") == primary_repo and i.get("state") == "open"
    }

    stack = list(visible)
    while stack:
        for ref in issues.get(stack.pop(), {}).get("blocking", []):
            rk = ref_key(ref)
            if rk in issues and rk not in visible:
                visible.add(rk)
                stack.append(rk)

    for k in list(visible):
        for ref in issues.get(k, {}).get("blocked_by", []):
            rk = ref_key(ref)
            if rk in issues:
                visible.add(rk)

    return visible


def epic_keys(layout_lanes: list[dict[str, Any]], primary_repo: str) -> set[str]:
    """Set of canonical keys for every epic issue declared in layout.json."""
    keys: set[str] = set()
    for lane in layout_lanes:
        epic = lane.get("epic", {})
        if epic.get("issue"):
            keys.add(f"{primary_repo}#{epic['issue']}")
    return keys


def build_matrix(
    data: GraphData,
) -> tuple[dict[tuple[str, str], list[dict[str, Any]]], dict[str, int], int]:
    """Build (ms_label, lane) → issues matrix + status counts + total.

    Only visibility-set issues are placed. Visible issues lacking a
    milestone or lane land in NO_MS / NO_LANE sentinel cells (hidden by
    the grid renderer when empty).
    """
    matrix: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    counts = {"ready": 0, "blocked": 0, "done": 0}
    total = 0
    for key, iss in data.issues.items():
        if key in data.epic_keys or key not in data.visible:
            continue
        ms = iss.get("milestone") or NO_MS
        lane = iss.get("lane_label") or NO_LANE
        matrix[(ms, lane)].append(iss)
        counts[status_of(iss, data.issues)] += 1
        total += 1
    return matrix, counts, total


def tasks_for_graph(data: GraphData) -> list[dict[str, Any]]:
    """Flat task list for graph-view layout. One entry per non-epic issue
    that has both a milestone and a lane.

    Keys match what v4/v4.5 layout math expects: num, title, url, state,
    status, milestone, lane, size, depth, blockers, unblocks.
    """
    col_of_lane = {c: label for label, _, codes in data.column_groups for c in codes}
    ms_short = {k: short for k, short, _ in data.milestones}
    ms_name_by_code = data.ms_name_by_code

    tasks: list[dict[str, Any]] = []
    for key, iss in data.issues.items():
        ms = iss.get("milestone")
        lane = iss.get("lane_label")
        if not ms or not lane or key in data.epic_keys or key not in data.visible:
            continue
        lmeta = data.lane_by_code.get(lane)
        tasks.append({
            "key": key,
            "repo": iss["repo"],
            "num": iss["number"],
            "title": iss["title"],
            "url": f"https://github.com/{iss['repo']}/issues/{iss['number']}",
            "state": iss["state"],
            "status": status_of(iss, data.issues),
            "milestone": ms_short.get(ms, ms),
            "milestone_name": ms_name_by_code.get(ms_short.get(ms, ms), ms),
            "lane": lane,
            "lane_name": lmeta.name if lmeta else "",
            "column": col_of_lane.get(lane, ""),
            "epic_num": (lmeta.epic.issue if lmeta and lmeta.epic else None),
            "size": iss.get("size") or None,
            "depth": data.depth_by_key.get(key, 0),
            "blockers": iss.get("blocked_by", []),
            "unblocks": iss.get("blocking", []),
            "labels": iss.get("labels", []),
        })
    tasks.sort(key=lambda t: (t["milestone"], t["column"], t["depth"], t["num"]))
    return tasks


def sort_cards_in_cell(
    cards: list[dict[str, Any]], depth_by_key: dict[str, int]
) -> list[dict[str, Any]]:
    """Topo depth first, then issue number. Stable."""
    return sorted(
        cards,
        key=lambda i: (
            depth_by_key.get(f"{i['repo']}#{i['number']}", 0),
            i["number"],
        ),
    )


def lane_by_code(lanes: list[Lane]) -> dict[str, Lane]:
    return {lane.code: lane for lane in lanes}
