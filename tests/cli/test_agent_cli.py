"""RED-phase tests for lyra agent CLI commands (issue #268).

Commands tested: init, list, show, validate, delete, assign, unassign.
`init`, `show`, `delete`, `assign`, `unassign` do not yet exist in cli.py —
these tests are expected to fail (RED) until the implementation lands.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lyra.cli import agent_app  # type: ignore[import-not-found]
from lyra.core.agent_store import AgentRow, AgentStore

# ---------------------------------------------------------------------------
# Shared runner
# ---------------------------------------------------------------------------

runner = CliRunner()


# ---------------------------------------------------------------------------
# TestAgentInitCommand
# ---------------------------------------------------------------------------


class TestAgentInitCommand:
    """Tests for `lyra agent init`."""

    def test_init_help(self) -> None:
        """--help exits 0 and mentions --force flag."""
        # Arrange / Act
        result = runner.invoke(agent_app, ["init", "--help"])

        # Assert
        assert result.exit_code == 0, result.output
        assert "--force" in result.output

    def test_init_runs_with_empty_db(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """init with empty vault dir exits 0 (creates tables, 0 TOMLs to import)."""
        # Arrange — redirect vault to tmp_path so no real ~/.lyra is touched
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))

        # Act
        result = runner.invoke(agent_app, ["init"])

        # Assert — command must exit cleanly
        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# TestAgentListCommand
# ---------------------------------------------------------------------------


class TestAgentListCommand:
    """Tests for `lyra agent list`."""

    def test_list_help(self) -> None:
        """--help exits 0."""
        # Arrange / Act
        result = runner.invoke(agent_app, ["list", "--help"])

        # Assert
        assert result.exit_code == 0, result.output

    def test_list_with_empty_db(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """list with empty vault dir exits 0 (no agents, no crash)."""
        # Arrange
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))

        # Act
        result = runner.invoke(agent_app, ["list"])

        # Assert
        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# TestAgentShowCommand
# ---------------------------------------------------------------------------


class TestAgentShowCommand:
    """Tests for `lyra agent show <name>`."""

    def test_show_missing_agent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """show of a non-existent agent exits 1 with 'not found' message."""
        # Arrange
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))

        # Act
        result = runner.invoke(agent_app, ["show", "nonexistent"])

        # Assert
        assert result.exit_code == 1, result.output
        assert "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# TestAgentValidateCommand
# ---------------------------------------------------------------------------


class TestAgentValidateCommand:
    """Tests for `lyra agent validate <name>`."""

    def test_validate_missing_agent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """validate of a non-existent agent exits 1."""
        # Arrange
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))

        # Act
        result = runner.invoke(agent_app, ["validate", "nonexistent"])

        # Assert
        assert result.exit_code == 1, result.output


# ---------------------------------------------------------------------------
# TestAgentDeleteCommand
# ---------------------------------------------------------------------------


class TestAgentDeleteCommand:
    """Tests for `lyra agent delete <name>`."""

    def test_delete_missing_agent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """delete of a non-existent agent exits 1."""
        # Arrange
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))

        # Act
        result = runner.invoke(agent_app, ["delete", "nonexistent", "--yes"])

        # Assert
        assert result.exit_code == 1, result.output


# ---------------------------------------------------------------------------
# TestAgentAssignCommand
# ---------------------------------------------------------------------------


class TestAgentAssignCommand:
    """Tests for `lyra agent assign <name> --bot <id> --platform <p>`."""

    def test_assign_unknown_agent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """assign of an agent not in DB exits 1 with 'not found' message."""
        # Arrange
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))

        # Act
        result = runner.invoke(
            agent_app,
            ["assign", "ghost", "--bot", "b1", "--platform", "telegram"],
        )

        # Assert
        assert result.exit_code == 1, result.output
        assert "not found" in result.output.lower()

    def test_assign_invalid_platform(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """assign with an unrecognised platform exits 1."""
        # Arrange
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))

        # Act
        result = runner.invoke(
            agent_app,
            ["assign", "myagent", "--bot", "b1", "--platform", "invalid"],
        )

        # Assert — typer Choice validation or explicit check must reject it
        assert result.exit_code == 1 or result.exit_code == 2, result.output


# ---------------------------------------------------------------------------
# TestAgentUnassignCommand
# ---------------------------------------------------------------------------


class TestAgentUnassignCommand:
    """Tests for `lyra agent unassign --bot <id> --platform <p>`."""

    def test_unassign_noop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """unassign when no mapping exists is a safe no-op: exits 0."""
        # Arrange
        monkeypatch.setenv("LYRA_VAULT_DIR", str(tmp_path))

        # Act
        result = runner.invoke(
            agent_app,
            ["unassign", "--bot", "b1", "--platform", "telegram"],
        )

        # Assert — no-op must not raise an error
        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# Helper: seed an AgentRow into DB synchronously
# ---------------------------------------------------------------------------


def _seed_agent(
    db_path: Path,
    name: str = "testagent",
    backend: str = "claude-cli",
    model: str = "claude-sonnet-4-6",
    smart_routing_json: str | None = None,
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

        # Act — send 7 blank lines (one per editable field)
        blank_inputs = "\n".join([""] * 7) + "\n"
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

        # Act — fields: backend, model, max_turns, persona,
        # show_intermediate, cwd, memory_namespace (blank = keep current)
        # Provide new model on 2nd prompt; leave all others blank
        inputs = "\n".join(["", "claude-opus-4-6", "", "", "", "", ""]) + "\n"
        result = runner.invoke(agent_app, ["edit", "edit-update"], input=inputs)

        # Assert — command succeeded
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
# TestAgentDeleteCommand — happy path
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

        # Assert — command succeeded
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
# TestAgentAssignCommand — happy path
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
# TestAgentValidateCommand — DB path tests
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
