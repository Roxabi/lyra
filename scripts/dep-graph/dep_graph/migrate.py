"""Migrate layout.json to multi-repo format (uniform {repo, issue} refs)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .schema import LayoutValidationError, validate_layout_dict

_BARE_INT_KEY = re.compile(r"^\d+$")


def run_migrate(layout_path: Path, *, verbose: bool = False) -> int:
    data = json.loads(layout_path.read_text())

    try:
        validate_layout_dict(data)
    except LayoutValidationError:
        pass
    else:
        print("Already migrated.")
        return 0

    _migrate_in_place(data)

    try:
        validate_layout_dict(data)
    except LayoutValidationError as e:
        print(f"ERROR: migration incomplete — {e}")
        return 1

    out = layout_path.with_suffix(layout_path.suffix + ".new")
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.rename(out)
    print(f"Wrote {out}. Review and rename to {layout_path} to commit.")
    return 0


def _wrap(primary: str, n: object) -> object:
    return {"repo": primary, "issue": n} if isinstance(n, int) else n


def _migrate_lanes(lanes: list, primary: str) -> None:
    for lane in lanes:
        lane["order"] = [_wrap(primary, x) for x in lane.get("order", [])]
        pg = lane.get("par_groups", {})
        for k in list(pg.keys()):
            pg[k] = [_wrap(primary, x) for x in pg[k]]
        for band in lane.get("bands", []):
            if isinstance(band.get("before"), int):
                band["before"] = _wrap(primary, band["before"])


def _migrate_extra_deps(extra_deps: dict, primary: str) -> None:
    for direction in ("extra_blocked_by", "extra_blocking"):
        section = extra_deps.get(direction, {})
        for key in list(section.keys()):
            new_key = f"{primary}#{key}" if _BARE_INT_KEY.match(key) else key
            value = section.pop(key)
            section[new_key] = [
                f"{primary}#{v}" if isinstance(v, int) else v for v in value
            ]


def _migrate_in_place(data: dict) -> None:
    meta = data["meta"]

    # meta.repo -> meta.repos
    if "repo" in meta and "repos" not in meta:
        meta["repos"] = [meta.pop("repo")]

    primary: str = meta["repos"][0]

    # meta.issue
    if isinstance(meta.get("issue"), int):
        meta["issue"] = _wrap(primary, meta["issue"])

    _migrate_lanes(data.get("lanes", []), primary)

    # standalone
    if "standalone" in data:
        data["standalone"]["order"] = [
            _wrap(primary, x) for x in data["standalone"].get("order", [])
        ]

    # overrides — rename bare int keys to "owner/repo#N"
    overrides = data.get("overrides", {})
    for key in list(overrides.keys()):
        if _BARE_INT_KEY.match(key):
            overrides[f"{primary}#{key}"] = overrides.pop(key)

    _migrate_extra_deps(data.get("extra_deps", {}), primary)
