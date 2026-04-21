"""JSON schema validation for layout.json (multi-repo format).

Loads layout.schema.json from the package root (scripts/dep-graph/).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft7Validator

from .keys import parse_key

_schema_cache: dict | None = None
_SCHEMA_PATH = Path(__file__).parent.parent / "layout.schema.json"


class LayoutValidationError(Exception):
    """Structured validation error with field path and message."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


def _load_schema() -> dict:
    global _schema_cache
    if _schema_cache is None:
        _schema_cache = json.loads(_SCHEMA_PATH.read_text())
    # cast (not assert) — assert is stripped under `python -O`
    return cast(dict, _schema_cache)


def _assert_repo(ref: Any, path: str, allowed_repos: set[str]) -> None:
    """Raise LayoutValidationError if *ref* is an IssueRef with an undeclared repo."""
    if isinstance(ref, dict) and "repo" in ref and "issue" in ref:
        if ref["repo"] not in allowed_repos:
            sorted_repos = sorted(allowed_repos)
            raise LayoutValidationError(
                path,
                f"repo '{ref['repo']}' not in meta.repos[] ({sorted_repos})",
            )


def _check_lanes(lanes: list, allowed_repos: set[str]) -> None:
    for li, lane in enumerate(lanes):
        for oi, ref in enumerate(lane.get("order", [])):
            _assert_repo(ref, f"lanes[{li}].order[{oi}]", allowed_repos)
        for pg_key, pg_list in lane.get("par_groups", {}).items():
            for pi, ref in enumerate(pg_list):
                loc = f"lanes[{li}].par_groups.{pg_key}[{pi}]"
                _assert_repo(ref, loc, allowed_repos)
        for bi, band in enumerate(lane.get("bands", [])):
            if isinstance(band.get("before"), dict):
                loc = f"lanes[{li}].bands[{bi}].before"
                _assert_repo(band["before"], loc, allowed_repos)


def _check_keyed_section(section: dict, prefix: str, allowed_repos: set[str]) -> None:
    """Check that owner/repo portion of each key in *section* is in allowed_repos."""
    for key in section:
        try:
            repo, _ = parse_key(key)
        except ValueError as exc:
            raise LayoutValidationError(f"{prefix}.{key}", str(exc)) from exc
        if repo not in allowed_repos:
            raise LayoutValidationError(
                f"{prefix}.{key}",
                f"repo '{repo}' not in meta.repos[]",
            )


def _check_refs(data: dict, allowed_repos: set[str]) -> None:
    # meta.issue
    if "issue" in data["meta"] and isinstance(data["meta"]["issue"], dict):
        _assert_repo(data["meta"]["issue"], "meta.issue", allowed_repos)

    # lanes (order/par_groups/bands are all optional now)
    _check_lanes(data.get("lanes", []), allowed_repos)

    # standalone (optional)
    for si, ref in enumerate(data.get("standalone", {}).get("order", [])):
        _assert_repo(ref, f"standalone.order[{si}]", allowed_repos)

    # overrides keys (optional)
    _check_keyed_section(data.get("overrides", {}), "overrides", allowed_repos)

    # extra_deps keys (optional, deprecated)
    extra = data.get("extra_deps", {})
    for direction in ("extra_blocked_by", "extra_blocking"):
        _check_keyed_section(
            extra.get(direction, {}), f"extra_deps.{direction}", allowed_repos
        )


def validate_layout_dict(data: dict) -> None:
    """Validate a layout dict. Raises LayoutValidationError on any violation.

    Two-phase validation:
      1. JSON Schema validation — structural checks (types, required fields, enums).
      2. Cross-reference validation — semantic checks (IssueRef.repo ∈ meta.repos[]).
    """
    # Phase 1: JSON Schema validation
    validator = Draft7Validator(_load_schema())
    errors = sorted(validator.iter_errors(data), key=lambda e: e.absolute_path)
    if errors:
        first = errors[0]
        path = ".".join(str(p) for p in first.absolute_path) or "(root)"
        raise LayoutValidationError(path, first.message)

    # Phase 2: cross-reference — IssueRef.repo must be in meta.repos[]
    allowed_repos: set[str] = set(data["meta"]["repos"])
    _check_refs(data, allowed_repos)


def validate_layout(path: Path) -> None:
    """Validate a layout file. Raises LayoutValidationError on any violation."""
    data = json.loads(path.read_text())
    validate_layout_dict(data)
