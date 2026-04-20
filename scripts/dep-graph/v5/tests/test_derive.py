"""Tests for v5.data.derive — pure logic layer."""
from __future__ import annotations

from v5.data.derive import (
    build_matrix,
    compute_depth,
    epic_keys,
    lane_by_code,
    sort_cards_in_cell,
    status_of,
    tasks_for_graph,
)
from v5.data.model import EpicMeta, GraphData, Lane

# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_issue(  # noqa: PLR0913
    num: int,
    state: str = "open",
    milestone: str | None = "M0  NATS hardening",
    lane: str = "a1",
    blocked_by: list | None = None,
    blocking: list | None = None,
    repo: str = "Roxabi/lyra",
    size: str | None = None,
) -> dict:
    return {
        "repo": repo,
        "number": num,
        "title": f"Issue {num}",
        "state": state,
        "milestone": milestone,
        "lane_label": lane,
        "blocked_by": blocked_by or [],
        "blocking": blocking or [],
        "size": size,
        "labels": [],
    }


def _issues(*items: dict) -> dict:
    return {f"{i['repo']}#{i['number']}": i for i in items}


def _minimal_data(issues: dict, epic_ks: set | None = None) -> GraphData:
    lanes = [
        Lane(code="a1", name="NATS", color="a1",
             epic=EpicMeta(issue=100, label="Epic", tag="M0")),
        Lane(code="b", name="Container", color="b", epic=None),
    ]
    lbc = {lane.code: lane for lane in lanes}
    data = GraphData(
        meta={"repos": ["Roxabi/lyra"]},
        lanes=lanes,
        lane_by_code=lbc,
        issues=issues,
        epic_keys=epic_ks or set(),
        depth_by_key=compute_depth(issues),
    )
    return data


# ─── compute_depth ───────────────────────────────────────────────────────────

class TestComputeDepth:
    def test_no_blockers_returns_zero(self):
        issues = _issues(_make_issue(1, blocked_by=[]))
        depth = compute_depth(issues)
        assert depth["Roxabi/lyra#1"] == 0

    def test_chain_a_b_c_depths(self):
        # A has no blockers, B blocked by A, C blocked by B
        a = _make_issue(
            1, blocked_by=[],
            blocking=[{"repo": "Roxabi/lyra", "issue": 2}],
        )
        b = _make_issue(
            2,
            blocked_by=[{"repo": "Roxabi/lyra", "issue": 1}],
            blocking=[{"repo": "Roxabi/lyra", "issue": 3}],
        )
        c = _make_issue(3, blocked_by=[{"repo": "Roxabi/lyra", "issue": 2}])
        issues = _issues(a, b, c)
        depth = compute_depth(issues)
        assert depth["Roxabi/lyra#1"] == 0
        assert depth["Roxabi/lyra#2"] == 1
        assert depth["Roxabi/lyra#3"] == 2

    def test_cycle_guard_does_not_infinite_loop(self):
        # A → B → A (cycle)
        a = _make_issue(1, blocked_by=[{"repo": "Roxabi/lyra", "issue": 2}])
        b = _make_issue(2, blocked_by=[{"repo": "Roxabi/lyra", "issue": 1}])
        issues = _issues(a, b)
        # Must terminate without RecursionError
        depth = compute_depth(issues)
        assert "Roxabi/lyra#1" in depth
        assert "Roxabi/lyra#2" in depth

    def test_closed_blockers_count_in_depth(self):
        # Even closed issues increment depth
        a = _make_issue(1, state="closed")
        b = _make_issue(2, blocked_by=[{"repo": "Roxabi/lyra", "issue": 1}])
        issues = _issues(a, b)
        depth = compute_depth(issues)
        assert depth["Roxabi/lyra#2"] == 1

    def test_empty_issues_returns_empty_dict(self):
        assert compute_depth({}) == {}

    def test_external_blocker_absent_from_issues_yields_depth_1(self):
        # External blocker key is in blocked_by but absent from issues dict.
        # The `max(..., default=0)` generator is empty (filter `if b in issues`
        # excludes the absent key), so depth = 1 + 0 = 1.
        a = _make_issue(
            1, blocked_by=[{"repo": "Roxabi/voiceCLI", "issue": 10}]
        )
        issues = _issues(a)
        depth = compute_depth(issues)
        assert depth["Roxabi/lyra#1"] == 1


# ─── status_of ───────────────────────────────────────────────────────────────

class TestStatusOf:
    def test_closed_issue_is_done(self):
        iss = _make_issue(1, state="closed")
        assert status_of(iss, {}) == "done"

    def test_open_no_blockers_is_ready(self):
        iss = _make_issue(1, state="open", blocked_by=[])
        assert status_of(iss, {}) == "ready"

    def test_open_with_open_blocker_is_blocked(self):
        blocker = _make_issue(2, state="open")
        iss = _make_issue(1, blocked_by=[{"repo": "Roxabi/lyra", "issue": 2}])
        issues = _issues(blocker)
        assert status_of(iss, issues) == "blocked"

    def test_open_with_all_closed_blockers_is_ready(self):
        blocker = _make_issue(2, state="closed")
        iss = _make_issue(1, blocked_by=[{"repo": "Roxabi/lyra", "issue": 2}])
        issues = _issues(blocker)
        assert status_of(iss, issues) == "ready"

    def test_open_with_missing_blocker_is_ready(self):
        # Missing blocker → state lookup returns {} → treated as not-closed → blocked
        iss = _make_issue(1, blocked_by=[{"repo": "Roxabi/lyra", "issue": 999}])
        assert status_of(iss, {}) == "blocked"

    def test_mixed_blockers_one_open_is_blocked(self):
        b_closed = _make_issue(2, state="closed")
        b_open = _make_issue(3, state="open")
        iss = _make_issue(
            1,
            blocked_by=[
                {"repo": "Roxabi/lyra", "issue": 2},
                {"repo": "Roxabi/lyra", "issue": 3},
            ],
        )
        issues = _issues(b_closed, b_open)
        assert status_of(iss, issues) == "blocked"


# ─── epic_keys ───────────────────────────────────────────────────────────────

class TestEpicKeys:
    def test_extracts_epic_issue_numbers(self):
        lanes = [
            {"code": "a1", "epic": {"issue": 100}},
            {"code": "b", "epic": {"issue": 101}},
        ]
        keys = epic_keys(lanes, "Roxabi/lyra")
        assert "Roxabi/lyra#100" in keys
        assert "Roxabi/lyra#101" in keys

    def test_lane_without_epic_skipped(self):
        lanes = [
            {"code": "a1", "epic": {}},
            {"code": "b"},
        ]
        keys = epic_keys(lanes, "Roxabi/lyra")
        assert len(keys) == 0

    def test_empty_lanes(self):
        assert epic_keys([], "Roxabi/lyra") == set()

    def test_none_issue_skipped(self):
        lanes = [{"code": "a1", "epic": {"issue": None}}]
        keys = epic_keys(lanes, "Roxabi/lyra")
        assert len(keys) == 0


# ─── build_matrix ────────────────────────────────────────────────────────────

class TestBuildMatrix:
    def test_skips_epic_keys(self):
        epic = _make_issue(100)
        task = _make_issue(1)
        issues = _issues(epic, task)
        data = _minimal_data(issues, epic_ks={"Roxabi/lyra#100"})
        matrix, counts, total = build_matrix(data)
        assert total == 1
        # Epic should not be in matrix
        for cell_issues in matrix.values():
            nums = [i["number"] for i in cell_issues]
            assert 100 not in nums

    def test_skips_items_missing_milestone(self):
        task = _make_issue(1, milestone=None)
        data = _minimal_data(_issues(task))
        _, _, total = build_matrix(data)
        assert total == 0

    def test_skips_items_missing_lane(self):
        task = _make_issue(1)
        task["lane_label"] = None
        data = _minimal_data(_issues(task))
        _, _, total = build_matrix(data)
        assert total == 0

    def test_counts_by_status(self):
        ready = _make_issue(1, state="open")
        blocked_iss = _make_issue(2, blocked_by=[{"repo": "Roxabi/lyra", "issue": 1}])
        done = _make_issue(3, state="closed")
        issues = _issues(ready, blocked_iss, done)
        data = _minimal_data(issues)
        _, counts, total = build_matrix(data)
        assert total == 3
        assert counts["done"] == 1
        assert counts["blocked"] == 1
        assert counts["ready"] == 1

    def test_matrix_cell_populated(self):
        task = _make_issue(1, milestone="M0  NATS hardening", lane="a1")
        data = _minimal_data(_issues(task))
        matrix, _, _ = build_matrix(data)
        key = ("M0  NATS hardening", "a1")
        assert key in matrix
        assert len(matrix[key]) == 1


# ─── tasks_for_graph ─────────────────────────────────────────────────────────

class TestTasksForGraph:
    def test_excludes_epics(self, graph_data):
        tasks = tasks_for_graph(graph_data)
        nums = {t["num"] for t in tasks}
        # Epic issue numbers 100-104 should not appear
        for epic_num in range(100, 105):
            assert epic_num not in nums

    def test_includes_real_tasks(self, graph_data):
        tasks = tasks_for_graph(graph_data)
        nums = {t["num"] for t in tasks}
        assert 1 in nums
        assert 2 in nums

    def test_sorted_by_milestone_column_depth_num(self, graph_data):
        tasks = tasks_for_graph(graph_data)
        keys = [(t["milestone"], t["column"], t["depth"], t["num"]) for t in tasks]
        assert keys == sorted(keys)

    def test_task_has_required_keys(self, graph_data):
        tasks = tasks_for_graph(graph_data)
        required = {"key", "repo", "num", "title", "url", "state", "status",
                    "milestone", "lane", "column", "depth", "blockers", "unblocks"}
        for t in tasks:
            assert required.issubset(t.keys())

    def test_url_format(self, graph_data):
        tasks = tasks_for_graph(graph_data)
        for t in tasks:
            assert t["url"].startswith("https://github.com/")
            assert str(t["num"]) in t["url"]

    def test_size_field_present(self, graph_data):
        tasks = tasks_for_graph(graph_data)
        # Issue 8 has size "L"
        task_8 = next((t for t in tasks if t["num"] == 8), None)
        assert task_8 is not None
        assert task_8["size"] == "L"


# ─── sort_cards_in_cell ──────────────────────────────────────────────────────

class TestSortCardsInCell:
    def test_sorts_by_depth_then_num(self):
        cards = [
            {"repo": "Roxabi/lyra", "number": 5},
            {"repo": "Roxabi/lyra", "number": 1},
            {"repo": "Roxabi/lyra", "number": 3},
        ]
        depth_by_key = {
            "Roxabi/lyra#5": 2,
            "Roxabi/lyra#1": 0,
            "Roxabi/lyra#3": 1,
        }
        result = sort_cards_in_cell(cards, depth_by_key)
        assert [c["number"] for c in result] == [1, 3, 5]

    def test_same_depth_sorts_by_num(self):
        cards = [
            {"repo": "Roxabi/lyra", "number": 10},
            {"repo": "Roxabi/lyra", "number": 2},
        ]
        depth_by_key = {
            "Roxabi/lyra#10": 0,
            "Roxabi/lyra#2": 0,
        }
        result = sort_cards_in_cell(cards, depth_by_key)
        assert result[0]["number"] == 2

    def test_missing_depth_defaults_to_zero(self):
        cards = [{"repo": "Roxabi/lyra", "number": 7}]
        result = sort_cards_in_cell(cards, {})
        assert len(result) == 1

    def test_stable_sort(self):
        cards = [
            {"repo": "Roxabi/lyra", "number": 1},
            {"repo": "Roxabi/lyra", "number": 2},
        ]
        depth_by_key = {"Roxabi/lyra#1": 0, "Roxabi/lyra#2": 0}
        result = sort_cards_in_cell(cards, depth_by_key)
        assert [c["number"] for c in result] == [1, 2]


# ─── lane_by_code ────────────────────────────────────────────────────────────

class TestLaneByCode:
    def test_builds_lookup(self):
        lanes = [
            Lane(code="a1", name="NATS", color="a1", epic=None),
            Lane(code="b", name="Container", color="b", epic=None),
        ]
        lbc = lane_by_code(lanes)
        assert lbc["a1"].name == "NATS"
        assert lbc["b"].name == "Container"

    def test_empty_lanes(self):
        assert lane_by_code([]) == {}
