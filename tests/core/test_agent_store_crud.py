"""AgentStore CRUD, bot-map, bot-settings, runtime-state, and reconnect tests."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from lyra.infrastructure.stores.agent_store import AgentRow, AgentStore

from .conftest import make_agent_row, make_store

# ---------------------------------------------------------------------------
# TestAgentStoreConnect
# ---------------------------------------------------------------------------


class TestAgentStoreConnect:
    """AgentStore.connect() — creates tables, is idempotent, guards after close."""

    async def test_connect_creates_tables(self, tmp_path: Path) -> None:
        # Arrange
        store = await make_store(tmp_path)
        try:
            # Act — connect() already called by make_store
            assert store._db is not None

            # Assert — all three tables must exist
            expected_tables = {"agents", "bot_agent_map", "agent_runtime_state"}
            async with store._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ) as cur:
                rows = await cur.fetchall()
            found = {row[0] for row in rows}
            assert expected_tables.issubset(found), (
                f"expected tables {expected_tables!r}, found {found!r}"
            )
        finally:
            await store.close()

    async def test_connect_idempotent(self, tmp_path: Path) -> None:
        # Arrange
        store = await make_store(tmp_path)
        try:
            # Act — call connect() a second time
            await store.connect()  # must not raise
        finally:
            await store.close()

    async def test_close_then_get_raises(self, tmp_path: Path) -> None:
        # Arrange
        store = await make_store(tmp_path)
        await store.close()

        # Act + Assert — get() after close() must raise RuntimeError
        with pytest.raises(RuntimeError, match="connect"):
            store.get("any-agent")


# ---------------------------------------------------------------------------
# TestAgentCRUD
# ---------------------------------------------------------------------------


class TestAgentCRUD:
    """AgentStore CRUD: upsert / get / get_all / delete."""

    async def test_upsert_and_get(self, agent_store: AgentStore) -> None:
        # Arrange
        row = make_agent_row("lyra-default")

        # Act
        await agent_store.upsert(row)
        result = agent_store.get("lyra-default")

        # Assert
        assert result is not None
        assert result.name == "lyra-default"
        assert result.backend == "anthropic-sdk"

    async def test_get_missing_returns_none(self, agent_store: AgentStore) -> None:
        # Act
        result = agent_store.get("nonexistent")

        # Assert
        assert result is None

    async def test_get_all(self, agent_store: AgentStore) -> None:
        # Arrange
        await agent_store.upsert(make_agent_row("agent-a"))
        await agent_store.upsert(make_agent_row("agent-b"))

        # Act
        all_agents = agent_store.get_all()

        # Assert
        names = {a.name for a in all_agents}
        assert "agent-a" in names
        assert "agent-b" in names

    async def test_delete_removes_agent(self, agent_store: AgentStore) -> None:
        # Arrange
        await agent_store.upsert(make_agent_row("to-delete"))

        # Act
        await agent_store.delete("to-delete")

        # Assert
        assert agent_store.get("to-delete") is None

    async def test_delete_raises_if_bot_assigned(self, agent_store: AgentStore) -> None:
        # Arrange
        await agent_store.upsert(make_agent_row("assigned-agent"))
        await agent_store.set_bot_agent("telegram", "bot-123", "assigned-agent")

        # Act + Assert — delete must refuse when a bot is assigned
        with pytest.raises(ValueError, match="assigned"):
            await agent_store.delete("assigned-agent")

    async def test_upsert_is_idempotent(self, agent_store: AgentStore) -> None:
        # Arrange
        row = make_agent_row("dup-agent")

        # Act — upsert same name twice
        await agent_store.upsert(row)
        await agent_store.upsert(row)

        # Assert — only one entry in get_all()
        all_agents = agent_store.get_all()
        dup_count = sum(1 for a in all_agents if a.name == "dup-agent")
        assert dup_count == 1


# ---------------------------------------------------------------------------
# TestBotMap
# ---------------------------------------------------------------------------


class TestBotMap:
    """AgentStore bot_agent_map: set / get / remove."""

    async def test_set_and_get_bot_agent(self, agent_store: AgentStore) -> None:
        # Arrange
        await agent_store.upsert(make_agent_row("telegram-agent"))

        # Act
        await agent_store.set_bot_agent("telegram", "bot-001", "telegram-agent")
        result = agent_store.get_bot_agent("telegram", "bot-001")

        # Assert
        assert result == "telegram-agent"

    async def test_get_missing_bot_returns_none(self, agent_store: AgentStore) -> None:
        # Act
        result = agent_store.get_bot_agent("telegram", "none")

        # Assert
        assert result is None

    async def test_remove_bot_agent(self, agent_store: AgentStore) -> None:
        # Arrange
        await agent_store.upsert(make_agent_row("removable-agent"))
        await agent_store.set_bot_agent("discord", "bot-002", "removable-agent")

        # Act
        await agent_store.remove_bot_agent("discord", "bot-002")

        # Assert
        assert agent_store.get_bot_agent("discord", "bot-002") is None

    async def test_remove_missing_bot_is_noop(self, agent_store: AgentStore) -> None:
        # Act + Assert — must not raise
        await agent_store.remove_bot_agent("telegram", "nonexistent-bot")


# ---------------------------------------------------------------------------
# TestBotSettings (#347 — watch channels)
# ---------------------------------------------------------------------------


class TestBotSettings:
    """AgentStore bot settings: set_bot_settings / get_bot_settings."""

    async def test_set_and_get_bot_settings(self, agent_store: AgentStore) -> None:
        await agent_store.upsert(make_agent_row("settings-agent"))
        await agent_store.set_bot_agent("discord", "bot-s1", "settings-agent")

        await agent_store.set_bot_settings(
            "discord", "bot-s1", {"watch_channels": [111, 222]}
        )

        result = agent_store.get_bot_settings("discord", "bot-s1")
        assert result == {"watch_channels": [111, 222]}

    async def test_get_settings_default_empty(self, agent_store: AgentStore) -> None:
        await agent_store.upsert(make_agent_row("no-settings-agent"))
        await agent_store.set_bot_agent("discord", "bot-s2", "no-settings-agent")

        result = agent_store.get_bot_settings("discord", "bot-s2")
        assert result == {}

    async def test_set_bot_agent_with_settings(self, agent_store: AgentStore) -> None:
        await agent_store.upsert(make_agent_row("inline-agent"))

        await agent_store.set_bot_agent(
            "discord",
            "bot-s3",
            "inline-agent",
            settings={"watch_channels": [333]},
        )

        result = agent_store.get_bot_settings("discord", "bot-s3")
        assert result == {"watch_channels": [333]}

    async def test_set_bot_agent_without_settings_preserves_existing(
        self, agent_store: AgentStore
    ) -> None:
        await agent_store.upsert(make_agent_row("preserve-agent"))
        await agent_store.set_bot_agent(
            "discord",
            "bot-s4",
            "preserve-agent",
            settings={"watch_channels": [444]},
        )

        # Reassign agent without settings — COALESCE preserves existing
        await agent_store.set_bot_agent("discord", "bot-s4", "preserve-agent")

        result = agent_store.get_bot_settings("discord", "bot-s4")
        assert result == {"watch_channels": [444]}

    async def test_remove_bot_clears_settings(self, agent_store: AgentStore) -> None:
        await agent_store.upsert(make_agent_row("rm-settings-agent"))
        await agent_store.set_bot_agent(
            "discord",
            "bot-s5",
            "rm-settings-agent",
            settings={"watch_channels": [555]},
        )

        await agent_store.remove_bot_agent("discord", "bot-s5")

        result = agent_store.get_bot_settings("discord", "bot-s5")
        assert result == {}

    async def test_settings_warm_on_reconnect(self, tmp_path: Path) -> None:
        db_path = tmp_path / "auth.db"
        store1 = AgentStore(db_path=str(db_path))
        await store1.connect()
        await store1.upsert(make_agent_row("warm-agent"))
        await store1.set_bot_agent(
            "discord",
            "bot-warm",
            "warm-agent",
            settings={"watch_channels": [666]},
        )
        await store1.close()

        store2 = AgentStore(db_path=str(db_path))
        await store2.connect()
        try:
            result = store2.get_bot_settings("discord", "bot-warm")
            assert result == {"watch_channels": [666]}
        finally:
            await store2.close()

    async def test_corrupt_settings_json_skipped_on_reconnect(
        self, tmp_path: Path
    ) -> None:
        """Corrupt settings_json in DB is skipped with a warning; cache stays empty."""
        db_path = tmp_path / "auth.db"
        store1 = AgentStore(db_path=str(db_path))
        await store1.connect()
        await store1.upsert(make_agent_row("corrupt-agent"))
        await store1.set_bot_agent("discord", "bot-corrupt", "corrupt-agent")
        await store1.close()

        # Inject corrupt JSON directly into the DB
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                "UPDATE bot_agent_map SET settings_json=? "
                "WHERE platform='discord' AND bot_id='bot-corrupt'",
                ("{not valid json",),
            )
            await db.commit()

        store2 = AgentStore(db_path=str(db_path))
        await store2.connect()
        try:
            # Corrupt row is skipped — cache returns empty dict
            result = store2.get_bot_settings("discord", "bot-corrupt")
            assert result == {}
        finally:
            await store2.close()

    async def test_set_bot_settings_raises_on_missing_row(
        self, agent_store: AgentStore
    ) -> None:
        """set_bot_settings raises ValueError if the bot mapping row does not exist."""
        with pytest.raises(ValueError, match="No bot_agent_map row"):
            await agent_store.set_bot_settings(
                "discord", "bot-nonexistent", {"watch_channels": [1]}
            )


# ---------------------------------------------------------------------------
# TestRuntimeState
# ---------------------------------------------------------------------------


class TestRuntimeState:
    """AgentStore runtime state: set_runtime_state / get_all_runtime_states."""

    async def test_set_runtime_state(self, agent_store: AgentStore) -> None:
        # Arrange
        await agent_store.upsert(make_agent_row("state-agent"))

        # Act
        await agent_store.set_runtime_state("state-agent", "active", pool_count=2)
        states = await agent_store.get_all_runtime_states()

        # Assert
        assert "state-agent" in states
        entry = states["state-agent"]
        assert entry.status == "active"
        assert entry.pool_count == 2

    async def test_get_all_runtime_states_empty(self, agent_store: AgentStore) -> None:
        # Act
        states = await agent_store.get_all_runtime_states()

        # Assert — fresh DB returns empty dict
        assert states == {}


# ---------------------------------------------------------------------------
# TestAgentStoreReconnect
# ---------------------------------------------------------------------------


class TestAgentStoreReconnect:
    """Cache warm-up on a fresh AgentStore instance against an existing DB."""

    async def test_cache_warm_on_reconnect(self, tmp_path: Path) -> None:
        # Arrange — seed a row using the first store instance, then close it
        db_path = tmp_path / "auth.db"
        store1 = AgentStore(db_path=str(db_path))
        await store1.connect()
        row = AgentRow(
            name="reconnect-agent",
            backend="anthropic-sdk",
            model="claude-3-5-haiku-20241022",
            source="db",
        )
        await store1.upsert(row)
        await store1.close()

        # Act — open a NEW store against the same DB and connect
        store2 = AgentStore(db_path=str(db_path))
        await store2.connect()
        try:
            result = store2.get("reconnect-agent")

            # Assert — cache must have been warmed from DB
            assert result is not None
            assert result.name == "reconnect-agent"
            assert result.backend == "anthropic-sdk"
            assert result.model == "claude-3-5-haiku-20241022"
        finally:
            await store2.close()
