"""Tests for scripts.corpus.sync — V2 sync helpers.

Covers canonical_key normalisation, upsert_edges dedup on repeat calls,
log_rate_limit stderr format, and closed_hop_pass stub insertion.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

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


def test_closed_hop_triggers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """closed_hop_pass must fetch stubs for referenced-but-missing blocker keys."""
    from scripts.corpus.sync import closed_hop_pass  # noqa: PLC0415

    # Arrange
    db_path = tmp_path / "corpus.db"
    bootstrap(db_path)
    conn = connect(db_path)

    # One open issue in Roxabi/lyra, blocked by a key that is NOT yet in issues.
    upsert_issue(conn, {
        "key": "Roxabi/lyra#100",
        "repo": "Roxabi/lyra",
        "number": 100,
        "title": "needs closed ancestor",
        "state": "open",
        "url": "https://github.com/Roxabi/lyra/issues/100",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "closed_at": None,
        "milestone": None,
        "is_stub": 0,
    })
    upsert_edges(conn, "Roxabi/lyra#100",
                 blocked_by=["Roxabi/lyra#42"], blocking=[])
    conn.commit()

    # Mock gh_graphql so closed_hop_pass receives a canned response for #42.
    def fake_gh_graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        return {"data": {"repository": {"issue": {
            "number": 42,
            "title": "ancient closed blocker",
            "state": "CLOSED",
            "url": "https://github.com/Roxabi/lyra/issues/42",
            "createdAt": "2025-06-01T00:00:00Z",
            "updatedAt": "2025-06-15T00:00:00Z",
            "closedAt": "2025-06-15T00:00:00Z",
        }}}, "rateLimit": {"cost": 1, "remaining": 4999, "resetAt": "2026-04-21T10:00:00Z"}}
    monkeypatch.setattr("scripts.corpus.sync.gh_graphql", fake_gh_graphql)

    # Act
    closed_hop_pass(conn)

    # Assert — stub row exists with is_stub=1
    row = conn.execute(
        "SELECT key, state, is_stub, title FROM issues WHERE key = ?",
        ("Roxabi/lyra#42",),
    ).fetchone()
    conn.close()
    assert row is not None, "closed_hop_pass should have upserted Roxabi/lyra#42"
    assert row[0] == "Roxabi/lyra#42"
    assert row[1] == "closed"
    assert row[2] == 1
    assert row[3] == "ancient closed blocker"
