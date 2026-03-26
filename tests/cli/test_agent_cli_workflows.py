"""Tests for lyra agent CLI workflows: edit, delete (happy path), assign (happy path),
validate (DB path), and TTS editing.

After #346, AgentRow no longer has tts_json/stt_json/persona/i18n_language.
Voice data lives in voice_json = '{"tts": {...}, "stt": {...}}'.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lyra.cli import agent_app  # type: ignore[import-not-found]
from lyra.core.stores.agent_store import AgentRow, AgentStore

# ---------------------------------------------------------------------------
# Shared runner
# ---------------------------------------------------------------------------

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helper: seed an AgentRow into DB synchronously
# ---------------------------------------------------------------------------


def _seed_agent(  # noqa: PLR0913
    db_path: Path,
    name: str = "testagent",
    backend: str = "claude-cli",
    model: str = "claude-sonnet-4-6",
    smart_routing_json: str | None = None,
    voice_json: str | None = None,
) -> None:
    """Insert an AgentRow into a fresh (or existing) DB at db_path."""

    async def _run() -> None:
        store = AgentStore(db_path=db_path)
        await store.connect()
        await store.upsert(
            AgentRow(
                name=name,
                backend=backend,
                model=model,
                smart_routing_json=smart_routing_json,
                voice_json=voice_json,
            )
        )
        await store.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# TestAgentEditCommand
# ---------------------------------------------------------------------------


class TestAgentEditCommand:
    """Tests for `lyra agent edit <name>`."""

    def test_edit_missing_agent_exits_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """edit of a non-existent agent exits non-zero with 'not found' message."""
        # Arrange
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))

        # Act
        result = runner.invoke(agent_app, ["edit", "nonexistent"])

        # Assert
        assert result.exit_code != 0, result.output
        assert "not found" in result.output.lower()

    def test_edit_no_changes_exits_0(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """edit with all-blank inputs exits 0 and prints 'No changes'."""
        # Arrange
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        _seed_agent(tmp_path / "auth.db", name="edit-nochange")

        # Act -- send 8 blank lines (editable fields) + "N" for TTS init prompt
        blank_inputs = "\n".join([""] * 7 + ["N"]) + "\n"
        result = runner.invoke(agent_app, ["edit", "edit-nochange"], input=blank_inputs)

        # Assert
        assert result.exit_code == 0, result.output
        assert "no changes" in result.output.lower()

    def test_edit_updates_field(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """edit with a non-blank model input persists the new value in DB."""
        # Arrange
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        db_path = tmp_path / "auth.db"
        _seed_agent(db_path, name="edit-update", model="claude-sonnet-4-6")

        # Act -- fields: backend, model, max_turns, persona_json,
        # show_intermediate, cwd, memory_namespace, fallback_language
        # (blank = keep current)
        # Provide new model on 2nd prompt; leave all others blank; "N" for TTS init
        inputs = "\n".join(["", "claude-opus-4-6", "", "", "", "", "", "N"]) + "\n"
        result = runner.invoke(agent_app, ["edit", "edit-update"], input=inputs)

        # Assert -- command succeeded
        assert result.exit_code == 0, result.output

        # Re-read from a new store instance to verify DB persistence
        async def _check() -> AgentRow | None:
            store = AgentStore(db_path=db_path)
            await store.connect()
            row = store.get("edit-update")
            await store.close()
            return row

        updated = asyncio.run(_check())
        assert updated is not None
        assert updated.model == "claude-opus-4-6"


# ---------------------------------------------------------------------------
# TestAgentDeleteHappyPath
# ---------------------------------------------------------------------------


class TestAgentDeleteHappyPath:
    """Happy-path test for `lyra agent delete <name> --yes`."""

    def test_delete_happy_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """delete an existing agent exits 0 and removes it from DB."""
        # Arrange
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        db_path = tmp_path / "auth.db"
        _seed_agent(db_path, name="to-delete")

        # Act
        result = runner.invoke(agent_app, ["delete", "to-delete", "--yes"])

        # Assert -- command succeeded
        assert result.exit_code == 0, result.output

        # Verify removal from DB
        async def _check() -> AgentRow | None:
            store = AgentStore(db_path=db_path)
            await store.connect()
            row = store.get("to-delete")
            await store.close()
            return row

        assert asyncio.run(_check()) is None


# ---------------------------------------------------------------------------
# TestAgentAssignHappyPath
# ---------------------------------------------------------------------------


class TestAgentAssignHappyPath:
    """Happy-path test for `lyra agent assign <name> --bot <id> --platform <p>`."""

    def test_assign_happy_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """assign an existing agent exits 0 and prints 'Assigned'."""
        # Arrange
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        _seed_agent(tmp_path / "auth.db", name="assign-me")

        # Act
        result = runner.invoke(
            agent_app,
            ["assign", "assign-me", "--bot", "mybot", "--platform", "telegram"],
        )

        # Assert
        assert result.exit_code == 0, result.output
        assert "assigned" in result.output.lower()


# ---------------------------------------------------------------------------
# TestAgentValidateDBPath
# ---------------------------------------------------------------------------


class TestAgentValidateDBPath:
    """Tests for `lyra agent validate <name>` against DB (no --agents-dir)."""

    def test_validate_valid_agent_exits_0(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """validate a well-formed agent from DB exits 0 and prints 'OK'."""
        # Arrange
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        _seed_agent(
            tmp_path / "auth.db",
            name="valid-agent",
            backend="anthropic-sdk",
            model="claude-sonnet-4-6",
        )

        # Act
        result = runner.invoke(agent_app, ["validate", "valid-agent"])

        # Assert
        assert result.exit_code == 0, result.output
        assert "ok" in result.output.lower()

    def test_validate_sr_mismatch_exits_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """validate exits 1 when smart_routing.enabled=true but backend=claude-cli."""
        # Arrange
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        _seed_agent(
            tmp_path / "auth.db",
            name="mismatch-agent",
            backend="claude-cli",
            model="claude-sonnet-4-6",
            smart_routing_json='{"enabled": true}',
        )

        # Act
        result = runner.invoke(agent_app, ["validate", "mismatch-agent"])

        # Assert
        assert result.exit_code != 0, result.output


# ---------------------------------------------------------------------------
# TestAgentEditTTS
# ---------------------------------------------------------------------------


class TestAgentEditTTS:
    """Tests for TTS sub-section in `lyra agent edit <name>` (issue #280)."""

    def test_edit_existing_tts_updates_voice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T1: edit with pre-existing voice_json updates voice field in DB."""
        # Arrange
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        db_path = tmp_path / "auth.db"
        _seed_agent(
            db_path,
            name="tts-update",
            voice_json='{"tts": {"engine":"qwen","voice":"mia"}, "stt": {}}',
        )

        # Act -- 8 blank scalars, then TTS fields:
        # engine=blank (keep), voice=new-voice, rest blank
        # TTS field order: engine, voice, language, accent, personality,
        #                  speed, emotion, exaggeration, cfg_weight
        tts_inputs = "\n".join(["", "new-voice"] + [""] * 7)
        inputs = "\n".join([""] * 7) + "\n" + tts_inputs + "\n"
        result = runner.invoke(agent_app, ["edit", "tts-update"], input=inputs)

        # Assert -- command succeeded
        assert result.exit_code == 0, result.output

        # Verify voice_json in DB has updated voice
        async def _check() -> AgentRow | None:
            store = AgentStore(db_path=db_path)
            await store.connect()
            row = store.get("tts-update")
            await store.close()
            return row

        updated = asyncio.run(_check())
        assert updated is not None
        voice = json.loads(updated.voice_json)  # type: ignore[arg-type]
        tts = voice["tts"]
        assert tts["voice"] == "new-voice"
        assert tts["engine"] == "qwen"  # unchanged

    def test_edit_no_tts_init_y_sets_engine(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T2: edit with no voice_json, answer 'y' to init, provide engine value."""
        # Arrange
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        db_path = tmp_path / "auth.db"
        _seed_agent(db_path, name="tts-init")

        # Act -- 8 blank scalars, "y" for TTS init, engine="qwen", rest blank
        # After "y", prompts are: engine, voice, language, accent, personality,
        #                          speed, emotion, exaggeration, cfg_weight
        tts_inputs = "\n".join(["qwen"] + [""] * 8)
        inputs = "\n".join([""] * 7) + "\n" + "y\n" + tts_inputs + "\n"
        result = runner.invoke(agent_app, ["edit", "tts-init"], input=inputs)

        # Assert -- command succeeded
        assert result.exit_code == 0, result.output

        # Verify voice_json in DB contains the engine
        async def _check() -> AgentRow | None:
            store = AgentStore(db_path=db_path)
            await store.connect()
            row = store.get("tts-init")
            await store.close()
            return row

        updated = asyncio.run(_check())
        assert updated is not None
        voice = json.loads(updated.voice_json)  # type: ignore[arg-type]
        assert voice["tts"].get("engine") == "qwen"

    def test_edit_tts_float_fields_stored_as_float(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T3: exaggeration and cfg_weight inputs are stored as floats, not strings."""
        # Arrange
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        db_path = tmp_path / "auth.db"
        _seed_agent(db_path, name="tts-float")

        # Act -- 8 blank scalars, "y" for TTS init, blank engine/voice/language/accent/
        #       personality/speed/emotion, exaggeration="0.6", cfg_weight="0.3"
        tts_inputs = "\n".join([""] * 7 + ["0.6", "0.3"])
        inputs = "\n".join([""] * 7) + "\n" + "y\n" + tts_inputs + "\n"
        result = runner.invoke(agent_app, ["edit", "tts-float"], input=inputs)

        # Assert
        assert result.exit_code == 0, result.output

        async def _check() -> AgentRow | None:
            store = AgentStore(db_path=db_path)
            await store.connect()
            row = store.get("tts-float")
            await store.close()
            return row

        updated = asyncio.run(_check())
        assert updated is not None
        voice = json.loads(updated.voice_json)  # type: ignore[arg-type]
        tts = voice["tts"]
        assert isinstance(tts["exaggeration"], float)
        assert isinstance(tts["cfg_weight"], float)
        assert tts["exaggeration"] == pytest.approx(0.6)
        assert tts["cfg_weight"] == pytest.approx(0.3)

    def test_edit_invalid_float_input_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T8: invalid float input for exaggeration is skipped, field unchanged."""
        # Arrange
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        db_path = tmp_path / "auth.db"
        _seed_agent(
            db_path,
            name="tts-badfloat",
            voice_json='{"tts": {"engine":"qwen"}, "stt": {}}',
        )

        # Act -- 8 blank scalars, TTS section (existing): engine=blank, voice=blank,
        #       language=blank, accent=blank, personality=blank, speed=blank,
        #       emotion=blank, exaggeration="notafloat", cfg_weight=blank
        tts_inputs = "\n".join([""] * 7 + ["notafloat", ""])
        inputs = "\n".join([""] * 7) + "\n" + tts_inputs + "\n"
        result = runner.invoke(agent_app, ["edit", "tts-badfloat"], input=inputs)

        # Assert -- exits 0 (invalid float is skipped, not a fatal error)
        assert result.exit_code == 0, result.output

        # Verify voice_json exaggeration was NOT set
        async def _check() -> AgentRow | None:
            store = AgentStore(db_path=db_path)
            await store.connect()
            row = store.get("tts-badfloat")
            await store.close()
            return row

        updated = asyncio.run(_check())
        assert updated is not None
        # voice_json.tts should not have gained an exaggeration field
        voice = json.loads(updated.voice_json)  # type: ignore[arg-type]
        assert "exaggeration" not in voice["tts"]
