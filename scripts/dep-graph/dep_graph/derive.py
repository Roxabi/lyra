"""Auto-derivation of lane order, par_groups, and bands from GitHub metadata.

Given a lane definition (code, name, color, epic) and gh_issues, this module
derives:
  - order[]       — all issues with matching lane_label, topo-sorted by
                    blocked_by (in-lane edges only), tie-broken by issue number
  - par_groups{}  — issues at the same topological depth with no edges between
                    them grouped together
  - bands[]       — milestone transitions in the derived order (derived from
                    the `milestone` field on each gh.json entry)

Design:
  - Only in-lane edges are used for topo sort (cross-lane blocked_by is ignored
    for ordering, but still rendered as arrows in build.py).
  - Open issues only are placed; closed issues are excluded from order/groups
    but remain in gh_issues for status rendering when referenced.
  - Topological cycles are logged as warnings and resolved by falling back to
    issue-number sort within the cycle.
  - The primary_repo is used to format IssueRef dicts.

This module is intentionally free of Lyra-specific logic — it only depends on
gh.json entry shape (lane_label, blocked_by, milestone, state, number, repo).
"""

from __future__ import annotations

import sys
from collections import defaultdict, deque

from .keys import format_key


def is_auto_derived_lane(lane: dict) -> bool:
    """A lane is auto-derived when no explicit `order` key is present.

    Mirrors the fast-path gate in `derive_lane` and the auto-derive check
    in `audit._scan_layout_placements`.
    """
    return "order" not in lane


def is_auto_derived_standalone(layout: dict) -> bool:
    """Standalone section is auto-derived when order[] is absent or empty.

    Matches the check at `audit._collect_auto_placed` (uses .get() chain,
    so a missing `standalone` key or `standalone: {}` both return True).
    """
    return not bool(layout.get("standalone", {}).get("order"))


def _in_lane_edges(
    issue_key: str,
    gh_entry: dict,
    lane_code: str,
    lane_issue_set: set[tuple[str, int]],
    own_repo: str,
) -> list[tuple[str, int]]:
    """Return blocked_by refs that are in the same lane (in-lane edges only)."""
    result: list[tuple[str, int]] = []
    for item in gh_entry.get("blocked_by", []):
        if isinstance(item, dict):
            ref_repo, ref_num = item["repo"], item["issue"]
        else:
            ref_repo, ref_num = own_repo, int(item)
        if (ref_repo, ref_num) in lane_issue_set:
            result.append((ref_repo, ref_num))
    return result


def _resolve_cycle(
    remaining: list[tuple[str, int]],
    depth: dict[tuple[str, int], int],
    result: list[tuple[str, int]],
) -> None:
    """Warn and append cycle members sorted by issue number (in-place)."""
    remaining_keys = ", ".join(
        f"{r}#{n}" for r, n in sorted(remaining, key=lambda x: x[1])
    )
    print(
        f"  WARN topo-sort: cycle detected in lane — "
        f"falling back to issue-number sort for: {remaining_keys}",
        file=sys.stderr,
    )
    max_depth = max(depth.values(), default=0) + 1
    for node in sorted(remaining, key=lambda x: x[1]):
        depth[node] = max_depth
        result.append(node)


def _topo_sort(
    nodes: list[tuple[str, int]],
    edges: dict[tuple[str, int], list[tuple[str, int]]],
) -> tuple[list[tuple[str, int]], dict[tuple[str, int], int]]:
    """Kahn's algorithm. Returns (sorted_nodes, depth_map).

    depth_map maps each node to its topological depth (0 = no deps in-lane).
    On cycle detection: logs a warning, falls back to issue-number sort within
    the cycle set, assigns all cycle members the same depth as their minimum
    predecessor depth + 1.
    """
    node_set = set(nodes)
    # Build in-degree and adjacency (edges: blocked_by → this node depends on it)
    in_degree: dict[tuple[str, int], int] = {n: 0 for n in nodes}
    rev_adj: dict[tuple[str, int], list[tuple[str, int]]] = defaultdict(list)

    for node in nodes:
        for dep in edges.get(node, []):
            if dep in node_set:
                in_degree[node] += 1
                rev_adj[dep].append(node)

    depth: dict[tuple[str, int], int] = {n: 0 for n in nodes}
    queue: deque[tuple[str, int]] = deque()
    for node in sorted(nodes, key=lambda x: x[1]):  # stable initial order
        if in_degree[node] == 0:
            queue.append(node)

    result: list[tuple[str, int]] = []
    while queue:
        node = queue.popleft()
        result.append(node)
        for successor in sorted(rev_adj[node], key=lambda x: x[1]):
            in_degree[successor] -= 1
            depth[successor] = max(depth[successor], depth[node] + 1)
            if in_degree[successor] == 0:
                queue.append(successor)

    remaining = [n for n in nodes if in_degree[n] > 0]
    if remaining:
        _resolve_cycle(remaining, depth, result)

    return result, depth


def _collect_lane_issues(
    code: str,
    gh_issues: dict,
    primary_repo: str,
    epic_issue_num: int | None,
) -> list[tuple[str, int]]:
    """Return (repo, num) pairs whose lane_label matches code, epic excluded.

    Closed issues are included so they render as `done` cards in-lane. They
    typically topo-sort to depth 0 (their in-lane blockers are also closed).
    """
    lane_issues: list[tuple[str, int]] = []
    for _key, entry in gh_issues.items():
        if not entry:
            continue
        if entry.get("lane_label") != code:
            continue
        repo = entry.get("repo", primary_repo)
        num = entry.get("number")
        if num is None:
            continue
        if num == epic_issue_num and repo == primary_repo:
            continue
        lane_issues.append((repo, num))
    return lane_issues


def _build_lane_edges(
    code: str,
    lane_issues: list[tuple[str, int]],
    lane_issue_set: set[tuple[str, int]],
    gh_issues: dict,
) -> dict[tuple[str, int], list[tuple[str, int]]]:
    """Build in-lane blocked_by edge map for topo sort."""
    edges: dict[tuple[str, int], list[tuple[str, int]]] = {}
    for repo, num in lane_issues:
        key = format_key(repo, num)
        entry = gh_issues.get(key, {})
        edges[(repo, num)] = _in_lane_edges(key, entry, code, lane_issue_set, repo)
    return edges


def _build_par_groups(
    code: str,
    sorted_issues: list[tuple[str, int]],
    depth_map: dict[tuple[str, int], int],
    edges: dict[tuple[str, int], list[tuple[str, int]]],
) -> dict[str, list[dict]]:
    """Group issues at the same topo depth with no intra-group edges."""
    par_groups: dict[str, list[dict]] = {}
    depth_buckets: dict[int, list[tuple[str, int]]] = defaultdict(list)
    for repo, num in sorted_issues:
        depth_buckets[depth_map.get((repo, num), 0)].append((repo, num))

    group_idx = 0
    for d in sorted(depth_buckets.keys()):
        bucket = depth_buckets[d]
        if len(bucket) <= 1:
            continue
        bucket_set = set(bucket)
        has_inner_edge = any(
            dep in bucket_set
            for repo, num in bucket
            for dep in edges.get((repo, num), [])
        )
        if not has_inner_edge:
            group_id = f"auto_{code}_{group_idx}"
            par_groups[group_id] = [{"repo": r, "issue": n} for r, n in bucket]
            group_idx += 1
    return par_groups


def derive_lane(
    lane: dict,
    gh_issues: dict,
    primary_repo: str,
) -> dict:
    """Derive order, par_groups, and bands for a lane if not already present.

    - If lane has an explicit 'order' key: return lane unchanged (graceful
      degradation — lets lanes be migrated one at a time).
    - Otherwise: auto-derive from gh_issues filtered by lane_label.

    Epic issues (lane['epic']['issue']) are excluded from the derived order[]
    — they appear only in the epic-banner, not as regular cards.
    """
    if not is_auto_derived_lane(lane):
        return lane

    code = lane["code"]
    epic_issue_num: int | None = None
    if lane.get("epic") and isinstance(lane["epic"], dict):
        epic_issue_num = lane["epic"].get("issue")

    lane_issues = _collect_lane_issues(code, gh_issues, primary_repo, epic_issue_num)
    if not lane_issues:
        return {**lane, "order": [], "par_groups": {}, "bands": []}

    lane_issue_set = set(lane_issues)
    edges = _build_lane_edges(code, lane_issues, lane_issue_set, gh_issues)
    sorted_issues, depth_map = _topo_sort(lane_issues, edges)

    order = [{"repo": repo, "issue": num} for repo, num in sorted_issues]
    par_groups = _build_par_groups(code, sorted_issues, depth_map, edges)
    bands = _derive_bands(sorted_issues, gh_issues, primary_repo)

    return {**lane, "order": order, "par_groups": par_groups, "bands": bands}


_UNSET = object()  # sentinel for "first iteration not yet seen"


def _derive_bands(
    sorted_issues: list[tuple[str, int]],
    gh_issues: dict,
    primary_repo: str,
) -> list[dict]:
    """Derive band headers from milestone transitions in the sorted order.

    Issues with no milestone go in an implicit first group (no band header).
    When the milestone changes, a band header is inserted before the first
    issue of the new milestone group.
    """
    bands: list[dict] = []
    prev_milestone: object = _UNSET  # use sentinel to detect first iteration

    for repo, num in sorted_issues:
        key = format_key(repo, num)
        entry = gh_issues.get(key, {})
        milestone = entry.get("milestone")  # str | None

        if prev_milestone is _UNSET:
            # First issue — no band for None, band for named milestone
            if milestone is not None:
                bands.append(
                    {
                        "before": {"repo": repo, "issue": num},
                        "text": f"{milestone} \u2225",
                    }
                )
            prev_milestone = milestone
            continue

        if milestone != prev_milestone:
            if milestone is not None:
                bands.append(
                    {
                        "before": {"repo": repo, "issue": num},
                        "text": f"{milestone} \u2225",
                    }
                )
            prev_milestone = milestone

    return bands


def derive_standalone_order(
    gh_issues: dict,
    primary_repo: str,
) -> list[dict]:
    """Derive standalone order from gh_issues with graph:standalone label.

    Returns a list of IssueRef dicts sorted by issue number ascending.
    Only open issues are included.
    """
    items: list[tuple[str, int]] = []
    for _key, entry in gh_issues.items():
        if not entry:
            continue
        if not entry.get("standalone"):
            continue
        if entry.get("state") == "closed":
            continue
        repo = entry.get("repo", primary_repo)
        num = entry.get("number")
        if num is not None:
            items.append((repo, num))

    items.sort(key=lambda x: x[1])
    return [{"repo": r, "issue": n} for r, n in items]
