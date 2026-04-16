"""Lane flattening utilities for dep-graph.

Converts new-schema lanes (with order, par_groups, bands) into flat_rows
consumed by render and inject_spacers functions.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from .keys import format_key


@dataclass(frozen=True, slots=True)
class FlatLaneContext:
    """Context for rendering a flattened lane."""

    fl: dict
    lane_of: dict[tuple[str, int], str]
    gh_issues: dict
    overrides: dict
    extra_deps: dict
    title_rules: list[dict] | None
    primary_repo: str = ""


def _check_drift(repo: str, n: int, code: str, gh_issues: dict) -> None:
    """Emit stderr warning if GH lane label differs from layout lane."""
    gh_entry = gh_issues.get(format_key(repo, n))
    if not gh_entry:
        return
    gh_lane = gh_entry.get("lane_label")
    if gh_lane and gh_lane != code:
        print(
            f"WARN drift: layout says {format_key(repo, n)} → lane {code},"
            f" gh label says {gh_lane}",
            file=sys.stderr,
        )


def _build_issue_to_pg(pg_map: dict) -> dict[tuple[str, int], str]:
    """Build (repo, issue) → par_group_id map from lane par_groups."""
    issue_to_pg: dict[tuple[str, int], str] = {}
    for gid, members in pg_map.items():
        for ref in members:
            if isinstance(ref, dict):
                issue_to_pg[(ref["repo"], ref["issue"])] = gid
            else:
                issue_to_pg[("", int(ref))] = gid
    return issue_to_pg


def _build_band_before(bands: list) -> dict[tuple[str, int] | int, str]:
    """Build ref → band text map from lane bands list."""
    band_before: dict[tuple[str, int] | int, str] = {}
    for b in bands:
        bef = b["before"]
        if isinstance(bef, dict):
            band_before[(bef["repo"], bef["issue"])] = b["text"]
        else:
            band_before[int(bef)] = b["text"]
    return band_before


def _flatten_order_item(
    item: dict | int,
    overrides: dict,
    issue_to_pg: dict[tuple[str, int], str],
    band_before: dict[tuple[str, int] | int, str],
) -> tuple[dict | None, dict]:
    """Convert one order item to an optional band row + an issue row."""
    if isinstance(item, dict):
        repo: str = item["repo"]
        n: int = item["issue"]
        ref_key: tuple[str, int] | int = (repo, n)
    else:
        repo = ""
        n = int(item)
        ref_key = n

    band_row: dict | None = None
    if ref_key in band_before:
        band_row = {"band": band_before[ref_key]}

    ovr_key = f"{repo}#{n}" if repo else str(n)
    ovr = overrides.get(ovr_key, {})
    row: dict = {"issue": n, "repo": repo}
    pg = issue_to_pg.get((repo, n)) or issue_to_pg.get(("", n))
    if pg is not None:
        row["par_group"] = pg
    if "anchor" in ovr:
        row["anchor"] = ovr["anchor"]
    if "anchor_after" in ovr:
        row["anchor_after"] = ovr["anchor_after"]
    return band_row, row


def flatten_lane(
    lane: dict,
    overrides: dict,
    label_drift_check: bool,
    gh_issues: dict,
) -> dict:
    """Convert new-schema lane into flat_rows consumed by render/inject."""
    code = lane["code"]
    order = lane.get("order", [])
    issue_to_pg = _build_issue_to_pg(lane.get("par_groups", {}))
    band_before = _build_band_before(lane.get("bands", []))

    flat_rows: list[dict] = []
    for item in order:
        band_row, row = _flatten_order_item(item, overrides, issue_to_pg, band_before)
        if band_row is not None:
            flat_rows.append(band_row)
        flat_rows.append(row)
        if label_drift_check and row.get("repo"):
            _check_drift(row["repo"], row["issue"], code, gh_issues)

    return {
        "code": code,
        "name": lane["name"],
        "color": lane["color"],
        "epic": lane.get("epic"),
        "flat_rows": flat_rows,
    }
