"""Integration tests for `lyra agent telegram` bot CLI commands (issue #1415)."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import typer
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
# TestTelegramList
# ---------------------------------------------------------------------------


class TestTelegramList:
    """`lyra agent telegram list`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["telegram", "list", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "list" in result.output.lower()

    def test_empty_db(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["telegram", "list"])
        assert result.exit_code == 0, result.output
        assert "no telegram bots" in result.output.lower()

    def test_with_bots(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="telegram", bot_id="main", agent="lyra"))
        db_upsert(db_path, BotRow(platform="telegram", bot_id="beta", agent="beta"))

        result = runner.invoke(agent_app, ["telegram", "list"])
        assert result.exit_code == 0, result.output
        assert "main" in result.output
        assert "beta" in result.output

    def test_ignores_other_platforms(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="discord", bot_id="dc1", agent="lyra"))

        result = runner.invoke(agent_app, ["telegram", "list"])
        assert result.exit_code == 0, result.output
        assert "no telegram bots" in result.output.lower()


# ---------------------------------------------------------------------------
# TestTelegramShow
# ---------------------------------------------------------------------------


class TestTelegramShow:
    """`lyra agent telegram show <bot_id>`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["telegram", "show", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "bot_id" in result.output.lower()

    def test_missing_bot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["telegram", "show", "ghost"])
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
                platform="telegram",
                bot_id="main",
                agent="lyra",
                default_trust="trusted",
                owner_users=["alice"],
            ),
        )

        result = runner.invoke(agent_app, ["telegram", "show", "main"])
        assert result.exit_code == 0, result.output
        assert "main" in result.output
        assert "lyra" in result.output
        assert "alice" in result.output

    def test_invalid_bot_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["telegram", "show", "../../evil"])
        assert result.exit_code == 2, result.output
        assert "invalid" in result.output.lower()


# ---------------------------------------------------------------------------
# TestTelegramAdd
# ---------------------------------------------------------------------------


class TestTelegramAdd:
    """`lyra agent telegram add <bot_id>`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["telegram", "add", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "bot_id" in result.output.lower()

    def test_add_minimal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["telegram", "add", "main"])
        assert result.exit_code == 0, result.output
        assert "added" in result.output.lower()

        db_path = tmp_path / "config.db"
        row = db_get(db_path, "telegram", "main")
        assert row is not None
        assert row.agent == ""
        assert row.default_trust == "blocked"

    def test_add_with_options(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(
            agent_app,
            [
                "telegram",
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
        row = db_get(db_path, "telegram", "main")
        assert row is not None
        assert row.agent == "lyra"
        assert row.default_trust == "trusted"
        assert row.owner_users == ["alice", "bob"]
        assert row.auto_thread is True
        assert row.thread_hot_hours == 12

    def test_add_invalid_bot_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["telegram", "add", "../../evil"])
        assert result.exit_code == 2, result.output
        assert "invalid" in result.output.lower()

    def test_add_invalid_default_trust(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(
            agent_app,
            ["telegram", "add", "main", "--default-trust", "evil"],
        )
        assert result.exit_code == 2, result.output
        assert "invalid" in result.output.lower()


# ---------------------------------------------------------------------------
# TestTelegramEdit
# ---------------------------------------------------------------------------


class TestTelegramEdit:
    """`lyra agent telegram edit <bot_id>`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["telegram", "edit", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "bot_id" in result.output.lower()

    def test_missing_bot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["telegram", "edit", "ghost"])
        assert result.exit_code == 1, result.output
        assert "not found" in result.output.lower()

    def test_edit_no_changes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All blank prompts → no changes."""
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="telegram", bot_id="main", agent="lyra"))

        # Simulate pressing Enter for every prompt (blank = keep)
        call_count = 0

        def _blank_prompt(*_args: Any, **_kwargs: Any) -> str:
            nonlocal call_count
            call_count += 1
            return ""

        monkeypatch.setattr(
            "lyra.agent_cmd.platforms._shared.typer.prompt", _blank_prompt
        )
        result = runner.invoke(agent_app, ["telegram", "edit", "main"])
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
                platform="telegram",
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
        result = runner.invoke(agent_app, ["telegram", "edit", "main"])
        assert result.exit_code == 0, result.output
        assert "updated" in result.output.lower()

        row = db_get(db_path, "telegram", "main")
        assert row is not None
        assert row.agent == "beta"
        assert row.webhook_enabled is True
        assert row.default_trust == "trusted"
        assert row.owner_users == ["alice", "charlie"]
        assert row.trusted_users == ["dave"]
        assert row.trusted_roles == ["mod"]
        assert row.auto_thread is False
        assert row.thread_hot_hours == 48

    def test_edit_invalid_thread_hot_hours(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Invalid int for thread_hot_hours prints error and skips field."""
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(
            db_path,
            BotRow(
                platform="telegram",
                bot_id="main",
                agent="lyra",
                thread_hot_hours=24,
            ),
        )
        responses = ["", "", "", "", "", "", "", "abc"]
        idx = 0

        def _seq_prompt(*_args: Any, **_kwargs: Any) -> str:
            nonlocal idx
            val = responses[idx]
            idx += 1
            return val

        monkeypatch.setattr(
            "lyra.agent_cmd.platforms._shared.typer.prompt", _seq_prompt
        )
        result = runner.invoke(agent_app, ["telegram", "edit", "main"])
        assert result.exit_code == 0, result.output
        assert "invalid int" in result.output.lower()
        row = db_get(db_path, "telegram", "main")
        assert row is not None
        assert row.thread_hot_hours == 24

    def test_edit_clear_list(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """'-' input clears list fields."""
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(
            db_path,
            BotRow(
                platform="telegram",
                bot_id="main",
                agent="lyra",
                owner_users=["alice"],
                trusted_users=["bob"],
                trusted_roles=["admin"],
            ),
        )
        # agent, webhook, default_trust, owner_users, trusted_users,
        # trusted_roles, auto_thread, thread_hot_hours
        responses = ["", "", "", "-", "-", "-", "", ""]
        idx = 0

        def _seq_prompt(*_args: Any, **_kwargs: Any) -> str:
            nonlocal idx
            val = responses[idx]
            idx += 1
            return val

        monkeypatch.setattr(
            "lyra.agent_cmd.platforms._shared.typer.prompt", _seq_prompt
        )
        result = runner.invoke(agent_app, ["telegram", "edit", "main"])
        assert result.exit_code == 0, result.output
        assert "updated" in result.output.lower()
        row = db_get(db_path, "telegram", "main")
        assert row is not None
        assert row.owner_users == []
        assert row.trusted_users == []
        assert row.trusted_roles == []


# ---------------------------------------------------------------------------
# TestTelegramPatch
# ---------------------------------------------------------------------------


class TestTelegramPatch:
    """`lyra agent telegram patch <bot_id>`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["telegram", "patch", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "bot_id" in result.output.lower()

    def test_missing_bot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(
            agent_app, ["telegram", "patch", "ghost", "--agent", "x"]
        )
        assert result.exit_code == 1, result.output
        assert "not found" in result.output.lower()

    def test_patch_agent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="telegram", bot_id="main", agent="lyra"))

        result = runner.invoke(
            agent_app, ["telegram", "patch", "main", "--agent", "beta"]
        )
        assert result.exit_code == 0, result.output
        assert "patched" in result.output.lower()

        row = db_get(db_path, "telegram", "main")
        assert row is not None
        assert row.agent == "beta"

    def test_patch_no_fields(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="telegram", bot_id="main", agent="lyra"))

        result = runner.invoke(agent_app, ["telegram", "patch", "main"])
        assert result.exit_code == 1, result.output
        assert "no fields" in result.output.lower()

    def test_patch_owner_users(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="telegram", bot_id="main", agent="lyra"))

        result = runner.invoke(
            agent_app,
            ["telegram", "patch", "main", "--owner-users", "alice,bob"],
        )
        assert result.exit_code == 0, result.output

        row = db_get(db_path, "telegram", "main")
        assert row is not None
        assert row.owner_users == ["alice", "bob"]

    def test_patch_webhook_enabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(
            db_path,
            BotRow(
                platform="telegram", bot_id="main", agent="lyra", webhook_enabled=False
            ),
        )
        result = runner.invoke(
            agent_app, ["telegram", "patch", "main", "--webhook-enabled"]
        )
        assert result.exit_code == 0, result.output
        row = db_get(db_path, "telegram", "main")
        assert row is not None
        assert row.webhook_enabled is True

    def test_patch_default_trust(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(
            db_path,
            BotRow(
                platform="telegram",
                bot_id="main",
                agent="lyra",
                default_trust="blocked",
            ),
        )
        result = runner.invoke(
            agent_app,
            ["telegram", "patch", "main", "--default-trust", "public"],
        )
        assert result.exit_code == 0, result.output
        row = db_get(db_path, "telegram", "main")
        assert row is not None
        assert row.default_trust == "public"

    def test_patch_auto_thread(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(
            db_path,
            BotRow(platform="telegram", bot_id="main", agent="lyra", auto_thread=False),
        )
        result = runner.invoke(
            agent_app, ["telegram", "patch", "main", "--auto-thread"]
        )
        assert result.exit_code == 0, result.output
        row = db_get(db_path, "telegram", "main")
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
                platform="telegram", bot_id="main", agent="lyra", thread_hot_hours=24
            ),
        )
        result = runner.invoke(
            agent_app,
            ["telegram", "patch", "main", "--thread-hot-hours", "6"],
        )
        assert result.exit_code == 0, result.output
        row = db_get(db_path, "telegram", "main")
        assert row is not None
        assert row.thread_hot_hours == 6

    def test_patch_default_trust_invalid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="telegram", bot_id="main", agent="lyra"))
        result = runner.invoke(
            agent_app,
            ["telegram", "patch", "main", "--default-trust", "evil"],
        )
        assert result.exit_code == 2, result.output
        assert "invalid" in result.output.lower()


# ---------------------------------------------------------------------------
# TestTelegramRemove
# ---------------------------------------------------------------------------


class TestTelegramRemove:
    """`lyra agent telegram remove <bot_id>`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["telegram", "remove", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "bot_id" in result.output.lower()

    def test_missing_bot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["telegram", "remove", "ghost", "--yes"])
        assert result.exit_code == 1, result.output
        assert "not found" in result.output.lower()

    def test_remove_with_yes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="telegram", bot_id="main", agent="lyra"))

        result = runner.invoke(agent_app, ["telegram", "remove", "main", "--yes"])
        assert result.exit_code == 0, result.output
        assert "deleted" in result.output.lower()

        row = db_get(db_path, "telegram", "main")
        assert row is None

    def test_remove_invalid_bot_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["telegram", "remove", "../../evil", "--yes"])
        assert result.exit_code == 2, result.output
        assert "invalid" in result.output.lower()

    def test_remove_confirm_yes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Confirm deletion without --yes flag (mock confirm=yes)."""
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="telegram", bot_id="main", agent="lyra"))
        monkeypatch.setattr(
            "lyra.agent_cmd.platforms._commands.typer.confirm", lambda *a, **k: None
        )
        result = runner.invoke(agent_app, ["telegram", "remove", "main"])
        assert result.exit_code == 0, result.output
        assert "deleted" in result.output.lower()
        row = db_get(db_path, "telegram", "main")
        assert row is None

    def test_remove_confirm_no(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Decline deletion without --yes flag (mock confirm=no)."""
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="telegram", bot_id="main", agent="lyra"))

        def _no(*a, **k):
            raise typer.Abort()

        monkeypatch.setattr("lyra.agent_cmd.platforms._commands.typer.confirm", _no)
        result = runner.invoke(agent_app, ["telegram", "remove", "main"])
        assert result.exit_code == 1, result.output
        row = db_get(db_path, "telegram", "main")
        assert row is not None


# ---------------------------------------------------------------------------
# TestTelegramAssign
# ---------------------------------------------------------------------------


class TestTelegramAssign:
    """`lyra agent telegram assign <bot_id> --agent <name>`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["telegram", "assign", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "bot_id" in result.output.lower()

    def test_missing_bot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(
            agent_app, ["telegram", "assign", "ghost", "--agent", "lyra"]
        )
        assert result.exit_code == 1, result.output
        assert "not found" in result.output.lower()

    def test_assign(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="telegram", bot_id="main", agent=""))

        result = runner.invoke(
            agent_app, ["telegram", "assign", "main", "--agent", "lyra"]
        )
        assert result.exit_code == 0, result.output
        assert "patched" in result.output.lower()

        row = db_get(db_path, "telegram", "main")
        assert row is not None
        assert row.agent == "lyra"


# ---------------------------------------------------------------------------
# TestTelegramUnassign
# ---------------------------------------------------------------------------


class TestTelegramUnassign:
    """`lyra agent telegram unassign <bot_id>`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["telegram", "unassign", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "bot_id" in result.output.lower()

    def test_missing_bot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["telegram", "unassign", "ghost"])
        assert result.exit_code == 1, result.output
        assert "not found" in result.output.lower()

    def test_unassign(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(db_path, BotRow(platform="telegram", bot_id="main", agent="lyra"))

        result = runner.invoke(agent_app, ["telegram", "unassign", "main"])
        assert result.exit_code == 0, result.output
        assert "patched" in result.output.lower()

        row = db_get(db_path, "telegram", "main")
        assert row is not None
        assert row.agent == ""


# ---------------------------------------------------------------------------
# TestTelegramValidate
# ---------------------------------------------------------------------------


class TestTelegramValidate:
    """`lyra agent telegram validate <bot_id>`"""

    def test_help(self) -> None:
        result = runner.invoke(
            agent_app, ["telegram", "validate", "--help"], env={"NO_COLOR": "1"}
        )
        assert result.exit_code == 0, result.output
        assert "bot_id" in result.output.lower()

    def test_missing_bot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        result = runner.invoke(agent_app, ["telegram", "validate", "ghost"])
        assert result.exit_code == 1, result.output
        assert "not found" in result.output.lower()

    def test_validate_ok(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        _seed_agent(db_path, "lyra")
        db_upsert(
            db_path,
            BotRow(
                platform="telegram",
                bot_id="main",
                agent="lyra",
                owner_users=["alice"],
            ),
        )

        secret_name = "factory-bot-telegram-main"
        mock_run = MagicMock(return_value=_make_proc(returncode=0, stdout=secret_name))
        monkeypatch.setattr(
            "lyra.agent_cmd.platforms._commands.subprocess.run", mock_run
        )

        result = runner.invoke(agent_app, ["telegram", "validate", "main"])
        assert result.exit_code == 0, result.output
        assert "ok" in result.output.lower()
        mock_run.assert_called_once()

    def test_validate_no_agent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bot with no agent assigned skips agent check and still passes."""
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        db_upsert(
            db_path,
            BotRow(
                platform="telegram",
                bot_id="main",
                agent="",
                owner_users=["alice"],
            ),
        )

        secret_name = "factory-bot-telegram-main"
        mock_run = MagicMock(return_value=_make_proc(returncode=0, stdout=secret_name))
        monkeypatch.setattr(
            "lyra.agent_cmd.platforms._commands.subprocess.run", mock_run
        )

        result = runner.invoke(agent_app, ["telegram", "validate", "main"])
        assert result.exit_code == 0, result.output
        assert "ok" in result.output.lower()

    def test_validate_no_owners(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        _seed_agent(db_path, "lyra")
        db_upsert(
            db_path,
            BotRow(
                platform="telegram",
                bot_id="main",
                agent="lyra",
                owner_users=[],
            ),
        )

        secret_name = "factory-bot-telegram-main"
        mock_run = MagicMock(return_value=_make_proc(returncode=0, stdout=secret_name))
        monkeypatch.setattr(
            "lyra.agent_cmd.platforms._commands.subprocess.run", mock_run
        )

        result = runner.invoke(agent_app, ["telegram", "validate", "main"])
        assert result.exit_code == 1, result.output
        assert "owner_users" in result.output.lower()

    def test_validate_no_secret(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        _seed_agent(db_path, "lyra")
        db_upsert(
            db_path,
            BotRow(
                platform="telegram",
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

        result = runner.invoke(agent_app, ["telegram", "validate", "main"])
        assert result.exit_code == 1, result.output
        assert "secret" in result.output.lower()

    def test_validate_missing_agent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Agent referenced by bot does not exist in AgentStore."""
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        _seed_agent(db_path, "lyra")
        db_upsert(
            db_path,
            BotRow(
                platform="telegram",
                bot_id="main",
                agent="other",
                owner_users=["alice"],
            ),
        )
        secret_name = "factory-bot-telegram-main"
        mock_run = MagicMock(return_value=_make_proc(returncode=0, stdout=secret_name))
        monkeypatch.setattr(
            "lyra.agent_cmd.platforms._commands.subprocess.run", mock_run
        )
        result = runner.invoke(agent_app, ["telegram", "validate", "main"])
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
        db_upsert(
            db_path,
            BotRow(
                platform="telegram",
                bot_id="main",
                agent="lyra",
                owner_users=["alice"],
            ),
        )
        mock_run = MagicMock(return_value=_make_proc(returncode=1, stdout=""))
        monkeypatch.setattr(
            "lyra.agent_cmd.platforms._commands.subprocess.run", mock_run
        )
        result = runner.invoke(agent_app, ["telegram", "validate", "main"])
        assert result.exit_code == 1, result.output
        assert "secret" in result.output.lower()

    def test_validate_ok_args(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Validate that subprocess.run was called with correct arguments."""
        monkeypatch.setenv("ROXABI_FACTORY_DIR", str(tmp_path))
        db_path = tmp_path / "config.db"
        _seed_agent(db_path, "lyra")
        db_upsert(
            db_path,
            BotRow(
                platform="telegram",
                bot_id="main",
                agent="lyra",
                owner_users=["alice"],
            ),
        )
        secret_name = "factory-bot-telegram-main"
        mock_run = MagicMock(return_value=_make_proc(returncode=0, stdout=secret_name))
        monkeypatch.setattr(
            "lyra.agent_cmd.platforms._commands.subprocess.run", mock_run
        )
        result = runner.invoke(agent_app, ["telegram", "validate", "main"])
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
        result = runner.invoke(agent_app, ["telegram", "validate", "../../evil"])
        assert result.exit_code == 2, result.output
        assert "invalid" in result.output.lower()
