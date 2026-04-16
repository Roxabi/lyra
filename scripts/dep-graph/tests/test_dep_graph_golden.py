"""Golden output verification for dep-graph template extraction.

This test ensures that refactoring the HTML generation (e.g., extracting
templates to Jinja2) produces identical output to the baseline.

Run with: uv run pytest scripts/dep-graph/tests/test_dep_graph_golden.py -v

To regenerate the golden file after intentional changes:
    cd ~/projects/lyra && make dep-graph build
    cp ~/.roxabi/forge/lyra/visuals/lyra-v2-dependency-graph.html \
        scripts/dep-graph/tests/fixtures/
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from dep_graph.build import build_html

# Paths to real data files in the forge visuals directory
VISUALS_DIR = Path.home() / ".roxabi" / "forge" / "lyra" / "visuals"
LAYOUT_PATH = VISUALS_DIR / "lyra-v2-dependency-graph.layout.json"
CACHE_PATH = VISUALS_DIR / "lyra-v2-dependency-graph.gh.json"
GOLDEN_PATH = VISUALS_DIR / "lyra-v2-dependency-graph.html"

# Alternative: use committed fixture files (more stable for CI)
FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURE_LAYOUT = FIXTURES_DIR / "lyra-v2-dependency-graph.layout.json"
FIXTURE_CACHE = FIXTURES_DIR / "lyra-v2-dependency-graph.gh.json"
FIXTURE_GOLDEN = FIXTURES_DIR / "lyra-v2-dependency-graph.html"


def _has_real_data() -> bool:
    """Check if real data files exist in the forge visuals directory."""
    return LAYOUT_PATH.exists() and CACHE_PATH.exists() and GOLDEN_PATH.exists()


def _has_fixture_data() -> bool:
    """Check if fixture files exist in the tests/fixtures directory."""
    return (
        FIXTURE_LAYOUT.exists() and FIXTURE_CACHE.exists() and FIXTURE_GOLDEN.exists()
    )


@pytest.mark.skipif(
    not _has_real_data() and not _has_fixture_data(),
    reason="No golden files available - run 'make dep-graph build' first",
)
def test_output_matches_golden():
    """Verify build_html produces identical output to the golden file.

    This test ensures template extraction or refactoring does not change
    the generated HTML. If the output differs, either:
    1. The refactoring broke something (fix the code)
    2. The golden is outdated (regenerate with intentional changes)
    """
    # Prefer real data files, fall back to fixtures
    if _has_real_data():
        layout_path = LAYOUT_PATH
        cache_path = CACHE_PATH
        golden_path = GOLDEN_PATH
    else:
        layout_path = FIXTURE_LAYOUT
        cache_path = FIXTURE_CACHE
        golden_path = FIXTURE_GOLDEN

    # Load input data
    layout = json.loads(layout_path.read_text())
    gh_data = json.loads(cache_path.read_text())
    gh_issues = gh_data.get("issues", {})

    # Build HTML using current code
    output = build_html(layout, gh_issues)

    # Load golden
    golden = golden_path.read_text()

    # Compare - exact match required
    assert output == golden, (
        "Output differs from golden.\n"
        "If this is intentional (template changes), regenerate the golden:\n"
        "  cd ~/projects/lyra && make dep-graph build\n"
        f"  cp {golden_path} scripts/dep-graph/tests/fixtures/\n"
        "If unexpected, the refactoring may have broken something."
    )


@pytest.mark.skipif(
    not _has_real_data(),
    reason="No real data files available",
)
def test_golden_file_is_recent():
    """Verify the golden file is reasonably recent (not stale).

    This catches cases where the golden was generated with old code
    and no longer reflects current output.
    """
    import time

    # Golden should be modified within the last 30 days
    golden_mtime = GOLDEN_PATH.stat().st_mtime
    age_days = (time.time() - golden_mtime) / 86400

    assert age_days < 30, (
        f"Golden file is {age_days:.0f} days old - may be stale.\n"
        "Regenerate with: cd ~/projects/lyra && make dep-graph build"
    )
