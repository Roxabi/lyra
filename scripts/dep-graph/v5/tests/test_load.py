"""Tests for v5.data.load — load_from_dicts, parse logic, and corpus.db-based load().

RED state for corpus-based load() tests: T5 has not landed yet.
Tests asserting the new load() contract (db_path param, PRIMARY_REPO constant,
removal of FORGE/CACHE_PATH) will FAIL until T5 is implemented.

Tests for load_from_dicts() are orthogonal and must stay GREEN.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from v5.data.load import load_from_dicts
from v5.data.model import COLUMN_GROUPS, MILESTONES, GraphData
from v5.tests.conftest import LAYOUT


# ─── Helpers ────────────────────────────────────────────────────────────────


def _bootstrap_corpus(db_path: Path) -> None:
    """Bootstrap an empty corpus DB schema via scripts.corpus.schema."""
    from scripts.corpus.schema import bootstrap

    bootstrap(db_path)


def _seed_open_lyra_issue(db_path: Path, number: int, lane: str) -> None:
    """Insert one open Roxabi/lyra issue with a graph:lane label."""
    conn = sqlite3.connect(db_path)
    try:
        key = f"Roxabi/lyra#{number}"
        conn.execute(
            """INSERT INTO issues (key, repo, number, title, state, milestone, is_stub)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (key, "Roxabi/lyra", number, f"Issue {number}", "open", "M0 alpha", 0),
        )
        conn.execute(
            "INSERT INTO labels (issue_key, name) VALUES (?, ?)",
            (key, f"graph:lane/{lane}"),
        )
        conn.commit()
    finally:
        conn.close()


def _write_layout(tmp_path: Path) -> Path:
    """Write the shared LAYOUT fixture to a JSON file and return its path."""
    layout_path = tmp_path / "layout.json"
    layout_path.write_text(json.dumps(LAYOUT))
    return layout_path


# ─── Module-level constant tests ────────────────────────────────────────────


class TestLoadModuleConstants:
    def test_primary_repo_constant_exists(self) -> None:
        """PRIMARY_REPO module constant must be 'Roxabi/lyra' (T5 contract)."""
        # Arrange / Act
        import v5.data.load as loadmod

        # Assert
        assert loadmod.PRIMARY_REPO == "Roxabi/lyra"

    def test_forge_constant_removed(self) -> None:
        """FORGE module constant must be absent after T5 lands."""
        # Arrange / Act
        import v5.data.load as loadmod

        # Assert
        assert not hasattr(loadmod, "FORGE")

    def test_cache_path_constant_removed(self) -> None:
        """CACHE_PATH module constant must be absent after T5 lands."""
        # Arrange / Act
        import v5.data.load as loadmod

        # Assert
        assert not hasattr(loadmod, "CACHE_PATH")


# ─── load() — corpus.db contract ────────────────────────────────────────────


class TestLoadCorpusContract:
    def test_load_empty_corpus_returns_empty_graph_data(
        self, tmp_path: Path
    ) -> None:
        """load() with empty corpus DB returns GraphData with issues == {} and no matrix cells."""
        # Arrange
        from v5.data.load import load

        db_path = tmp_path / "corpus.db"
        _bootstrap_corpus(db_path)
        layout_path = _write_layout(tmp_path)

        # Act
        result = load(layout_path=layout_path, db_path=db_path)

        # Assert
        assert isinstance(result, GraphData)
        assert result.issues == {}
        assert result.visible == set()
        assert result.total == 0
        assert result.matrix == {}

    def test_load_seeded_corpus_visible_contains_open_lyra_issue(
        self, tmp_path: Path
    ) -> None:
        """load() with seeded corpus includes open Roxabi/lyra issue in visible set."""
        # Arrange
        from v5.data.load import load

        db_path = tmp_path / "corpus.db"
        _bootstrap_corpus(db_path)
        _seed_open_lyra_issue(db_path, number=1, lane="a1")
        _seed_open_lyra_issue(db_path, number=2, lane="b")
        layout_path = _write_layout(tmp_path)

        # Act
        result = load(layout_path=layout_path, db_path=db_path)

        # Assert — at least one open primary-repo issue is in visible
        assert len(result.visible) > 0
        # "Roxabi/lyra#1" is open and has a lane label → must be in visible
        assert "Roxabi/lyra#1" in result.visible

    def test_load_missing_db_raises_file_not_found_with_hint(
        self, tmp_path: Path
    ) -> None:
        """load() with non-existent db_path raises FileNotFoundError mentioning make corpus-sync."""
        # Arrange
        from v5.data.load import load

        layout_path = _write_layout(tmp_path)
        db_path = tmp_path / "nope.db"

        # Act / Assert
        with pytest.raises(FileNotFoundError, match="make corpus-sync"):
            load(layout_path=layout_path, db_path=db_path)


# ─── load_from_dicts() — unchanged contract ─────────────────────────────────


class TestLoadFromDicts:
    def test_returns_graph_data(self, layout, gh):
        result = load_from_dicts(layout, gh)
        assert isinstance(result, GraphData)

    def test_primary_repo(self, layout, gh):
        data = load_from_dicts(layout, gh)
        assert data.primary_repo == "Roxabi/lyra"

    def test_lanes_parsed(self, layout, gh):
        data = load_from_dicts(layout, gh)
        assert len(data.lanes) == len(layout["lanes"])

    def test_lane_by_code_populated(self, layout, gh):
        data = load_from_dicts(layout, gh)
        assert "a1" in data.lane_by_code
        assert "b" in data.lane_by_code
        assert "e" in data.lane_by_code

    def test_lane_epic_metadata_parsed(self, layout, gh):
        data = load_from_dicts(layout, gh)
        lane_a1 = data.lane_by_code["a1"]
        assert lane_a1.epic is not None
        assert lane_a1.epic.issue == 100
        assert lane_a1.epic.label == "NATS hardening"
        assert lane_a1.epic.tag == "M0-NATS"

    def test_lane_without_epic(self, layout, gh):
        data = load_from_dicts(layout, gh)
        # Lane a2 has no epic in fixture
        lane_a2 = data.lane_by_code["a2"]
        assert lane_a2.epic is None

    def test_issues_loaded(self, layout, gh):
        data = load_from_dicts(layout, gh)
        assert "Roxabi/lyra#1" in data.issues
        assert "Roxabi/lyra#4" in data.issues

    def test_epic_keys_detected(self, layout, gh):
        data = load_from_dicts(layout, gh)
        # All 5 epic issues should be detected
        for n in [100, 101, 102, 103, 104]:
            assert f"Roxabi/lyra#{n}" in data.epic_keys

    def test_matrix_populated(self, layout, gh):
        data = load_from_dicts(layout, gh)
        # At least one non-epic task should be in the matrix
        assert data.total > 0
        assert len(data.matrix) > 0

    def test_total_excludes_epics(self, layout, gh):
        data = load_from_dicts(layout, gh)
        # Fixture has 10 issues including 5 epics and 1 cross-repo voiceCLI#10
        # Tasks in the matrix = issues with milestone + lane + not epic
        # voiceCLI#10 has no milestone/lane so also excluded
        assert data.total >= 1

    def test_counts_keys_present(self, layout, gh):
        data = load_from_dicts(layout, gh)
        assert "ready" in data.counts
        assert "blocked" in data.counts
        assert "done" in data.counts

    def test_counts_sum_to_total(self, layout, gh):
        data = load_from_dicts(layout, gh)
        c = data.counts
        assert c["ready"] + c["blocked"] + c["done"] == data.total

    def test_depth_by_key_populated(self, layout, gh):
        data = load_from_dicts(layout, gh)
        assert isinstance(data.depth_by_key, dict)
        # issue 1 is root → depth 0
        assert data.depth_by_key.get("Roxabi/lyra#1") == 0
        # issue 2 blocked by 1 → depth 1
        assert data.depth_by_key.get("Roxabi/lyra#2") == 1
        # issue 3 blocked by 2 → depth 2
        assert data.depth_by_key.get("Roxabi/lyra#3") == 2

    def test_load_uses_primary_repo_constant_not_meta_repos(self):
        # load_from_dicts no longer reads layout.meta.repos — it uses the
        # PRIMARY_REPO module constant. meta.repos stays in the schema for
        # Phase 3's toolbar dropdown to enumerate available projects.
        from v5.data.load import PRIMARY_REPO

        layout_without_repos = {"meta": {}, "lanes": []}
        result = load_from_dicts(layout_without_repos, {"issues": {}})
        # Visibility computed against PRIMARY_REPO; empty-issues corpus → empty set.
        assert result.visible == set()
        assert PRIMARY_REPO == "Roxabi/lyra"

    def test_missing_lanes_key_raises(self):
        bad_layout = {"meta": {"repos": ["Roxabi/lyra"]}}
        with pytest.raises(KeyError):
            load_from_dicts(bad_layout, {"issues": {}})

    def test_empty_issues_gh(self, layout):
        data = load_from_dicts(layout, {"issues": {}})
        assert data.total == 0
        assert data.issues == {}

    def test_meta_preserved(self, layout, gh):
        data = load_from_dicts(layout, gh)
        assert data.meta["title"] == "Lyra v2 dep graph"
        assert data.meta["date"] == "2026-04-20"


class TestLayoutDrivenMatrixConfig:
    """layout.json milestones + column_groups override module defaults."""

    def test_defaults_when_absent(self, layout, gh):
        # Fixture layout has neither 'milestones' nor 'column_groups' keys
        data = load_from_dicts(layout, gh)
        assert data.column_groups == list(COLUMN_GROUPS)
        assert data.milestones == list(MILESTONES)

    def test_column_groups_override(self, layout, gh):
        custom = dict(layout)
        custom["column_groups"] = [
            {"label": "ALPHA", "tone": "a1", "lane_codes": ["a1", "a2"]},
            {"label": "BETA", "tone": "b", "lane_codes": ["b"]},
        ]
        data = load_from_dicts(custom, gh)
        assert data.column_groups == [
            ("ALPHA", "a1", ["a1", "a2"]),
            ("BETA", "b", ["b"]),
        ]
        # Isolation: override must not mutate the module-level default
        assert data.column_groups is not COLUMN_GROUPS

    def test_milestones_override(self, layout, gh):
        custom = dict(layout)
        custom["milestones"] = [
            {"label": "Phase one", "code": "P1", "short": "Phase 1"},
            {"label": "Phase two", "code": "P2", "short": "Phase 2"},
        ]
        data = load_from_dicts(custom, gh)
        assert data.milestones == [
            ("Phase one", "P1", "Phase 1"),
            ("Phase two", "P2", "Phase 2"),
        ]
        assert data.ms_codes == ["P1", "P2"]
        assert data.ms_name_by_code == {"P1": "Phase 1", "P2": "Phase 2"}

    def test_partial_override_keeps_other_default(self, layout, gh):
        custom = dict(layout)
        custom["milestones"] = [
            {"label": "Only one", "code": "X", "short": "X"},
        ]
        data = load_from_dicts(custom, gh)
        assert len(data.milestones) == 1
        # column_groups still the default
        assert data.column_groups == list(COLUMN_GROUPS)

    def test_empty_override_lists_yield_empty_matrix_config(self, layout, gh):
        custom = dict(layout)
        custom["column_groups"] = []
        custom["milestones"] = []
        data = load_from_dicts(custom, gh)
        assert data.column_groups == []
        assert data.milestones == []
