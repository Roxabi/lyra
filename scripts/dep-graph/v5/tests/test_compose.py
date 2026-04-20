"""Tests for v5.compose — full-page HTML assembly."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from v5 import compose


class TestBuildHtml:
    def test_returns_string(self, graph_data):
        result = compose.build_html(graph_data)
        assert isinstance(result, str)

    def test_starts_with_doctype(self, graph_data):
        result = compose.build_html(graph_data)
        assert result.startswith("<!DOCTYPE html>")

    def test_ends_with_html_close(self, graph_data):
        result = compose.build_html(graph_data)
        assert "</html>" in result

    def test_has_html_open_tag(self, graph_data):
        result = compose.build_html(graph_data)
        assert "<html" in result

    # ── CSS markers ──────────────────────────────────────────────────────────

    def test_contains_lane_a1_token(self, graph_data):
        """tokens.css defines --lane-a1."""
        result = compose.build_html(graph_data)
        assert "--lane-a1" in result

    def test_contains_view_active_class(self, graph_data):
        """base.css or toggle.css references .view-active."""
        result = compose.build_html(graph_data)
        assert ".view-active" in result

    def test_contains_mode_toggle_class(self, graph_data):
        """toggle.css defines .mode-toggle."""
        result = compose.build_html(graph_data)
        assert ".mode-toggle" in result

    def test_contains_issue_card_class(self, graph_data):
        """card.css defines .issue-card."""
        result = compose.build_html(graph_data)
        assert ".issue-card" in result

    def test_contains_lane_swim_grid_class(self, graph_data):
        """grid.css defines .lane-swim-grid."""
        result = compose.build_html(graph_data)
        assert ".lane-swim-grid" in result

    def test_contains_gg_msrow_class(self, graph_data):
        """graph.css defines .gg-msrow."""
        result = compose.build_html(graph_data)
        assert ".gg-msrow" in result

    # ── JS markers ────────────────────────────────────────────────────────────

    def test_contains_storage_key(self, graph_data):
        """app.js defines STORAGE_KEY."""
        result = compose.build_html(graph_data)
        assert "STORAGE_KEY" in result

    def test_contains_traverse_function(self, graph_data):
        """hover.js defines traverse(start, adj)."""
        result = compose.build_html(graph_data)
        assert "traverse(start, adj)" in result

    # ── Both view sections ────────────────────────────────────────────────────

    def test_has_grid_section(self, graph_data):
        result = compose.build_html(graph_data)
        assert 'data-view="grid"' in result

    def test_has_graph_section(self, graph_data):
        result = compose.build_html(graph_data)
        assert 'data-view="graph"' in result

    # ── Active view switching ─────────────────────────────────────────────────

    def test_active_graph_graph_has_view_active(self, graph_data):
        result = compose.build_html(graph_data, active="graph")
        assert 'class="view view-graph view-active"' in result

    def test_active_graph_grid_has_no_view_active(self, graph_data):
        result = compose.build_html(graph_data, active="graph")
        assert 'class="view view-grid"' in result
        assert 'class="view view-grid view-active"' not in result

    def test_active_grid_grid_has_view_active(self, graph_data):
        result = compose.build_html(graph_data, active="grid")
        assert 'class="view view-grid view-active"' in result

    def test_active_grid_graph_has_no_view_active(self, graph_data):
        result = compose.build_html(graph_data, active="grid")
        assert 'class="view view-graph"' in result
        assert 'class="view view-graph view-active"' not in result

    def test_invalid_active_raises(self, graph_data):
        with pytest.raises(AssertionError):
            compose.build_html(graph_data, active="table")

    # ── Diagram meta ──────────────────────────────────────────────────────────

    def test_diagram_title_meta(self, graph_data):
        result = compose.build_html(graph_data)
        assert 'name="diagram:title"' in result

    def test_diagram_color_amber(self, graph_data):
        result = compose.build_html(graph_data)
        assert 'content="amber"' in result

    def test_diagram_meta_start_end_markers(self, graph_data):
        result = compose.build_html(graph_data)
        assert "<!-- diagram-meta:start -->" in result
        assert "<!-- diagram-meta:end -->" in result

    def test_diagram_category_plan(self, graph_data):
        result = compose.build_html(graph_data)
        assert 'content="plan"' in result

    def test_diagram_badges_latest(self, graph_data):
        result = compose.build_html(graph_data)
        assert 'content="latest"' in result


class TestWrite:
    def test_writes_file_to_path(self, graph_data):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "output.html"
            byte_count = compose.write(out, graph_data)
            assert out.exists()
            assert byte_count > 0

    def test_returns_byte_count(self, graph_data):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "output.html"
            byte_count = compose.write(out, graph_data)
            content = out.read_text()
            assert byte_count == len(content)

    def test_written_content_is_valid_html(self, graph_data):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "output.html"
            compose.write(out, graph_data)
            content = out.read_text()
            assert content.startswith("<!DOCTYPE html>")
            assert "</html>" in content

    def test_creates_parent_dirs(self, graph_data):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "nested" / "dir" / "output.html"
            compose.write(out, graph_data)
            assert out.exists()

    def test_active_param_forwarded(self, graph_data):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "output.html"
            compose.write(out, graph_data, active="grid")
            content = out.read_text()
            assert 'class="view view-grid view-active"' in content
