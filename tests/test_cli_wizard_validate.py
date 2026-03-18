"""Tests for `lyra-agent validate` wizard command."""

from __future__ import annotations

from pathlib import Path

from click.testing import Result
from typer.testing import CliRunner

from lyra.cli import agent_app as app  # type: ignore[import-not-found]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

runner = CliRunner()


def _invoke_validate(agents_dir: Path, name: str) -> Result:
    """Invoke `lyra-agent validate <name> --agents-dir <dir>`."""
    return runner.invoke(app, ["validate", name, "--agents-dir", str(agents_dir)])


class TestValidate:
    """Tests for `lyra-agent validate`."""

    def _write_valid_toml(self, agents_dir: Path, name: str = "validagent") -> None:
        (agents_dir / f"{name}.toml").write_text(
            f'[agent]\nmemory_namespace = "{name}"\n\n'
            '[model]\nbackend = "claude-cli"\nmodel = "claude-sonnet-4-5"\n\n'
            '[prompt]\nsystem = "You are a valid agent."\n'
        )

    def test_valid_agent_exits_zero(self, tmp_path: Path) -> None:
        """Valid agent TOML exits 0 with no warnings."""
        # Arrange
        self._write_valid_toml(tmp_path, "validagent")

        # Act
        result = _invoke_validate(tmp_path, "validagent")

        # Assert
        assert result.exit_code == 0, result.output
        # No error/warning keywords in output
        assert "error" not in result.output.lower()
        assert "invalid" not in result.output.lower()

    def test_sr_cli_mismatch_warns_exits_zero(self, tmp_path: Path) -> None:
        """sr+cli mismatch emits warning but exits 0 (non-fatal)."""
        # Arrange — claude-cli backend with smart_routing enabled (mismatch)
        name = "mismatch"
        (tmp_path / f"{name}.toml").write_text(
            f'[agent]\nmemory_namespace = "{name}"\n\n'
            "[agent.smart_routing]\nenabled = true\n\n"
            '[model]\nbackend = "claude-cli"\nmodel = "claude-sonnet-4-5"\n\n'
            '[prompt]\nsystem = "I have a mismatch."\n'
        )

        # Act
        result = _invoke_validate(tmp_path, name)

        # Assert — exits 0 (warning, not error)
        assert result.exit_code == 0, result.output
        # Assert — warning is present in output
        assert (
            "warn" in result.output.lower() or "smart_routing" in result.output.lower()
        )

    def test_nonexistent_exits_nonzero(self, tmp_path: Path) -> None:
        """Non-existent agent name exits non-zero with 'not found' message."""
        # Act
        result = _invoke_validate(tmp_path, "ghost")

        # Assert
        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "ghost" in result.output.lower()

    def test_invalid_toml_exits_nonzero(self, tmp_path: Path) -> None:
        """Invalid TOML syntax exits non-zero with error message."""
        # Arrange — write a file with broken TOML
        name = "badtoml"
        (tmp_path / f"{name}.toml").write_text("[agent\nthis is not valid toml ===\n")

        # Act
        result = _invoke_validate(tmp_path, name)

        # Assert
        assert result.exit_code != 0
        assert (
            "error" in result.output.lower()
            or "invalid" in result.output.lower()
            or "parse" in result.output.lower()
        )

    def test_sr_sdk_constraint_satisfied_exits_zero(self, tmp_path: Path) -> None:
        """anthropic-sdk + SR enabled exits 0 with constraint-satisfied message."""
        name = "sdkagent"
        (tmp_path / f"{name}.toml").write_text(
            f'[agent]\nmemory_namespace = "{name}"\n\n'
            "[agent.smart_routing]\nenabled = true\n\n"
            '[model]\nbackend = "anthropic-sdk"\nmodel = "claude-sonnet-4-6"\n\n'
            '[prompt]\nsystem = "I use anthropic-sdk with SR."\n'
        )
        result = _invoke_validate(tmp_path, name)
        assert result.exit_code == 0, result.output
        assert "smart_routing" in result.output.lower()
        assert "warn" not in result.output.lower()
