"""Tests for scripts.corpus.sync — V2 sync helpers.

Covers canonical_key normalisation, upsert_edges dedup on repeat calls,
and log_rate_limit stderr format.
"""

from __future__ import annotations

import re
from pathlib import Path

from scripts.corpus.schema import bootstrap, connect
from scripts.corpus.sync import (
    canonical_key,
    log_rate_limit,
    upsert_edges,
    upsert_issue,
)


def test_edge_dedup(tmp_path: Path) -> None:
    """upsert_edges() called twice must not create duplicate rows.

    The canonical direction is always (blocker → blocked), so a
    blocked_by=["Roxabi/lyra#2"] on src "Roxabi/lyra#1" must produce the
    row src_key="Roxabi/lyra#2", dst_key="Roxabi/lyra#1".
    """
    # Arrange
    db_path = tmp_path / "corpus.db"
    bootstrap(db_path)
    conn = connect(db_path)
    try:
        upsert_issue(
            conn,
            {
                "key": "Roxabi/lyra#1",
                "repo": "Roxabi/lyra",
                "number": 1,
                "title": "x",
                "state": "open",
                "url": "https://github.com/Roxabi/lyra/issues/1",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "closed_at": None,
                "milestone": None,
                "is_stub": 0,
            },
        )

        # Act — call twice with identical data
        upsert_edges(
            conn,
            "Roxabi/lyra#1",
            blocked_by=["Roxabi/lyra#2"],
            blocking=[],
        )
        upsert_edges(
            conn,
            "Roxabi/lyra#1",
            blocked_by=["Roxabi/lyra#2"],
            blocking=[],
        )

        # Assert — exactly one edge row
        count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        assert count == 1, f"Expected 1 edge row, got {count}"

        # Assert — canonical direction: blocker is src, blocked is dst
        row = conn.execute(
            "SELECT src_key, dst_key FROM edges"
        ).fetchone()
        assert row == ("Roxabi/lyra#2", "Roxabi/lyra#1"), (
            f"Expected canonical (blocker→blocked) row, got {row}"
        )
    finally:
        conn.close()


def test_canonical_key() -> None:
    """canonical_key() normalises issue refs to 'owner/repo#N' form."""
    # Arrange / Act / Assert — bare integer in repo context
    assert canonical_key(42, "Roxabi/lyra") == "Roxabi/lyra#42"

    # Arrange / Act / Assert — already-qualified cross-repo ref passes through
    assert canonical_key("Roxabi/voiceCLI#7", "Roxabi/lyra") == "Roxabi/voiceCLI#7"

    # Arrange / Act / Assert — same-repo short form "#N" resolves to full key
    assert canonical_key("#9", "Roxabi/lyra") == "Roxabi/lyra#9"


def test_rate_limit_log(capsys) -> None:
    """log_rate_limit() writes a structured line to stderr."""
    # Arrange
    rl = {"cost": 3, "remaining": 4997, "resetAt": "2026-04-21T10:00:00Z"}

    # Act
    log_rate_limit(rl)

    # Assert
    captured = capsys.readouterr()
    assert re.search(
        r"\[corpus\] cost=3 remaining=4997 reset=2026-04-21T10:00:00Z",
        captured.err,
    ), f"Expected structured rate-limit line in stderr, got: {captured.err!r}"
