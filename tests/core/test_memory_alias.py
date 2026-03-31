"""Tests for MemoryManager alias-awareness — cross-platform recall (#472)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

from lyra.core.memory import MemoryManager, SessionSnapshot
from lyra.core.stores.identity_alias_store import IdentityAliasStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_snap(
    user_id: str = "tg:user:1",
    session_id: str = "sess-1",
    agent_namespace: str = "lyra",
) -> SessionSnapshot:
    return SessionSnapshot(
        session_id=session_id,
        user_id=user_id,
        medium="telegram",
        agent_namespace=agent_namespace,
        session_start=datetime.now(UTC),
        session_end=datetime.now(UTC),
        message_count=3,
        source_turns=5,
    )


@pytest_asyncio.fixture
async def mm():
    """Real MemoryManager backed by an in-memory SQLite DB."""
    manager = MemoryManager(":memory:")
    await manager.connect()
    yield manager
    await manager.close()


@pytest_asyncio.fixture
async def alias_store(tmp_path: Path):
    store = IdentityAliasStore(db_path=tmp_path / "aliases.db")
    await store.connect()
    yield store
    await store.close()


# ---------------------------------------------------------------------------
# Session recall across aliases
# ---------------------------------------------------------------------------


class TestSessionRecallAliases:
    @pytest.mark.asyncio
    async def test_session_recall_covers_aliases(
        self, mm: MemoryManager, alias_store: IdentityAliasStore
    ) -> None:
        """recall() as dc:user:2 finds a session written as tg:user:1 after linking."""
        snap = _make_snap(user_id="tg:user:1")
        await mm.upsert_session(snap, "summary of cross-platform session")

        await alias_store.link("tg:user:1", "dc:user:2")
        mm.set_alias_store(alias_store)

        result = await mm.recall("dc:user:2", "lyra", first_msg="", token_budget=1000)
        assert "cross-platform session" in result

    @pytest.mark.asyncio
    async def test_session_recall_empty_without_alias(
        self, mm: MemoryManager
    ) -> None:
        """Without alias, recall as dc:user:2 doesn't find tg:user:1's session."""
        snap = _make_snap(user_id="tg:user:1")
        await mm.upsert_session(snap, "summary of tg session")

        result = await mm.recall("dc:user:2", "lyra", first_msg="", token_budget=1000)
        assert result == ""

    @pytest.mark.asyncio
    async def test_memory_writes_unchanged(
        self, mm: MemoryManager, alias_store: IdentityAliasStore
    ) -> None:
        """upsert_session uses snap.user_id — the platform ID, not any alias."""
        await alias_store.link("tg:user:1", "dc:user:2")
        mm.set_alias_store(alias_store)

        snap = _make_snap(user_id="tg:user:1")
        await mm.upsert_session(snap, "my summary")

        # Verify DB stores tg:user:1 as user_id, not dc:user:2
        db = mm._db._db_or_raise()
        async with db.execute(
            "SELECT metadata FROM entries WHERE type='session'"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        meta = json.loads(row[0])
        assert meta["user_id"] == "tg:user:1"


# ---------------------------------------------------------------------------
# Concept recall across alias namespaces
# ---------------------------------------------------------------------------


class TestConceptRecallAcrossNamespaces:
    @pytest.mark.asyncio
    async def test_concept_recall_across_namespaces(
        self, mm: MemoryManager, alias_store: IdentityAliasStore
    ) -> None:
        """Concepts stored under 'lyra:tg:user:1' are found via recall as dc:user:2."""
        snap = _make_snap(user_id="tg:user:1")
        await mm.upsert_concept(
            snap,
            {
                "name": "roxabi-platform",
                "category": "technology",
                "content": "cross-platform identity lib",
                "relations": [],
                "confidence": 0.9,
            },
        )

        await alias_store.link("tg:user:1", "dc:user:2")
        mm.set_alias_store(alias_store)

        result = await mm.recall(
            "dc:user:2",
            "lyra",
            first_msg="roxabi-platform",
            token_budget=1000,
        )
        assert "cross-platform identity lib" in result

    @pytest.mark.asyncio
    async def test_concept_not_found_without_alias(
        self, mm: MemoryManager
    ) -> None:
        """Without alias, concept stored under tg:user:1 is not found for dc:user:2."""
        snap = _make_snap(user_id="tg:user:1")
        await mm.upsert_concept(
            snap,
            {
                "name": "unique-concept",
                "category": "fact",
                "content": "secret info",
                "relations": [],
                "confidence": 0.9,
            },
        )

        result = await mm.recall(
            "dc:user:2",
            "lyra",
            first_msg="unique-concept",
            token_budget=1000,
        )
        assert "secret info" not in result


# ---------------------------------------------------------------------------
# Preference fetch across aliases
# ---------------------------------------------------------------------------


class TestFetchPreferencesAliases:
    @pytest.mark.asyncio
    async def test_fetch_preferences_filters_aliases(
        self, mm: MemoryManager, alias_store: IdentityAliasStore
    ) -> None:
        """Preferences stored for tg:user:1 appear in recall() as dc:user:2 after link."""
        snap = _make_snap(user_id="tg:user:1")
        await mm.upsert_preference(
            snap,
            {
                "name": "verbosity",
                "domain": "communication",
                "strength": 0.8,
                "source": "explicit",
                "content": "prefers verbose responses",
            },
        )

        await alias_store.link("tg:user:1", "dc:user:2")
        mm.set_alias_store(alias_store)

        result = await mm.recall("dc:user:2", "lyra", first_msg="", token_budget=1000)
        assert "[PREFERENCES]" in result
        assert "verbosity" in result

    @pytest.mark.asyncio
    async def test_fetch_preferences_empty_without_alias(
        self, mm: MemoryManager
    ) -> None:
        """Without alias, preferences for tg:user:1 are not shown when recalling as dc:user:2."""
        snap = _make_snap(user_id="tg:user:1")
        await mm.upsert_preference(
            snap,
            {
                "name": "terse-mode",
                "domain": "communication",
                "strength": 0.8,
                "source": "explicit",
                "content": "prefers terse",
            },
        )

        result = await mm.recall("dc:user:2", "lyra", first_msg="", token_budget=1000)
        assert "[PREFERENCES]" not in result
