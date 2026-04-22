"""Tests for v5.data.derive — pure logic layer."""

from __future__ import annotations

from v5.data.derive import (
    build_matrix,
    compute_depth,
    compute_visible,
    epic_keys,
    lane_by_code,
    sort_cards_in_cell,
    status_of,
    tasks_for_graph,
)
from v5.data.model import COLUMN_GROUPS, MILESTONES, EpicMeta, GraphData, Lane, ref_key

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
        Lane(
            code="a1",
            name="NATS",
            color="a1",
            epic=EpicMeta(issue=100, label="Epic", tag="M0"),
        ),
        Lane(code="b", name="Container", color="b", epic=None),
    ]
    lbc = {lane.code: lane for lane in lanes}
    data = GraphData(
        meta={"repos": ["Roxabi/lyra"]},
        lanes=lanes,
        lane_by_code=lbc,
        issues=issues,
        column_groups=list(COLUMN_GROUPS),
        milestones=list(MILESTONES),
        epic_keys=epic_ks or set(),
        visible=set(issues.keys()),
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
            1,
            blocked_by=[],
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
        a = _make_issue(1, blocked_by=[{"repo": "Roxabi/voiceCLI", "issue": 10}])
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


# ─── compute_visible ─────────────────────────────────────────────────────────


class TestComputeVisible:
    def test_seeds_open_primary_items(self):
        a = _make_issue(1, state="open")
        b = _make_issue(2, state="open", repo="Roxabi/other")
        issues = _issues(a, b)
        visible = compute_visible(issues, "Roxabi/lyra")
        assert "Roxabi/lyra#1" in visible
        assert "Roxabi/other#2" not in visible

    def test_excludes_closed_primary_items_not_on_chain(self):
        a = _make_issue(1, state="closed")
        issues = _issues(a)
        visible = compute_visible(issues, "Roxabi/lyra")
        assert visible == set()

    def test_forward_cascade_any_state(self):
        a = _make_issue(
            1,
            state="open",
            blocking=[{"repo": "Roxabi/lyra", "issue": 2}],
        )
        b = _make_issue(
            2,
            state="closed",
            blocking=[{"repo": "Roxabi/lyra", "issue": 3}],
        )
        c = _make_issue(3, state="open")
        issues = _issues(a, b, c)
        visible = compute_visible(issues, "Roxabi/lyra")
        assert {"Roxabi/lyra#1", "Roxabi/lyra#2", "Roxabi/lyra#3"} <= visible

    def test_one_step_backward_any_state_any_repo(self):
        blocker = _make_issue(10, state="closed", repo="Roxabi/voiceCLI")
        iss = _make_issue(
            1,
            state="open",
            blocked_by=[{"repo": "Roxabi/voiceCLI", "issue": 10}],
        )
        issues = _issues(blocker, iss)
        visible = compute_visible(issues, "Roxabi/lyra")
        assert "Roxabi/voiceCLI#10" in visible

    def test_backward_full_closure_same_repo(self):
        # New rule: full backward closure within tree(P) — grand (#20) is reached
        # because #10 is in tree(P) and #10 is blocked_by #20 (same repo, no hop cap).
        grand = _make_issue(20, state="closed")
        blocker = _make_issue(
            10,
            state="closed",
            blocked_by=[{"repo": "Roxabi/lyra", "issue": 20}],
        )
        iss = _make_issue(
            1,
            state="open",
            blocked_by=[{"repo": "Roxabi/lyra", "issue": 10}],
        )
        issues = _issues(grand, blocker, iss)
        visible = compute_visible(issues, "Roxabi/lyra")
        assert "Roxabi/lyra#10" in visible
        assert "Roxabi/lyra#20" in visible


# ─── build_matrix ────────────────────────────────────────────────────────────


class TestBuildMatrix:
    def test_skips_epic_keys(self):
        epic = _make_issue(100)
        task = _make_issue(1)
        issues = _issues(epic, task)
        data = _minimal_data(issues, epic_ks={"Roxabi/lyra#100"})
        matrix, _counts, total = build_matrix(data)
        assert total == 1
        # Epic should not be in matrix
        for cell_issues in matrix.values():
            nums = [i["number"] for i in cell_issues]
            assert 100 not in nums

    def test_items_missing_milestone_go_to_no_ms_sentinel(self):
        from v5.data.model import NO_MS

        task = _make_issue(1, milestone=None)
        data = _minimal_data(_issues(task))
        matrix, _, total = build_matrix(data)
        assert total == 1
        assert (NO_MS, "a1") in matrix

    def test_items_missing_lane_go_to_no_lane_sentinel(self):
        from v5.data.model import NO_LANE

        task = _make_issue(1)
        task["lane_label"] = None
        data = _minimal_data(_issues(task))
        matrix, _, total = build_matrix(data)
        assert total == 1
        assert ("M0  NATS hardening", NO_LANE) in matrix

    def test_items_not_in_visibility_set_excluded(self):
        task = _make_issue(1)
        issues = _issues(task)
        data = _minimal_data(issues)
        data.visible = set()  # force empty visibility
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
        required = {
            "key",
            "repo",
            "num",
            "title",
            "url",
            "state",
            "status",
            "milestone",
            "lane",
            "column",
            "depth",
            "blockers",
            "unblocks",
        }
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


# ─── compute_visible — new algebra (T8 RED tests) ────────────────────────────
#
# These tests pin the contract for the new rule:
#   compute_visible = tree(P) ∪ ⋃_Q shared_subtree(Q, P)
#
# All four MUST fail against the current (old) implementation.


class TestComputeVisibleNewAlgebra:
    @staticmethod
    def _ref(repo: str, n: int) -> dict:
        """Compact ref builder: {repo, issue}."""
        return {"repo": repo, "issue": n}

    def test_tree_full_backward_closure(self):
        # Old rule: 1-hop backward — stops at {A, B}.
        # New rule: full backward closure through tree(P) — must reach C.
        #
        # A#1 open, blocked_by B#2
        # B#2 closed, blocked_by C#3, blocking A#1
        # C#3 closed, blocking B#2
        repo = "Roxabi/lyra"
        a = _make_issue(
            1,
            state="open",
            repo=repo,
            blocked_by=[self._ref(repo, 2)],
        )
        b = _make_issue(
            2,
            state="closed",
            repo=repo,
            blocked_by=[self._ref(repo, 3)],
            blocking=[self._ref(repo, 1)],
        )
        c = _make_issue(
            3,
            state="closed",
            repo=repo,
            blocking=[self._ref(repo, 2)],
        )
        issues = _issues(a, b, c)

        visible = compute_visible(issues, repo)

        assert visible >= {f"{repo}#1", f"{repo}#2", f"{repo}#3"}

    def test_shared_subtree_empty_when_disjoint(self):
        # voiceCLI issues have no edge touching lyra — shared_subtree is empty.
        # Only lyra#1 (open, no edges) should be visible.
        lyra = "Roxabi/lyra"
        voice = "Roxabi/voiceCLI"
        l1 = _make_issue(1, state="open", repo=lyra)
        v10 = _make_issue(10, state="open", repo=voice)
        v11 = _make_issue(
            11,
            state="closed",
            repo=voice,
            blocking=[self._ref(voice, 10)],
        )
        issues = _issues(l1, v10, v11)

        visible = compute_visible(issues, lyra)

        assert visible == {f"{lyra}#1"}

    def test_shared_subtree_qlocal_closure(self):
        # lyra#1 is blocked by voiceCLI#10.
        # voiceCLI#10 is blocked by voiceCLI#11, which is blocked by voiceCLI#12.
        # The Q-local chain (#10 → #11 → #12) should all be pulled in via
        # shared_subtree — the old 1-hop backward rule would stop at #10.
        lyra = "Roxabi/lyra"
        voice = "Roxabi/voiceCLI"
        l1 = _make_issue(
            1,
            state="open",
            repo=lyra,
            blocked_by=[self._ref(voice, 10)],
        )
        v10 = _make_issue(
            10,
            state="closed",
            repo=voice,
            blocking=[self._ref(lyra, 1)],
            blocked_by=[self._ref(voice, 11)],
        )
        v11 = _make_issue(
            11,
            state="closed",
            repo=voice,
            blocking=[self._ref(voice, 10)],
            blocked_by=[self._ref(voice, 12)],
        )
        v12 = _make_issue(
            12,
            state="closed",
            repo=voice,
            blocking=[self._ref(voice, 11)],
        )
        issues = _issues(l1, v10, v11, v12)

        visible = compute_visible(issues, lyra)

        assert visible >= {
            f"{lyra}#1",
            f"{voice}#10",
            f"{voice}#11",
            f"{voice}#12",
        }

    def test_superset_contains_voicecli_chain(self):
        # Approximates the real lyra graph:
        #   lyra has several open issues; one is transitively blocked by
        #   voiceCLI#83.  voiceCLI#69 (open) blocks voiceCLI#83, forming a
        #   Q-local sub-chain that must appear in the new visible set.
        #
        # The inline _old_compute_visible helper mirrors the CURRENT rule so we
        # can prove: old_visible ⊆ new_visible (nothing regresses).
        lyra = "Roxabi/lyra"
        voice = "Roxabi/voiceCLI"

        # lyra issues
        l1 = _make_issue(1, state="open", repo=lyra)
        l2 = _make_issue(2, state="open", repo=lyra)
        l3 = _make_issue(
            3,
            state="open",
            repo=lyra,
            blocked_by=[self._ref(lyra, 4)],
        )
        l4 = _make_issue(
            4,
            state="closed",
            repo=lyra,
            blocking=[self._ref(lyra, 3)],
            blocked_by=[self._ref(voice, 83)],
        )
        l5 = _make_issue(5, state="open", repo=lyra)

        # voiceCLI sub-chain: #69 → #83 (69 blocks 83)
        v83 = _make_issue(
            83,
            state="closed",
            repo=voice,
            blocking=[self._ref(lyra, 4)],
            blocked_by=[self._ref(voice, 69)],
        )
        v69 = _make_issue(
            69,
            state="open",
            repo=voice,
            blocking=[self._ref(voice, 83)],
        )

        issues = _issues(l1, l2, l3, l4, l5, v83, v69)

        new_visible = compute_visible(issues, lyra)

        # Named chain must be present
        assert f"{voice}#83" in new_visible
        assert f"{voice}#69" in new_visible

        # Superset invariant: old rule ⊆ new rule
        def _old_compute_visible(iss: dict, primary: str) -> set[str]:
            """Inline replica of the CURRENT (old) 1-hop-backward rule."""
            vis: set[str] = {
                k
                for k, i in iss.items()
                if i.get("repo") == primary and i.get("state") == "open"
            }
            stk = list(vis)
            while stk:
                for r in iss.get(stk.pop(), {}).get("blocking", []):
                    rk = ref_key(r)
                    if rk in iss and rk not in vis:
                        vis.add(rk)
                        stk.append(rk)
            for k in list(vis):
                for r in iss.get(k, {}).get("blocked_by", []):
                    rk = ref_key(r)
                    if rk in iss:
                        vis.add(rk)
            return vis

        old_visible = _old_compute_visible(issues, lyra)
        assert old_visible <= new_visible

    def test_cycle_does_not_infinite_loop(self):
        # A ↔ B cycle (each blocks the other). The closure visited-set
        # guard must terminate without RecursionError.
        lyra = "Roxabi/lyra"
        a = _make_issue(
            1,
            state="open",
            repo=lyra,
            blocking=[self._ref(lyra, 2)],
            blocked_by=[self._ref(lyra, 2)],
        )
        b = _make_issue(
            2,
            state="closed",
            repo=lyra,
            blocking=[self._ref(lyra, 1)],
            blocked_by=[self._ref(lyra, 1)],
        )
        issues = _issues(a, b)

        visible = compute_visible(issues, lyra)

        assert visible == {f"{lyra}#1", f"{lyra}#2"}
