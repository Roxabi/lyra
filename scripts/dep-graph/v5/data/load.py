"""Load layout.json + gh.json, derive matrix + counts, return GraphData."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .derive import (
    build_matrix,
    compute_depth,
    compute_visible,
    epic_keys,
    lane_by_code,
)
from .model import (
    COLUMN_GROUPS,
    MILESTONES,
    EpicMeta,
    GraphData,
    Lane,
    parse_column_groups,
    parse_milestones,
)

FORGE = Path.home() / ".roxabi/forge/lyra/visuals"
LAYOUT_PATH = FORGE / "lyra-v2-dependency-graph.layout.json"
CACHE_PATH = FORGE / "lyra-v2-dependency-graph.gh.json"


def _parse_lane(raw: dict[str, Any]) -> Lane:
    epic = raw.get("epic")
    epic_meta: EpicMeta | None = None
    if epic:
        epic_meta = EpicMeta(
            issue=epic.get("issue"),
            label=epic.get("label", ""),
            tag=epic.get("tag", ""),
        )
    return Lane(
        code=raw["code"],
        name=raw["name"],
        color=raw.get("color", raw["code"]),
        epic=epic_meta,
    )


def load(
    layout_path: Path | None = None,
    cache_path: Path | None = None,
) -> GraphData:
    layout_path = layout_path or LAYOUT_PATH
    cache_path = cache_path or CACHE_PATH
    try:
        layout_raw = layout_path.read_text()
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"layout not found: {layout_path}") from exc
    try:
        cache_raw = cache_path.read_text()
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"gh cache not found: {cache_path}") from exc
    return load_from_dicts(json.loads(layout_raw), json.loads(cache_raw))


def load_from_dicts(layout: dict[str, Any], gh: dict[str, Any]) -> GraphData:
    lanes = [_parse_lane(raw) for raw in layout["lanes"]]
    issues = gh.get("issues", {})
    primary_repo = layout["meta"]["repos"][0]
    ekeys = epic_keys(layout["lanes"], primary_repo)
    depth = compute_depth(issues)
    visible = compute_visible(issues, primary_repo)

    column_groups = (
        parse_column_groups(layout["column_groups"])
        if "column_groups" in layout
        else list(COLUMN_GROUPS)
    )
    milestones = (
        parse_milestones(layout["milestones"])
        if "milestones" in layout
        else list(MILESTONES)
    )

    data = GraphData(
        meta=layout["meta"],
        lanes=lanes,
        lane_by_code=lane_by_code(lanes),
        issues=issues,
        column_groups=column_groups,
        milestones=milestones,
        epic_keys=ekeys,
        visible=visible,
        depth_by_key=depth,
    )
    matrix, counts, total = build_matrix(data)
    data.matrix = matrix
    data.counts = counts
    data.total = total
    return data
