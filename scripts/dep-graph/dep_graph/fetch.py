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
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# Module-level event: set after first shape assertion is performed (thread-safe).
_dep_shape_checked = threading.Event()


def check_gh() -> None:
    if not shutil.which("gh"):
        print(
            "ERROR: `gh` CLI not found. Install from https://cli.github.com/",
            file=sys.stderr,
        )
        sys.exit(1)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _derive_label_fields(labels: list[str], label_prefix: str) -> dict:
    """Derive lane_label/standalone/defer from a labels list.

    Keeps gh.json consumers (build, audit) from re-scanning labels each time
    and lets the Untriaged section render without a pre-populated layout entry.
    """
    lane_prefix = f"{label_prefix}lane/"
    lane_label: str | None = None
    for lbl in labels:
        if lbl.startswith(lane_prefix):
            lane_label = lbl[len(lane_prefix) :]
            break
    return {
        "lane_label": lane_label,
        "standalone": f"{label_prefix}standalone" in labels,
        "defer": f"{label_prefix}defer" in labels,
    }


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
) -> set[tuple[str, int]]:
    """List all issues with any <prefix>lane/* or <prefix>standalone label."""
    nums: set[int] = set()
    labels = [f"{label_prefix}standalone"] + [
        f"{label_prefix}lane/{c}" for c in lane_codes
    ]
    for lbl in labels:
        try:
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
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            print(
                f"  WARN list timed out for '{lbl}'",
                file=sys.stderr,
            )
            continue
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
    return {(repo, n) for n in nums}


# Allowlist for size label suffixes: alphanumerics and dash only
_SIZE_ALLOWED = re.compile(r"[^A-Za-z0-9\-]")


def _derive_size_from_labels(labels: list[str]) -> str | None:
    """Extract size string from size:* label, e.g. 'size:S' -> 'S'.

    Applies strict allowlist (alphanumerics + dash) after the 16-char cap
    to ensure cache keys are safe-by-construction.
    """
    for lbl in labels:
        if lbl.startswith("size:"):
            raw = lbl[5:21]  # cap at 16 chars
            return _SIZE_ALLOWED.sub("", raw) or None
    return None


_MILESTONE_ALLOWED = re.compile(r"[^A-Za-z0-9 \-_.#/()]")

# C0/C1 control chars + Unicode bidi override + zero-width chars
_TITLE_UNSAFE = re.compile(
    r"[\x00-\x1f\x7f-\x9f\u200b-\u200d\ufeff\u202a-\u202e\u2066-\u2069]"
)


def _sanitize_milestone(raw: str | None) -> str | None:
    """Strip unsafe chars from a GH milestone title, cap to 64 chars.

    Allowlist: alphanumerics, space, dash, underscore, dot, hash, slash,
    parens. Preserves realistic milestone names like 'v2.4.0 (alpha)',
    'Sprint #3', 'Q2 2026 / Backend'. Everything else is dropped silently.

    Note: HTML injection is prevented at render time via `html.escape`;
    this allowlist is a defense-in-depth guard that limits the cache
    key surface, not the primary XSS defense. Widening the allowlist
    without reviewing the render path is safe but not encouraged.

    Returns None on empty, None, or all-stripped input.
    """
    if not raw:
        return None
    cleaned = _MILESTONE_ALLOWED.sub("", raw).strip()
    if not cleaned:
        return None
    return cleaned[:64]


def _sanitize_title(raw: str | None) -> str:
    """Strip control and bidi override chars from a GH issue title.

    Removes C0/C1 control characters and Unicode bidirectional override
    characters that could be used for cache poisoning or display exploits.
    Preserves all other content verbatim (titles are wide-character by design).

    Returns empty string on None input (consistent with data.get("title", "")).
    """
    if not raw:
        return ""
    return _TITLE_UNSAFE.sub("", raw)


def fetch_issue_meta(
    issue_num: int, repo: str, label_prefix: str
) -> tuple[int, str, str, list[str], str | None, str | None]:
    """Fetch title, state, labels, milestone, size for one issue via REST.

    Returns (issue_num, title, state, label_names, milestone_title, size).
    milestone_title is the GH milestone name (e.g. "M0", "M1") or None.
    size is derived from a size:* label (e.g. "S", "F-lite") or None.
    """
    endpoint = f"repos/{repo}/issues/{issue_num}"
    try:
        result = subprocess.run(
            ["gh", "api", endpoint],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        print(
            f"  WARN #{issue_num} meta: timed out",
            file=sys.stderr,
        )
        return (issue_num, "", "open", [], None, None)
    if result.returncode != 0:
        print(
            f"  WARN #{issue_num} meta: {result.stderr.strip()}",
            file=sys.stderr,
        )
        return (issue_num, "", "open", [], None, None)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"  WARN #{issue_num}: non-JSON response", file=sys.stderr)
        return (issue_num, "", "open", [], None, None)

    if not isinstance(data, dict):
        print(
            f"  WARN #{issue_num}: unexpected meta shape",
            file=sys.stderr,
        )
        return (issue_num, "", "open", [], None, None)

    raw_labels = data.get("labels", [])
    label_names: list[str] = (
        [lbl["name"] for lbl in raw_labels] if isinstance(raw_labels, list) else []
    )
    title: str = _sanitize_title(data.get("title"))
    state: str = data.get("state", data.get("State", "open"))

    # Milestone: read from GH API milestone object
    raw_milestone = data.get("milestone")
    milestone_title: str | None = None
    if isinstance(raw_milestone, dict):
        milestone_title = _sanitize_milestone(raw_milestone.get("title"))

    # Size: derived from size:* label
    size: str | None = _derive_size_from_labels(label_names)

    return (issue_num, title, state, label_names, milestone_title, size)


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
    endpoint = f"repos/{repo}/issues/{issue_num}/dependencies/{direction}"
    try:
        result = subprocess.run(
            ["gh", "api", endpoint],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        print(
            f"  WARN #{issue_num} {direction}: timed out",
            file=sys.stderr,
        )
        return (issue_num, direction, [])
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
    if not _dep_shape_checked.is_set():
        _dep_shape_checked.set()
        _check_dep_shape(payload)

    return (issue_num, direction, _parse_dep_refs(payload, repo))


def _discover_from_layout(
    layout: dict, repos: list[str], label_prefix: str, lane_codes: list[str]
) -> set[tuple[str, int]]:
    """Return all (repo, issue_num) pairs to fetch."""
    discovered: set[tuple[str, int]] = set()

    # Label search — run repos in parallel; each repo spawns several `gh`
    # subprocesses, and they don't depend on each other.
    if repos:
        with ThreadPoolExecutor(max_workers=min(8, len(repos))) as pool:
            futures = [
                pool.submit(search_labeled_issues, repo, label_prefix, lane_codes)
                for repo in repos
            ]
            for f in as_completed(futures):
                discovered |= f.result()

    # Explicit refs from lanes.
    for lane in layout.get("lanes", []):
        for ref in _iter_lane_refs(lane):
            discovered.add((ref["repo"], ref["issue"]))

    # Explicit refs from standalone.
    for ref in layout.get("standalone", {}).get("order", []):
        if isinstance(ref, dict):
            discovered.add((ref["repo"], ref["issue"]))

    return discovered


def run_fetch(layout_path: Path, cache_path: Path, *, verbose: bool = False) -> int:
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
    lane_codes: list[str] = [lane["code"] for lane in layout.get("lanes", [])]

    discovered = _discover_from_layout(layout, repos, label_prefix, lane_codes)

    issues: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        meta_fut: dict = {
            pool.submit(fetch_issue_meta, n, r, label_prefix): (r, n)
            for (r, n) in discovered
        }
        for f in as_completed(meta_fut):
            repo_key, n = meta_fut[f]
            _, title, state, labels, milestone, size = f.result()
            key = f"{repo_key}#{n}"
            entry: dict = {
                "repo": repo_key,
                "number": n,
                "title": title,
                "state": state,
                "labels": labels,
                **_derive_label_fields(labels, label_prefix),
                # milestone: GH milestone title (e.g. "M0", "M1") or None.
                # Always present so downstream consumers can distinguish
                # "not fetched" (key absent) from "no milestone" (None).
                "milestone": milestone,
                # size: derived from size:* label or None. Always present.
                "size": size,
                "blocked_by": [],
                "blocking": [],
            }
            issues[key] = entry

        dep_fut: dict = {}
        for r, n in discovered:
            for direction in ("blocked_by", "blocking"):
                dep_fut[pool.submit(fetch_dep_list, n, direction, r)] = (
                    r,
                    n,
                    direction,
                )
        for f in as_completed(dep_fut):
            repo_key, n, direction = dep_fut[f]
            _, _, refs = f.result()
            key = f"{repo_key}#{n}"
            if key in issues:
                issues[key][direction] = refs

        # Second pass: fetch meta for blocker/blocking refs that aren't in the
        # initial discovery set. Without this, closed-but-unlabeled blockers
        # appear as missing → status_of() treats them as non-closed → false
        # "blocked" status downstream.
        extra: set[tuple[str, int]] = set()
        for entry in issues.values():
            for direction in ("blocked_by", "blocking"):
                for ref in entry.get(direction, []):
                    k = f"{ref['repo']}#{ref['issue']}"
                    if k not in issues:
                        extra.add((ref["repo"], ref["issue"]))

        if extra:
            if verbose:
                print(f"  second pass: fetching {len(extra)} referenced issues")
            extra_fut: dict = {
                pool.submit(fetch_issue_meta, n, r, label_prefix): (r, n)
                for (r, n) in extra
            }
            for f in as_completed(extra_fut):
                repo_key, n = extra_fut[f]
                _, title, state, labels, milestone, size = f.result()
                key = f"{repo_key}#{n}"
                issues[key] = {
                    "repo": repo_key,
                    "number": n,
                    "title": title,
                    "state": state,
                    "labels": labels,
                    **_derive_label_fields(labels, label_prefix),
                    "milestone": milestone,
                    "size": size,
                    "blocked_by": [],
                    "blocking": [],
                }

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
