"""Pure derivations over loaded issue data: depth, status, counts, task list."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .model import (
    COLUMN_GROUPS,
    MILESTONES,
    MS_NAME_BY_CODE,
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
    """Build (ms_label, lane) → issues matrix + status counts + total."""
    matrix: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    counts = {"ready": 0, "blocked": 0, "done": 0}
    total = 0
    for key, iss in data.issues.items():
        ms = iss.get("milestone")
        lane = iss.get("lane_label")
        if not ms or not lane or key in data.epic_keys:
            continue
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
    col_of_lane = {c: label for label, _, codes in COLUMN_GROUPS for c in codes}
    ms_short = {k: short for k, short, _ in MILESTONES}

    tasks: list[dict[str, Any]] = []
    for key, iss in data.issues.items():
        ms = iss.get("milestone")
        lane = iss.get("lane_label")
        if not ms or not lane or key in data.epic_keys:
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
            "milestone_name": MS_NAME_BY_CODE.get(ms_short.get(ms, ms), ms),
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
