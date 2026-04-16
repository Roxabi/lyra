"""Test that template extraction produces identical output."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from dep_graph.build import build_html


def test_output_matches_golden() -> None:
    """Verify extracted templates produce identical HTML."""
    visuals_dir = Path.home() / ".roxabi" / "forge" / "lyra" / "visuals"
    layout_path = visuals_dir / "lyra-v2-dependency-graph.layout.json"
    cache_path = visuals_dir / "lyra-v2-dependency-graph.gh.json"
    golden_path = visuals_dir / "lyra-v2-dependency-graph.html"

    if not layout_path.exists():
        pytest.skip(f"Layout file not found: {layout_path}")
    if not cache_path.exists():
        pytest.skip(f"GH cache file not found: {cache_path}")
    if not golden_path.exists():
        pytest.skip(f"Golden file not found: {golden_path}")

    # Load data and build
    layout = json.loads(layout_path.read_text())
    gh_data = json.loads(cache_path.read_text())
    gh_issues = gh_data.get("issues", {})

    output = build_html(layout, gh_issues)
    golden = golden_path.read_text()

    # Compare - normalize line endings
    assert output.replace("\r\n", "\n") == golden.replace("\r\n", "\n"), (
        "Output differs from golden - run 'make dep-graph build' to regenerate"
    )
