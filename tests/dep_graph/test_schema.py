"""Tests for dep_graph.schema.validate_layout — multi-repo layout shape.

Tests verify that:
- The new multi-repo format (meta.repos[] plural) is accepted.
- The old single-repo format (meta.repo singular) is rejected.
- IssueRef objects replace bare integers in order lists.
- Having both meta.repo and meta.repos simultaneously is rejected.
- IssueRef.repo must be declared in meta.repos.

RED state: validate_layout currently accepts a dict, not a Path, and the
underlying JSON schema still uses the old meta.repo key.  All 5 tests will
fail until T2/T3 land the new signature and updated schema.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from dep_graph.schema import validate_layout

FIXTURES = Path(__file__).parent / "fixtures"


def _write_layout(tmp_path: Path, data: dict) -> Path:
    """Write *data* as JSON to a temp file and return the path."""
    p = tmp_path / "layout.json"
    p.write_text(json.dumps(data))
    return p


def _valid_multi_repo_layout() -> dict:
    """Return a minimal, fully-valid multi-repo layout dict."""
    return {
        "meta": {
            "title": "T",
            "date": "2026-04-15",
            "repos": ["Roxabi/lyra"],
            "label_prefix": "graph:",
        },
        "lanes": [],
        "standalone": {"order": []},
        "overrides": {},
        "extra_deps": {"extra_blocked_by": {}, "extra_blocking": {}},
        "cross_deps": [],
        "title_rules": [],
    }


def test_rejects_meta_repo_singular(tmp_path: Path) -> None:
    """meta.repo (singular, old format) must be rejected; only meta.repos[] is valid."""
    # Arrange
    layout = _valid_multi_repo_layout()
    layout["meta"]["repo"] = "Roxabi/lyra"  # old singular key
    del layout["meta"]["repos"]  # remove new plural key
    p = _write_layout(tmp_path, layout)

    # Act + Assert
    with pytest.raises(Exception):
        validate_layout(p)


def test_rejects_bare_int_issue_ref(tmp_path: Path) -> None:
    """lanes[].order must contain IssueRef objects, not bare integers."""
    # Arrange
    layout = _valid_multi_repo_layout()
    layout["lanes"] = [
        {
            "code": "a",
            "name": "A",
            "color": "#aaa",
            "epic": {"issue": 1, "label": "x", "tag": "T"},
            "order": [641],  # bare int — illegal in new schema
            "par_groups": {},
            "bands": [],
        }
    ]
    p = _write_layout(tmp_path, layout)

    # Act + Assert
    with pytest.raises(Exception):
        validate_layout(p)


def test_rejects_issue_ref_repo_not_in_meta_repos(tmp_path: Path) -> None:
    """IssueRef.repo must be a member of meta.repos[]."""
    # Arrange
    layout = _valid_multi_repo_layout()
    layout["lanes"] = [
        {
            "code": "a",
            "name": "A",
            "color": "#aaa",
            "epic": {"issue": 1, "label": "x", "tag": "T"},
            "order": [
                {"repo": "Other/repo", "issue": 1}
            ],  # repo not declared in meta.repos
            "par_groups": {},
            "bands": [],
        }
    ]
    p = _write_layout(tmp_path, layout)

    # Act + Assert
    with pytest.raises(Exception):
        validate_layout(p)


def test_rejects_both_meta_repo_and_meta_repos(tmp_path: Path) -> None:
    """Having both meta.repo (old) and meta.repos (new) simultaneously is forbidden."""
    # Arrange
    layout = _valid_multi_repo_layout()
    layout["meta"]["repo"] = "Roxabi/lyra"  # old key present alongside repos[]
    p = _write_layout(tmp_path, layout)

    # Act + Assert
    with pytest.raises(Exception):
        validate_layout(p)


def test_accepts_valid_multi_repo_layout(tmp_path: Path) -> None:
    """A fully-valid multi-repo layout passes validation without raising."""
    # Arrange
    layout = _valid_multi_repo_layout()
    layout["lanes"] = [
        {
            "code": "a",
            "name": "A",
            "color": "#aaa",
            "epic": {"issue": 1, "label": "x", "tag": "T"},
            "order": [{"repo": "Roxabi/lyra", "issue": 641}],  # proper IssueRef
            "par_groups": {},
            "bands": [],
        }
    ]
    layout["overrides"] = {"Roxabi/lyra#641": {"title": "test"}}
    p = _write_layout(tmp_path, layout)

    # Act + Assert — must not raise
    validate_layout(p)
