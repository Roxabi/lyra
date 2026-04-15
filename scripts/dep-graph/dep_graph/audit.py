"""Label-drift audit for dep-graph layout.

Reports:
  1. Labeled issues not in any lane order[] (untriaged).
  2. Issues in order[] missing their GH lane label.
  3. graph:defer label vs defer field in gh.json.
  4. graph:standalone label vs standalone.order[].

Exit 0 if no drift; exit 1 otherwise.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


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
    gh_issues: dict,
    all_placed: set[int],
    label_prefix: str,
) -> bool:
    """Report labeled issues not in any lane order[]. Returns drift_found."""
    untriaged: list[tuple[int, str, str]] = []
    for n_str, entry in gh_issues.items():
        if not entry:
            continue
        n = int(n_str)
        lane_lbl = entry.get("lane_label")
        if lane_lbl is not None and n not in all_placed and not entry.get("hidden"):
            title = entry.get("title", "")[:60]
            untriaged.append((n, lane_lbl, title))

    if untriaged:
        print("Labeled but not in any lane order[]:")
        for n, lbl, title in sorted(untriaged):
            print(f"  #{n} ({label_prefix}lane/{lbl})   {title}")
    else:
        print("Labeled but not in any lane order[]:  (none)")
    print()
    return bool(untriaged)


def _check_label_mismatches(
    gh_issues: dict,
    layout_lane_of: dict[int, str],
    label_prefix: str,
) -> bool:
    """Report issues in order[] with wrong/missing GH lane label.

    Returns drift_found.
    """
    missing_label: list[tuple[int, str, str]] = []
    for n, expected_lane in layout_lane_of.items():
        entry = gh_issues.get(str(n))
        if entry is None:
            missing_label.append((n, expected_lane, "(not in gh.json)"))
            continue
        gh_lane = entry.get("lane_label")
        if gh_lane != expected_lane:
            gh_lane_str = gh_lane if gh_lane else "(no lane label)"
            missing_label.append((n, expected_lane, f"has label: {gh_lane_str}"))

    if missing_label:
        print("In order[] but wrong/missing GH label:")
        for n, expected, note in sorted(missing_label):
            print(f"  #{n} (expected {label_prefix}lane/{expected})   {note}")
    else:
        print("In order[] but wrong/missing GH label:  (none)")
    print()
    return bool(missing_label)


def _check_defer(
    gh_issues: dict,
    layout: dict,
    label_prefix: str,
) -> bool:
    """Report defer label drift. Returns drift_found."""
    defer_lbl = f"{label_prefix}defer"
    gh_deferred = {int(n) for n, e in gh_issues.items() if e and e.get("defer")}
    layout_deferred: set[int] = set()
    for lane in layout.get("lanes", []):
        epic = lane.get("epic")
        if epic and epic.get("defer"):
            layout_deferred.add(epic["issue"])

    layout_lane_of: dict[int, str] = {}
    for lane in layout.get("lanes", []):
        for n in lane.get("order", []):
            layout_lane_of[n] = lane["code"]

    only_in_gh = gh_deferred - layout_deferred - set(layout_lane_of.keys())
    only_in_layout = layout_deferred - gh_deferred
    if only_in_gh or only_in_layout:
        print(f"{defer_lbl} label vs layout defer field:")
        for n in sorted(only_in_gh):
            print(f"  #{n} has GH defer label but not in layout deferred set")
        for n in sorted(only_in_layout):
            print(f"  #{n} in layout defer but missing GH {defer_lbl} label")
    else:
        print(f"{defer_lbl} label vs layout:  (in sync)")
    print()
    return bool(only_in_gh or only_in_layout)


def _check_standalone(
    gh_issues: dict,
    label_prefix: str,
    standalone_order: set[int],
) -> bool:
    """Report standalone label drift. Returns drift_found."""
    standalone_lbl = f"{label_prefix}standalone"
    gh_standalone = {int(n) for n, e in gh_issues.items() if e and e.get("standalone")}
    only_in_gh_sa = gh_standalone - standalone_order
    only_in_layout_sa = standalone_order - gh_standalone

    if only_in_gh_sa or only_in_layout_sa:
        print(f"{standalone_lbl} label vs standalone.order[]:")
        for n in sorted(only_in_gh_sa):
            title = gh_issues.get(str(n), {}).get("title", "")[:50]
            print(
                f"  #{n} has GH standalone label"
                f" but not in standalone.order[]   {title}"
            )
        for n in sorted(only_in_layout_sa):
            print(f"  #{n} in standalone.order[] but missing GH {standalone_lbl} label")
    else:
        print(f"{standalone_lbl} label vs standalone.order[]:  (in sync)")
    print()
    return bool(only_in_gh_sa or only_in_layout_sa)


def run_audit(layout_path: Path, cache_path: Path, *, verbose: bool = False) -> int:
    """Run the drift audit. Returns exit code (0 = clean, 1 = drift found)."""
    result = _load_inputs(layout_path, cache_path)
    if result is None:
        return 1
    layout, gh_issues = result

    label_prefix: str = layout.get("meta", {}).get("label_prefix", "graph:")

    # Build: issue → expected lane code from layout
    layout_lane_of: dict[int, str] = {}
    for lane in layout.get("lanes", []):
        for n in lane.get("order", []):
            layout_lane_of[n] = lane["code"]

    standalone_order: set[int] = set(layout.get("standalone", {}).get("order", []))
    epic_issues: set[int] = {
        lane["epic"]["issue"] for lane in layout.get("lanes", []) if lane.get("epic")
    }
    all_placed: set[int] = set(layout_lane_of.keys()) | standalone_order | epic_issues

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"LABEL DRIFT AUDIT — {date_str}")
    print()

    drift_found = False
    drift_found |= _check_untriaged(gh_issues, all_placed, label_prefix)
    drift_found |= _check_label_mismatches(gh_issues, layout_lane_of, label_prefix)
    drift_found |= _check_defer(gh_issues, layout, label_prefix)
    drift_found |= _check_standalone(gh_issues, label_prefix, standalone_order)

    if drift_found:
        print("RESULT: drift detected — exit 1")
        return 1
    else:
        print("RESULT: clean — exit 0")
        return 0
