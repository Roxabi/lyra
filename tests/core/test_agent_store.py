"""RED-phase tests for AgentStore (issue #268, S1).

All tests in this file are expected to FAIL with ImportError or
ModuleNotFoundError until lyra.core.agent_store is implemented.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lyra.core.agent_store import AgentRow, AgentStore

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_agent_row(name: str = "test-agent") -> AgentRow:
    """Return a minimal valid AgentRow for the given name."""
    return AgentRow(
        name=name,
        backend="anthropic-sdk",
        model="claude-3-5-haiku-20241022",
        max_turns=10,
        tools_json="[]",
        persona=None,
        show_intermediate=False,
        smart_routing_json=None,
        plugins_json="[]",
        memory_namespace=None,
        cwd=None,
        source="test",
    )


async def make_store(tmp_path: Path) -> AgentStore:
    """Create and connect a real AgentStore backed by a tmp file DB."""
    store = AgentStore(db_path=str(tmp_path / "agents.db"))
    await store.connect()
    return store


@pytest.fixture
async def agent_store(tmp_path: Path):
    """Fixture-based AgentStore with automatic teardown."""
    store = await make_store(tmp_path)
    try:
        yield store
    finally:
        await store.close()


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
        row = _make_agent_row("lyra-default")

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
        await agent_store.upsert(_make_agent_row("agent-a"))
        await agent_store.upsert(_make_agent_row("agent-b"))

        # Act
        all_agents = agent_store.get_all()

        # Assert
        names = {a.name for a in all_agents}
        assert "agent-a" in names
        assert "agent-b" in names

    async def test_delete_removes_agent(self, agent_store: AgentStore) -> None:
        # Arrange
        await agent_store.upsert(_make_agent_row("to-delete"))

        # Act
        await agent_store.delete("to-delete")

        # Assert
        assert agent_store.get("to-delete") is None

    async def test_delete_raises_if_bot_assigned(self, agent_store: AgentStore) -> None:
        # Arrange
        await agent_store.upsert(_make_agent_row("assigned-agent"))
        await agent_store.set_bot_agent("telegram", "bot-123", "assigned-agent")

        # Act + Assert — delete must refuse when a bot is assigned
        with pytest.raises(ValueError, match="assigned"):
            await agent_store.delete("assigned-agent")

    async def test_upsert_is_idempotent(self, agent_store: AgentStore) -> None:
        # Arrange
        row = _make_agent_row("dup-agent")

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
        await agent_store.upsert(_make_agent_row("telegram-agent"))

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
        await agent_store.upsert(_make_agent_row("removable-agent"))
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
        await agent_store.upsert(_make_agent_row("settings-agent"))
        await agent_store.set_bot_agent("discord", "bot-s1", "settings-agent")

        await agent_store.set_bot_settings(
            "discord", "bot-s1", {"watch_channels": [111, 222]}
        )

        result = agent_store.get_bot_settings("discord", "bot-s1")
        assert result == {"watch_channels": [111, 222]}

    async def test_get_settings_default_empty(self, agent_store: AgentStore) -> None:
        await agent_store.upsert(_make_agent_row("no-settings-agent"))
        await agent_store.set_bot_agent("discord", "bot-s2", "no-settings-agent")

        result = agent_store.get_bot_settings("discord", "bot-s2")
        assert result == {}

    async def test_set_bot_agent_with_settings(self, agent_store: AgentStore) -> None:
        await agent_store.upsert(_make_agent_row("inline-agent"))

        await agent_store.set_bot_agent(
            "discord", "bot-s3", "inline-agent",
            settings={"watch_channels": [333]},
        )

        result = agent_store.get_bot_settings("discord", "bot-s3")
        assert result == {"watch_channels": [333]}

    async def test_set_bot_agent_without_settings_preserves_existing(
        self, agent_store: AgentStore
    ) -> None:
        await agent_store.upsert(_make_agent_row("preserve-agent"))
        await agent_store.set_bot_agent(
            "discord", "bot-s4", "preserve-agent",
            settings={"watch_channels": [444]},
        )

        # Reassign agent without settings — COALESCE preserves existing
        await agent_store.set_bot_agent("discord", "bot-s4", "preserve-agent")

        result = agent_store.get_bot_settings("discord", "bot-s4")
        assert result == {"watch_channels": [444]}

    async def test_remove_bot_clears_settings(self, agent_store: AgentStore) -> None:
        await agent_store.upsert(_make_agent_row("rm-settings-agent"))
        await agent_store.set_bot_agent(
            "discord", "bot-s5", "rm-settings-agent",
            settings={"watch_channels": [555]},
        )

        await agent_store.remove_bot_agent("discord", "bot-s5")

        result = agent_store.get_bot_settings("discord", "bot-s5")
        assert result == {}

    async def test_settings_warm_on_reconnect(self, tmp_path: Path) -> None:
        db_path = tmp_path / "auth.db"
        store1 = AgentStore(db_path=str(db_path))
        await store1.connect()
        await store1.upsert(_make_agent_row("warm-agent"))
        await store1.set_bot_agent(
            "discord", "bot-warm", "warm-agent",
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
        import aiosqlite

        db_path = tmp_path / "auth.db"
        store1 = AgentStore(db_path=str(db_path))
        await store1.connect()
        await store1.upsert(_make_agent_row("corrupt-agent"))
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
        await agent_store.upsert(_make_agent_row("state-agent"))

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
# TestSeedFromToml
# ---------------------------------------------------------------------------


class TestSeedFromToml:
    """AgentStore.seed_from_toml() — TOML import, idempotency, force, bad files."""

    def _write_toml(self, path: Path, name: str = "seeded-agent") -> Path:
        """Write a minimal valid agent TOML to path and return the file path."""
        toml_file = path / f"{name}.toml"
        toml_file.write_text(
            f"""
[agent]
name = "{name}"
backend = "anthropic-sdk"
model = "claude-3-5-haiku-20241022"
max_turns = 5
tools = []
plugins = []
show_intermediate = false
""",
            encoding="utf-8",
        )
        return toml_file

    async def test_seed_imports_toml(self, tmp_path: Path) -> None:
        # Arrange
        store = await make_store(tmp_path)
        try:
            toml_file = self._write_toml(tmp_path, "seeded-agent")

            # Act
            count = await store.seed_from_toml(toml_file)

            # Assert
            assert count == 1
            result = store.get("seeded-agent")
            assert result is not None
            assert result.name == "seeded-agent"
        finally:
            await store.close()

    async def test_seed_idempotent(self, tmp_path: Path) -> None:
        # Arrange
        store = await make_store(tmp_path)
        try:
            toml_file = self._write_toml(tmp_path, "idem-agent")

            # Act — seed twice
            await store.seed_from_toml(toml_file)
            count = await store.seed_from_toml(toml_file)

            # Assert — second call inserts 0 rows
            assert count == 0
        finally:
            await store.close()

    async def test_seed_force_overwrites(self, tmp_path: Path) -> None:
        # Arrange
        store = await make_store(tmp_path)
        try:
            toml_file = self._write_toml(tmp_path, "force-agent")
            await store.seed_from_toml(toml_file)

            # Modify the row in DB so we can detect the overwrite
            existing = store.get("force-agent")
            assert existing is not None
            modified = AgentRow(
                name=existing.name,
                backend="openai",  # changed value
                model=existing.model,
                max_turns=existing.max_turns,
                tools_json=existing.tools_json,
                persona=existing.persona,
                show_intermediate=existing.show_intermediate,
                smart_routing_json=existing.smart_routing_json,
                plugins_json=existing.plugins_json,
                memory_namespace=existing.memory_namespace,
                cwd=existing.cwd,
                source="manual",
            )
            await store.upsert(modified)
            row_modified = store.get("force-agent")
            assert row_modified is not None and row_modified.backend == "openai"

            # Act — seed with force=True must restore TOML values
            count = await store.seed_from_toml(toml_file, force=True)

            # Assert
            assert count == 1
            row_restored = store.get("force-agent")
            assert row_restored is not None and row_restored.backend == "anthropic-sdk"
        finally:
            await store.close()

    async def test_seed_skips_unparseable(self, tmp_path: Path) -> None:
        # Arrange
        store = await make_store(tmp_path)
        try:
            garbage_file = tmp_path / "garbage.toml"
            garbage_file.write_text("this is [[[not valid toml", encoding="utf-8")

            # Act — must not raise
            count = await store.seed_from_toml(garbage_file)

            # Assert — 0 rows imported, no exception
            assert count == 0
        finally:
            await store.close()

    async def test_seed_name_from_model_section(self, tmp_path: Path) -> None:
        # Arrange — TOML has [model].name but no [agent].name
        store = await make_store(tmp_path)
        try:
            toml_file = tmp_path / "model-named.toml"
            toml_file.write_text(
                """
[model]
name = "model-named-agent"
backend = "anthropic-sdk"
model = "claude-3-5-haiku-20241022"
max_turns = 5
tools = []
""",
                encoding="utf-8",
            )

            # Act
            count = await store.seed_from_toml(toml_file)

            # Assert
            assert count == 1
            result = store.get("model-named-agent")
            assert result is not None
            assert result.name == "model-named-agent"
        finally:
            await store.close()

    async def test_seed_skips_toml_without_name(self, tmp_path: Path) -> None:
        # Arrange — TOML has [agent] section but no name key and no [model] section
        store = await make_store(tmp_path)
        try:
            toml_file = tmp_path / "nameless.toml"
            toml_file.write_text(
                """
[agent]
backend = "anthropic-sdk"
model = "claude-3-5-haiku-20241022"
""",
                encoding="utf-8",
            )

            # Act
            count = await store.seed_from_toml(toml_file)

            # Assert — no name found, must return 0 and not insert anything
            assert count == 0
            assert store.get_all() == []
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# TestAgentStoreConnect — reconnect / cache warm
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


# ---------------------------------------------------------------------------
# TestBotMap — additional tests
# ---------------------------------------------------------------------------


class TestBotMapExtra:
    """Additional bot_agent_map tests: reassign and reconnect."""

    async def test_set_bot_agent_reassigns(self, tmp_path: Path) -> None:
        # Arrange
        db_path = tmp_path / "auth.db"
        store = AgentStore(db_path=str(db_path))
        await store.connect()
        try:
            await store.upsert(
                AgentRow(
                    name="agent-a",
                    backend="anthropic-sdk",
                    model="claude-3-5-haiku-20241022",
                    source="db",
                )
            )
            await store.upsert(
                AgentRow(
                    name="agent-b",
                    backend="anthropic-sdk",
                    model="claude-3-5-haiku-20241022",
                    source="db",
                )
            )

            # Act — assign then reassign
            await store.set_bot_agent("telegram", "b1", "agent-a")
            await store.set_bot_agent("telegram", "b1", "agent-b")

            # Assert — cache reflects the new assignment
            assert store.get_bot_agent("telegram", "b1") == "agent-b"
        finally:
            await store.close()

    async def test_bot_map_warm_on_reconnect(self, tmp_path: Path) -> None:
        # Arrange — set mapping in store1, close it
        db_path = tmp_path / "auth.db"
        store1 = AgentStore(db_path=str(db_path))
        await store1.connect()
        await store1.upsert(
            AgentRow(
                name="mapped-agent",
                backend="anthropic-sdk",
                model="claude-3-5-haiku-20241022",
                source="db",
            )
        )
        await store1.set_bot_agent("telegram", "bot-reconnect", "mapped-agent")
        await store1.close()

        # Act — open fresh store against same DB
        store2 = AgentStore(db_path=str(db_path))
        await store2.connect()
        try:
            result = store2.get_bot_agent("telegram", "bot-reconnect")

            # Assert — bot map was warmed from DB
            assert result == "mapped-agent"
        finally:
            await store2.close()


# ---------------------------------------------------------------------------
# TestTTSSTTColumns
# ---------------------------------------------------------------------------


class TestTTSSTTColumns:
    """AgentRow tts_json / stt_json columns: upsert, warm cache, seed_from_toml."""

    async def test_upsert_and_get_with_tts_stt(self, agent_store: AgentStore) -> None:
        # Arrange
        import json

        tts_data = {"engine": "chatterbox", "voice": "en-US-1", "chunked": True}
        stt_data = {"language_detection_threshold": 0.8, "language_fallback": "en"}
        row = AgentRow(
            name="tts-agent",
            backend="anthropic-sdk",
            model="claude-3-5-haiku-20241022",
            tts_json=json.dumps(tts_data),
            stt_json=json.dumps(stt_data),
        )

        # Act
        await agent_store.upsert(row)
        result = agent_store.get("tts-agent")

        # Assert
        assert result is not None
        assert result.tts_json is not None
        assert result.stt_json is not None
        assert json.loads(result.tts_json) == tts_data
        assert json.loads(result.stt_json) == stt_data

    async def test_upsert_null_tts_stt(self, agent_store: AgentStore) -> None:
        # Arrange — no tts_json / stt_json (nullable columns default to None)
        row = _make_agent_row("no-tts-agent")

        # Act
        await agent_store.upsert(row)
        result = agent_store.get("no-tts-agent")

        # Assert
        assert result is not None
        assert result.tts_json is None
        assert result.stt_json is None

    async def test_tts_stt_warm_on_reconnect(self, tmp_path: Path) -> None:
        # Arrange — write tts/stt row in store1, close it
        import json

        db_path = tmp_path / "auth.db"
        store1 = AgentStore(db_path=str(db_path))
        await store1.connect()
        tts_data = {"voice": "en-GB-2"}
        row = AgentRow(
            name="reconnect-tts-agent",
            backend="anthropic-sdk",
            model="claude-3-5-haiku-20241022",
            tts_json=json.dumps(tts_data),
            stt_json=None,
            source="db",
        )
        await store1.upsert(row)
        await store1.close()

        # Act — open fresh store against same DB
        store2 = AgentStore(db_path=str(db_path))
        await store2.connect()
        try:
            result = store2.get("reconnect-tts-agent")

            # Assert — tts_json must survive round-trip through DB
            assert result is not None
            assert result.tts_json is not None
            assert json.loads(result.tts_json) == tts_data
            assert result.stt_json is None
        finally:
            await store2.close()

    async def test_seed_from_toml_with_tts_stt(self, tmp_path: Path) -> None:
        # Arrange — TOML with [tts] and [stt] sections
        store = await make_store(tmp_path)
        try:
            toml_file = tmp_path / "tts-agent.toml"
            toml_file.write_text(
                """
[agent]
name = "tts-seeded-agent"
backend = "anthropic-sdk"
model = "claude-3-5-haiku-20241022"
max_turns = 5

[tts]
engine = "chatterbox"
voice = "en-US-1"
chunked = true
chunk_size = 200

[stt]
language_detection_threshold = 0.75
language_fallback = "en"
""",
                encoding="utf-8",
            )

            # Act
            count = await store.seed_from_toml(toml_file)

            # Assert
            import json

            assert count == 1
            result = store.get("tts-seeded-agent")
            assert result is not None
            assert result.tts_json is not None
            assert result.stt_json is not None
            tts = json.loads(result.tts_json)
            stt = json.loads(result.stt_json)
            assert tts["engine"] == "chatterbox"
            assert tts["voice"] == "en-US-1"
            assert tts["chunked"] is True
            assert tts["chunk_size"] == 200
            assert stt["language_detection_threshold"] == 0.75
            assert stt["language_fallback"] == "en"
        finally:
            await store.close()

    async def test_seed_from_toml_no_tts_stt_is_null(self, tmp_path: Path) -> None:
        # Arrange — TOML without [tts] or [stt] sections
        store = await make_store(tmp_path)
        try:
            toml_file = tmp_path / "plain-agent.toml"
            toml_file.write_text(
                """
[agent]
name = "plain-agent"
backend = "anthropic-sdk"
model = "claude-3-5-haiku-20241022"
""",
                encoding="utf-8",
            )

            # Act
            await store.seed_from_toml(toml_file)
            result = store.get("plain-agent")

            # Assert — missing sections → NULL columns
            assert result is not None
            assert result.tts_json is None
            assert result.stt_json is None
        finally:
            await store.close()
