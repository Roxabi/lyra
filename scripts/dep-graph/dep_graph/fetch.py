"""Fetch GitHub issue data for a dep-graph layout.

Reads layout.json (meta.repo, meta.label_prefix), queries GitHub via `gh` CLI,
writes gh.json.

Issue discovery (union of):
  1. All issues labeled <prefix>lane/* or <prefix>standalone (via gh issue list).
  2. Explicit numbers from layout.json (epics, extra_deps targets).

Per issue: REST metadata + /dependencies/blocked_by + /dependencies/blocking.

Emits gh.json with enriched lane_label / defer / standalone / hidden fields
derived from GitHub labels.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path


def check_gh() -> None:
    if not shutil.which("gh"):
        print(
            "ERROR: `gh` CLI not found. Install from https://cli.github.com/",
            file=sys.stderr,
        )
        sys.exit(1)


def collect_explicit_numbers(layout: dict) -> set[int]:
    """Collect issue numbers explicitly listed in layout.json."""
    nums: set[int] = set()
    for lane in layout.get("lanes", []):
        if lane.get("epic"):
            nums.add(lane["epic"]["issue"])
        for n in lane.get("order", []):
            nums.add(n)
    standalone = layout.get("standalone", {})
    for n in standalone.get("order", []):
        nums.add(n)
    extra_deps = layout.get("extra_deps", {})
    for lst in extra_deps.get("extra_blocked_by", {}).values():
        nums.update(lst)
    for lst in extra_deps.get("extra_blocking", {}).values():
        nums.update(lst)
    return nums


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
                print(f"  WARN bad JSON for '{lbl}': {raw[:80]}", file=sys.stderr)
    return nums


def fetch_issue_meta(
    issue_num: int, repo: str, label_prefix: str
) -> tuple[int, dict | None]:
    """Fetch title, state, labels for one issue/PR via REST."""
    endpoint = f"repos/{repo}/issues/{issue_num}"
    result = subprocess.run(
        ["gh", "api", endpoint],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  WARN #{issue_num} meta: {result.stderr.strip()}", file=sys.stderr)
        return (issue_num, None)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"  WARN #{issue_num}: non-JSON response", file=sys.stderr)
        return (issue_num, None)

    label_names = [lbl["name"] for lbl in data.get("labels", [])]
    lane_prefix = f"{label_prefix}lane/"
    lane_label: str | None = None
    for lbl in label_names:
        if lbl.startswith(lane_prefix):
            lane_label = lbl.removeprefix(lane_prefix)
            break
    defer = f"{label_prefix}defer" in label_names
    standalone = f"{label_prefix}standalone" in label_names
    hidden = f"{label_prefix}hide" in label_names

    return (
        issue_num,
        {
            "number": issue_num,
            "title": data.get("title", ""),
            "state": data.get("state", "open"),
            "labels": label_names,
            "lane_label": lane_label,
            "defer": defer,
            "standalone": standalone,
            "hidden": hidden,
            "blocked_by": [],
            "blocking": [],
        },
    )


def fetch_dep_list(
    issue_num: int, direction: str, repo: str
) -> tuple[int, str, list[int]]:
    """Fetch blocked_by or blocking list for one issue via REST."""
    endpoint = f"repos/{repo}/issues/{issue_num}/dependencies/{direction}"
    result = subprocess.run(
        ["gh", "api", endpoint, "--jq", "[.[].number]"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "404" not in stderr and "Not Found" not in stderr:
            print(f"  WARN #{issue_num} {direction}: {stderr}", file=sys.stderr)
        return (issue_num, direction, [])
    raw = result.stdout.strip()
    if not raw:
        return (issue_num, direction, [])
    try:
        return (issue_num, direction, json.loads(raw))
    except json.JSONDecodeError:
        print(f"  WARN #{issue_num} {direction}: bad JSON {raw!r}", file=sys.stderr)
        return (issue_num, direction, [])


def run_fetch(layout_path: Path, out_path: Path, *, verbose: bool = False) -> int:
    """Main fetch logic. Returns exit code."""
    check_gh()

    if not layout_path.exists():
        print(f"ERROR: Layout file not found: {layout_path}", file=sys.stderr)
        return 1

    layout = json.loads(layout_path.read_text())
    meta = layout.get("meta", {})
    repo: str = meta.get("repo", "Roxabi/lyra")
    label_prefix: str = meta.get("label_prefix", "graph:")
    lane_codes: list[str] = [lane["code"] for lane in layout.get("lanes", [])]

    print(f"Fetching for repo={repo}, label_prefix={label_prefix!r}")

    print("Searching labeled issues on GitHub...")
    labeled = search_labeled_issues(repo, label_prefix, lane_codes)
    explicit = collect_explicit_numbers(layout)
    all_nums = sorted(labeled | explicit)
    print(f"  labeled={len(labeled)}, explicit={len(explicit)}, union={len(all_nums)}")

    print(f"Fetching metadata ({len(all_nums)} calls, up to 8 parallel)...")
    issues: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        meta_futures = {
            pool.submit(fetch_issue_meta, n, repo, label_prefix): n for n in all_nums
        }
        done_count = 0
        for future in as_completed(meta_futures):
            n, entry = future.result()
            done_count += 1
            if entry is not None:
                issues[str(n)] = entry
            else:
                print(f"  WARN #{n}: skipped", file=sys.stderr)
            if done_count % 16 == 0:
                print(f"  {done_count}/{len(all_nums)} metadata done...")

    print(f"Metadata: {len(issues)} ok, {len(all_nums) - len(issues)} failed.")

    valid_nums = sorted(issues.keys(), key=int)
    tasks = [(int(n), d) for n in valid_nums for d in ("blocked_by", "blocking")]
    print(f"Fetching deps ({len(tasks)} calls, up to 8 parallel)...")
    with ThreadPoolExecutor(max_workers=8) as pool:
        dep_futures = {
            pool.submit(fetch_dep_list, n, d, repo): (n, d) for n, d in tasks
        }
        done_count = 0
        for future in as_completed(dep_futures):
            issue_num, direction, dep_nums = future.result()
            done_count += 1
            if dep_nums:
                issues[str(issue_num)][direction] = dep_nums
            if done_count % 20 == 0:
                print(f"  {done_count}/{len(tasks)} dep calls done...")

    print("All dep calls complete.")
    output = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "issues": issues,
    }
    out_path.write_text(json.dumps(output, indent=2))
    print(
        f"Written: {out_path} ({out_path.stat().st_size} bytes, {len(issues)} issues)"
    )
    return 0
