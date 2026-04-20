"""Tests for scripts.corpus.schema — SQLite corpus DB bootstrap.

Tests verify that:
- bootstrap() is idempotent (safe to call twice on the same path).
- All expected tables and indexes are created: issues, labels, edges,
  sync_state, and the ix_edges_dst index.
- The issues table does NOT contain body or body_hash columns (explicitly
  excluded per spec to keep the corpus lean).

RED state: scripts/corpus/schema.py does not exist yet.  All tests will
fail with ModuleNotFoundError until the implementation lands in T2.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.corpus.schema import bootstrap


def test_bootstrap_idempotent(tmp_path: Path) -> None:
    """Calling bootstrap() twice on the same DB must not raise and must leave
    the expected tables and index in place."""
    # Arrange
    db_path = tmp_path / "corpus.db"

    # Act — first call creates the schema
    bootstrap(db_path)
    # Act — second call must be a no-op (idempotent)
    bootstrap(db_path)

    # Assert — expected tables exist
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'index')"
        ).fetchall()
    finally:
        con.close()

    names_by_type: dict[str, set[str]] = {"table": set(), "index": set()}
    for name, kind in rows:
        names_by_type[kind].add(name)

    expected_tables = {"issues", "labels", "edges", "sync_state"}
    assert expected_tables <= names_by_type["table"], (
        f"Missing tables: {expected_tables - names_by_type['table']}"
    )

    assert "ix_edges_dst" in names_by_type["index"], (
        "Expected index ix_edges_dst not found in sqlite_master"
    )


def test_schema_has_no_body_column(tmp_path: Path) -> None:
    """The issues table must not contain a 'body' or 'body_hash' column.

    The spec explicitly excludes these to keep the corpus lean.
    """
    # Arrange
    db_path = tmp_path / "corpus.db"

    # Act
    bootstrap(db_path)

    # Assert
    con = sqlite3.connect(db_path)
    try:
        columns = {
            row[1]
            for row in con.execute("PRAGMA table_info(issues)").fetchall()
        }
    finally:
        con.close()

    assert "body" not in columns, (
        "Column 'body' found in issues table — spec says to drop it"
    )
    assert "body_hash" not in columns, (
        "Column 'body_hash' found in issues table — spec says to drop it"
    )
