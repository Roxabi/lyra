"""Integration tests for `lyra agent discord` bot CLI commands (issue #1415).

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
from typer.testing import CliRunner

from lyra.cli import agent_app
from lyra.core.agent.agent_models import AgentRow
from lyra.core.agent.bot_models import BotRow
from lyra.infrastructure.stores.agent_store import AgentStore
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
    """`lyra agent discord list`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["discord", "list", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "list" in result.output.lower()

    def test_empty_db(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["discord", "list"])
        assert result.exit_code == 0, result.output
        assert "no discord bots" in result.output.lower()

    def test_with_bots(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
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
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="telegram", bot_id="tg1", agent="lyra"))

        result = runner.invoke(agent_app, ["discord", "list"])
        assert result.exit_code == 0, result.output
        assert "no discord bots" in result.output.lower()


# ---------------------------------------------------------------------------
# TestDiscordShow
# ---------------------------------------------------------------------------


class TestDiscordShow:
    """`lyra agent discord show <bot_id>`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["discord", "show", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "bot_id" in result.output.lower()

    def test_missing_bot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["discord", "show", "ghost"])
        assert result.exit_code == 1, result.output
        assert "not found" in result.output.lower()

    def test_existing_bot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(
            db_path,
            BotRow(
                platform="discord",
                bot_id="main",
                agent="lyra",
                default_trust="trusted",
                owner_users=["alice"],
            ),
        )

        result = runner.invoke(agent_app, ["discord", "show", "main"])
        assert result.exit_code == 0, result.output
        assert "main" in result.output
        assert "lyra" in result.output
        assert "alice" in result.output

    def test_invalid_bot_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["discord", "show", "../../evil"])
        assert result.exit_code == 2, result.output
        assert "invalid" in result.output.lower()


# ---------------------------------------------------------------------------
# TestDiscordAdd
# ---------------------------------------------------------------------------


class TestDiscordAdd:
    """`lyra agent discord add <bot_id>`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["discord", "add", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "bot_id" in result.output.lower()

    def test_add_minimal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["discord", "add", "main"])
        assert result.exit_code == 0, result.output
        assert "added" in result.output.lower()

        db_path = tmp_path / "config.db"
        row = db_get(db_path, "discord", "main")
        assert row is not None
        assert row.agent == ""
        assert row.default_trust == "blocked"

    def test_add_with_options(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        result = runner.invoke(
            agent_app,
            [
                "discord",
                "add",
                "main",
                "--agent",
                "lyra",
                "--default-trust",
                "trusted",
                "--owner-users",
                "alice,bob",
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
        assert row.default_trust == "trusted"
        assert row.owner_users == ["alice", "bob"]
        assert row.auto_thread is True
        assert row.thread_hot_hours == 12

    def test_add_invalid_bot_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["discord", "add", "../../evil"])
        assert result.exit_code == 2, result.output
        assert "invalid" in result.output.lower()


# ---------------------------------------------------------------------------
# TestDiscordEdit
# ---------------------------------------------------------------------------


class TestDiscordEdit:
    """`lyra agent discord edit <bot_id>`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["discord", "edit", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "bot_id" in result.output.lower()

    def test_missing_bot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["discord", "edit", "ghost"])
        assert result.exit_code == 1, result.output
        assert "not found" in result.output.lower()

    def test_edit_no_changes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All blank prompts -> no changes."""
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="discord", bot_id="main", agent="lyra"))

        # Simulate pressing Enter for every prompt (blank = keep)
        call_count = 0

        def _blank_prompt(*_args: Any, **_kwargs: Any) -> str:
            nonlocal call_count
            call_count += 1
            return ""

        monkeypatch.setattr(
            "lyra.agent_cmd.platforms._shared.typer.prompt", _blank_prompt
        )
        result = runner.invoke(agent_app, ["discord", "edit", "main"])
        assert result.exit_code == 0, result.output
        assert "no changes" in result.output.lower()

    def test_edit_changes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Provide new values for all prompts."""
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(
            db_path,
            BotRow(
                platform="discord",
                bot_id="main",
                agent="lyra",
                webhook_enabled=False,
                default_trust="blocked",
                owner_users=["alice"],
                trusted_users=["bob"],
                trusted_roles=["admin"],
                auto_thread=False,
                thread_hot_hours=24,
            ),
        )

        # Side-effect sequence: agent, webhook, default_trust, owner_users,
        # trusted_users, trusted_roles, auto_thread, thread_hot_hours
        responses = [
            "beta",  # agent
            "y",  # webhook_enabled
            "trusted",  # default_trust
            "alice,charlie",  # owner_users
            "dave",  # trusted_users
            "mod",  # trusted_roles
            "n",  # auto_thread
            "48",  # thread_hot_hours
        ]
        idx = 0

        def _seq_prompt(*_args: Any, **_kwargs: Any) -> str:
            nonlocal idx
            val = responses[idx]
            idx += 1
            return val

        monkeypatch.setattr(
            "lyra.agent_cmd.platforms._shared.typer.prompt", _seq_prompt
        )
        result = runner.invoke(agent_app, ["discord", "edit", "main"])
        assert result.exit_code == 0, result.output
        assert "updated" in result.output.lower()

        row = db_get(db_path, "discord", "main")
        assert row is not None
        assert row.agent == "beta"
        assert row.webhook_enabled is True
        assert row.default_trust == "trusted"
        assert row.owner_users == ["alice", "charlie"]
        assert row.trusted_users == ["dave"]
        assert row.trusted_roles == ["mod"]
        assert row.auto_thread is False
        assert row.thread_hot_hours == 48


# ---------------------------------------------------------------------------
# TestDiscordPatch
# ---------------------------------------------------------------------------


class TestDiscordPatch:
    """`lyra agent discord patch <bot_id>`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["discord", "patch", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "bot_id" in result.output.lower()

    def test_missing_bot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["discord", "patch", "ghost", "--agent", "x"])
        assert result.exit_code == 1, result.output
        assert "not found" in result.output.lower()

    def test_patch_agent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
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
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="discord", bot_id="main", agent="lyra"))

        result = runner.invoke(agent_app, ["discord", "patch", "main"])
        assert result.exit_code == 1, result.output
        assert "no fields" in result.output.lower()

    def test_patch_owner_users(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="discord", bot_id="main", agent="lyra"))

        result = runner.invoke(
            agent_app,
            ["discord", "patch", "main", "--owner-users", "alice,bob"],
        )
        assert result.exit_code == 0, result.output

        row = db_get(db_path, "discord", "main")
        assert row is not None
        assert row.owner_users == ["alice", "bob"]


# ---------------------------------------------------------------------------
# TestDiscordRemove
# ---------------------------------------------------------------------------


class TestDiscordRemove:
    """`lyra agent discord remove <bot_id>`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["discord", "remove", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "bot_id" in result.output.lower()

    def test_missing_bot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["discord", "remove", "ghost", "--yes"])
        assert result.exit_code == 1, result.output
        assert "not found" in result.output.lower()

    def test_remove_with_yes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
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
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["discord", "remove", "../../evil", "--yes"])
        assert result.exit_code == 2, result.output
        assert "invalid" in result.output.lower()


# ---------------------------------------------------------------------------
# TestDiscordAssign
# ---------------------------------------------------------------------------


class TestDiscordAssign:
    """`lyra agent discord assign <bot_id> --agent <name>`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["discord", "assign", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "bot_id" in result.output.lower()

    def test_missing_bot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        result = runner.invoke(
            agent_app, ["discord", "assign", "ghost", "--agent", "lyra"]
        )
        assert result.exit_code == 1, result.output
        assert "not found" in result.output.lower()

    def test_assign(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
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
    """`lyra agent discord unassign <bot_id>`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["discord", "unassign", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "bot_id" in result.output.lower()

    def test_missing_bot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["discord", "unassign", "ghost"])
        assert result.exit_code == 1, result.output
        assert "not found" in result.output.lower()

    def test_unassign(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
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
    """`lyra agent discord validate <bot_id>`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["discord", "validate", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "bot_id" in result.output.lower()

    def test_missing_bot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["discord", "validate", "ghost"])
        assert result.exit_code == 1, result.output
        assert "not found" in result.output.lower()

    def test_validate_ok(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        _seed_agent(db_path, "lyra")
        db_upsert(
            db_path,
            BotRow(
                platform="discord",
                bot_id="main",
                agent="lyra",
                owner_users=["alice"],
            ),
        )

        secret_name = "lyra-bot-discord-main"
        mock_run = MagicMock(return_value=_make_proc(returncode=0, stdout=secret_name))
        monkeypatch.setattr(
            "lyra.agent_cmd.platforms._commands.subprocess.run", mock_run
        )

        result = runner.invoke(agent_app, ["discord", "validate", "main"])
        assert result.exit_code == 0, result.output
        assert "ok" in result.output.lower()
        mock_run.assert_called_once()

    def test_validate_no_agent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bot with no agent assigned skips agent check and still passes."""
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(
            db_path,
            BotRow(
                platform="discord",
                bot_id="main",
                agent="",
                owner_users=["alice"],
            ),
        )

        secret_name = "lyra-bot-discord-main"
        mock_run = MagicMock(return_value=_make_proc(returncode=0, stdout=secret_name))
        monkeypatch.setattr(
            "lyra.agent_cmd.platforms._commands.subprocess.run", mock_run
        )

        result = runner.invoke(agent_app, ["discord", "validate", "main"])
        assert result.exit_code == 0, result.output
        assert "ok" in result.output.lower()

    def test_validate_no_owners(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        _seed_agent(db_path, "lyra")
        db_upsert(
            db_path,
            BotRow(
                platform="discord",
                bot_id="main",
                agent="lyra",
                owner_users=[],
            ),
        )

        secret_name = "lyra-bot-discord-main"
        mock_run = MagicMock(return_value=_make_proc(returncode=0, stdout=secret_name))
        monkeypatch.setattr(
            "lyra.agent_cmd.platforms._commands.subprocess.run", mock_run
        )

        result = runner.invoke(agent_app, ["discord", "validate", "main"])
        assert result.exit_code == 1, result.output
        assert "owner_users" in result.output.lower()

    def test_validate_no_secret(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        _seed_agent(db_path, "lyra")
        db_upsert(
            db_path,
            BotRow(
                platform="discord",
                bot_id="main",
                agent="lyra",
                owner_users=["alice"],
            ),
        )

        mock_run = MagicMock(
            return_value=_make_proc(returncode=0, stdout="other-secret")
        )
        monkeypatch.setattr(
            "lyra.agent_cmd.platforms._commands.subprocess.run", mock_run
        )

        result = runner.invoke(agent_app, ["discord", "validate", "main"])
        assert result.exit_code == 1, result.output
        assert "secret" in result.output.lower()

    def test_validate_invalid_bot_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["discord", "validate", "../../evil"])
        assert result.exit_code == 2, result.output
        assert "invalid" in result.output.lower()
