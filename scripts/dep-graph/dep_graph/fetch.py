"""Fetch GitHub issue data for a dep-graph layout.

Reads layout.json (meta.repos[], meta.label_prefix), queries GitHub via `gh` CLI,
writes gh.json.

Issue discovery (union of):
  1. All issues labeled <prefix>lane/* or <prefix>standalone (via gh issue list).
  2. Explicit IssueRef dicts from layout.json lanes / standalone / par_groups / bands.

Per issue: REST metadata + /dependencies/blocked_by + /dependencies/blocking.

Emits gh.json with issues keyed as "owner/repo#N" containing IssueRef dicts for
blocked_by / blocking (shape: {repo: str, issue: int}).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# Module-level flag: set to True after first shape assertion is performed.
_dep_shape_checked: bool = False


def check_gh() -> None:
    if not shutil.which("gh"):
        print(
            "ERROR: `gh` CLI not found. Install from https://cli.github.com/",
            file=sys.stderr,
        )
        sys.exit(1)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iter_lane_refs(lane: dict):
    """Yield all IssueRef dicts from a lane (order, par_groups, bands)."""
    for ref in lane.get("order", []):
        if isinstance(ref, dict):
            yield ref
    for pg in lane.get("par_groups", {}).values():
        for ref in pg:
            if isinstance(ref, dict):
                yield ref
    for band in lane.get("bands", []):
        if isinstance(band.get("before"), dict):
            yield band["before"]


def search_labeled_issues(
    repo: str, label_prefix: str, lane_codes: list[str]
) -> set[int]:
    """List all issues with any <prefix>lane/* or <prefix>standalone label."""
    nums: set[int] = set()
    labels = [f"{label_prefix}standalone"] + [
        f"{label_prefix}lane/{c}" for c in lane_codes
    ]
    for lbl in labels:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--repo",
                repo,
                "--label",
                lbl,
                "--state",
                "all",
                "--limit",
                "200",
                "--json",
                "number",
                "--jq",
                "[.[].number]",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(
                f"  WARN list failed for '{lbl}': {result.stderr.strip()}",
                file=sys.stderr,
            )
            continue
        raw = result.stdout.strip()
        if raw:
            try:
                nums.update(json.loads(raw))
            except json.JSONDecodeError:
                print(
                    f"  WARN bad JSON for '{lbl}': {raw[:80]}",
                    file=sys.stderr,
                )
    return nums


def fetch_issue_meta(
    issue_num: int, repo: str, label_prefix: str
) -> tuple[int, str, str, list[str]]:
    """Fetch title, state, labels for one issue via REST.

    Returns (issue_num, title, state, label_names).
    """
    endpoint = f"repos/{repo}/issues/{issue_num}"
    result = subprocess.run(
        ["gh", "api", endpoint],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            f"  WARN #{issue_num} meta: {result.stderr.strip()}",
            file=sys.stderr,
        )
        return (issue_num, "", "open", [])
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"  WARN #{issue_num}: non-JSON response", file=sys.stderr)
        return (issue_num, "", "open", [])

    if not isinstance(data, dict):
        print(
            f"  WARN #{issue_num}: unexpected meta shape",
            file=sys.stderr,
        )
        return (issue_num, "", "open", [])

    raw_labels = data.get("labels", [])
    label_names: list[str] = (
        [lbl["name"] for lbl in raw_labels]
        if isinstance(raw_labels, list)
        else []
    )
    title: str = data.get("title", "")
    state: str = data.get("state", data.get("State", "open"))

    return (issue_num, title, state, label_names)


def _check_dep_shape(payload: list) -> None:
    """Assert that payload items match a known shape (stability guard)."""
    for item in payload:
        if isinstance(item, dict):
            has_issue_ref = "repo" in item and "issue" in item
            has_gh_raw = "number" in item and "repository" in item
            if not (has_issue_ref or has_gh_raw):
                raise ValueError(
                    "fetch_dep_list: unexpected payload shape. "
                    "Expected items with ('repo','issue') or "
                    f"('number','repository') keys, got: {payload!r}"
                )


def _parse_dep_refs(payload: list, fallback_repo: str) -> list[dict]:
    """Convert raw gh API payload to a list of IssueRef dicts."""
    refs: list[dict] = []
    for item in payload:
        if isinstance(item, dict):
            if "repo" in item and "issue" in item:
                refs.append({"repo": item["repo"], "issue": item["issue"]})
            elif "number" in item and "repository" in item:
                repo_name = item["repository"].get("full_name", fallback_repo)
                refs.append({"repo": repo_name, "issue": item["number"]})
    return refs


def fetch_dep_list(
    issue_num: int, direction: str, repo: str
) -> tuple[int, str, list[dict]]:
    """Fetch blocked_by or blocking list for one issue via REST.

    Returns (issue_num, direction, list_of_IssueRef_dicts).
    Each IssueRef has shape: {repo: str, issue: int}.
    """
    global _dep_shape_checked

    endpoint = (
        f"repos/{repo}/issues/{issue_num}/dependencies/{direction}"
    )
    result = subprocess.run(
        ["gh", "api", endpoint],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "404" not in stderr and "Not Found" not in stderr:
            print(
                f"  WARN #{issue_num} {direction}: {stderr}",
                file=sys.stderr,
            )
        return (issue_num, direction, [])
    raw = result.stdout.strip()
    if not raw:
        return (issue_num, direction, [])
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print(
            f"  WARN #{issue_num} {direction}: bad JSON {raw!r}",
            file=sys.stderr,
        )
        return (issue_num, direction, [])

    if not isinstance(payload, list) or not payload:
        return (issue_num, direction, [])

    # Shape assertion on first non-empty response (API stability check).
    # Accepted: IssueRef {repo, issue} or raw gh API {number, repository}.
    if not _dep_shape_checked:
        _dep_shape_checked = True
        _check_dep_shape(payload)

    return (issue_num, direction, _parse_dep_refs(payload, repo))


def _discover_from_layout(
    layout: dict, repos: list[str], label_prefix: str, lane_codes: list[str]
) -> set[tuple[str, int]]:
    """Return all (repo, issue_num) pairs to fetch."""
    discovered: set[tuple[str, int]] = set()

    # Label search per repo.
    for repo in repos:
        nums = search_labeled_issues(repo, label_prefix, lane_codes)
        discovered |= {(repo, n) for n in nums}

    # Explicit refs from lanes.
    for lane in layout.get("lanes", []):
        for ref in _iter_lane_refs(lane):
            discovered.add((ref["repo"], ref["issue"]))

    # Explicit refs from standalone.
    for ref in layout.get("standalone", {}).get("order", []):
        if isinstance(ref, dict):
            discovered.add((ref["repo"], ref["issue"]))

    return discovered


def run_fetch(
    layout_path: Path, cache_path: Path, *, verbose: bool = False
) -> int:
    """Main fetch logic. Returns exit code."""
    check_gh()

    if not layout_path.exists():
        print(
            f"ERROR: Layout file not found: {layout_path}",
            file=sys.stderr,
        )
        return 1

    layout = json.loads(Path(layout_path).read_text())
    meta = layout["meta"]
    repos: list[str] = meta["repos"]
    label_prefix: str = meta.get("label_prefix", "graph:")
    lane_codes: list[str] = [
        lane["code"] for lane in layout.get("lanes", [])
    ]

    discovered = _discover_from_layout(layout, repos, label_prefix, lane_codes)

    issues: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        meta_fut: dict = {
            pool.submit(fetch_issue_meta, n, r, label_prefix): (r, n)
            for (r, n) in discovered
        }
        for f in as_completed(meta_fut):
            repo_key, n = meta_fut[f]
            _, title, state, labels = f.result()
            key = f"{repo_key}#{n}"
            issues[key] = {
                "repo": repo_key,
                "number": n,
                "title": title,
                "state": state,
                "labels": labels,
                "blocked_by": [],
                "blocking": [],
            }

        dep_fut: dict = {}
        for r, n in discovered:
            for direction in ("blocked_by", "blocking"):
                dep_fut[
                    pool.submit(fetch_dep_list, n, direction, r)
                ] = (r, n, direction)
        for f in as_completed(dep_fut):
            repo_key, n, direction = dep_fut[f]
            _, _, refs = f.result()
            key = f"{repo_key}#{n}"
            if key in issues:
                issues[key][direction] = refs

    output = {
        "fetched_at": _now_iso(),
        "repos": repos,
        "issues": issues,
    }
    cache_path.write_text(json.dumps(output, indent=2))
    size = cache_path.stat().st_size
    count = len(issues)
    print(f"Written: {cache_path} ({size} bytes, {count} issues)")
    return 0
