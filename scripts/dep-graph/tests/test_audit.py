"""Unit tests for dep_graph.audit — auto-derive helpers.

All tests use synthetic dicts; no filesystem, no network.
"""

from __future__ import annotations

import pytest
from dep_graph.audit import (
    _build_layout_sets,
    _check_defer,
    _check_meta,
    _check_placement,
    _check_standalone,
    _collect_auto_placed,
)

REPO = "Owner/repo"
PREFIX = "graph:"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _issue(
    num: int,
    *,
    lane: str | None = None,
    standalone: bool = False,
    defer: bool = False,
    state: str = "open",
) -> dict:
    """Build a minimal gh_issues entry."""
    labels: list[str] = []
    if lane is not None:
        labels.append(f"{PREFIX}lane/{lane}")
    if standalone:
        labels.append(f"{PREFIX}standalone")
    if defer:
        labels.append(f"{PREFIX}defer")
    return {
        "repo": REPO,
        "number": num,
        "title": f"Issue #{num}",
        "state": state,
        "labels": labels,
        "lane_label": lane,
        "standalone": standalone,
        "defer": defer,
        "blocked_by": [],
        "blocking": [],
    }


def _gh(*issues: tuple[int, dict]) -> dict:
    """Build a gh_issues dict keyed as '{REPO}#{num}'."""
    return {f"{REPO}#{num}": entry for num, entry in issues}


def _gh_from_list(*entries: dict) -> dict:
    """Build a gh_issues dict from _issue() dicts, keyed as '{repo}#{number}'."""
    return {f"{e['repo']}#{e['number']}": e for e in entries}


# ---------------------------------------------------------------------------
# T8 — G2a _build_layout_sets
# ---------------------------------------------------------------------------


class TestBuildLayoutSets:
    def test_build_layout_sets_lane_with_explicit_order(self) -> None:
        """Explicit order[] → layout_lane_of populated, auto_lane_codes empty."""
        # Arrange
        layout = {
            "lanes": [
                {
                    "code": "a",
                    "order": [{"repo": REPO, "issue": 1}],
                }
            ]
        }
        # Act
        layout_lane_of, standalone_set, epic_set, auto_lane_codes = _build_layout_sets(
            layout
        )
        # Assert
        assert layout_lane_of == {(REPO, 1): "a"}
        assert auto_lane_codes == set()

    def test_build_layout_sets_lane_without_order_added_to_auto_codes(self) -> None:
        """Lane without order key → added to auto_lane_codes, layout_lane_of empty."""
        # Arrange
        layout = {
            "lanes": [
                {"code": "b"}
                # no "order" key
            ]
        }
        # Act
        layout_lane_of, standalone_set, epic_set, auto_lane_codes = _build_layout_sets(
            layout
        )
        # Assert
        assert auto_lane_codes == {"b"}
        assert layout_lane_of == {}

    def test_build_layout_sets_epic_missing_repo_excluded(self) -> None:
        """Epic with no 'repo' field → not added to epic_set (audit.py:301 branch)."""
        # Arrange
        layout = {
            "lanes": [
                {
                    "code": "c",
                    "order": [],
                    "epic": {"issue": 99},  # NO 'repo' field
                }
            ]
        }
        # Act
        layout_lane_of, standalone_set, epic_set, auto_lane_codes = _build_layout_sets(
            layout
        )
        # Assert
        assert epic_set == set()


# ---------------------------------------------------------------------------
# T9 — G2b _collect_auto_placed
# ---------------------------------------------------------------------------


class TestCollectAutoPlaced:
    def test_collect_auto_placed_empty_lane_codes(self) -> None:
        """auto_lane_codes=empty → auto_placed is empty, standalone_set unchanged."""
        # Arrange
        gh = _gh_from_list(_issue(1, lane="x"), _issue(2, lane="y"))
        layout = {"standalone": {"order": [{"repo": REPO, "issue": 10}]}}
        standalone_set = {(REPO, 10)}
        # Act
        auto_placed, updated_standalone = _collect_auto_placed(
            gh, set(), standalone_set, layout, PREFIX
        )
        # Assert
        assert auto_placed == set()
        assert updated_standalone == standalone_set

    def test_collect_auto_placed_populated_match(self) -> None:
        """auto_lane_codes={'x'} → only the 2 x-labeled issues in auto_placed."""
        # Arrange
        gh = _gh_from_list(
            _issue(1, lane="x"),
            _issue(2, lane="x"),
            _issue(3, lane="y"),
        )
        layout = {"standalone": {"order": [{"repo": REPO, "issue": 99}]}}
        standalone_set: set[tuple[str, int]] = set()
        # Act
        auto_placed, _ = _collect_auto_placed(gh, {"x"}, standalone_set, layout, PREFIX)
        # Assert
        assert auto_placed == {(REPO, 1), (REPO, 2)}
        assert (REPO, 3) not in auto_placed

    def test_collect_auto_placed_standalone_absent_extends_set(self) -> None:
        """No standalone.order[] → gh standalone issues extend standalone_set."""
        # Arrange
        gh = _gh_from_list(
            _issue(1, standalone=True),
            _issue(2, standalone=True),
            _issue(3, lane="x"),
        )
        layout = {"standalone": {}}  # no order key → standalone_auto=True
        standalone_set: set[tuple[str, int]] = set()
        # Act
        auto_placed, updated_standalone = _collect_auto_placed(
            gh, set(), standalone_set, layout, PREFIX
        )
        # Assert
        assert (REPO, 1) in updated_standalone
        assert (REPO, 2) in updated_standalone
        assert (REPO, 3) not in updated_standalone


# ---------------------------------------------------------------------------
# T10 — G2c _check_placement
# ---------------------------------------------------------------------------


class TestCheckPlacement:
    def test_check_placement_empty_layout_lane_of_prints_skipped(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Empty layout_lane_of → prints 'skipped' message, returns False (no drift)."""
        # Arrange
        gh = _gh_from_list(_issue(1, lane="a"))
        # All issues are in all_placed so no untriaged drift
        labeled: set[tuple[str, int]] = {(REPO, 1)}
        all_placed: set[tuple[str, int]] = {(REPO, 1)}
        # Act
        result = _check_placement({}, labeled, all_placed, gh, PREFIX)
        # Assert
        captured = capsys.readouterr()
        # Exact skip-branch marker — "skipped" alone would also match if the
        # message drifted elsewhere, so pin the full auto-derive sentinel.
        assert "all lanes auto-derived, skipped" in captured.out
        # Sanity: _check_untriaged ran (proves parse_key path exercised)
        assert "Labeled but not in any lane order" in captured.out
        assert result is False

    def test_check_placement_populated_no_drift(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """All labeled issues are placed → no drift reported."""
        # Arrange
        gh = _gh_from_list(_issue(1, lane="a"), _issue(2, lane="a"))
        layout_lane_of = {(REPO, 1): "a", (REPO, 2): "a"}
        labeled: set[tuple[str, int]] = {(REPO, 1), (REPO, 2)}
        all_placed: set[tuple[str, int]] = {(REPO, 1), (REPO, 2)}
        # Act
        result = _check_placement(layout_lane_of, labeled, all_placed, gh, PREFIX)
        # Assert
        assert result is False


# ---------------------------------------------------------------------------
# T11 — G2d _check_meta forwards auto_placed to _check_defer only
# ---------------------------------------------------------------------------


class TestCheckMetaForwards:
    def test_check_meta_forwards_auto_placed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_check_meta forwards auto_placed to _check_defer, not _check_standalone."""
        # Arrange
        captured: dict = {}

        def stub_defer(
            gh_issues: dict,
            layout: dict,
            label_prefix: str,
            auto_placed: set | None = None,
        ) -> bool:
            captured["defer_auto_placed"] = auto_placed
            return False

        def stub_standalone(*args: object, **kwargs: object) -> bool:
            captured["standalone_args"] = args
            captured["standalone_kwargs"] = kwargs
            return False

        monkeypatch.setattr("dep_graph.audit._check_defer", stub_defer)
        monkeypatch.setattr("dep_graph.audit._check_standalone", stub_standalone)

        test_auto_placed = {("r", 1)}

        # Act
        _check_meta({}, {}, PREFIX, auto_placed=test_auto_placed)

        # Assert — _check_defer received auto_placed
        assert captured["defer_auto_placed"] == test_auto_placed
        # _check_standalone called with exactly 3 positional args (gh, layout, prefix)
        # and NO auto_placed anywhere — would regress if caller plumbed it through
        assert len(captured["standalone_args"]) == 3
        assert "auto_placed" not in captured["standalone_kwargs"]
        assert test_auto_placed not in captured["standalone_args"]


# ---------------------------------------------------------------------------
# T12 — G2e _check_standalone auto-mode
# ---------------------------------------------------------------------------


class TestCheckStandalone:
    def test_check_standalone_auto_mode_empty_order(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No standalone.order[] → auto-mode; prints 'auto-derived', returns False."""
        # Arrange
        layout = {"standalone": {}}  # empty/absent order → auto_mode=True
        gh = _gh_from_list(_issue(1, standalone=True))
        # Act
        result = _check_standalone(gh, layout, PREFIX)
        # Assert
        captured = capsys.readouterr()
        assert "auto-derived" in captured.out
        assert result is False

    def test_check_standalone_explicit_order_detects_drift(self) -> None:
        """Issue #2 standalone in GH but not in layout.standalone.order → drift=True."""
        # Arrange
        layout = {"standalone": {"order": [{"repo": REPO, "issue": 1}]}}
        gh = _gh_from_list(
            _issue(1, standalone=True),
            _issue(2, standalone=True),  # NOT in layout → drift
        )
        # Act
        result = _check_standalone(gh, layout, PREFIX)
        # Assert
        assert result is True


# ---------------------------------------------------------------------------
# T13 — G2f _check_defer auto_placed arg
# ---------------------------------------------------------------------------


class TestCheckDefer:
    def test_check_defer_auto_placed_none_includes_drift(self) -> None:
        """Defer-labeled issue missing from layout → drift when auto_placed=None."""
        # Arrange
        layout: dict = {"lanes": []}  # no layout deferred entries
        gh = _gh_from_list(_issue(5, defer=True))
        # Act
        result = _check_defer(gh, layout, PREFIX)  # no auto_placed
        # Assert
        assert result is True

    def test_check_defer_auto_placed_populated_excludes_item(self) -> None:
        """Same gh but defer issue key in auto_placed → excluded from drift."""
        # Arrange
        layout: dict = {"lanes": []}
        gh = _gh_from_list(_issue(5, defer=True))
        # The issue is auto-placed, so it should NOT count as drift
        auto_placed = {(REPO, 5)}
        # Act
        result = _check_defer(gh, layout, PREFIX, auto_placed=auto_placed)
        # Assert
        assert result is False
