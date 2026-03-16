"""Tests for lyra-agent CLI wizard (create / list / validate)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

# These imports will fail until cli.py exists — that's expected for RED phase
from click.testing import Result
from typer.testing import CliRunner

from lyra.cli import agent_app as app  # type: ignore[import-not-found]
from lyra.core.agent import load_agent_config

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


def _invoke_list(agents_dir: Path) -> Result:
    """Invoke `lyra-agent list --agents-dir <dir>`."""
    return runner.invoke(app, ["list", "--agents-dir", str(agents_dir)])


def _invoke_validate(agents_dir: Path, name: str) -> Result:
    """Invoke `lyra-agent validate <name> --agents-dir <dir>`."""
    return runner.invoke(app, ["validate", name, "--agents-dir", str(agents_dir)])


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
        """Happy path: wizard writes a valid TOML that load_agent_config can load."""
        # Arrange
        name = "myagent"

        # Act
        result = _invoke_create(tmp_path, _HAPPY_PATH_INPUT)

        # Assert — exit code must be 0
        assert result.exit_code == 0, result.output

        # Assert — TOML file was written
        toml_path = tmp_path / f"{name}.toml"
        assert toml_path.exists(), f"Expected {toml_path} to be created"

        # Assert — load_agent_config can parse the written file without error
        agent = load_agent_config(name, agents_dir=tmp_path)
        assert agent.name == name

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
        import lyra.cli_agent as cli_mod

        user_dir = tmp_path / "user_agents"
        monkeypatch.setattr(cli_mod, "_USER_AGENTS_DIR", user_dir)
        monkeypatch.setattr(cli_mod, "_SYSTEM_AGENTS_DIR", tmp_path / "system_agents")

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
        import lyra.cli_agent as cli_mod

        system_dir = tmp_path / "system_agents"
        monkeypatch.setattr(cli_mod, "_USER_AGENTS_DIR", tmp_path / "user_agents")
        monkeypatch.setattr(cli_mod, "_SYSTEM_AGENTS_DIR", system_dir)

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
