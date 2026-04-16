"""Unit tests for dep_graph.derive — auto-derivation logic.

All tests use synthetic gh_issues dicts; no GitHub API calls.
"""

from __future__ import annotations

import pytest
from dep_graph.derive import (
    _build_par_groups,
    derive_lane,
    derive_standalone_order,
    is_auto_derived_lane,
    is_auto_derived_standalone,
)

REPO = "Owner/repo"


def _issue(
    num: int,
    *,
    lane: str,
    blocked_by: list[int] | None = None,
    state: str = "open",
    milestone: str | None = None,
) -> dict:
    """Build a minimal gh_issues entry."""
    return {
        "repo": REPO,
        "number": num,
        "title": f"Issue #{num}",
        "state": state,
        "labels": [f"graph:lane/{lane}"],
        "lane_label": lane,
        "standalone": False,
        "defer": False,
        "blocked_by": [{"repo": REPO, "issue": b} for b in (blocked_by or [])],
        "blocking": [],
        **({"milestone": milestone} if milestone is not None else {}),
    }


def _lane(code: str) -> dict:
    """Build a minimal lane definition without explicit order."""
    return {"code": code, "name": code.upper(), "color": code}


def _gh(*issues: dict) -> dict:
    """Build a gh_issues dict keyed as 'Owner/repo#N'."""
    return {f"{e['repo']}#{e['number']}": e for e in issues}


# ---------------------------------------------------------------------------
# derive_lane — order tests
# ---------------------------------------------------------------------------


def test_derive_lane_linear_chain_topo_order():
    """Linear chain A→B→C produces correct topo order (A first, C last)."""
    # #1 has no deps, #2 blocked by #1, #3 blocked by #2
    gh = _gh(
        _issue(1, lane="x"),
        _issue(2, lane="x", blocked_by=[1]),
        _issue(3, lane="x", blocked_by=[2]),
    )
    result = derive_lane(_lane("x"), gh, REPO)
    order_nums = [r["issue"] for r in result["order"]]
    assert order_nums == [1, 2, 3]


def test_derive_lane_independent_issues_sorted_by_number():
    """Three independent issues → tie-broken by issue number ascending."""
    gh = _gh(
        _issue(10, lane="y"),
        _issue(3, lane="y"),
        _issue(7, lane="y"),
    )
    result = derive_lane(_lane("y"), gh, REPO)
    order_nums = [r["issue"] for r in result["order"]]
    assert order_nums == [3, 7, 10]


def test_derive_lane_closed_issue_included_in_order():
    """Closed lane-labeled issues are included in order[] for done-styling.

    Since b188e69 ("fix(dep-graph): include closed issues in lane order[]
    for done-styling"), `_collect_lane_issues` no longer filters by state —
    closed issues land in-lane and render as `.card done` in the template.
    """
    gh = _gh(
        _issue(1, lane="z", state="closed"),
        _issue(2, lane="z", blocked_by=[1]),
        _issue(3, lane="z"),
    )
    result = derive_lane(_lane("z"), gh, REPO)
    order_nums = [r["issue"] for r in result["order"]]
    # All three issues placed in-lane, including the closed one
    assert 1 in order_nums
    assert 2 in order_nums
    assert 3 in order_nums


def test_derive_lane_cross_lane_blocker_ignored_for_sort():
    """Cross-lane blocked_by edge is ignored for in-lane topo sort."""
    # #10 is in lane "a", #20 is in lane "b" (different lane)
    # #5 in lane "a" is blocked by #20 (cross-lane) — should still be depth 0 in lane a
    gh = _gh(
        _issue(5, lane="a", blocked_by=[20]),  # blocker #20 is cross-lane
        _issue(8, lane="a"),
        {
            "repo": REPO,
            "number": 20,
            "title": "cross issue",
            "state": "open",
            "labels": ["graph:lane/b"],
            "lane_label": "b",
            "standalone": False,
            "defer": False,
            "blocked_by": [],
            "blocking": [],
        },
    )
    result = derive_lane(_lane("a"), gh, REPO)
    order_nums = [r["issue"] for r in result["order"]]
    # #20 must not appear in lane a (wrong lane)
    assert 20 not in order_nums
    # Both lane-a issues must be present
    assert 5 in order_nums
    assert 8 in order_nums
    # Both at depth 0 (cross-lane blocker doesn't count); tie-break by issue number
    assert order_nums == [5, 8]


def test_derive_lane_explicit_order_respected():
    """Lane with existing order[] is returned unchanged (graceful degradation)."""
    explicit_order = [{"repo": REPO, "issue": 99}]
    lane = {**_lane("q"), "order": explicit_order, "par_groups": {}, "bands": []}
    gh = _gh(_issue(1, lane="q"), _issue(2, lane="q"))
    result = derive_lane(lane, gh, REPO)
    assert result["order"] == explicit_order  # unchanged


def test_derive_lane_cycle_no_exception(capsys):
    """Topological cycle logs a warning and falls back without raising."""
    # #1 blocked by #2, #2 blocked by #1 — mutual cycle
    gh = _gh(
        _issue(1, lane="c", blocked_by=[2]),
        _issue(2, lane="c", blocked_by=[1]),
    )
    result = derive_lane(_lane("c"), gh, REPO)
    order_nums = [r["issue"] for r in result["order"]]
    # Both issues must appear despite the cycle
    assert set(order_nums) == {1, 2}
    # Warning must have been emitted to stderr
    captured = capsys.readouterr()
    assert "cycle" in captured.err


# ---------------------------------------------------------------------------
# derive_lane — par_groups tests
# ---------------------------------------------------------------------------


def test_derive_lane_par_groups_diamond_dag():
    """Diamond DAG A→B, A→C, B→D, C→D produces groups {0:[A], 1:[B,C], 2:[D]}."""
    # #1=A (root), #2=B blocked by #1, #3=C blocked by #1, #4=D blocked by #2 and #3
    gh = _gh(
        _issue(1, lane="d"),
        _issue(2, lane="d", blocked_by=[1]),
        _issue(3, lane="d", blocked_by=[1]),
        _issue(4, lane="d", blocked_by=[2, 3]),
    )
    result = derive_lane(_lane("d"), gh, REPO)
    order_nums = [r["issue"] for r in result["order"]]

    # #1 must come first, #4 must come last
    assert order_nums[0] == 1
    assert order_nums[-1] == 4
    # #2 and #3 must be between #1 and #4
    assert set(order_nums[1:3]) == {2, 3}

    # par_groups: exactly one group for {2,3} (depth 1)
    pg_members = {
        frozenset(m["issue"] for m in members)
        for members in result["par_groups"].values()
    }
    assert frozenset({2, 3}) in pg_members


# ---------------------------------------------------------------------------
# derive_lane — bands tests
# ---------------------------------------------------------------------------


def test_derive_lane_bands_milestone_transitions():
    """Issues with milestones M0, M0, M1, M1, M2 produce 3 band headers.

    Each distinct named milestone gets a band header before its first issue,
    including the very first milestone group in the order.
    """
    gh = _gh(
        _issue(1, lane="b", milestone="M0"),
        _issue(2, lane="b", milestone="M0"),
        _issue(3, lane="b", blocked_by=[2], milestone="M1"),
        _issue(4, lane="b", blocked_by=[2], milestone="M1"),
        _issue(5, lane="b", blocked_by=[3], milestone="M2"),
    )
    result = derive_lane(_lane("b"), gh, REPO)
    bands = result["bands"]
    band_texts = [b["text"] for b in bands]
    # M0 band before #1, M1 band before #3, M2 band before #5
    assert len(bands) == 3
    assert any("M0" in t for t in band_texts)
    assert any("M1" in t for t in band_texts)
    assert any("M2" in t for t in band_texts)
    # Band before #1 (first issue in M0 group)
    assert bands[0]["before"]["issue"] == 1
    # Band before first M1 issue (#3 or #4 — whichever topo-sort puts first)
    m1_band_issue = bands[1]["before"]["issue"]
    assert m1_band_issue in (3, 4)


def test_derive_lane_bands_no_milestone_no_bands():
    """Issues with no milestone field produce no bands."""
    gh = _gh(
        _issue(1, lane="e"),
        _issue(2, lane="e"),
    )
    result = derive_lane(_lane("e"), gh, REPO)
    assert result["bands"] == []


# ---------------------------------------------------------------------------
# derive_standalone_order tests
# ---------------------------------------------------------------------------


def test_derive_standalone_order_returns_labeled_sorted():
    """gh_issues with standalone=True are returned sorted by issue number."""
    gh = {
        f"{REPO}#10": {
            "repo": REPO,
            "number": 10,
            "title": "sa10",
            "state": "open",
            "labels": ["graph:standalone"],
            "lane_label": None,
            "standalone": True,
            "defer": False,
            "blocked_by": [],
            "blocking": [],
        },
        f"{REPO}#3": {
            "repo": REPO,
            "number": 3,
            "title": "sa3",
            "state": "open",
            "labels": ["graph:standalone"],
            "lane_label": None,
            "standalone": True,
            "defer": False,
            "blocked_by": [],
            "blocking": [],
        },
        f"{REPO}#7": {
            "repo": REPO,
            "number": 7,
            "title": "not-standalone",
            "state": "open",
            "labels": ["graph:lane/x"],
            "lane_label": "x",
            "standalone": False,
            "defer": False,
            "blocked_by": [],
            "blocking": [],
        },
    }
    result = derive_standalone_order(gh, REPO)
    issue_nums = [r["issue"] for r in result]
    assert issue_nums == [3, 10]


def test_derive_standalone_order_excludes_closed():
    """Closed standalone issues are excluded."""
    gh = {
        f"{REPO}#1": {
            "repo": REPO,
            "number": 1,
            "state": "closed",
            "title": "old",
            "labels": [],
            "lane_label": None,
            "standalone": True,
            "defer": False,
            "blocked_by": [],
            "blocking": [],
        },
        f"{REPO}#2": {
            "repo": REPO,
            "number": 2,
            "state": "open",
            "title": "current",
            "labels": [],
            "lane_label": None,
            "standalone": True,
            "defer": False,
            "blocked_by": [],
            "blocking": [],
        },
    }
    result = derive_standalone_order(gh, REPO)
    issue_nums = [r["issue"] for r in result]
    assert issue_nums == [2]


# ---------------------------------------------------------------------------
# T2 (G4) — Epic exclusion
# ---------------------------------------------------------------------------


def test_derive_lane_epic_excluded_same_repo():
    """Epic issue in same repo is excluded from derived order."""
    lane = {**_lane("ep"), "epic": {"repo": REPO, "issue": 5}}
    gh = _gh(
        _issue(5, lane="ep"),
        _issue(6, lane="ep"),
    )
    result = derive_lane(lane, gh, REPO)
    order_nums = [r["issue"] for r in result["order"]]
    assert 5 not in order_nums
    assert 6 in order_nums


def test_derive_lane_epic_not_excluded_when_issue_is_cross_repo():
    """Cross-repo issue matching epic number is NOT excluded.

    _collect_lane_issues excludes only when `num == epic_issue_num AND
    repo == primary_repo` (derive.py:134). An issue whose repo differs from
    primary_repo falls through — this is the dark branch the same-repo test
    above does not exercise.
    """
    lane = {**_lane("ep2"), "epic": {"repo": REPO, "issue": 5}}
    cross_repo_entry = {
        "repo": "Other/other",
        "number": 5,
        "title": "cross #5",
        "state": "open",
        "labels": ["graph:lane/ep2"],
        "lane_label": "ep2",
        "standalone": False,
        "defer": False,
        "blocked_by": [],
        "blocking": [],
    }
    gh = {"Other/other#5": cross_repo_entry, **_gh(_issue(6, lane="ep2"))}
    result = derive_lane(lane, gh, REPO)
    order_pairs = [(r["repo"], r["issue"]) for r in result["order"]]
    # Cross-repo #5 IS included (repo != primary_repo, so exclusion skipped)
    assert ("Other/other", 5) in order_pairs
    # Primary-repo #6 also included
    assert (REPO, 6) in order_pairs


# ---------------------------------------------------------------------------
# T3 (G5) — derive_standalone_order malformed + default_repo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entry",
    [
        None,
        {},
        {"standalone": True},
        {"standalone": False, "number": 1, "state": "open"},
    ],
)
def test_derive_standalone_order_skips_malformed(entry):
    """Malformed or non-standalone entries produce empty result."""
    gh = {"key": entry}
    result = derive_standalone_order(gh, REPO)
    assert result == []


def test_derive_standalone_order_defaults_repo():
    """Entry with standalone=True but no repo field defaults to primary_repo."""
    gh = {
        "norepkey": {
            "standalone": True,
            "number": 5,
            "state": "open",
        }
    }
    result = derive_standalone_order(gh, REPO)
    assert result == [{"repo": REPO, "issue": 5}]


# ---------------------------------------------------------------------------
# T4 (G6) — Empty-lane fast path
# ---------------------------------------------------------------------------


def test_derive_lane_empty_fast_path():
    """Lane with no matching issues produces empty order, par_groups, and bands."""
    result = derive_lane(_lane("nomatch"), _gh(_issue(1, lane="other")), REPO)
    assert result["order"] == []
    assert result["par_groups"] == {}
    assert result["bands"] == []


# ---------------------------------------------------------------------------
# T5 (G7) — Single-node par_groups guard
# ---------------------------------------------------------------------------


def test_derive_lane_single_node_no_par_group():
    """Single issue in a lane produces no par_groups entry."""
    result = derive_lane(_lane("s"), _gh(_issue(1, lane="s")), REPO)
    assert result["par_groups"] == {}


# ---------------------------------------------------------------------------
# T6 (G8) — Milestone transitions
# ---------------------------------------------------------------------------


def test_derive_bands_none_to_named():
    """None → named milestone inserts 1 band before the named issue."""
    gh = _gh(
        _issue(1, lane="mb1"),
        _issue(2, lane="mb1", blocked_by=[1], milestone="M1"),
    )
    result = derive_lane(_lane("mb1"), gh, REPO)
    bands = result["bands"]
    assert len(bands) == 1
    assert bands[0]["before"]["issue"] == 2


def test_derive_bands_named_to_none():
    """Named → None milestone: band only for the named group."""
    gh = _gh(
        _issue(1, lane="mb2", milestone="M1"),
        _issue(2, lane="mb2", blocked_by=[1]),
    )
    result = derive_lane(_lane("mb2"), gh, REPO)
    bands = result["bands"]
    # One band for M1 before #1; no band when transitioning from named to None
    assert len(bands) == 1
    assert bands[0]["before"]["issue"] == 1


def test_derive_bands_all_same_milestone():
    """All issues sharing one milestone produce exactly 1 band at the start."""
    gh = _gh(
        _issue(1, lane="mb3", milestone="M1"),
        _issue(2, lane="mb3", blocked_by=[1], milestone="M1"),
        _issue(3, lane="mb3", blocked_by=[2], milestone="M1"),
    )
    result = derive_lane(_lane("mb3"), gh, REPO)
    bands = result["bands"]
    assert len(bands) == 1
    assert bands[0]["before"]["issue"] == 1


# ---------------------------------------------------------------------------
# T7 (G9) — _build_par_groups has_inner_edge skip
# ---------------------------------------------------------------------------


def test_build_par_groups_has_inner_edge_skip():
    """Bucket with an intra-bucket edge is skipped (no par_group created)."""
    # Issues 2 and 3 both at depth 1; edge 2→3 is an inner edge
    sorted_issues = [(REPO, 1), (REPO, 2), (REPO, 3)]
    depth_map = {(REPO, 1): 0, (REPO, 2): 1, (REPO, 3): 1}
    edges = {(REPO, 2): [(REPO, 3)], (REPO, 3): []}
    result = _build_par_groups("lane", sorted_issues, depth_map, edges)
    # Depth-1 bucket has inner edge → must be skipped
    assert not any(
        {m["issue"] for m in members} == {2, 3} for members in result.values()
    )


def test_build_par_groups_creates_group_without_inner_edge():
    """Bucket with no intra-bucket edges creates a par_group entry."""
    sorted_issues = [(REPO, 1), (REPO, 2), (REPO, 3)]
    depth_map = {(REPO, 1): 0, (REPO, 2): 1, (REPO, 3): 1}
    edges = {(REPO, 2): [], (REPO, 3): []}
    result = _build_par_groups("lane", sorted_issues, depth_map, edges)
    # Depth-1 bucket has no inner edges → must appear as a par_group
    assert any({m["issue"] for m in members} == {2, 3} for members in result.values())


# ---------------------------------------------------------------------------
# Predicate helpers (#741 item 2, 7)
# ---------------------------------------------------------------------------


def test_predicate_lane_with_order_is_explicit():
    assert is_auto_derived_lane({"code": "A", "order": []}) is False
    assert (
        is_auto_derived_lane({"code": "A", "order": [{"repo": "r", "issue": 1}]})
        is False
    )


def test_predicate_lane_without_order_is_auto():
    assert is_auto_derived_lane({"code": "A"}) is True
    assert is_auto_derived_lane({"code": "A", "name": "Alpha"}) is True


def test_predicate_standalone_missing_key_is_auto():
    assert is_auto_derived_standalone({}) is True


def test_predicate_standalone_empty_object_is_auto():
    assert is_auto_derived_standalone({"standalone": {}}) is True


def test_predicate_standalone_empty_order_is_auto():
    assert is_auto_derived_standalone({"standalone": {"order": []}}) is True


def test_predicate_standalone_non_empty_order_is_explicit():
    layout = {"standalone": {"order": [{"repo": "r", "issue": 1}]}}
    assert is_auto_derived_standalone(layout) is False
