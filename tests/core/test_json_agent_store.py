"""JsonAgentStore test suite.

Mirrors test_agent_store_crud.py and test_agent_store_seed.py against
JsonAgentStore, verifying that the in-memory + JSON-file implementation
satisfies the same contract as AgentStore — no SQLite required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lyra.core.stores.json_agent_store import JsonAgentStore

from .conftest import make_agent_row

# ---------------------------------------------------------------------------
# TestJsonAgentStoreConnect
# ---------------------------------------------------------------------------


class TestJsonAgentStoreConnect:
    """JsonAgentStore.connect() lifecycle tests."""

    async def test_connect_with_missing_file_starts_empty(self, tmp_path: Path) -> None:
        # Arrange — point to a non-existent path
        store = JsonAgentStore(path=tmp_path / "does_not_exist.json")

        # Act
        await store.connect()

        # Assert — empty store, no error
        assert store.get_all() == []
        assert store.get_all_bot_mappings() == {}
        await store.close()

    async def test_connect_idempotent(self, json_agent_store: JsonAgentStore) -> None:
        # Act — connect() already called by fixture; call again
        await json_agent_store.connect()  # must not raise or reset state

        # Assert — store still operational
        assert json_agent_store.get_all() == []

    async def test_connect_loads_existing_json(self, tmp_path: Path) -> None:
        # Arrange — create store1, write data, close it
        path = tmp_path / "agents_test.json"
        store1 = JsonAgentStore(path=path)
        await store1.connect()
        await store1.upsert(make_agent_row("persist-agent"))
        await store1.set_bot_agent("telegram", "bot-1", "persist-agent")
        await store1.close()

        # Act — open a fresh store against the same file
        store2 = JsonAgentStore(path=path)
        await store2.connect()

        # Assert — data survives round-trip
        result = store2.get("persist-agent")
        assert result is not None
        assert result.name == "persist-agent"
        assert store2.get_bot_agent("telegram", "bot-1") == "persist-agent"
        await store2.close()


# ---------------------------------------------------------------------------
# TestJsonAgentStoreCRUD
# ---------------------------------------------------------------------------


class TestJsonAgentStoreCRUD:
    """JsonAgentStore CRUD: upsert / get / get_all / delete."""

    async def test_upsert_and_get(self, json_agent_store: JsonAgentStore) -> None:
        # Arrange
        row = make_agent_row("lyra-default")

        # Act
        await json_agent_store.upsert(row)
        result = json_agent_store.get("lyra-default")

        # Assert
        assert result is not None
        assert result.name == "lyra-default"
        assert result.backend == "claude-cli"

    async def test_get_missing_returns_none(
        self, json_agent_store: JsonAgentStore
    ) -> None:
        # Act
        result = json_agent_store.get("nonexistent")

        # Assert
        assert result is None

    async def test_get_all(self, json_agent_store: JsonAgentStore) -> None:
        # Arrange
        await json_agent_store.upsert(make_agent_row("agent-a"))
        await json_agent_store.upsert(make_agent_row("agent-b"))

        # Act
        all_agents = json_agent_store.get_all()

        # Assert
        names = {a.name for a in all_agents}
        assert "agent-a" in names
        assert "agent-b" in names

    async def test_delete_removes_agent(self, json_agent_store: JsonAgentStore) -> None:
        # Arrange
        await json_agent_store.upsert(make_agent_row("to-delete"))

        # Act
        await json_agent_store.delete("to-delete")

        # Assert
        assert json_agent_store.get("to-delete") is None

    async def test_delete_raises_if_bot_assigned(
        self, json_agent_store: JsonAgentStore
    ) -> None:
        # Arrange
        await json_agent_store.upsert(make_agent_row("assigned-agent"))
        await json_agent_store.set_bot_agent("telegram", "bot-123", "assigned-agent")

        # Act + Assert — delete must refuse when a bot is assigned
        with pytest.raises(ValueError, match="assigned"):
            await json_agent_store.delete("assigned-agent")

    async def test_upsert_is_idempotent(self, json_agent_store: JsonAgentStore) -> None:
        # Arrange
        row = make_agent_row("dup-agent")

        # Act — upsert same name twice
        await json_agent_store.upsert(row)
        await json_agent_store.upsert(row)

        # Assert — only one entry in get_all()
        all_agents = json_agent_store.get_all()
        dup_count = sum(1 for a in all_agents if a.name == "dup-agent")
        assert dup_count == 1


# ---------------------------------------------------------------------------
# TestJsonBotMap
# ---------------------------------------------------------------------------


class TestJsonBotMap:
    """JsonAgentStore bot map: set / get / remove."""

    async def test_set_and_get_bot_agent(
        self, json_agent_store: JsonAgentStore
    ) -> None:
        # Arrange
        await json_agent_store.upsert(make_agent_row("telegram-agent"))

        # Act
        await json_agent_store.set_bot_agent("telegram", "bot-001", "telegram-agent")
        result = json_agent_store.get_bot_agent("telegram", "bot-001")

        # Assert
        assert result == "telegram-agent"

    async def test_get_missing_bot_returns_none(
        self, json_agent_store: JsonAgentStore
    ) -> None:
        # Act
        result = json_agent_store.get_bot_agent("telegram", "none")

        # Assert
        assert result is None

    async def test_remove_bot_agent(self, json_agent_store: JsonAgentStore) -> None:
        # Arrange
        await json_agent_store.upsert(make_agent_row("removable-agent"))
        await json_agent_store.set_bot_agent("discord", "bot-002", "removable-agent")

        # Act
        await json_agent_store.remove_bot_agent("discord", "bot-002")

        # Assert
        assert json_agent_store.get_bot_agent("discord", "bot-002") is None

    async def test_remove_missing_bot_is_noop(
        self, json_agent_store: JsonAgentStore
    ) -> None:
        # Act + Assert — must not raise
        await json_agent_store.remove_bot_agent("telegram", "nonexistent-bot")

    async def test_get_all_bot_mappings(self, json_agent_store: JsonAgentStore) -> None:
        # Arrange
        await json_agent_store.upsert(make_agent_row("map-agent"))
        await json_agent_store.set_bot_agent("telegram", "bot-map-1", "map-agent")
        await json_agent_store.set_bot_agent("discord", "bot-map-2", "map-agent")

        # Act
        mappings = json_agent_store.get_all_bot_mappings()

        # Assert
        assert mappings[("telegram", "bot-map-1")] == "map-agent"
        assert mappings[("discord", "bot-map-2")] == "map-agent"


# ---------------------------------------------------------------------------
# TestJsonBotSettings
# ---------------------------------------------------------------------------


class TestJsonBotSettings:
    """JsonAgentStore bot settings: set / get / defaults / error cases."""

    async def test_set_and_get_bot_settings(
        self, json_agent_store: JsonAgentStore
    ) -> None:
        await json_agent_store.upsert(make_agent_row("settings-agent"))
        await json_agent_store.set_bot_agent("discord", "bot-s1", "settings-agent")

        await json_agent_store.set_bot_settings(
            "discord", "bot-s1", {"watch_channels": [111, 222]}
        )

        result = json_agent_store.get_bot_settings("discord", "bot-s1")
        assert result == {"watch_channels": [111, 222]}

    async def test_get_settings_default_empty(
        self, json_agent_store: JsonAgentStore
    ) -> None:
        await json_agent_store.upsert(make_agent_row("no-settings-agent"))
        await json_agent_store.set_bot_agent("discord", "bot-s2", "no-settings-agent")

        result = json_agent_store.get_bot_settings("discord", "bot-s2")
        assert result == {}

    async def test_set_bot_agent_with_settings(
        self, json_agent_store: JsonAgentStore
    ) -> None:
        await json_agent_store.upsert(make_agent_row("inline-agent"))

        await json_agent_store.set_bot_agent(
            "discord",
            "bot-s3",
            "inline-agent",
            settings={"watch_channels": [333]},
        )

        result = json_agent_store.get_bot_settings("discord", "bot-s3")
        assert result == {"watch_channels": [333]}

    async def test_set_bot_agent_without_settings_preserves_existing(
        self, json_agent_store: JsonAgentStore
    ) -> None:
        await json_agent_store.upsert(make_agent_row("preserve-agent"))
        await json_agent_store.set_bot_agent(
            "discord",
            "bot-s4",
            "preserve-agent",
            settings={"watch_channels": [444]},
        )

        # Reassign agent without settings — must not clear existing settings
        await json_agent_store.set_bot_agent("discord", "bot-s4", "preserve-agent")

        result = json_agent_store.get_bot_settings("discord", "bot-s4")
        assert result == {"watch_channels": [444]}

    async def test_remove_bot_clears_settings(
        self, json_agent_store: JsonAgentStore
    ) -> None:
        await json_agent_store.upsert(make_agent_row("rm-settings-agent"))
        await json_agent_store.set_bot_agent(
            "discord",
            "bot-s5",
            "rm-settings-agent",
            settings={"watch_channels": [555]},
        )

        await json_agent_store.remove_bot_agent("discord", "bot-s5")

        result = json_agent_store.get_bot_settings("discord", "bot-s5")
        assert result == {}

    async def test_set_bot_settings_raises_on_missing_row(
        self, json_agent_store: JsonAgentStore
    ) -> None:
        with pytest.raises(ValueError, match="No bot_agent_map row"):
            await json_agent_store.set_bot_settings(
                "discord", "bot-nonexistent", {"watch_channels": [1]}
            )


# ---------------------------------------------------------------------------
# TestJsonRuntimeState
# ---------------------------------------------------------------------------


class TestJsonRuntimeState:
    """JsonAgentStore runtime state: always-empty reads, status validation."""

    async def test_get_all_runtime_states_returns_empty(
        self, json_agent_store: JsonAgentStore
    ) -> None:
        # Act
        states = await json_agent_store.get_all_runtime_states()

        # Assert
        assert states == {}

    async def test_set_runtime_state_invalid_status_raises(
        self, json_agent_store: JsonAgentStore
    ) -> None:
        with pytest.raises(ValueError, match="invalid status"):
            await json_agent_store.set_runtime_state("some-agent", "unknown")

    async def test_set_runtime_state_valid_is_noop(
        self, json_agent_store: JsonAgentStore
    ) -> None:
        # Act + Assert — must not raise
        await json_agent_store.set_runtime_state("some-agent", "active", pool_count=2)
        await json_agent_store.set_runtime_state("some-agent", "idle")
        await json_agent_store.set_runtime_state("some-agent", "error")

        # Runtime state not persisted
        states = await json_agent_store.get_all_runtime_states()
        assert states == {}


# ---------------------------------------------------------------------------
# TestMakeAgentStore
# ---------------------------------------------------------------------------


class TestMakeAgentStore:
    """make_agent_store() factory: env-var-driven dispatch."""

    def test_default_returns_agent_store(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LYRA_DB", raising=False)

        from lyra.core.stores.agent_store_protocol import make_agent_store
        from lyra.infrastructure.stores.agent_store import AgentStore

        store = make_agent_store()
        assert isinstance(store, AgentStore)

    def test_lyra_db_json_returns_json_store(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LYRA_DB", "json")
        monkeypatch.delenv("LYRA_AGENT_STORE_PATH", raising=False)

        from lyra.core.stores.agent_store_protocol import make_agent_store

        store = make_agent_store()
        assert isinstance(store, JsonAgentStore)

    def test_lyra_agent_store_path_overrides_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        custom_path = tmp_path / "custom_store.json"
        monkeypatch.setenv("LYRA_DB", "json")
        monkeypatch.setenv("LYRA_AGENT_STORE_PATH", str(custom_path))

        from lyra.core.stores.agent_store_protocol import make_agent_store

        store = make_agent_store()
        assert isinstance(store, JsonAgentStore)
        assert store.path == custom_path

    def test_lyra_db_other_value_returns_agent_store(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LYRA_DB", "sqlite")

        from lyra.core.stores.agent_store_protocol import make_agent_store
        from lyra.infrastructure.stores.agent_store import AgentStore

        store = make_agent_store()
        assert isinstance(store, AgentStore)


# ---------------------------------------------------------------------------
# TestJsonSeedFromToml
# ---------------------------------------------------------------------------


class TestJsonSeedFromToml:
    """JsonAgentStore.seed_from_toml() — same matrix as test_agent_store_seed."""

    def _write_toml(self, path: Path, name: str = "seeded-agent") -> Path:
        toml_file = path / f"{name}.toml"
        toml_file.write_text(
            f"""
[agent]
name = "{name}"
backend = "claude-cli"
model = "claude-3-5-haiku-20241022"
max_turns = 5
tools = []
plugins = []
show_intermediate = false
""",
            encoding="utf-8",
        )
        return toml_file

    async def test_seed_imports_toml(
        self, json_agent_store: JsonAgentStore, tmp_path: Path
    ) -> None:
        # Arrange
        toml_file = self._write_toml(tmp_path, "seeded-agent")

        # Act
        count = await json_agent_store.seed_from_toml(toml_file)

        # Assert
        assert count == 1
        result = json_agent_store.get("seeded-agent")
        assert result is not None
        assert result.name == "seeded-agent"

    async def test_seed_idempotent(
        self, json_agent_store: JsonAgentStore, tmp_path: Path
    ) -> None:
        # Arrange
        toml_file = self._write_toml(tmp_path, "idem-agent")

        # Act — seed twice
        await json_agent_store.seed_from_toml(toml_file)
        count = await json_agent_store.seed_from_toml(toml_file)

        # Assert — second call returns 0 (already exists)
        assert count == 0

    async def test_seed_force_overwrites(
        self, json_agent_store: JsonAgentStore, tmp_path: Path
    ) -> None:
        # Arrange
        toml_file = self._write_toml(tmp_path, "force-agent")
        await json_agent_store.seed_from_toml(toml_file)

        # Modify the row in store
        from lyra.core.agent.agent_models import AgentRow

        existing = json_agent_store.get("force-agent")
        assert existing is not None
        modified = AgentRow(
            name=existing.name,
            backend="openai",  # changed value
            model=existing.model,
            source="manual",
        )
        await json_agent_store.upsert(modified)
        _row = json_agent_store.get("force-agent")
        assert _row is not None
        assert _row.backend == "openai"

        # Act — seed with force=True must restore TOML values
        count = await json_agent_store.seed_from_toml(toml_file, force=True)

        # Assert
        assert count == 1
        row_restored = json_agent_store.get("force-agent")
        assert row_restored is not None and row_restored.backend == "claude-cli"

    async def test_seed_skips_unparseable(
        self, json_agent_store: JsonAgentStore, tmp_path: Path
    ) -> None:
        # Arrange
        garbage_file = tmp_path / "garbage.toml"
        garbage_file.write_text("this is [[[not valid toml", encoding="utf-8")

        # Act — must not raise
        count = await json_agent_store.seed_from_toml(garbage_file)

        # Assert — 0 rows imported, no exception
        assert count == 0


# ---------------------------------------------------------------------------
# TestJsonConnectPersistence
# ---------------------------------------------------------------------------


class TestJsonConnectPersistence:
    """Persistence: data written by JsonAgentStore survives close/connect."""

    async def test_bot_map_survives_reconnect(self, tmp_path: Path) -> None:
        path = tmp_path / "agents_test.json"
        store1 = JsonAgentStore(path=path)
        await store1.connect()
        await store1.upsert(make_agent_row("reconnect-agent"))
        await store1.set_bot_agent("discord", "bot-rc", "reconnect-agent")
        await store1.close()

        store2 = JsonAgentStore(path=path)
        await store2.connect()
        try:
            assert store2.get_bot_agent("discord", "bot-rc") == "reconnect-agent"
        finally:
            await store2.close()

    async def test_bot_settings_survive_reconnect(self, tmp_path: Path) -> None:
        path = tmp_path / "agents_test.json"
        store1 = JsonAgentStore(path=path)
        await store1.connect()
        await store1.upsert(make_agent_row("settings-rc-agent"))
        await store1.set_bot_agent(
            "discord",
            "bot-settings-rc",
            "settings-rc-agent",
            settings={"watch_channels": [999]},
        )
        await store1.close()

        store2 = JsonAgentStore(path=path)
        await store2.connect()
        try:
            result = store2.get_bot_settings("discord", "bot-settings-rc")
            assert result == {"watch_channels": [999]}
        finally:
            await store2.close()

    async def test_corrupt_json_starts_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "corrupt.json"
        path.write_text("{not valid json", encoding="utf-8")

        store = JsonAgentStore(path=path)
        await store.connect()  # must not raise
        try:
            assert store.get_all() == []
            assert store.get_all_bot_mappings() == {}
        finally:
            await store.close()
