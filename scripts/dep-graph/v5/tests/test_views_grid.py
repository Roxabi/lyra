"""Tests for v5.views.grid — grid view HTML output."""
from __future__ import annotations

from v5.data.model import COLUMN_GROUPS, MILESTONES, NO_LANE, NO_MS
from v5.views import grid


def _sentinel_cols(data) -> int:
    return 1 if any(lane == NO_LANE and v for (_, lane), v in data.matrix.items()) else 0


def _sentinel_rows(data) -> int:
    return 1 if any(ms == NO_MS and v for (ms, _), v in data.matrix.items()) else 0


class TestGridRender:
    def test_returns_string(self, graph_data):
        result = grid.render(graph_data)
        assert isinstance(result, str)

    def test_has_view_grid_section(self, graph_data):
        result = grid.render(graph_data)
        assert '<section class="view view-grid"' in result

    def test_inactive_by_default_has_no_view_active(self, graph_data):
        result = grid.render(graph_data, active=False)
        assert "view-active" not in result

    def test_active_true_adds_view_active_class(self, graph_data):
        result = grid.render(graph_data, active=True)
        assert 'class="view view-grid view-active"' in result

    def test_col_headers_count(self, graph_data):
        result = grid.render(graph_data)
        expected = len(COLUMN_GROUPS) + _sentinel_cols(graph_data)
        assert result.count('class="col-header"') == expected

    def test_has_spacer_div(self, graph_data):
        result = grid.render(graph_data)
        assert '<div class="spacer">' in result

    def test_grid_rows_count(self, graph_data):
        result = grid.render(graph_data)
        expected = len(MILESTONES) + _sentinel_rows(graph_data)
        assert result.count('class="grid-row"') == expected

    def test_each_row_has_one_row_header(self, graph_data):
        result = grid.render(graph_data)
        expected = len(MILESTONES) + _sentinel_rows(graph_data)
        assert result.count('class="row-header"') == expected

    def test_grid_cells_count(self, graph_data):
        # Each row has 1 row-header + n_cols grid-cells
        result = grid.render(graph_data)
        n_cols = len(COLUMN_GROUPS) + _sentinel_cols(graph_data)
        n_rows = len(MILESTONES) + _sentinel_rows(graph_data)
        assert result.count('class="grid-cell"') == n_rows * n_cols

    def test_empty_cells_show_dot(self, graph_data):
        result = grid.render(graph_data)
        assert '<div class="cell-empty">·</div>' in result

    def test_cols_custom_property(self, graph_data):
        result = grid.render(graph_data)
        expected = len(COLUMN_GROUPS) + _sentinel_cols(graph_data)
        assert f"--cols: {expected}" in result

    def test_lane_swim_grid_present(self, graph_data):
        result = grid.render(graph_data)
        assert 'class="lane-swim-grid"' in result

    def test_ms_codes_present_in_rows(self, graph_data):
        result = grid.render(graph_data)
        for _, ms_code, _ in MILESTONES:
            assert ms_code in result

    def test_col_labels_present(self, graph_data):
        result = grid.render(graph_data)
        for col_label, _, _ in COLUMN_GROUPS:
            assert col_label in result

    def test_issue_cards_rendered(self, graph_data):
        result = grid.render(graph_data)
        assert 'class="issue-card' in result

    def test_cards_have_data_iss(self, graph_data):
        result = grid.render(graph_data)
        assert "data-iss=" in result

    def test_data_view_attribute(self, graph_data):
        result = grid.render(graph_data)
        assert 'data-view="grid"' in result
