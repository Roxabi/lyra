"""AgentStore seed, bot-map reconnect, and voice_json column tests.

After #346, AgentRow no longer has persona, tts_json, stt_json, i18n_language.
Voice data lives in voice_json = '{"tts": {...}, "stt": {...}}'.
"""

from __future__ import annotations

import json
from pathlib import Path

from lyra.infrastructure.stores.agent_store import AgentRow, AgentStore

from .conftest import make_agent_row, make_store

# ---------------------------------------------------------------------------
# TestSeedFromToml
# ---------------------------------------------------------------------------


class TestSeedFromToml:
    """AgentStore.seed_from_toml() -- TOML import, idempotency, force, bad files."""

    def _write_toml(self, path: Path, name: str = "seeded-agent") -> Path:
        """Write a minimal valid agent TOML to path and return the file path."""
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

            # Act -- seed twice
            await store.seed_from_toml(toml_file)
            count = await store.seed_from_toml(toml_file)

            # Assert -- second call inserts 0 rows
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

            # Act -- seed with force=True must restore TOML values
            count = await store.seed_from_toml(toml_file, force=True)

            # Assert
            assert count == 1
            row_restored = store.get("force-agent")
            assert row_restored is not None and row_restored.backend == "claude-cli"
        finally:
            await store.close()

    async def test_seed_skips_unparseable(self, tmp_path: Path) -> None:
        # Arrange
        store = await make_store(tmp_path)
        try:
            garbage_file = tmp_path / "garbage.toml"
            garbage_file.write_text("this is [[[not valid toml", encoding="utf-8")

            # Act -- must not raise
            count = await store.seed_from_toml(garbage_file)

            # Assert -- 0 rows imported, no exception
            assert count == 0
        finally:
            await store.close()

    async def test_seed_name_from_model_section(self, tmp_path: Path) -> None:
        # Arrange -- TOML has [model].name but no [agent].name
        store = await make_store(tmp_path)
        try:
            toml_file = tmp_path / "model-named.toml"
            toml_file.write_text(
                """
[model]
name = "model-named-agent"
backend = "claude-cli"
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
        # Arrange -- TOML has [agent] section but no name key and no [model] section
        store = await make_store(tmp_path)
        try:
            toml_file = tmp_path / "nameless.toml"
            toml_file.write_text(
                """
[agent]
backend = "claude-cli"
model = "claude-3-5-haiku-20241022"
""",
                encoding="utf-8",
            )

            # Act
            count = await store.seed_from_toml(toml_file)

            # Assert -- no name found, must return 0 and not insert anything
            assert count == 0
            assert store.get_all() == []
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# TestBotMapExtra
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
                    backend="claude-cli",
                    model="claude-3-5-haiku-20241022",
                    source="db",
                )
            )
            await store.upsert(
                AgentRow(
                    name="agent-b",
                    backend="claude-cli",
                    model="claude-3-5-haiku-20241022",
                    source="db",
                )
            )

            # Act -- assign then reassign
            await store.set_bot_agent("telegram", "b1", "agent-a")
            await store.set_bot_agent("telegram", "b1", "agent-b")

            # Assert -- cache reflects the new assignment
            assert store.get_bot_agent("telegram", "b1") == "agent-b"
        finally:
            await store.close()

    async def test_bot_map_warm_on_reconnect(self, tmp_path: Path) -> None:
        # Arrange -- set mapping in store1, close it
        db_path = tmp_path / "auth.db"
        store1 = AgentStore(db_path=str(db_path))
        await store1.connect()
        await store1.upsert(
            AgentRow(
                name="mapped-agent",
                backend="claude-cli",
                model="claude-3-5-haiku-20241022",
                source="db",
            )
        )
        await store1.set_bot_agent("telegram", "bot-reconnect", "mapped-agent")
        await store1.close()

        # Act -- open fresh store against same DB
        store2 = AgentStore(db_path=str(db_path))
        await store2.connect()
        try:
            result = store2.get_bot_agent("telegram", "bot-reconnect")

            # Assert -- bot map was warmed from DB
            assert result == "mapped-agent"
        finally:
            await store2.close()


# ---------------------------------------------------------------------------
# TestVoiceJsonColumns
# ---------------------------------------------------------------------------


class TestVoiceJsonColumns:
    """AgentRow voice_json column: upsert, warm cache, seed_from_toml."""

    async def test_upsert_and_get_with_voice_json(
        self, agent_store: AgentStore
    ) -> None:
        # Arrange
        voice_data = {
            "tts": {"engine": "chatterbox", "voice": "en-US-1", "chunked": True},
            "stt": {"language_detection_threshold": 0.8, "language_fallback": "en"},
        }
        row = AgentRow(
            name="tts-agent",
            backend="claude-cli",
            model="claude-3-5-haiku-20241022",
            voice_json=json.dumps(voice_data),
        )

        # Act
        await agent_store.upsert(row)
        result = agent_store.get("tts-agent")

        # Assert
        assert result is not None
        assert result.voice_json is not None
        parsed = json.loads(result.voice_json)
        assert parsed["tts"]["engine"] == "chatterbox"
        assert parsed["stt"]["language_fallback"] == "en"

    async def test_upsert_null_voice_json(self, agent_store: AgentStore) -> None:
        # Arrange -- no voice_json (nullable column defaults to None)
        row = make_agent_row("no-voice-agent")

        # Act
        await agent_store.upsert(row)
        result = agent_store.get("no-voice-agent")

        # Assert
        assert result is not None
        assert result.voice_json is None

    async def test_voice_json_warm_on_reconnect(self, tmp_path: Path) -> None:
        # Arrange -- write voice_json row in store1, close it
        db_path = tmp_path / "auth.db"
        store1 = AgentStore(db_path=str(db_path))
        await store1.connect()
        voice_data = {"tts": {"voice": "en-GB-2"}, "stt": {}}
        row = AgentRow(
            name="reconnect-voice-agent",
            backend="claude-cli",
            model="claude-3-5-haiku-20241022",
            voice_json=json.dumps(voice_data),
            source="db",
        )
        await store1.upsert(row)
        await store1.close()

        # Act -- open fresh store against same DB
        store2 = AgentStore(db_path=str(db_path))
        await store2.connect()
        try:
            result = store2.get("reconnect-voice-agent")

            # Assert -- voice_json must survive round-trip through DB
            assert result is not None
            assert result.voice_json is not None
            parsed = json.loads(result.voice_json)
            assert parsed["tts"]["voice"] == "en-GB-2"
        finally:
            await store2.close()

    async def test_seed_from_toml_with_tts_stt(self, tmp_path: Path) -> None:
        # Arrange -- TOML with [tts] and [stt] sections -> merged into voice_json
        store = await make_store(tmp_path)
        try:
            toml_file = tmp_path / "tts-agent.toml"
            toml_file.write_text(
                """
[agent]
name = "tts-seeded-agent"
backend = "claude-cli"
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

            # Assert -- seeder merges [tts] + [stt] into voice_json
            assert count == 1
            result = store.get("tts-seeded-agent")
            assert result is not None
            assert result.voice_json is not None
            voice = json.loads(result.voice_json)
            assert voice["tts"]["engine"] == "chatterbox"
            assert voice["tts"]["voice"] == "en-US-1"
            assert voice["tts"]["chunked"] is True
            assert voice["tts"]["chunk_size"] == 200
            assert voice["stt"]["language_detection_threshold"] == 0.75
            assert voice["stt"]["language_fallback"] == "en"
        finally:
            await store.close()

    async def test_seed_from_toml_no_tts_stt_is_null(self, tmp_path: Path) -> None:
        # Arrange -- TOML without [tts] or [stt] sections
        store = await make_store(tmp_path)
        try:
            toml_file = tmp_path / "plain-agent.toml"
            toml_file.write_text(
                """
[agent]
name = "plain-agent"
backend = "claude-cli"
model = "claude-3-5-haiku-20241022"
""",
                encoding="utf-8",
            )

            # Act
            await store.seed_from_toml(toml_file)
            result = store.get("plain-agent")

            # Assert -- missing sections -> NULL voice_json
            assert result is not None
            assert result.voice_json is None
        finally:
            await store.close()
