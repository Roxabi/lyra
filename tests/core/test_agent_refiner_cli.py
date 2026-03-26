"""Tests for lyra agent patch and refine CLI commands."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.agent_models import AgentRow
from lyra.core.agent_refiner import (
    AgentRefiner,
    RefinementCancelled,
    RefinementPatch,
    TerminalIO,
)


@pytest.fixture(autouse=True)
def _restore_event_loop():  # noqa: F841  # autouse fixture
    """Restore a fresh event loop after each test."""
    yield
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)


def make_row(**kwargs) -> AgentRow:
    """Minimal AgentRow for testing."""
    defaults = dict(
        name="lyra_default",
        backend="anthropic-sdk",
        model="claude-haiku-4-5-20251001",
        persona_json='{"identity": {"display_name": "Lyra"}}',
        voice_json='{"tts": {"voice": "echo"}, "stt": {}}',
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
            "All done!\n<<PATCH>>\n"
            '{"persona_json": "{\\"identity\\": {\\"display_name\\": \\"Aryl\\"}}"}'
            "\n<<END_PATCH>>",
        ]

        mock_io = MagicMock(spec=TerminalIO)
        mock_io.prompt.side_effect = ["change the model first", "confirm"]

        refiner = AgentRefiner("lyra_default", store, driver=mock_driver)

        # Act
        result = refiner.run_session(mock_io)

        # Assert
        assert result.fields == {
            "persona_json": '{"identity": {"display_name": "Aryl"}}'
        }
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

        with (
            mock_patch("lyra.cli_agent_crud._connect_store", side_effect=fake_connect),
            mock_patch(
                "lyra.core.agent_refiner.AgentRefiner.run_session",
                return_value=RefinementPatch(fields={"model": "claude-opus-4-6"}),
            ),
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

        with (
            mock_patch("lyra.cli_agent_crud._connect_store", side_effect=fake_connect),
            mock_patch(
                "lyra.core.agent_refiner.AgentRefiner.run_session",
                side_effect=RefinementCancelled(),
            ),
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
