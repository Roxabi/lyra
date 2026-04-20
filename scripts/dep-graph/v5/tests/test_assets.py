"""Tests for v5/assets/ — static CSS and JS files."""
from __future__ import annotations

from pathlib import Path

import pytest

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

CSS_FILES = [
    "tokens.css",
    "base.css",
    "toggle.css",
    "card.css",
    "grid.css",
    "graph.css",
]
JS_FILES = [
    "hover.js",
    "app.js",
]


# ─── Basic existence + content ───────────────────────────────────────────────

class TestAssetFilesNonEmpty:
    @pytest.mark.parametrize("name", CSS_FILES + JS_FILES)
    def test_file_is_non_empty(self, name):
        path = ASSETS_DIR / name
        assert path.exists(), f"{name} not found"
        assert path.stat().st_size > 0, f"{name} is empty"

    @pytest.mark.parametrize("name", CSS_FILES + JS_FILES)
    def test_file_is_readable(self, name):
        path = ASSETS_DIR / name
        content = path.read_text()
        assert len(content) > 0


# ─── CSS: no Python f-string leakage ─────────────────────────────────────────

class TestCssNoFStringLeakage:
    @pytest.mark.parametrize("name", CSS_FILES)
    def test_no_double_braces(self, name):
        content = (ASSETS_DIR / name).read_text()
        # Python {{ and }} would indicate unprocessed f-string escapes
        assert "{{" not in content, f"{name} contains '{{{{'"
        assert "}}" not in content, f"{name} contains '}}}}'"


# ─── JS: syntactic plausibility (balanced braces/parens) ─────────────────────

class TestJsBalancedBrackets:
    @pytest.mark.parametrize("name", JS_FILES)
    def test_balanced_parens(self, name):
        content = (ASSETS_DIR / name).read_text()
        assert content.count("(") == content.count(")"), \
            f"{name}: unbalanced parentheses"

    @pytest.mark.parametrize("name", JS_FILES)
    def test_balanced_braces(self, name):
        content = (ASSETS_DIR / name).read_text()
        assert content.count("{") == content.count("}"), \
            f"{name}: unbalanced curly braces"


# ─── tokens.css defines all lane tokens used by grid.css and card.css ────────

class TestTokensDefinesAllLanes:
    def test_tokens_has_all_lane_variables(self):
        tokens = (ASSETS_DIR / "tokens.css").read_text()
        expected_lanes = [
            "a1", "a2", "a3", "b", "c1", "c2", "c3",
            "d", "e", "f", "g", "h", "i",
        ]
        for lane in expected_lanes:
            token = f"--lane-{lane}"
            assert token in tokens, f"token {token} missing from tokens.css"

    def test_tokens_defines_status_variables(self):
        tokens = (ASSETS_DIR / "tokens.css").read_text()
        for status in ["ready", "blocked", "done"]:
            assert f"--status-{status}" in tokens

    def test_tokens_defines_bg_variables(self):
        tokens = (ASSETS_DIR / "tokens.css").read_text()
        assert "--bg:" in tokens
        assert "--bg-card:" in tokens
        assert "--bg-panel:" in tokens

    def test_card_css_references_issue_card(self):
        card = (ASSETS_DIR / "card.css").read_text()
        assert ".issue-card" in card

    def test_grid_css_references_lane_swim_grid(self):
        grid = (ASSETS_DIR / "grid.css").read_text()
        assert ".lane-swim-grid" in grid or "lane-swim-grid" in grid

    def test_graph_css_references_gg_msrow(self):
        graph = (ASSETS_DIR / "graph.css").read_text()
        assert ".gg-msrow" in graph

    def test_toggle_css_references_mode_toggle(self):
        toggle = (ASSETS_DIR / "toggle.css").read_text()
        assert ".mode-toggle" in toggle

    def test_app_js_references_storage_key(self):
        app = (ASSETS_DIR / "app.js").read_text()
        assert "STORAGE_KEY" in app

    def test_hover_js_references_traverse(self):
        hover = (ASSETS_DIR / "hover.js").read_text()
        assert "traverse" in hover

    def test_all_css_files_exist(self):
        for name in CSS_FILES:
            assert (ASSETS_DIR / name).exists()

    def test_all_js_files_exist(self):
        for name in JS_FILES:
            assert (ASSETS_DIR / name).exists()
