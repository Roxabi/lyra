"""Tests for v5.data.corpus — corpus.db adapter + lane/size projector.

RED state: scripts/dep-graph/v5/data/corpus.py does not exist yet.
All tests will fail with ModuleNotFoundError until T2 (implementation) lands.

Tests cover:
- _project_lane_size() — unit tests for the isolated label projector
- load_issues() — integration tests seeding a real sqlite DB via
  scripts.corpus.schema.bootstrap + raw INSERTs, asserting the projected
  issue dict shape matches spec §Data Model.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from v5.data.corpus import _project_lane_size, load_issues


# ─── Unit: _project_lane_size ────────────────────────────────────────────────


def test_project_lane_size_strips_prefixes() -> None:
    """First graph:lane/* and size:* labels are stripped to bare values."""
    # Arrange
    labels = ["graph:lane/a2", "size:S", "foo"]

    # Act
    lane, size = _project_lane_size(labels)

    # Assert
    assert lane == "a2"
    assert size == "S"


def test_project_lane_size_first_wins() -> None:
    """When multiple matching labels exist, the first match wins."""
    # Arrange
    labels = ["graph:lane/a1", "graph:lane/a2", "size:S", "size:M"]

    # Act
    lane, size = _project_lane_size(labels)

    # Assert
    assert lane == "a1"
    assert size == "S"


def test_project_lane_size_returns_none_when_absent() -> None:
    """Returns (None, None) when neither graph:lane/ nor size: labels present."""
    # Arrange
    labels = ["graph:standalone", "priority:high", "unrelated"]

    # Act
    lane, size = _project_lane_size(labels)

    # Assert
    assert lane is None
    assert size is None


def test_project_lane_size_empty_labels() -> None:
    """Degenerate case: empty label list → (None, None) — exercised for
    every issue with no labels in _fetch_issues."""
    assert _project_lane_size([]) == (None, None)


# ─── Integration: load_issues ────────────────────────────────────────────────


def _seed_db(db_path: Path) -> None:
    """Bootstrap schema and insert one issue with labels, no edges."""
    from scripts.corpus.schema import bootstrap

    bootstrap(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO issues (key, repo, number, title, state, milestone, is_stub)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                "Roxabi/lyra#1",
                "Roxabi/lyra",
                1,
                "Test issue",
                "open",
                "M1 alpha",
                0,
            ),
        )
        for label in ["graph:lane/a1", "size:M", "graph:standalone"]:
            conn.execute(
                "INSERT INTO labels (issue_key, name) VALUES (?, ?)",
                ("Roxabi/lyra#1", label),
            )
        conn.commit()
    finally:
        conn.close()


def test_load_issues_projects_all_fields(tmp_path: Path) -> None:
    """Projected dict carries every field documented in spec §Data Model."""
    # Arrange
    db_path = tmp_path / "corpus.db"
    _seed_db(db_path)

    # Act
    issues = load_issues(db_path)

    # Assert
    assert "Roxabi/lyra#1" in issues
    issue = issues["Roxabi/lyra#1"]

    assert issue["repo"] == "Roxabi/lyra"
    assert issue["number"] == 1
    assert issue["title"] == "Test issue"
    assert issue["state"] == "open"
    assert set(issue["labels"]) == {"graph:lane/a1", "size:M", "graph:standalone"}
    assert issue["lane_label"] == "a1"
    assert issue["standalone"] is True
    assert issue["defer"] is False
    assert issue["milestone"] == "M1 alpha"
    assert issue["size"] == "M"
    assert issue["blocking"] == []
    assert issue["blocked_by"] == []


def test_edges_populate_blocking_and_blocked_by(tmp_path: Path) -> None:
    """Edge src blocks dst → A.blocking has B ref; B.blocked_by has A ref."""
    # Arrange
    from scripts.corpus.schema import bootstrap

    db_path = tmp_path / "corpus.db"
    bootstrap(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            """INSERT INTO issues (key, repo, number, title, state, is_stub)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                ("Roxabi/lyra#1", "Roxabi/lyra", 1, "Issue A", "open", 0),
                ("Roxabi/voiceCLI#10", "Roxabi/voiceCLI", 10, "Issue B", "open", 0),
            ],
        )
        conn.execute(
            "INSERT INTO edges (src_key, dst_key) VALUES (?, ?)",
            ("Roxabi/lyra#1", "Roxabi/voiceCLI#10"),
        )
        conn.commit()
    finally:
        conn.close()

    # Act
    issues = load_issues(db_path)

    # Assert — A blocks B
    assert issues["Roxabi/lyra#1"]["blocking"] == [
        {"repo": "Roxabi/voiceCLI", "issue": 10}
    ]
    assert issues["Roxabi/lyra#1"]["blocked_by"] == []

    # Assert — B is blocked by A
    assert issues["Roxabi/voiceCLI#10"]["blocked_by"] == [
        {"repo": "Roxabi/lyra", "issue": 1}
    ]
    assert issues["Roxabi/voiceCLI#10"]["blocking"] == []


def test_missing_db_hints_make_corpus_sync(tmp_path: Path) -> None:
    """Missing db_path raises FileNotFoundError hinting at make corpus-sync."""
    # Arrange
    db_path = tmp_path / "nonexistent.db"

    # Act / Assert
    with pytest.raises(FileNotFoundError, match="make corpus-sync"):
        load_issues(db_path)


def test_load_issues_is_stub_true_projection(tmp_path: Path) -> None:
    """is_stub=1 in SQLite → is_stub: True in projected dict."""
    # Arrange
    from scripts.corpus.schema import bootstrap

    db_path = tmp_path / "corpus.db"
    bootstrap(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO issues (key, repo, number, title, state, is_stub)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("Roxabi/lyra#1", "Roxabi/lyra", 1, "stub", "closed", 1),
        )
        conn.commit()
    finally:
        conn.close()

    # Act
    issues = load_issues(db_path)

    # Assert
    assert issues["Roxabi/lyra#1"]["is_stub"] is True


def test_load_issues_dangling_edge_produces_orphan_ref(tmp_path: Path) -> None:
    """Edge whose dst_key has no issues row → src.blocking still lists the
    orphan ref (downstream compute_visible filters on `rk in issues`)."""
    # Arrange
    from scripts.corpus.schema import bootstrap

    db_path = tmp_path / "corpus.db"
    bootstrap(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO issues (key, repo, number, title, state, is_stub)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("Roxabi/lyra#1", "Roxabi/lyra", 1, "A", "open", 0),
        )
        # Edge points at a key that has no issues row.
        conn.execute(
            "INSERT INTO edges (src_key, dst_key) VALUES (?, ?)",
            ("Roxabi/lyra#1", "Roxabi/voiceCLI#999"),
        )
        conn.commit()
    finally:
        conn.close()

    # Act
    issues = load_issues(db_path)

    # Assert — orphan ref present on src side; no phantom issues entry.
    assert issues["Roxabi/lyra#1"]["blocking"] == [
        {"repo": "Roxabi/voiceCLI", "issue": 999}
    ]
    assert "Roxabi/voiceCLI#999" not in issues
