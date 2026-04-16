"""Label-drift audit for dep-graph layout.

Reports:
  1. Labeled issues not in any lane order[] (untriaged).
     When a lane has no explicit order[] (auto-derived mode), all issues with
     matching lane_label are considered placed — no untriaged drift for that lane.
  2. Issues in order[] missing their GH lane label.
     Only checked for lanes with explicit order[].
  3. graph:defer label vs defer field in gh.json.
  4. graph:standalone label vs standalone.order[].
     When standalone.order[] is empty/absent, all gh:standalone issues are
     considered placed (auto-derived).

Exit 0 if no drift; exit 1 otherwise.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .derive import is_auto_derived_lane, is_auto_derived_standalone
from .fetch import search_labeled_issues
from .keys import format_key, parse_key


def _lane_label_from_entry(entry: dict, label_prefix: str) -> str | None:
    """Extract lane code from gh.json entry.

    Prefers the pre-computed 'lane_label' field; falls back to scanning 'labels' list.
    """
    if "lane_label" in entry and entry["lane_label"] is not None:
        return entry["lane_label"]
    lane_prefix = f"{label_prefix}lane/"
    for lbl in entry.get("labels", []):
        if isinstance(lbl, str) and lbl.startswith(lane_prefix):
            return lbl[len(lane_prefix) :]
    return None


def _is_standalone(entry: dict, label_prefix: str) -> bool:
    if entry.get("standalone"):
        return True
    return f"{label_prefix}standalone" in entry.get("labels", [])


def _is_defer(entry: dict, label_prefix: str) -> bool:
    if entry.get("defer"):
        return True
    return f"{label_prefix}defer" in entry.get("labels", [])


def _load_inputs(layout_path: Path, cache_path: Path) -> tuple[dict, dict] | None:
    """Read and parse layout.json and gh.json.

    Returns (layout, gh_issues) or None on error.
    """
    if not layout_path.exists():
        print(f"ERROR: {layout_path} not found", file=sys.stderr)
        return None
    if not cache_path.exists():
        print(
            f"ERROR: {cache_path} not found — run dep-graph fetch first",
            file=sys.stderr,
        )
        return None
    layout = json.loads(layout_path.read_text())
    gh_data = json.loads(cache_path.read_text())
    gh_issues: dict[str, dict] = gh_data.get("issues", {})
    return layout, gh_issues


def _check_untriaged(
    labeled: set[tuple[str, int]],
    all_placed: set[tuple[str, int]],
    gh_issues: dict,
    label_prefix: str = "graph:",
) -> bool:
    """Report labeled issues not in any lane order[]. Returns drift_found."""
    untriaged: list[tuple[str, int, str, str]] = []
    for key, entry in gh_issues.items():
        if not entry:
            continue
        try:
            repo, n = parse_key(key)
        except ValueError:
            continue
        ref = (repo, n)
        lane_lbl = _lane_label_from_entry(entry, label_prefix)
        if lane_lbl is not None and ref not in all_placed and not entry.get("hidden"):
            title = entry.get("title", "")[:60]
            untriaged.append((repo, n, lane_lbl, title))

    if untriaged:
        print("Labeled but not in any lane order[]:")
        for repo, n, lbl, title in sorted(untriaged):
            print(f"  {format_key(repo, n)} (graph:lane/{lbl})   {title}")
    else:
        print("Labeled but not in any lane order[]:  (none)")
    print()
    return bool(untriaged)


def _check_label_mismatches(
    layout_lane_of: dict[tuple[str, int], str],
    gh_issues: dict,
    label_prefix: str,
) -> bool:
    """Report issues in order[] with wrong/missing GH lane label.

    Returns drift_found.
    """
    missing_label: list[tuple[str, int, str, str]] = []
    for (repo, n), expected_lane in layout_lane_of.items():
        key = format_key(repo, n)
        entry = gh_issues.get(key)
        if entry is None:
            missing_label.append((repo, n, expected_lane, "(not in gh.json)"))
            continue
        gh_lane = _lane_label_from_entry(entry, label_prefix)
        if gh_lane != expected_lane:
            gh_lane_str = gh_lane if gh_lane else "(no lane label)"
            missing_label.append((repo, n, expected_lane, f"has label: {gh_lane_str}"))

    if missing_label:
        print("In order[] but wrong/missing GH label:")
        for repo, n, expected, note in sorted(missing_label):
            k = format_key(repo, n)
            print(f"  {k} (expected {label_prefix}lane/{expected})   {note}")
    else:
        print("In order[] but wrong/missing GH label:  (none)")
    print()
    return bool(missing_label)


def _collect_gh_deferred(gh_issues: dict, label_prefix: str) -> set[tuple[str, int]]:
    """Collect (repo, issue) tuples flagged defer in gh.json."""
    result: set[tuple[str, int]] = set()
    for key, e in gh_issues.items():
        if e and _is_defer(e, label_prefix):
            try:
                repo, n = parse_key(key)
                result.add((repo, n))
            except ValueError:
                pass
    return result


def _collect_layout_deferred(layout: dict) -> set[tuple[str, int]]:
    """Collect (repo, issue) tuples marked defer in layout epic fields."""
    result: set[tuple[str, int]] = set()
    for lane in layout.get("lanes", []):
        epic = lane.get("epic")
        if epic and epic.get("defer"):
            # Epic refs: plain int in old layout; IssueRef dict in new layout
            epic_repo = epic.get("repo")
            if epic_repo:
                result.add((epic_repo, epic["issue"]))
    return result


def _collect_layout_ref_set(layout: dict) -> set[tuple[str, int]]:
    """Collect all (repo, issue) tuples from lane order[].

    Only includes lanes with explicit order[]. Auto-derived lanes (no order key)
    are not included here; _build_layout_sets handles those separately.
    """
    result: set[tuple[str, int]] = set()
    for lane in layout.get("lanes", []):
        for ref in lane.get("order", []):
            if isinstance(ref, dict):
                result.add((ref["repo"], ref["issue"]))
    return result


def _check_defer(
    gh_issues: dict,
    layout: dict,
    label_prefix: str,
    auto_placed: set[tuple[str, int]] | None = None,
) -> bool:
    """Report defer label drift. Returns drift_found.

    In auto-derive mode, GH defer labels drive card status directly —
    issues in auto-derived lanes with graph:defer labels are correct by
    definition (no layout sync needed).  auto_placed carries all such
    auto-lane issues so they are excluded from the "only_in_gh" bucket.
    """
    defer_lbl = f"{label_prefix}defer"
    gh_deferred = _collect_gh_deferred(gh_issues, label_prefix)
    layout_deferred = _collect_layout_deferred(layout)
    layout_ref_set = _collect_layout_ref_set(layout)
    # Auto-derived lanes: defer is GH-driven, no layout representation needed
    already_ok = layout_ref_set | (auto_placed or set())

    only_in_gh = gh_deferred - layout_deferred - already_ok
    only_in_layout = layout_deferred - gh_deferred
    if only_in_gh or only_in_layout:
        print(f"{defer_lbl} label vs layout defer field:")
        for repo, n in sorted(only_in_gh):
            k = format_key(repo, n)
            print(f"  {k} has GH defer label but not in layout deferred set")
        for repo, n in sorted(only_in_layout):
            k = format_key(repo, n)
            print(f"  {k} in layout defer but missing GH {defer_lbl} label")
    else:
        print(f"{defer_lbl} label vs layout:  (in sync)")
    print()
    return bool(only_in_gh or only_in_layout)


def _check_standalone(
    gh_issues: dict,
    layout: dict,
    label_prefix: str,
) -> bool:
    """Report standalone label drift. Returns drift_found.

    When standalone.order[] is absent/empty, auto-derive mode is active:
    all gh:standalone issues are considered placed — no drift for that direction.
    """
    standalone_lbl = f"{label_prefix}standalone"
    auto_mode = is_auto_derived_standalone(layout)

    if auto_mode:
        print(f"{standalone_lbl} label vs standalone.order[]:  (auto-derived, skipped)")
        print()
        return False

    # gh_standalone: set of (repo, issue) tuples where standalone flag is set
    gh_standalone: set[tuple[str, int]] = set()
    for key, e in gh_issues.items():
        if e and _is_standalone(e, label_prefix):
            try:
                repo, n = parse_key(key)
                gh_standalone.add((repo, n))
            except ValueError:
                pass

    # standalone_order: set of (repo, issue) tuples from layout standalone.order[]
    standalone_order: set[tuple[str, int]] = set()
    for ref in layout.get("standalone", {}).get("order", []):
        if isinstance(ref, dict):
            standalone_order.add((ref["repo"], ref["issue"]))

    only_in_gh_sa = gh_standalone - standalone_order
    only_in_layout_sa = standalone_order - gh_standalone

    if only_in_gh_sa or only_in_layout_sa:
        print(f"{standalone_lbl} label vs standalone.order[]:")
        for repo, n in sorted(only_in_gh_sa):
            key = format_key(repo, n)
            title = gh_issues.get(key, {}).get("title", "")[:50]
            print(
                f"  {key} has GH standalone label"
                f" but not in standalone.order[]   {title}"
            )
        for repo, n in sorted(only_in_layout_sa):
            k = format_key(repo, n)
            print(f"  {k} in standalone.order[] but missing GH {standalone_lbl} label")
    else:
        print(f"{standalone_lbl} label vs standalone.order[]:  (in sync)")
    print()
    return bool(only_in_gh_sa or only_in_layout_sa)


def _build_layout_sets(
    layout: dict,
) -> tuple[
    dict[tuple[str, int], str], set[tuple[str, int]], set[tuple[str, int]], set[str]
]:
    """Return (layout_lane_of, standalone_set, epic_set, auto_lane_codes).

    layout_lane_of: explicit order[] members only.
    auto_lane_codes: set of lane codes that have NO explicit order[] (auto-derived);
    callers combine this with GH issue labels via `_collect_auto_placed` to
    expand auto-derived lanes into the full placed set.
    """
    layout_lane_of: dict[tuple[str, int], str] = {}
    auto_lane_codes: set[str] = set()
    for lane in layout.get("lanes", []):
        code = lane["code"]
        if is_auto_derived_lane(lane):
            auto_lane_codes.add(code)
        else:
            for ref in lane.get("order", []):
                if isinstance(ref, dict):
                    layout_lane_of[(ref["repo"], ref["issue"])] = code

    standalone_set: set[tuple[str, int]] = set()
    for ref in layout.get("standalone", {}).get("order", []):
        if isinstance(ref, dict):
            standalone_set.add((ref["repo"], ref["issue"]))

    epic_set: set[tuple[str, int]] = set()
    for lane in layout.get("lanes", []):
        epic = lane.get("epic")
        if epic:
            epic_repo = epic.get("repo")
            if epic_repo:
                epic_set.add((epic_repo, epic["issue"]))

    return layout_lane_of, standalone_set, epic_set, auto_lane_codes


def _collect_auto_placed(
    gh_issues: dict,
    auto_lane_codes: set[str],
    standalone_set: set[tuple[str, int]],
    layout: dict,
    label_prefix: str,
) -> tuple[set[tuple[str, int]], set[tuple[str, int]]]:
    """Return (auto_placed, updated_standalone_set) for auto-derived lanes/standalone.

    auto_placed: issues in lanes that have no explicit order[] (GH-label driven).
    standalone_set: extended with gh:standalone issues when layout.standalone is empty.
    """
    auto_placed: set[tuple[str, int]] = set()
    if auto_lane_codes:
        for key, entry in gh_issues.items():
            if not entry:
                continue
            ll = _lane_label_from_entry(entry, label_prefix)
            if ll in auto_lane_codes:
                try:
                    repo, n = parse_key(key)
                    auto_placed.add((repo, n))
                except ValueError:
                    pass

    standalone_auto = is_auto_derived_standalone(layout)
    if standalone_auto:
        updated: set[tuple[str, int]] = set(standalone_set)
        for key, entry in gh_issues.items():
            if entry and _is_standalone(entry, label_prefix):
                try:
                    repo, n = parse_key(key)
                    updated.add((repo, n))
                except ValueError:
                    pass
        return auto_placed, updated

    return auto_placed, standalone_set


def _check_placement(
    layout_lane_of: dict[tuple[str, int], str],
    labeled: set[tuple[str, int]],
    all_placed: set[tuple[str, int]],
    gh_issues: dict,
    label_prefix: str,
) -> bool:
    """Check untriaged and label-mismatch drift. Returns drift_found."""
    drift_found = _check_untriaged(labeled, all_placed, gh_issues, label_prefix)
    if layout_lane_of:
        return drift_found | _check_label_mismatches(
            layout_lane_of, gh_issues, label_prefix
        )
    print(
        "In order[] but wrong/missing GH label:  (all lanes auto-derived, skipped)"
    )
    print()
    return drift_found


def _check_meta(
    gh_issues: dict,
    layout: dict,
    label_prefix: str,
    auto_placed: set[tuple[str, int]],
) -> bool:
    """Check defer and standalone drift. Returns drift_found."""
    drift_found = _check_defer(
        gh_issues, layout, label_prefix, auto_placed=auto_placed
    )
    drift_found |= _check_standalone(gh_issues, layout, label_prefix)
    return drift_found


def run_audit(layout_path: Path, cache_path: Path, *, verbose: bool = False) -> int:
    """Run the drift audit. Returns exit code (0 = clean, 1 = drift found)."""
    result = _load_inputs(layout_path, cache_path)
    if result is None:
        return 1
    layout, gh_issues = result

    meta = layout.get("meta", {})
    label_prefix: str = meta.get("label_prefix", "graph:")
    repos: list[str] = meta.get("repos", [])
    lane_codes: list[str] = [lane["code"] for lane in layout.get("lanes", [])]

    labeled: set[tuple[str, int]] = set()
    for repo in repos:
        labeled |= search_labeled_issues(repo, label_prefix, lane_codes)

    layout_lane_of, standalone_set, epic_set, auto_lane_codes = _build_layout_sets(
        layout
    )
    auto_placed, standalone_set = _collect_auto_placed(
        gh_issues, auto_lane_codes, standalone_set, layout, label_prefix
    )
    all_placed: set[tuple[str, int]] = (
        set(layout_lane_of.keys()) | standalone_set | epic_set | auto_placed
    )

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"LABEL DRIFT AUDIT — {date_str}")
    if auto_lane_codes:
        print(f"  (auto-derived lanes: {', '.join(sorted(auto_lane_codes))})")
    print()

    drift_found = _check_placement(
        layout_lane_of, labeled, all_placed, gh_issues, label_prefix
    )
    drift_found |= _check_meta(gh_issues, layout, label_prefix, auto_placed)

    if drift_found:
        print("RESULT: drift detected — exit 1")
        return 1
    print("RESULT: clean — exit 0")
    return 0
