"""Tests for ContextResolver — DB lookup for reply-to-resume (#244).

RED phase — tests describe the ContextResolver API that does not exist yet.
They will fail with ImportError until backend-dev completes T2.1 / T2.2.

SC trace: SC-2, SC-3, SC-10
"""

from __future__ import annotations

import aiosqlite
import pytest

from lyra.core.context_resolver import (  # type: ignore[import]
    ContextResolver,
    ResolvedSession,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_with_turn(tmp_path):
    """SQLite DB with one conversation_turns row for reply_message_id='tg-msg-42'."""
    db_path = tmp_path / "vault.db"
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            "CREATE TABLE conversation_turns"
            " (id INTEGER PRIMARY KEY, pool_id TEXT, session_id TEXT,"
            " role TEXT, platform TEXT, user_id TEXT, content TEXT,"
            " message_id TEXT, reply_message_id TEXT, timestamp TEXT)"
        )
        await db.execute(
            "INSERT INTO conversation_turns VALUES (1,'pool:tg:main','sess-abc',"
            "'assistant','telegram','u1','hi',NULL,'tg-msg-42','2026-01-01T00:00:00Z')"
        )
        await db.commit()
    return db_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_resolve_hit(db_with_turn):
    """resolve() returns a ResolvedSession when reply_message_id matches a row."""
    # Arrange
    resolver = ContextResolver(db_with_turn)

    # Act
    result = await resolver.resolve("tg-msg-42")

    # Assert
    assert result == ResolvedSession(session_id="sess-abc", pool_id="pool:tg:main")


async def test_resolve_miss(db_with_turn):
    """resolve() returns None when no row matches the given reply_to_id."""
    # Arrange
    resolver = ContextResolver(db_with_turn)

    # Act
    result = await resolver.resolve("nonexistent-id")

    # Assert
    assert result is None


async def test_resolve_no_db(tmp_path):
    """resolve() returns None gracefully when the DB file does not exist."""
    # Arrange — point resolver at a path that doesn't exist
    resolver = ContextResolver(tmp_path / "missing.db")

    # Act
    result = await resolver.resolve("any-id")

    # Assert — must not raise, must return None
    assert result is None


async def test_resolve_no_table(tmp_path):
    """resolve() returns None gracefully when conversation_turns table is absent."""
    # Arrange — DB exists but has no conversation_turns table
    db_path = tmp_path / "empty.db"
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute("CREATE TABLE other (id INTEGER PRIMARY KEY)")
        await db.commit()

    resolver = ContextResolver(db_path)

    # Act
    result = await resolver.resolve("any-id")

    # Assert — must not raise, must return None
    assert result is None
