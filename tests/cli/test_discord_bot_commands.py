"""Integration tests for `factory agent discord` bot CLI commands (issue #1415).

Mirror of `test_telegram_bot_commands.py` with `discord` platform.
Commands: list, show, add, edit, patch, remove, assign, unassign, validate.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import typer
from typer.testing import CliRunner

from factory.cli import agent_app
from factory.core.agent.agent_models import AgentRow
from factory.core.agent.bot_models import BotRow
from factory.infrastructure.stores.registry.agent_store import AgentStore
from tests.helpers.bot_store import db_get, db_upsert

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_agent(db_path: Path, name: str) -> None:
    """Insert an agent row into the DB synchronously."""

    async def _run() -> None:
        store = AgentStore(db_path=str(db_path))
        await store.connect()
        await store.upsert(AgentRow(name=name, backend="openai", model="gpt-4o-mini"))
        await store.close()

    asyncio.run(_run())


def _make_proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    """Return a mock CompletedProcess."""
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ---------------------------------------------------------------------------
# TestDiscordList
# ---------------------------------------------------------------------------


class TestDiscordList:
    """`factory agent discord list`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["discord", "list", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "list" in result.output.lower()

    def test_empty_db(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["discord", "list"])
        assert result.exit_code == 0, result.output
        assert "no discord bots" in result.output.lower()

    def test_with_bots(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="discord", bot_id="main", agent="lyra"))
        db_upsert(db_path, BotRow(platform="discord", bot_id="beta", agent="beta"))

        result = runner.invoke(agent_app, ["discord", "list"])
        assert result.exit_code == 0, result.output
        assert "main" in result.output
        assert "beta" in result.output

    def test_ignores_other_platforms(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="telegram", bot_id="tg1", agent="lyra"))

        result = runner.invoke(agent_app, ["discord", "list"])
        assert result.exit_code == 0, result.output
        assert "no discord bots" in result.output.lower()


# ---------------------------------------------------------------------------
# TestDiscordShow
# ---------------------------------------------------------------------------


class TestDiscordShow:
    """`factory agent discord show <bot_id>`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["discord", "show", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "bot_id" in result.output.lower()

    def test_missing_bot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["discord", "show", "ghost"])
        assert result.exit_code == 1, result.output
        assert "not found" in result.output.lower()

    def test_existing_bot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(
            db_path,
            BotRow(
                platform="discord",
                bot_id="main",
                agent="lyra",
                webhook_enabled=True,
            ),
        )

        result = runner.invoke(agent_app, ["discord", "show", "main"])
        assert result.exit_code == 0, result.output
        assert "main" in result.output
        assert "lyra" in result.output

    def test_invalid_bot_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["discord", "show", "../../evil"])
        assert result.exit_code == 2, result.output
        assert "invalid" in result.output.lower()


# ---------------------------------------------------------------------------
# TestDiscordAdd
# ---------------------------------------------------------------------------


class TestDiscordAdd:
    """`factory agent discord add <bot_id>`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["discord", "add", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "bot_id" in result.output.lower()

    def test_add_minimal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["discord", "add", "main"])
        assert result.exit_code == 0, result.output
        assert "added" in result.output.lower()

        db_path = tmp_path / "config.db"
        row = db_get(db_path, "discord", "main")
        assert row is not None
        assert row.agent == ""

    def test_add_with_options(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(
            agent_app,
            [
                "discord",
                "add",
                "main",
                "--agent",
                "lyra",
                "--webhook-enabled",
                "--auto-thread",
                "--thread-hot-hours",
                "12",
            ],
        )
        assert result.exit_code == 0, result.output

        db_path = tmp_path / "config.db"
        row = db_get(db_path, "discord", "main")
        assert row is not None
        assert row.agent == "lyra"
        assert row.webhook_enabled is True
        assert row.auto_thread is True
        assert row.thread_hot_hours == 12

    def test_add_invalid_bot_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["discord", "add", "../../evil"])
        assert result.exit_code == 2, result.output
        assert "invalid" in result.output.lower()

# ---------------------------------------------------------------------------
# TestDiscordEdit
# ---------------------------------------------------------------------------


class TestDiscordEdit:
    """`factory agent discord edit <bot_id>`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["discord", "edit", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "bot_id" in result.output.lower()

    def test_missing_bot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["discord", "edit", "ghost"])
        assert result.exit_code == 1, result.output
        assert "not found" in result.output.lower()

    def test_edit_no_changes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All blank prompts -> no changes."""
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="discord", bot_id="main", agent="lyra"))

        # Simulate pressing Enter for every prompt (blank = keep)
        call_count = 0

        def _blank_prompt(*_args: Any, **_kwargs: Any) -> str:
            nonlocal call_count
            call_count += 1
            return ""

        monkeypatch.setattr(
            "factory.agent_cmd.platforms._shared.typer.prompt", _blank_prompt
        )
        result = runner.invoke(agent_app, ["discord", "edit", "main"])
        assert result.exit_code == 0, result.output
        assert "no changes" in result.output.lower()

    def test_edit_changes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Provide new values for all prompts."""
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(
            db_path,
            BotRow(
                platform="discord",
                bot_id="main",
                agent="lyra",
                webhook_enabled=False,
                auto_thread=False,
                thread_hot_hours=24,
            ),
        )

        # Side-effect sequence: agent, webhook_enabled, auto_thread,
        # thread_hot_hours, public_bot
        responses = [
            "beta",  # agent
            "y",  # webhook_enabled
            "n",  # auto_thread
            "48",  # thread_hot_hours
            "@public",  # public_bot
        ]
        idx = 0

        def _seq_prompt(*_args: Any, **_kwargs: Any) -> str:
            nonlocal idx
            val = responses[idx]
            idx += 1
            return val

        monkeypatch.setattr(
            "factory.agent_cmd.platforms._shared.typer.prompt", _seq_prompt
        )
        result = runner.invoke(agent_app, ["discord", "edit", "main"])
        assert result.exit_code == 0, result.output
        assert "updated" in result.output.lower()

        row = db_get(db_path, "discord", "main")
        assert row is not None
        assert row.agent == "beta"
        assert row.webhook_enabled is True
        assert row.auto_thread is False
        assert row.thread_hot_hours == 48
        assert row.public_bot == "@public"

    def test_edit_invalid_thread_hot_hours(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Invalid int for thread_hot_hours prints error and skips field."""
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(
            db_path,
            BotRow(
                platform="discord",
                bot_id="main",
                agent="lyra",
                thread_hot_hours=24,
            ),
        )
        responses = ["", "", "", "abc", ""]
        idx = 0

        def _seq_prompt(*_args: Any, **_kwargs: Any) -> str:
            nonlocal idx
            val = responses[idx]
            idx += 1
            return val

        monkeypatch.setattr(
            "factory.agent_cmd.platforms._shared.typer.prompt", _seq_prompt
        )
        result = runner.invoke(agent_app, ["discord", "edit", "main"])
        assert result.exit_code == 0, result.output
        assert "invalid int" in result.output.lower()
        row = db_get(db_path, "discord", "main")
        assert row is not None
        assert row.thread_hot_hours == 24

# ---------------------------------------------------------------------------
# TestDiscordPatch
# ---------------------------------------------------------------------------


class TestDiscordPatch:
    """`factory agent discord patch <bot_id>`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["discord", "patch", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "bot_id" in result.output.lower()

    def test_missing_bot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["discord", "patch", "ghost", "--agent", "x"])
        assert result.exit_code == 1, result.output
        assert "not found" in result.output.lower()

    def test_patch_agent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="discord", bot_id="main", agent="lyra"))

        result = runner.invoke(
            agent_app, ["discord", "patch", "main", "--agent", "beta"]
        )
        assert result.exit_code == 0, result.output
        assert "patched" in result.output.lower()

        row = db_get(db_path, "discord", "main")
        assert row is not None
        assert row.agent == "beta"

    def test_patch_no_fields(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="discord", bot_id="main", agent="lyra"))

        result = runner.invoke(agent_app, ["discord", "patch", "main"])
        assert result.exit_code == 1, result.output
        assert "no fields" in result.output.lower()

    def test_patch_webhook_enabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(
            db_path,
            BotRow(
                platform="discord", bot_id="main", agent="lyra", webhook_enabled=False
            ),
        )
        result = runner.invoke(
            agent_app, ["discord", "patch", "main", "--webhook-enabled"]
        )
        assert result.exit_code == 0, result.output
        row = db_get(db_path, "discord", "main")
        assert row is not None
        assert row.webhook_enabled is True

    def test_patch_auto_thread(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(
            db_path,
            BotRow(platform="discord", bot_id="main", agent="lyra", auto_thread=False),
        )
        result = runner.invoke(agent_app, ["discord", "patch", "main", "--auto-thread"])
        assert result.exit_code == 0, result.output
        row = db_get(db_path, "discord", "main")
        assert row is not None
        assert row.auto_thread is True

    def test_patch_thread_hot_hours(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(
            db_path,
            BotRow(
                platform="discord", bot_id="main", agent="lyra", thread_hot_hours=24
            ),
        )
        result = runner.invoke(
            agent_app,
            ["discord", "patch", "main", "--thread-hot-hours", "6"],
        )
        assert result.exit_code == 0, result.output
        row = db_get(db_path, "discord", "main")
        assert row is not None
        assert row.thread_hot_hours == 6

# ---------------------------------------------------------------------------
# TestDiscordRemove
# ---------------------------------------------------------------------------


class TestDiscordRemove:
    """`factory agent discord remove <bot_id>`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["discord", "remove", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "bot_id" in result.output.lower()

    def test_missing_bot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["discord", "remove", "ghost", "--yes"])
        assert result.exit_code == 1, result.output
        assert "not found" in result.output.lower()

    def test_remove_with_yes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="discord", bot_id="main", agent="lyra"))

        result = runner.invoke(agent_app, ["discord", "remove", "main", "--yes"])
        assert result.exit_code == 0, result.output
        assert "deleted" in result.output.lower()

        row = db_get(db_path, "discord", "main")
        assert row is None

    def test_remove_invalid_bot_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["discord", "remove", "../../evil", "--yes"])
        assert result.exit_code == 2, result.output
        assert "invalid" in result.output.lower()

    def test_remove_confirm_yes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Confirm deletion without --yes flag (mock confirm=yes)."""
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="discord", bot_id="main", agent="lyra"))
        monkeypatch.setattr(
            "factory.agent_cmd.platforms._commands.typer.confirm", lambda *a, **k: None
        )
        result = runner.invoke(agent_app, ["discord", "remove", "main"])
        assert result.exit_code == 0, result.output
        assert "deleted" in result.output.lower()
        row = db_get(db_path, "discord", "main")
        assert row is None

    def test_remove_confirm_no(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Decline deletion without --yes flag (mock confirm=no)."""
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="discord", bot_id="main", agent="lyra"))

        def _no(*a, **k):
            raise typer.Abort()

        monkeypatch.setattr("factory.agent_cmd.platforms._commands.typer.confirm", _no)
        result = runner.invoke(agent_app, ["discord", "remove", "main"])
        assert result.exit_code == 1, result.output
        row = db_get(db_path, "discord", "main")
        assert row is not None


# ---------------------------------------------------------------------------
# TestDiscordAssign
# ---------------------------------------------------------------------------


class TestDiscordAssign:
    """`factory agent discord assign <bot_id> --agent <name>`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["discord", "assign", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "bot_id" in result.output.lower()

    def test_missing_bot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(
            agent_app, ["discord", "assign", "ghost", "--agent", "lyra"]
        )
        assert result.exit_code == 1, result.output
        assert "not found" in result.output.lower()

    def test_assign(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="discord", bot_id="main", agent=""))

        result = runner.invoke(
            agent_app, ["discord", "assign", "main", "--agent", "lyra"]
        )
        assert result.exit_code == 0, result.output
        assert "patched" in result.output.lower()

        row = db_get(db_path, "discord", "main")
        assert row is not None
        assert row.agent == "lyra"


# ---------------------------------------------------------------------------
# TestDiscordUnassign
# ---------------------------------------------------------------------------


class TestDiscordUnassign:
    """`factory agent discord unassign <bot_id>`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["discord", "unassign", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "bot_id" in result.output.lower()

    def test_missing_bot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["discord", "unassign", "ghost"])
        assert result.exit_code == 1, result.output
        assert "not found" in result.output.lower()

    def test_unassign(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="discord", bot_id="main", agent="lyra"))

        result = runner.invoke(agent_app, ["discord", "unassign", "main"])
        assert result.exit_code == 0, result.output
        assert "patched" in result.output.lower()

        row = db_get(db_path, "discord", "main")
        assert row is not None
        assert row.agent == ""


# ---------------------------------------------------------------------------
# TestDiscordValidate
# ---------------------------------------------------------------------------


class TestDiscordValidate:
    """`factory agent discord validate <bot_id>`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["discord", "validate", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "bot_id" in result.output.lower()

    def test_missing_bot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["discord", "validate", "ghost"])
        assert result.exit_code == 1, result.output
        assert "not found" in result.output.lower()

    def test_validate_ok(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        _seed_agent(db_path, "lyra")
        db_upsert(db_path, BotRow(platform="discord", bot_id="main", agent="lyra"))

        secret_name = "factory-bot-discord-main"
        mock_run = MagicMock(return_value=_make_proc(returncode=0, stdout=secret_name))
        monkeypatch.setattr(
            "factory.agent_cmd.platforms._commands.subprocess.run", mock_run
        )

        result = runner.invoke(agent_app, ["discord", "validate", "main"])
        assert result.exit_code == 0, result.output
        assert "ok" in result.output.lower()
        mock_run.assert_called_once()

    def test_validate_no_agent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bot with no agent assigned skips agent check and still passes."""
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="discord", bot_id="main", agent=""))

        secret_name = "factory-bot-discord-main"
        mock_run = MagicMock(return_value=_make_proc(returncode=0, stdout=secret_name))
        monkeypatch.setattr(
            "factory.agent_cmd.platforms._commands.subprocess.run", mock_run
        )

        result = runner.invoke(agent_app, ["discord", "validate", "main"])
        assert result.exit_code == 0, result.output
        assert "ok" in result.output.lower()

    def test_validate_no_secret(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        _seed_agent(db_path, "lyra")
        db_upsert(db_path, BotRow(platform="discord", bot_id="main", agent="lyra"))

        mock_run = MagicMock(
            return_value=_make_proc(returncode=0, stdout="other-secret")
        )
        monkeypatch.setattr(
            "factory.agent_cmd.platforms._commands.subprocess.run", mock_run
        )

        result = runner.invoke(agent_app, ["discord", "validate", "main"])
        assert result.exit_code == 1, result.output
        assert "secret" in result.output.lower()

    def test_validate_missing_agent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Agent referenced by bot does not exist in AgentStore."""
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        _seed_agent(db_path, "lyra")
        db_upsert(db_path, BotRow(platform="discord", bot_id="main", agent="other"))
        secret_name = "factory-bot-discord-main"
        mock_run = MagicMock(return_value=_make_proc(returncode=0, stdout=secret_name))
        monkeypatch.setattr(
            "factory.agent_cmd.platforms._commands.subprocess.run", mock_run
        )
        result = runner.invoke(agent_app, ["discord", "validate", "main"])
        assert result.exit_code == 1, result.output
        assert "agent" in result.output.lower()
        assert "other" in result.output.lower()

    def test_validate_podman_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """subprocess.run returns non-zero exit code."""
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        _seed_agent(db_path, "lyra")
        db_upsert(db_path, BotRow(platform="discord", bot_id="main", agent="lyra"))
        mock_run = MagicMock(return_value=_make_proc(returncode=1, stdout=""))
        monkeypatch.setattr(
            "factory.agent_cmd.platforms._commands.subprocess.run", mock_run
        )
        result = runner.invoke(agent_app, ["discord", "validate", "main"])
        assert result.exit_code == 1, result.output
        assert "secret" in result.output.lower()

    def test_validate_ok_args(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Validate that subprocess.run was called with correct arguments."""
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        _seed_agent(db_path, "lyra")
        db_upsert(db_path, BotRow(platform="discord", bot_id="main", agent="lyra"))
        secret_name = "factory-bot-discord-main"
        mock_run = MagicMock(return_value=_make_proc(returncode=0, stdout=secret_name))
        monkeypatch.setattr(
            "factory.agent_cmd.platforms._commands.subprocess.run", mock_run
        )
        result = runner.invoke(agent_app, ["discord", "validate", "main"])
        assert result.exit_code == 0, result.output
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0] == [
            "podman",
            "secret",
            "ls",
            "--filter",
            f"name={secret_name}",
            "--format",
            "{{.Name}}",
        ]
        assert call_args[1].get("capture_output") is True
        assert call_args[1].get("text") is True
        assert call_args[1].get("timeout") == 10

    def test_validate_invalid_bot_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["discord", "validate", "../../evil"])
        assert result.exit_code == 2, result.output
        assert "invalid" in result.output.lower()
