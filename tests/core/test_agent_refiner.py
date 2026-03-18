"""Tests for AgentRefiner, RefinementContext, RefinementPatch."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.agent_models import AgentRow
from lyra.core.agent_refiner import (
    AgentRefiner,
    RefinementCancelled,
    RefinementContext,
    RefinementPatch,
    TerminalIO,
)


@pytest.fixture(autouse=True)
def _restore_event_loop():
    """Restore a fresh event loop after each test.

    CliRunner.invoke() calls asyncio.run() which closes the current event
    loop. Subsequent tests using asyncio.get_event_loop() (the deprecated
    API) would fail without this reset.
    """
    yield
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_row(**kwargs) -> AgentRow:
    """Minimal AgentRow for testing."""
    defaults = dict(
        name="lyra_default",
        backend="anthropic-sdk",
        model="claude-haiku-4-5-20251001",
        persona="lyra",
        voice_json='{"tts": {"voice": "echo"}}',
        plugins_json='["plugin_a"]',
        patterns_json='{"bare_url": true}',
    )
    defaults.update(kwargs)
    return AgentRow(**defaults)  # type: ignore[arg-type]


def make_store(row: AgentRow | None = None) -> MagicMock:
    store = MagicMock()
    store.get.return_value = row
    store.upsert = AsyncMock()
    store.close = AsyncMock()
    return store


# ---------------------------------------------------------------------------
# T1 — read_profile (SC-1)
# ---------------------------------------------------------------------------


class TestReadProfile:
    """AgentRefiner.read_profile() — builds RefinementContext from store."""

    def test_read_profile_returns_correct_context(self) -> None:
        # Arrange
        row = make_row()
        store = make_store(row)
        refiner = AgentRefiner("lyra_default", store)

        # Act
        ctx = refiner.read_profile()

        # Assert
        assert ctx.agent_name == "lyra_default"
        assert ctx.model == "claude-haiku-4-5-20251001"
        assert ctx.persona == "lyra"
        assert ctx.plugins == ["plugin_a"]
        assert ctx.patterns == {"bare_url": True}

    def test_read_profile_raises_on_missing_agent(self) -> None:
        # Arrange
        store = make_store(row=None)
        refiner = AgentRefiner("missing", store)

        # Act + Assert
        with pytest.raises(ValueError, match="not found"):
            refiner.read_profile()

    def test_read_profile_handles_null_plugins_json(self) -> None:
        # Arrange — plugins_json defaults to "[]" in AgentRow
        row = make_row(plugins_json="[]", patterns_json=None)
        store = make_store(row)
        refiner = AgentRefiner("lyra_default", store)

        # Act
        ctx = refiner.read_profile()

        # Assert
        assert ctx.plugins == []
        assert ctx.patterns == {}

    def test_read_profile_parses_voice_json(self) -> None:
        # Arrange
        row = make_row(voice_json='{"tts": {"voice": "echo"}, "stt": {}}')
        store = make_store(row)
        refiner = AgentRefiner("lyra_default", store)

        # Act
        ctx = refiner.read_profile()

        # Assert — voice_json is passed through as raw string
        assert ctx.voice_json == '{"tts": {"voice": "echo"}, "stt": {}}'

    def test_read_profile_returns_frozen_context(self) -> None:
        # Arrange
        row = make_row()
        store = make_store(row)
        refiner = AgentRefiner("lyra_default", store)

        # Act
        ctx = refiner.read_profile()

        # Assert — RefinementContext is frozen
        assert isinstance(ctx, RefinementContext)
        with pytest.raises(AttributeError):
            ctx.agent_name = "new_name"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# T2 — apply_patch / RefinementPatch (SC-1)
# ---------------------------------------------------------------------------


class TestRefinementPatch:
    """RefinementPatch — as_json() and to_agent_row() behaviour."""

    def test_as_json_returns_valid_json_string(self) -> None:
        # Arrange
        patch = RefinementPatch(fields={"model": "claude-opus-4-6"})

        # Act
        result = patch.as_json()

        # Assert
        parsed = json.loads(result)
        assert parsed == {"model": "claude-opus-4-6"}

    def test_to_agent_row_applies_single_field(self) -> None:
        # Arrange
        existing = make_row()
        patch = RefinementPatch(fields={"model": "claude-opus-4-6"})

        # Act
        updated = patch.to_agent_row(existing)

        # Assert — patched field changed
        assert updated.model == "claude-opus-4-6"
        # Assert — all other fields unchanged
        assert updated.name == existing.name
        assert updated.backend == existing.backend
        assert updated.persona == existing.persona
        assert updated.voice_json == existing.voice_json
        assert updated.plugins_json == existing.plugins_json

    def test_to_agent_row_applies_multiple_fields(self) -> None:
        # Arrange
        existing = make_row()
        patch = RefinementPatch(fields={"model": "claude-opus-4-6", "persona": "aryl"})

        # Act
        updated = patch.to_agent_row(existing)

        # Assert
        assert updated.model == "claude-opus-4-6"
        assert updated.persona == "aryl"
        assert updated.name == existing.name

    def test_to_agent_row_does_not_mutate_original(self) -> None:
        # Arrange
        existing = make_row()
        original_model = existing.model
        patch = RefinementPatch(fields={"model": "claude-opus-4-6"})

        # Act
        patch.to_agent_row(existing)

        # Assert — original row is unchanged
        assert existing.model == original_model

    def test_as_json_multi_field_patch(self) -> None:
        # Arrange
        patch = RefinementPatch(
            fields={"model": "claude-opus-4-6", "streaming": True}
        )

        # Act
        result = patch.as_json()

        # Assert
        parsed = json.loads(result)
        assert parsed["model"] == "claude-opus-4-6"
        assert parsed["streaming"] is True


# ---------------------------------------------------------------------------
# T3 — CLI: lyra agent patch (SC-1)
# ---------------------------------------------------------------------------


class TestPatchCommand:
    """lyra agent patch — CLI integration via typer CliRunner."""

    @pytest.fixture()
    def cli(self):
        from typer.testing import CliRunner

        from lyra.cli_agent import agent_app

        return CliRunner(), agent_app

    def test_patch_command_success(self) -> None:
        # Arrange
        from unittest.mock import patch as mock_patch

        from typer.testing import CliRunner

        from lyra.cli_agent import agent_app

        row = make_row()
        mock_store = make_store(row)

        runner = CliRunner()

        async def fake_connect():
            return mock_store

        # Act
        with mock_patch("lyra.cli_agent_crud._connect_store", side_effect=fake_connect):
            result = runner.invoke(
                agent_app,
                ["patch", "lyra_default", "--json", '{"model": "claude-opus-4-6"}'],
            )

        # Assert
        assert result.exit_code == 0
        assert "Patched" in result.output

    def test_patch_command_outputs_field_names(self) -> None:
        # Arrange
        from unittest.mock import patch as mock_patch

        from typer.testing import CliRunner

        from lyra.cli_agent import agent_app

        row = make_row()
        mock_store = make_store(row)

        runner = CliRunner()

        async def fake_connect():
            return mock_store

        # Act
        with mock_patch("lyra.cli_agent_crud._connect_store", side_effect=fake_connect):
            result = runner.invoke(
                agent_app,
                ["patch", "lyra_default", "--json", '{"model": "claude-opus-4-6"}'],
            )

        # Assert
        assert "model" in result.output

    def test_patch_command_calls_store_upsert(self) -> None:
        # Arrange
        from unittest.mock import patch as mock_patch

        from typer.testing import CliRunner

        from lyra.cli_agent import agent_app

        row = make_row()
        mock_store = make_store(row)

        runner = CliRunner()

        async def fake_connect():
            return mock_store

        # Act
        with mock_patch("lyra.cli_agent_crud._connect_store", side_effect=fake_connect):
            runner.invoke(
                agent_app,
                ["patch", "lyra_default", "--json", '{"model": "claude-opus-4-6"}'],
            )

        # Assert — upsert was called with updated row
        mock_store.upsert.assert_awaited_once()
        updated_row = mock_store.upsert.call_args[0][0]
        assert updated_row.model == "claude-opus-4-6"


# ---------------------------------------------------------------------------
# T4 — CLI: invalid JSON input (SC-8)
# ---------------------------------------------------------------------------


class TestPatchInvalidJson:
    """lyra agent patch — rejects invalid JSON with exit_code 1."""

    def test_patch_invalid_json_exits_with_error(self) -> None:
        # Arrange
        from typer.testing import CliRunner

        from lyra.cli_agent import agent_app

        # Typer's CliRunner merges stderr into output by default — no mix_stderr needed
        runner = CliRunner()

        # Act
        result = runner.invoke(
            agent_app,
            ["patch", "lyra_default", "--json", "not-json"],
        )

        # Assert
        assert result.exit_code == 1
        # typer CliRunner combines stderr into output; check the combined output
        assert "invalid JSON" in result.output

    def test_patch_non_object_json_exits_with_error(self) -> None:
        # Arrange
        from typer.testing import CliRunner

        from lyra.cli_agent import agent_app

        runner = CliRunner()

        # Act — pass a JSON array (not an object)
        result = runner.invoke(
            agent_app,
            ["patch", "lyra_default", "--json", '["not", "an", "object"]'],
        )

        # Assert
        assert result.exit_code == 1
        assert "JSON object" in result.output or "dict" in result.output.lower()


# ---------------------------------------------------------------------------
# T5 — unknown agent raises ValueError (SC-9)
# ---------------------------------------------------------------------------


class TestRefineUnknownAgent:
    """AgentRefiner.read_profile() on an unknown agent raises ValueError."""

    def test_read_profile_unknown_agent_raises_value_error(self) -> None:
        # Arrange
        store = make_store(row=None)
        refiner = AgentRefiner("unknown", store)

        # Act + Assert
        with pytest.raises(ValueError, match="not found"):
            refiner.read_profile()

    def test_apply_patch_unknown_agent_raises_value_error(self) -> None:
        # Arrange — store returns None on first get call
        store = make_store(row=None)
        refiner = AgentRefiner("unknown", store)
        patch = RefinementPatch(fields={"model": "claude-opus-4-6"})

        # Act + Assert
        with pytest.raises(ValueError, match="not found"):
            refiner.apply_patch(patch)


# ---------------------------------------------------------------------------
# T6 — run_session returns RefinementPatch
# ---------------------------------------------------------------------------


class TestRunSession:
    """AgentRefiner.run_session() — drives LLM loop and extracts patch."""

    def test_run_session_returns_patch_when_llm_outputs_patch_block(self) -> None:
        # Arrange
        row = make_row()
        store = make_store(row)

        mock_driver = MagicMock()
        mock_driver.chat.side_effect = [
            # First call: initial greeting
            "Here is your profile. What would you like to change?",
            # Second call: response to "change the model" — no patch yet
            "Got it, I'll update the model to 'new-model'. Confirm?",
            # Third call: response to "confirm" — contains PATCH block
            'I\'ll update the model.\n<<PATCH>>\n{"model": "new-model"}\n<<END_PATCH>>',
        ]

        mock_io = MagicMock(spec=TerminalIO)
        # First prompt: describe what to change; second prompt: confirm
        mock_io.prompt.side_effect = ["change the model", "confirm"]

        refiner = AgentRefiner("lyra_default", store, driver=mock_driver)

        # Act
        result = refiner.run_session(mock_io)

        # Assert
        assert isinstance(result, RefinementPatch)
        assert result.fields == {"model": "new-model"}

    def test_run_session_continues_until_patch_found(self) -> None:
        # Arrange — two user turns before the patch appears
        row = make_row()
        store = make_store(row)

        mock_driver = MagicMock()
        mock_driver.chat.side_effect = [
            # First call: initial greeting (before loop)
            "Hello! Here's your current profile. What would you like to change?",
            # Second call: no patch yet
            "Got it. Any other changes?",
            # Third call: patch block
            'All done!\n<<PATCH>>\n{"persona": "aryl"}\n<<END_PATCH>>',
        ]

        mock_io = MagicMock(spec=TerminalIO)
        mock_io.prompt.side_effect = ["change the model first", "confirm"]

        refiner = AgentRefiner("lyra_default", store, driver=mock_driver)

        # Act
        result = refiner.run_session(mock_io)

        # Assert
        assert result.fields == {"persona": "aryl"}
        # Assert driver was called for greeting + the 2 loop turns
        assert mock_driver.chat.call_count == 3
        assert mock_io.prompt.call_count == 2

    def test_run_session_raises_on_abort(self) -> None:
        # Arrange
        row = make_row()
        store = make_store(row)

        mock_driver = MagicMock()
        mock_driver.chat.return_value = (
            "Here is your profile. What would you like to change?"
        )

        mock_io = MagicMock(spec=TerminalIO)
        mock_io.prompt.return_value = "quit"

        refiner = AgentRefiner("lyra_default", store, driver=mock_driver)

        # Act + Assert
        with pytest.raises(RefinementCancelled):
            refiner.run_session(mock_io)

        # Assert driver was called only for the greeting, not again after abort
        mock_driver.chat.assert_called_once()

    def test_run_session_skips_empty_input(self) -> None:
        # Arrange — first prompt returns empty (skipped), second returns abort
        row = make_row()
        store = make_store(row)

        mock_driver = MagicMock()
        mock_driver.chat.return_value = "Profile loaded. What to change?"

        mock_io = MagicMock(spec=TerminalIO)
        # Empty string is skipped; second call returns abort
        mock_io.prompt.side_effect = ["", "exit"]

        refiner = AgentRefiner("lyra_default", store, driver=mock_driver)

        # Act + Assert
        with pytest.raises(RefinementCancelled):
            refiner.run_session(mock_io)


# ---------------------------------------------------------------------------
# T7 — _extract_patch valid (static method)
# ---------------------------------------------------------------------------


class TestExtractPatchValid:
    """AgentRefiner._extract_patch() — extracts embedded JSON patch block."""

    def test_extract_patch_returns_patch_from_valid_block(self) -> None:
        # Arrange
        text = 'Some text <<PATCH>>\n{"voice_json": "test"}\n<<END_PATCH>>'

        # Act
        result = AgentRefiner._extract_patch(text)

        # Assert
        assert result is not None
        assert isinstance(result, RefinementPatch)
        assert result.fields == {"voice_json": "test"}

    def test_extract_patch_with_multiple_fields(self) -> None:
        # Arrange
        text = (
            "Here are the changes:\n"
            '<<PATCH>>\n{"model": "claude-opus-4-6", "streaming": true}\n<<END_PATCH>>'
        )

        # Act
        result = AgentRefiner._extract_patch(text)

        # Assert
        assert result is not None
        assert result.fields["model"] == "claude-opus-4-6"
        assert result.fields["streaming"] is True

    def test_extract_patch_inline_block(self) -> None:
        # Arrange — patch block on a single line
        text = '<<PATCH>>{"persona": "aryl"}<<END_PATCH>>'

        # Act
        result = AgentRefiner._extract_patch(text)

        # Assert
        assert result is not None
        assert result.fields == {"persona": "aryl"}

    def test_extract_patch_with_surrounding_text(self) -> None:
        # Arrange
        text = (
            "I'll update the following fields:\n"
            '<<PATCH>>\n{"i18n_language": "fr"}\n<<END_PATCH>>\n'
            "Please confirm these changes."
        )

        # Act
        result = AgentRefiner._extract_patch(text)

        # Assert
        assert result is not None
        assert result.fields == {"i18n_language": "fr"}


# ---------------------------------------------------------------------------
# T8 — _extract_patch missing (static method)
# ---------------------------------------------------------------------------


class TestExtractPatchMissing:
    """AgentRefiner._extract_patch() — returns None when no patch block present."""

    def test_extract_patch_returns_none_when_no_block(self) -> None:
        # Arrange
        text = "No patch here"

        # Act
        result = AgentRefiner._extract_patch(text)

        # Assert
        assert result is None

    def test_extract_patch_returns_none_for_empty_string(self) -> None:
        # Arrange + Act + Assert
        assert AgentRefiner._extract_patch("") is None

    def test_extract_patch_returns_none_for_malformed_block(self) -> None:
        # Arrange — markers present but JSON is invalid
        text = "<<PATCH>>\nnot valid json\n<<END_PATCH>>"

        # Act
        result = AgentRefiner._extract_patch(text)

        # Assert
        assert result is None

    def test_extract_patch_returns_none_for_non_dict_json(self) -> None:
        # Arrange — valid JSON but not a dict
        text = '<<PATCH>>\n["list", "not", "dict"]\n<<END_PATCH>>'

        # Act
        result = AgentRefiner._extract_patch(text)

        # Assert
        assert result is None

    def test_extract_patch_returns_none_when_only_start_marker(self) -> None:
        # Arrange — missing end marker
        text = '<<PATCH>>\n{"model": "new"}'

        # Act
        result = AgentRefiner._extract_patch(text)

        # Assert
        assert result is None


# ---------------------------------------------------------------------------
# T9 — apply_patch happy path (SC-1)
# ---------------------------------------------------------------------------


class TestApplyPatch:
    """AgentRefiner.apply_patch() — happy path and error branch."""

    def test_apply_patch_updates_and_returns_row(self) -> None:
        # Arrange
        row = make_row()
        store = make_store(row)
        refiner = AgentRefiner("lyra_default", store)
        patch = RefinementPatch(fields={"model": "claude-opus-4-6"})

        # Act
        updated = refiner.apply_patch(patch)

        # Assert
        assert updated.model == "claude-opus-4-6"
        assert updated.name == "lyra_default"
        store.upsert.assert_awaited_once()

    def test_apply_patch_unknown_agent_raises_value_error(self) -> None:
        # Arrange
        store = make_store(row=None)
        refiner = AgentRefiner("missing", store)
        patch = RefinementPatch(fields={"model": "claude-opus-4-6"})

        # Act + Assert
        with pytest.raises(ValueError, match="not found"):
            refiner.apply_patch(patch)


# ---------------------------------------------------------------------------
# T10 — refine CLI command (SC-2 + SC-3)
# ---------------------------------------------------------------------------


class TestRefineCommand:
    """Integration tests for `lyra agent refine` CLI command."""

    def test_refine_applies_patch_and_prints_diff(self) -> None:
        # Arrange
        from unittest.mock import patch as mock_patch

        from typer.testing import CliRunner

        from lyra.cli_agent import agent_app

        row = make_row()
        store = make_store(row)

        async def fake_connect():
            return store

        runner = CliRunner()

        with mock_patch(
            "lyra.cli_agent_crud._connect_store", side_effect=fake_connect
        ), mock_patch(
            "lyra.core.agent_refiner.AgentRefiner.run_session",
            return_value=RefinementPatch(fields={"model": "claude-opus-4-6"}),
        ):
            result = runner.invoke(agent_app, ["refine", "lyra_default"])

        # Assert
        assert result.exit_code == 0, result.output
        assert "claude-opus-4-6" in result.output  # diff shows new value
        store.upsert.assert_awaited_once()

    def test_refine_cancelled_by_keyboard_interrupt(self) -> None:
        # Arrange
        from unittest.mock import patch as mock_patch

        from typer.testing import CliRunner

        from lyra.cli_agent import agent_app

        row = make_row()
        store = make_store(row)

        async def fake_connect():
            return store

        runner = CliRunner()

        with mock_patch(
            "lyra.cli_agent_crud._connect_store", side_effect=fake_connect
        ), mock_patch(
            "lyra.core.agent_refiner.AgentRefiner.run_session",
            side_effect=RefinementCancelled(),
        ):
            result = runner.invoke(agent_app, ["refine", "lyra_default"])

        # Assert
        assert result.exit_code == 0
        assert "cancelled" in result.output.lower()
        store.close.assert_awaited()

    def test_refine_unknown_agent_exits_with_error(self) -> None:
        # Arrange
        from unittest.mock import patch as mock_patch

        from typer.testing import CliRunner

        from lyra.cli_agent import agent_app

        store = make_store(row=None)  # agent not found

        async def fake_connect():
            return store

        runner = CliRunner()

        with mock_patch("lyra.cli_agent_crud._connect_store", side_effect=fake_connect):
            result = runner.invoke(agent_app, ["refine", "unknown_agent"])

        # Assert — agent not found propagates as exit code 1
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# T11 — _extract_patch multi-line / pretty-printed JSON (SC-7)
# ---------------------------------------------------------------------------


class TestExtractPatchMultiLine:
    """AgentRefiner._extract_patch() — handles pretty-printed and complex JSON."""

    def test_extract_patch_handles_pretty_printed_json(self) -> None:
        # Arrange — LLMs commonly emit pretty-printed JSON
        text = (
            "I'll update the model and persona.\n"
            "<<PATCH>>\n"
            '{\n  "model": "claude-opus-4-6",\n  "persona": "aryl"\n}\n'
            "<<END_PATCH>>\n"
            "These changes will take effect on restart."
        )

        # Act
        result = AgentRefiner._extract_patch(text)

        # Assert
        assert result is not None
        assert result.fields == {"model": "claude-opus-4-6", "persona": "aryl"}

    def test_extract_patch_handles_json_with_boolean_values(self) -> None:
        # Arrange — JSON with booleans (a common LLM output pattern)
        text = '<<PATCH>>\n{"model": "new", "streaming": true}\n<<END_PATCH>>'

        # Act
        result = AgentRefiner._extract_patch(text)

        # Assert
        assert result is not None
        assert result.fields["model"] == "new"
        assert result.fields["streaming"] is True


# ---------------------------------------------------------------------------
# T12 — RefinementPatch allow-list validation (future: REFINABLE_FIELDS)
# ---------------------------------------------------------------------------


class TestRefinementPatchValidation:
    """RefinementPatch — allow-list validation via REFINABLE_FIELDS + __post_init__."""

    def test_disallowed_field_raises_value_error(self) -> None:
        # A security-sensitive field that must not be patchable via LLM
        with pytest.raises(ValueError, match="disallowed"):
            RefinementPatch(fields={"skip_permissions": True})

    def test_unknown_field_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="disallowed"):
            RefinementPatch(fields={"nonexistent_field": "value"})

    def test_valid_fields_accepted(self) -> None:
        # Should not raise — model and persona are in REFINABLE_FIELDS
        patch = RefinementPatch(
            fields={"model": "claude-opus-4-6", "persona": "lyra"}
        )
        assert patch.fields["model"] == "claude-opus-4-6"
