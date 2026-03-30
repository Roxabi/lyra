"""Tests for lyra agent CLI commands: init, list, show, validate, delete, assign,
unassign.

`init`, `show`, `delete`, `assign`, `unassign` do not yet exist in cli.py —
these tests are expected to fail (RED) until the implementation lands.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lyra.cli import agent_app

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

        # Assert — strip ANSI codes before checking (rich/typer may wrap each char)
        plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert result.exit_code == 0, result.output
        assert "--force" in plain

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
