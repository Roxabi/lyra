"""Tests for v5.views.grid — grid view HTML output."""
from __future__ import annotations

from v5.data.model import COLUMN_GROUPS, MILESTONES
from v5.views import grid


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

    def test_has_nine_col_headers(self, graph_data):
        result = grid.render(graph_data)
        assert result.count('class="col-header"') == len(COLUMN_GROUPS)
        assert result.count('class="col-header"') == 9

    def test_has_spacer_div(self, graph_data):
        result = grid.render(graph_data)
        assert '<div class="spacer">' in result

    def test_has_six_grid_rows(self, graph_data):
        result = grid.render(graph_data)
        assert result.count('class="grid-row"') == len(MILESTONES)
        assert result.count('class="grid-row"') == 6

    def test_each_row_has_one_row_header(self, graph_data):
        result = grid.render(graph_data)
        assert result.count('class="row-header"') == 6

    def test_nine_plus_one_columns_per_row(self, graph_data):
        # Each row has 1 row-header + 9 grid-cells
        result = grid.render(graph_data)
        n_cols = len(COLUMN_GROUPS)
        assert result.count('class="grid-cell"') == len(MILESTONES) * n_cols

    def test_empty_cells_show_dot(self, graph_data):
        result = grid.render(graph_data)
        assert '<div class="cell-empty">·</div>' in result

    def test_cols_custom_property(self, graph_data):
        result = grid.render(graph_data)
        assert "--cols: 9" in result

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
