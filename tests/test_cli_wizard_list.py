"""Tests for `lyra-agent list` wizard command."""

from __future__ import annotations

from pathlib import Path

from click.testing import Result
from typer.testing import CliRunner

from lyra.cli import agent_app as app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

runner = CliRunner()


def _invoke_list(agents_dir: Path) -> Result:
    """Invoke `lyra-agent list --agents-dir <dir>`."""
    return runner.invoke(app, ["list", "--agents-dir", str(agents_dir)])


class TestList:
    """Tests for `lyra-agent list`."""

    def _write_agent_toml(
        self,
        agents_dir: Path,
        name: str,
        backend: str = "claude-cli",
        model: str = "claude-sonnet-4-5",
        smart_routing_enabled: bool = False,
    ) -> None:
        sr_block = ""
        if smart_routing_enabled:
            sr_block = "\n[agent.smart_routing]\nenabled = true\n"
        content = (
            f'[agent]\nname = "{name}"\nmemory_namespace = "{name}"\n'
            f"{sr_block}\n"
            f'[model]\nbackend = "{backend}"\nmodel = "{model}"\n'
            f'\n[prompt]\nsystem = "You are {name}."\n'
        )
        (agents_dir / f"{name}.toml").write_text(content)

    def test_lists_agents(self, tmp_path: Path) -> None:
        """Lists agent name, backend, model, smart_routing status."""
        # Arrange
        self._write_agent_toml(tmp_path, "alpha", backend="claude-cli")
        self._write_agent_toml(tmp_path, "beta", backend="ollama", model="llama3")

        # Act
        result = _invoke_list(tmp_path)

        # Assert
        assert result.exit_code == 0, result.output
        assert "alpha" in result.output
        assert "beta" in result.output
        assert "claude-cli" in result.output
        assert "ollama" in result.output

    def test_empty_dir_exits_zero(self, tmp_path: Path) -> None:
        """Empty agents dir exits 0 (prints headers only or empty message)."""
        # Arrange — tmp_path exists but has no TOML files

        # Act
        result = _invoke_list(tmp_path)

        # Assert
        assert result.exit_code == 0, result.output

    def test_lists_dir_with_agents_dir_flag(self, tmp_path: Path) -> None:
        """--agents-dir lists agents from the given TOML directory."""
        self._write_agent_toml(tmp_path, "alpha")
        self._write_agent_toml(tmp_path, "beta")

        result = runner.invoke(app, ["list", "--agents-dir", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "alpha" in result.output
        assert "beta" in result.output

    def test_absent_dir_exits_zero(self, tmp_path: Path) -> None:
        """Non-existent agents dir exits 0 (prints headers only or empty message)."""
        # Arrange — point to a dir that doesn't exist
        absent = tmp_path / "no_such_dir"
        assert not absent.exists()

        # Act
        result = _invoke_list(absent)

        # Assert
        assert result.exit_code == 0, result.output
