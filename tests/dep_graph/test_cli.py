"""Tests for dep_graph CLI entry points.

Covers:
  - main() routing to correct subcommand
  - _resolve_paths() deriving default cache/out paths from layout
  - validate subcommand exit codes for valid/invalid/missing layouts
"""

from __future__ import annotations

import argparse
import json

from dep_graph.cli import _resolve_paths, main

# ---------------------------------------------------------------------------
# _resolve_paths tests
# ---------------------------------------------------------------------------


def test_resolve_paths_defaults_from_layout(tmp_path):
    """_resolve_paths derives cache/out paths as siblings of layout."""
    # Arrange
    layout = tmp_path / "lyra-v2-dependency-graph.layout.json"
    layout.touch()

    args = argparse.Namespace(layout=str(layout), cache=None, out=None)

    # Act
    resolved_layout, cache_path, out_path = _resolve_paths(args)

    # Assert
    assert resolved_layout == layout
    assert cache_path == tmp_path / "lyra-v2-dependency-graph.gh.json"
    assert out_path == tmp_path / "lyra-v2-dependency-graph.html"


def test_resolve_paths_explicit_cache_out(tmp_path):
    """Explicit --cache and --out override default derivation."""
    # Arrange
    layout = tmp_path / "layout.json"
    layout.touch()
    explicit_cache = tmp_path / "custom.gh.json"
    explicit_out = tmp_path / "custom.html"

    args = argparse.Namespace(
        layout=str(layout), cache=str(explicit_cache), out=str(explicit_out)
    )

    # Act
    _, cache_path, out_path = _resolve_paths(args)

    # Assert
    assert cache_path == explicit_cache
    assert out_path == explicit_out


def test_resolve_paths_strips_layout_suffix(tmp_path):
    """Stem removes '.layout' suffix when deriving cache/out names."""
    # Arrange
    layout = tmp_path / "my-deps.layout.json"
    layout.touch()

    args = argparse.Namespace(layout=str(layout), cache=None, out=None)

    # Act
    _, cache_path, out_path = _resolve_paths(args)

    # Assert
    assert cache_path.name == "my-deps.gh.json"
    assert out_path.name == "my-deps.html"


# ---------------------------------------------------------------------------
# validate subcommand tests
# ---------------------------------------------------------------------------


def _valid_layout() -> dict:
    """Minimal schema-valid multi-repo layout."""
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


def test_validate_valid_layout_exits_0(tmp_path, capsys):
    """validate subcommand returns 0 for a schema-valid layout."""
    # Arrange
    layout = tmp_path / "layout.json"
    layout.write_text(json.dumps(_valid_layout()))

    # Act
    result = main(["validate", "--layout", str(layout)])

    # Assert
    assert result == 0
    captured = capsys.readouterr()
    assert "Schema validation passed" in captured.out


def test_validate_missing_layout_exits_1(tmp_path, capsys):
    """validate subcommand returns 1 when layout file not found."""
    # Arrange
    missing = tmp_path / "missing.json"

    # Act
    result = main(["validate", "--layout", str(missing)])

    # Assert
    assert result == 1
    captured = capsys.readouterr()
    assert "not found" in captured.err


def test_validate_invalid_layout_exits_1(tmp_path, capsys):
    """validate subcommand returns 1 for schema-invalid layout."""
    # Arrange
    layout = tmp_path / "layout.json"
    layout.write_text(json.dumps({"invalid": True}))

    # Act
    result = main(["validate", "--layout", str(layout)])

    # Assert
    assert result == 1
    captured = capsys.readouterr()
    assert "SCHEMA ERROR" in captured.err


# ---------------------------------------------------------------------------
# migrate subcommand tests
# ---------------------------------------------------------------------------


def test_migrate_uses_layout_flag(tmp_path):
    """migrate subcommand accepts --layout flag (consistency with other subcommands)."""
    # Arrange
    old_layout = {
        "meta": {
            "title": "T",
            "date": "2026-04-15",
            "repo": "Roxabi/lyra",
            "label_prefix": "graph:",
        },
        "lanes": [],
        "standalone": {"order": []},
        "overrides": {},
        "extra_deps": {"extra_blocked_by": {}, "extra_blocking": {}},
        "cross_deps": [],
        "title_rules": [],
    }
    layout = tmp_path / "layout.json"
    layout.write_text(json.dumps(old_layout))

    # Act
    result = main(["migrate", "--layout", str(layout)])

    # Assert
    assert result == 0
    new = layout.with_suffix(layout.suffix + ".new")
    assert new.exists()
