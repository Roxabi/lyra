"""Anchor and spacer computation for cross-lane alignment.

This module handles the computation of anchor positions and spacer insertions
used to align cards across lanes in the dependency graph layout.

Functions:
    compute_slot_index: 1-based slot index of target_issue in a flat row list
    inject_spacers: Insert synthetic spacer rows for anchor-based alignment

Internal helpers:
    _collect_anchors: Pass 1 — collect anchor positions from all lanes
    _compute_lane_insertions: Compute (insert_before_index, count) pairs per lane
"""

from __future__ import annotations

import copy
import sys


def compute_slot_index(flat_rows: list[dict], target_issue: int) -> int:
    """1-based slot index of target_issue in a flat row list (bands + cards)."""
    idx = 0
    for row in flat_rows:
        if row.get("band"):
            idx += 1
        elif "issue" in row:
            idx += 1
            if row["issue"] == target_issue:
                return idx
    return -1


def _collect_anchors(flat_lanes: list[dict]) -> dict[str, tuple[str, int]]:
    """Pass 1: collect anchor positions from all lanes."""
    anchors: dict[str, tuple[str, int]] = {}
    for fl in flat_lanes:
        code = fl["code"]
        for row in fl["flat_rows"]:
            if "issue" not in row or "anchor" not in row:
                continue
            slot = compute_slot_index(fl["flat_rows"], row["issue"])
            if slot == -1:
                print(
                    f"WARN: anchor '{row['anchor']}' issue #{row['issue']} "
                    f"not found in lane {code}",
                    file=sys.stderr,
                )
                continue
            anchors[row["anchor"]] = (code, slot)
    return anchors


def _compute_lane_insertions(
    fl: dict,
    anchors: dict[str, tuple[str, int]],
) -> list[tuple[int, int]]:
    """Compute (insert_before_index, count) pairs for one lane."""
    code = fl["code"]
    rows = fl["flat_rows"]
    insertions: list[tuple[int, int]] = []

    for i, row in enumerate(rows):
        if "issue" not in row or "anchor_after" not in row:
            continue
        anchor_id = row["anchor_after"]
        if anchor_id not in anchors:
            print(
                f"WARN: anchor_after '{anchor_id}' in lane {code}: unknown anchor",
                file=sys.stderr,
            )
            continue
        _, ref_slot = anchors[anchor_id]
        target_slot = compute_slot_index(rows, row["issue"])
        if target_slot == -1:
            continue

        pg = row.get("par_group")
        insert_before = i
        if pg is not None:
            for j in range(i - 1, -1, -1):
                if rows[j].get("par_group") == pg:
                    insert_before = j
                else:
                    break

        needed = (ref_slot + 1) - target_slot
        if needed <= 0:
            print(
                f"WARN: anchor_after '{anchor_id}' lane {code}: "
                f"slot {target_slot} >= {ref_slot}+1",
                file=sys.stderr,
            )
            continue
        insertions.append((insert_before, needed))
    return insertions


def inject_spacers(flat_lanes: list[dict]) -> list[dict]:
    """Insert synthetic spacer rows for anchor-based cross-lane alignment."""
    flat_lanes = copy.deepcopy(flat_lanes)

    anchors = _collect_anchors(flat_lanes)
    if not anchors:
        return flat_lanes

    # Pass 2: insert spacers
    for fl in flat_lanes:
        rows = fl["flat_rows"]
        insertions = _compute_lane_insertions(fl, anchors)
        for insert_before, count in sorted(
            insertions, key=lambda x: x[0], reverse=True
        ):
            rows[insert_before:insert_before] = [{"spacer": True}] * count

    return flat_lanes
