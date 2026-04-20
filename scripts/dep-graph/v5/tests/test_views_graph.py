"""Tests for v5.views.graph — graph view HTML output."""
from __future__ import annotations

import re

from v5.data.derive import tasks_for_graph
from v5.views import graph


class TestGraphRender:
    def test_returns_string(self, graph_data):
        result = graph.render(graph_data)
        assert isinstance(result, str)

    def test_active_true_has_view_active_class(self, graph_data):
        result = graph.render(graph_data, active=True)
        assert 'class="view view-graph view-active"' in result

    def test_active_false_has_no_view_active(self, graph_data):
        result = graph.render(graph_data, active=False)
        assert "view-active" not in result

    def test_default_active_is_true(self, graph_data):
        result = graph.render(graph_data)
        assert "view-active" in result

    def test_has_graph_section(self, graph_data):
        result = graph.render(graph_data)
        assert '<section class="view view-graph' in result

    def test_has_graph_stage(self, graph_data):
        result = graph.render(graph_data)
        assert 'class="graph-stage"' in result

    def test_node_count_matches_tasks_for_graph(self, graph_data):
        tasks = tasks_for_graph(graph_data)
        result = graph.render(graph_data)
        # One .gg-node dot per task
        assert result.count('class="gg-node') == len(tasks)

    def test_label_count_matches_tasks_for_graph(self, graph_data):
        tasks = tasks_for_graph(graph_data)
        result = graph.render(graph_data)
        # One .gg-ilabel (outermost) per task — ignore inner spans
        assert len(re.findall(r'class="gg-ilabel[ "]', result)) == len(tasks)

    def test_no_issue_cards_in_graph_view(self, graph_data):
        """v5.1: graph uses dots + pills, NOT full issue-cards."""
        result = graph.render(graph_data)
        assert 'class="issue-card' not in result

    def test_nodes_have_absolute_position_style(self, graph_data):
        result = graph.render(graph_data)
        # Every .gg-node should carry left:X.XX%; top:Y.YY%; inline style
        matches = re.findall(
            r'class="gg-node[^"]*"[^>]*style="left:\d+\.\d+%; top:\d+\.\d+%;"',
            result,
        )
        assert len(matches) > 0

    def test_x_values_are_sensible(self, graph_data):
        result = graph.render(graph_data)
        # Extract all left:XX.XX% values
        lefts = [float(m) for m in re.findall(r"left:(\d+\.\d+)%", result)]
        for x in lefts:
            assert 0 <= x <= 100, f"x={x} out of 0..100"

    def test_y_values_are_sensible(self, graph_data):
        result = graph.render(graph_data)
        tops = [float(m) for m in re.findall(r"top:(\d+\.\d+)%", result)]
        for y in tops:
            assert 0 <= y <= 100, f"y={y} out of 0..100"

    def test_svg_edges_present(self, graph_data):
        result = graph.render(graph_data)
        assert '<svg class="graph-svg"' in result

    def test_edge_paths_are_svg_paths(self, graph_data):
        result = graph.render(graph_data)
        # At least 0 paths (may have no edges if no unblocks in data)
        paths = re.findall(r'<path class="gg-edge', result)
        # fixture has issue#1 blocking issue#2, issue#2 blocking issue#3
        assert len(paths) >= 2

    def test_data_view_attribute(self, graph_data):
        result = graph.render(graph_data)
        assert 'data-view="graph"' in result

    def test_graph_wrap_has_height(self, graph_data):
        result = graph.render(graph_data)
        assert re.search(r'style="height:\d+px;"', result)

    def test_msrows_present(self, graph_data):
        result = graph.render(graph_data)
        # At least some milestone rows should appear
        assert 'class="gg-msrow"' in result

    def test_msrow_count_matches_active_milestones(self, graph_data):
        """One .gg-msrow per milestone that has at least one task.
        The fixture has tasks in M0..M4 (not M5), so 5 msrows expected."""
        from v5.data.derive import tasks_for_graph
        tasks = tasks_for_graph(graph_data)
        active_ms = {t["milestone"] for t in tasks}
        result = graph.render(graph_data)
        count = result.count('class="gg-msrow"')
        assert count == len(active_ms)

    def test_msrow_separators_count(self, graph_data):
        """Separators = msrows - 1."""
        from v5.data.derive import tasks_for_graph
        tasks = tasks_for_graph(graph_data)
        active_ms = {t["milestone"] for t in tasks}
        result = graph.render(graph_data)
        n_msrows = len(active_ms)
        count = result.count('class="gg-msrow-sep"')
        assert count == n_msrows - 1

    def test_msrow_top_values_unique(self, graph_data):
        result = graph.render(graph_data)
        tops = re.findall(r'class="gg-msrow" style="top:(\d+)px', result)
        assert len(tops) == len(set(tops)), "Duplicate top values in msrows"

    def test_active_milestone_codes_in_msrows(self, graph_data):
        """Each milestone that has tasks should appear in msrow labels."""
        from v5.data.derive import tasks_for_graph
        tasks = tasks_for_graph(graph_data)
        active_ms = {t["milestone"] for t in tasks}
        result = graph.render(graph_data)
        # ms_short values (M0, M1, ...) should be in the rendered output
        for ms_short in active_ms:
            assert ms_short in result
