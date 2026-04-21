"""Tests for `lyra-agent create` wizard."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from click.testing import Result
from typer.testing import CliRunner

from lyra.cli import agent_app as app
from lyra.core.agent.agent_seeder import _parse_toml

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

runner = CliRunner()


def _invoke_create(agents_dir: Path, input_str: str) -> Result:
    """Invoke `lyra-agent create --agents-dir <dir>` with the given stdin input.

    Passing --agents-dir bypasses the interactive location prompt.
    """
    return runner.invoke(
        app, ["create", "--agents-dir", str(agents_dir)], input=input_str
    )


# Default answers for the happy-path wizard prompts.
# Order matches the expected prompt sequence:
#   1. Agent name
#   2. Backend (claude-cli / ollama / anthropic-sdk)
#   3. Model
#   4. Working directory (blank → skip)
#   5. Max turns (blank → default)
#   6. Tools (blank → none)
#   7. Persona name (blank → none)
#   8. Show intermediate steps? (N)
#   9. Enable smart routing? (N)
#  10. Plugins (blank → none)
_HAPPY_PATH_INPUT = "\n".join(
    [
        "myagent",  # name
        "claude-cli",  # backend
        "claude-sonnet-4-5",  # model
        "",  # cwd — blank (skip)
        "",  # max_turns — blank (default)
        "",  # tools — blank (none)
        "",  # persona — blank (none)
        "N",  # show_intermediate
        "N",  # smart_routing enabled
        "",  # plugins — blank (none)
        "",  # trailing newline guard
    ]
)


class TestCreate:
    """Tests for `lyra-agent create`."""

    def test_happy_path_writes_toml(self, tmp_path: Path) -> None:
        """Happy path: wizard writes a valid TOML that _parse_toml can parse."""
        # Arrange
        name = "myagent"

        # Act
        result = _invoke_create(tmp_path, _HAPPY_PATH_INPUT)

        # Assert -- exit code must be 0
        assert result.exit_code == 0, result.output

        # Assert -- TOML file was written
        toml_path = tmp_path / f"{name}.toml"
        assert toml_path.exists(), f"Expected {toml_path} to be created"

        # Assert -- _parse_toml can parse the written file without error
        row = _parse_toml(toml_path)
        assert row is not None
        assert row.name == name

    def test_invalid_name_rejected(self, tmp_path: Path) -> None:
        """Names with invalid chars are rejected with exit code 1."""
        # Arrange — name contains a space (invalid)
        bad_input = "\n".join(["bad name!", "", ""])

        # Act
        result = _invoke_create(tmp_path, bad_input)

        # Assert
        assert result.exit_code != 0
        assert "invalid" in result.output.lower() or "error" in result.output.lower()

        # Assert — no TOML file was created
        assert not any(tmp_path.glob("*.toml"))

    def test_duplicate_name_exits_nonzero(self, tmp_path: Path) -> None:
        """Existing agent TOML causes early exit with non-zero code."""
        # Arrange — create the TOML file beforehand
        name = "existing"
        (tmp_path / f"{name}.toml").write_text(
            "[prompt]\nsystem = 'pre-existing agent'\n"
        )

        dup_input = "\n".join([name, "", ""])

        # Act
        result = _invoke_create(tmp_path, dup_input)

        # Assert
        assert result.exit_code != 0
        assert (
            "already exists" in result.output.lower()
            or "exists" in result.output.lower()
        )

    def test_creates_agents_dir_if_missing(self, tmp_path: Path) -> None:
        """Wizard creates agents dir if it doesn't exist."""
        # Arrange — point to a subdirectory that does not exist yet
        missing_dir = tmp_path / "agents" / "subdir"
        assert not missing_dir.exists()

        input_str = "\n".join(
            [
                "newagent",
                "claude-cli",
                "claude-sonnet-4-5",
                "",  # cwd
                "",  # max_turns
                "",  # tools
                "",  # persona
                "N",  # show_intermediate
                "N",  # smart_routing
                "",  # plugins
                "",
            ]
        )

        # Act
        result = runner.invoke(
            app, ["create", "--agents-dir", str(missing_dir)], input=input_str
        )

        # Assert
        assert result.exit_code == 0, result.output
        assert missing_dir.exists()
        assert (missing_dir / "newagent.toml").exists()

    def test_invalid_max_turns_rejected(self, tmp_path: Path) -> None:
        """Non-numeric max_turns should be rejected gracefully (no traceback)."""
        bad_input = "\n".join(
            [
                "validagent",  # name
                "claude-cli",  # backend
                "claude-sonnet-4-5",  # model
                "",  # cwd
                "abc",  # max_turns — invalid
                "",  # tools
                "",  # persona
                "N",  # show_intermediate
                "N",  # smart_routing
                "",  # plugins
                "",
            ]
        )
        result = _invoke_create(tmp_path, bad_input)
        # Should not produce a Python traceback
        assert "traceback" not in result.output.lower()
        assert "valueerror" not in result.output.lower()

    def test_sr_forced_off_on_claude_cli(self, tmp_path: Path) -> None:
        """smart_routing forced off when backend=claude-cli + SR enabled by user."""
        # Arrange — user answers Y to smart routing but backend is claude-cli
        sr_input = "\n".join(
            [
                "sragent",  # name
                "claude-cli",  # backend (SR not supported)
                "claude-sonnet-4-5",  # model
                "",  # cwd
                "",  # max_turns
                "",  # tools
                "",  # persona
                "N",  # show_intermediate
                "Y",  # smart_routing — user says YES
                "",  # plugins
                "",
            ]
        )

        # Act
        result = _invoke_create(tmp_path, sr_input)

        # Assert — wizard exits 0 (it warns and overrides, not aborts)
        assert result.exit_code == 0, result.output

        # Assert — written TOML has enabled = false under [agent.smart_routing]
        toml_path = tmp_path / "sragent.toml"
        assert toml_path.exists()
        data = tomllib.loads(toml_path.read_text())
        sr_section = data.get("agent", {}).get("smart_routing", {})
        assert sr_section.get("enabled") is False, (
            f"Expected smart_routing.enabled=false for claude-cli, got: {sr_section}"
        )

        # Assert — warning was printed to output
        assert (
            "warn" in result.output.lower()
            or "disabling" in result.output.lower()
            or "smart_routing" in result.output.lower()
        )

    def test_location_prompt_user(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Choosing 'u' at the location prompt writes to the user agents dir."""
        import lyra.cli_agent_create as create_mod

        user_dir = tmp_path / "user_agents"
        monkeypatch.setattr(create_mod, "_user_agents_dir", lambda: user_dir)
        sys_dir = tmp_path / "system_agents"
        monkeypatch.setattr(create_mod, "_SYSTEM_AGENTS_DIR", sys_dir)

        # No --agents-dir → location prompt appears; answer 'u'
        input_str = "\n".join(
            [
                "myagent",  # name
                "u",  # location: user
                "claude-cli",  # backend
                "claude-sonnet-4-5",
                "",  # cwd
                "",  # max_turns
                "",  # tools
                "",  # persona
                "N",  # show_intermediate
                "N",  # smart_routing
                "",  # plugins
                "",
            ]
        )
        result = runner.invoke(app, ["create"], input=input_str)
        assert result.exit_code == 0, result.output
        assert (user_dir / "myagent.toml").exists()

    def test_location_prompt_system(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Choosing 's' at the location prompt writes to the system agents dir."""
        import lyra.cli_agent_create as create_mod

        system_dir = tmp_path / "system_agents"
        _user_dir = tmp_path / "user_agents"
        monkeypatch.setattr(create_mod, "_user_agents_dir", lambda: _user_dir)
        monkeypatch.setattr(create_mod, "_SYSTEM_AGENTS_DIR", system_dir)

        input_str = "\n".join(
            [
                "sysagent",
                "s",  # location: system
                "claude-cli",
                "claude-sonnet-4-5",
                "",
                "",
                "",
                "",
                "N",
                "N",
                "",
                "",
            ]
        )
        result = runner.invoke(app, ["create"], input=input_str)
        assert result.exit_code == 0, result.output
        assert (system_dir / "sysagent.toml").exists()
