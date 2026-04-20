"""Tests for v5.data.layout_graph — DAG positioning math."""
from __future__ import annotations

import pytest

from v5.data.layout_graph import (
    LANE_X_END,
    LANE_X_START,
    Y_BOT,
    Y_TOP,
    edge_path,
    layout_grid,
    ms_idx,
    ms_vertical_extents,
)

# ─── ms_idx ──────────────────────────────────────────────────────────────────

class TestMsIdx:
    def test_m0_returns_0(self):
        assert ms_idx("M0") == 0

    def test_m1_returns_1(self):
        assert ms_idx("M1") == 1

    def test_m2_returns_2(self):
        assert ms_idx("M2") == 2

    def test_m3_returns_3(self):
        assert ms_idx("M3") == 3

    def test_m4_returns_4(self):
        assert ms_idx("M4") == 4

    def test_m5_returns_5(self):
        assert ms_idx("M5") == 5

    def test_unknown_returns_99(self):
        assert ms_idx("M99") == 99

    def test_none_returns_99(self):
        assert ms_idx(None) == 99

    def test_empty_string_returns_99(self):
        assert ms_idx("") == 99

    def test_ordering_preserved(self):
        assert ms_idx("M0") < ms_idx("M1") < ms_idx("M2") < ms_idx("M5")


# ─── edge_path ───────────────────────────────────────────────────────────────

class TestEdgePath:
    def test_starts_with_M(self):
        path = edge_path(10.0, 20.0, 50.0, 60.0)
        assert path.startswith("M ")

    def test_vertical_line_is_straight(self):
        # Same x → straight line
        path = edge_path(50.0, 10.0, 50.0, 90.0)
        assert " L " in path
        assert "C " not in path

    def test_vertical_nearly_same_x(self):
        # dx < 0.1 → straight line
        path = edge_path(50.0, 10.0, 50.05, 90.0)
        assert " L " in path

    def test_diagonal_is_bezier(self):
        path = edge_path(10.0, 20.0, 80.0, 60.0)
        assert " C " in path
        assert " L " not in path

    def test_diagonal_contains_start_coords(self):
        path = edge_path(10.0, 20.0, 80.0, 60.0)
        assert "10.00" in path
        assert "20.00" in path

    def test_diagonal_contains_end_coords(self):
        path = edge_path(10.0, 20.0, 80.0, 60.0)
        assert "80.00" in path
        assert "60.00" in path

    def test_valid_svg_path_format(self):
        path = edge_path(5.0, 10.0, 95.0, 90.0)
        # Must start with 'M' followed by x,y
        assert path[0] == "M"


# ─── layout_grid ─────────────────────────────────────────────────────────────

def _make_task(
    num: int, ms: str, lane: str, depth: int, repo: str = "Roxabi/lyra",
) -> dict:
    return {
        "key": f"{repo}#{num}",
        "repo": repo,
        "num": num,
        "title": f"Task {num}",
        "url": f"https://github.com/{repo}/issues/{num}",
        "state": "open",
        "status": "ready",
        "milestone": ms,
        "milestone_name": ms,
        "lane": lane,
        "lane_name": lane,
        "column": lane,
        "depth": depth,
        "epic_num": None,
        "size": None,
        "blockers": [],
        "unblocks": [],
        "labels": [],
    }


class TestLayoutGrid:
    def _fixture_tasks(self):
        """3 bands in M0: 2 tasks at depth 0, 3 tasks at depth 1, 1 task at depth 2."""
        return [
            _make_task(1, "M0", "a1", 0),
            _make_task(2, "M0", "b", 0),
            _make_task(3, "M0", "a1", 1),
            _make_task(4, "M0", "b", 1),
            _make_task(5, "M0", "c1", 1),
            _make_task(6, "M0", "a1", 2),
        ]

    def test_node_count_matches_task_count(self):
        tasks = self._fixture_tasks()
        node_records, _, _ = layout_grid(tasks)
        assert len(node_records) == len(tasks)

    def test_all_x_in_bounds(self):
        tasks = self._fixture_tasks()
        node_records, _, _ = layout_grid(tasks)
        for n in node_records:
            assert LANE_X_START <= n["x"] <= LANE_X_END, \
                f"x={n['x']} out of [{LANE_X_START}, {LANE_X_END}]"

    def test_all_y_in_bounds(self):
        tasks = self._fixture_tasks()
        node_records, _, _ = layout_grid(tasks)
        for n in node_records:
            assert Y_TOP <= n["y"] <= Y_BOT, f"y={n['y']} out of [{Y_TOP}, {Y_BOT}]"

    def test_tasks_in_same_band_share_y(self):
        tasks = self._fixture_tasks()
        node_records, _, _ = layout_grid(tasks)
        # Map num → y
        y_by_num = {n["task"]["num"]: n["y"] for n in node_records}
        # depth-0 tasks (nums 1 and 2) should share same y
        assert y_by_num[1] == pytest.approx(y_by_num[2])
        # depth-1 tasks (nums 3, 4, 5) should share same y
        assert y_by_num[3] == pytest.approx(y_by_num[4])
        assert y_by_num[4] == pytest.approx(y_by_num[5])

    def test_bands_sorted_by_ms_then_depth(self):
        tasks = self._fixture_tasks()
        _, band_records, _ = layout_grid(tasks)
        depths = [b["depth"] for b in band_records]
        assert depths == sorted(depths)

    def test_empty_tasks_returns_empty(self):
        node_records, band_records, gsize = layout_grid([])
        assert node_records == []
        assert band_records == []
        assert gsize == {}

    def test_single_task_layout(self):
        tasks = [_make_task(1, "M0", "a1", 0)]
        node_records, band_records, _ = layout_grid(tasks)
        assert len(node_records) == 1
        assert len(band_records) == 1

    def test_no_x_overlap_under_min_cell_gap(self):
        """Nodes in the same band must be separated by at least MIN_CELL_GAP cells."""
        # 4 tasks in same band → should be spread out, no two at same x
        tasks = [_make_task(i, "M0", "a1", 0) for i in range(1, 5)]
        node_records, _, gsize = layout_grid(tasks)
        xs = sorted(n["x"] for n in node_records)
        for i in range(len(xs) - 1):
            # x diff should be > 0 (distinct positions)
            assert xs[i + 1] > xs[i], "Two nodes share the same x position"

    def test_multi_milestone_tasks(self):
        tasks = [
            _make_task(1, "M0", "a1", 0),
            _make_task(2, "M1", "b", 0),
            _make_task(3, "M2", "c1", 0),
        ]
        node_records, band_records, _ = layout_grid(tasks)
        assert len(node_records) == 3
        assert len(band_records) == 3
        # Y values should differ between milestones
        ys = [b["y"] for b in band_records]
        assert len(set(ys)) == 3


# ─── ms_vertical_extents ─────────────────────────────────────────────────────

class TestMsVerticalExtents:
    def test_top_less_than_bot(self):
        bands = [
            {"ms": "M0", "depth": 0, "y": 10.0},
            {"ms": "M0", "depth": 1, "y": 30.0},
            {"ms": "M1", "depth": 0, "y": 50.0},
            {"ms": "M1", "depth": 1, "y": 70.0},
        ]
        extents = ms_vertical_extents(bands)
        for ms, (top, bot) in extents.items():
            assert top < bot, f"{ms}: top={top} >= bot={bot}"

    def test_all_milestones_represented(self):
        bands = [
            {"ms": "M0", "depth": 0, "y": 10.0},
            {"ms": "M1", "depth": 0, "y": 50.0},
            {"ms": "M2", "depth": 0, "y": 90.0},
        ]
        extents = ms_vertical_extents(bands)
        assert set(extents.keys()) == {"M0", "M1", "M2"}

    def test_single_band_returns_extent(self):
        bands = [{"ms": "M0", "depth": 0, "y": 50.0}]
        extents = ms_vertical_extents(bands)
        assert "M0" in extents
        top, bot = extents["M0"]
        assert top < bot

    def test_values_in_1_to_99(self):
        tasks = [_make_task(i, f"M{i % 3}", "a1", 0) for i in range(1, 10)]
        _, bands, _ = layout_grid(tasks)
        extents = ms_vertical_extents(bands)
        for ms, (top, bot) in extents.items():
            assert 1.0 <= top <= 99.0, f"{ms}: top={top}"
            assert 1.0 <= bot <= 99.0, f"{ms}: bot={bot}"

    def test_top_values_distinct_per_ms(self):
        bands = [
            {"ms": "M0", "depth": 0, "y": 10.0},
            {"ms": "M1", "depth": 0, "y": 50.0},
        ]
        extents = ms_vertical_extents(bands)
        m0_top, _ = extents["M0"]
        m1_top, _ = extents["M1"]
        assert m0_top != m1_top
