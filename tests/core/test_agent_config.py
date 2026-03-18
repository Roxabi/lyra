"""Tests for ModelConfig and load_agent_config (non-persona, non-conversion)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lyra.core.agent_config import ModelConfig
from lyra.core.agent_loader import load_agent_config


class TestModelConfig:
    def test_defaults(self) -> None:
        cfg = ModelConfig()
        assert cfg.backend == "claude-cli"
        assert cfg.model == "claude-sonnet-4-5"
        assert cfg.max_turns == 10
        assert cfg.tools == ()

    def test_tools_field_is_tuple(self) -> None:
        cfg = ModelConfig(tools=("Read", "Grep"))
        assert isinstance(cfg.tools, tuple)
        assert cfg.tools == ("Read", "Grep")

    def test_frozen(self) -> None:
        cfg = ModelConfig()
        with pytest.raises(AttributeError):
            cfg.backend = "ollama"  # type: ignore[misc]

    def test_cwd_defaults_to_none(self) -> None:
        cfg = ModelConfig()
        assert cfg.cwd is None

    def test_cwd_accepts_path(self, tmp_path: Path) -> None:
        cfg = ModelConfig(cwd=tmp_path)
        assert cfg.cwd == tmp_path


class TestLoadAgentConfig:
    def test_valid_load(self, tmp_path: Path) -> None:
        toml_content = """
[agent]
memory_namespace = "myagent"
permissions = ["read", "write"]

[model]
backend = "claude-cli"
model = "claude-opus-4-5"
max_turns = 5
tools = ["Read", "Grep"]

[prompt]
system = "You are a helpful assistant."
"""
        (tmp_path / "myagent.toml").write_text(toml_content)
        agent = load_agent_config("myagent", agents_dir=tmp_path)

        assert agent.name == "myagent"
        assert agent.system_prompt == "You are a helpful assistant."
        assert agent.memory_namespace == "myagent"
        assert agent.model_config.backend == "claude-cli"
        assert agent.model_config.model == "claude-opus-4-5"
        assert agent.model_config.max_turns == 5
        assert agent.model_config.tools == ("Read", "Grep")
        assert agent.permissions == ("read", "write")

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="Agent config not found"):
            load_agent_config("nonexistent", agents_dir=tmp_path)

    def test_missing_model_section_uses_defaults(self, tmp_path: Path) -> None:
        toml_content = """
[agent]
memory_namespace = "myagent"

[prompt]
system = "Hello."
"""
        (tmp_path / "myagent.toml").write_text(toml_content)
        agent = load_agent_config("myagent", agents_dir=tmp_path)

        assert agent.model_config.backend == "claude-cli"
        assert agent.model_config.model == "claude-sonnet-4-5"
        assert agent.model_config.max_turns == 10
        assert agent.model_config.tools == ()

    def test_missing_agent_section_uses_name_as_namespace(self, tmp_path: Path) -> None:
        toml_content = """
[model]
backend = "claude-cli"

[prompt]
system = "Hello."
"""
        (tmp_path / "ghostagent.toml").write_text(toml_content)
        agent = load_agent_config("ghostagent", agents_dir=tmp_path)

        assert agent.memory_namespace == "ghostagent"
        assert agent.permissions == ()

    def test_tools_list_becomes_tuple(self, tmp_path: Path) -> None:
        toml_content = """
[model]
tools = ["Read", "Grep"]

[prompt]
system = ""
"""
        (tmp_path / "toolagent.toml").write_text(toml_content)
        agent = load_agent_config("toolagent", agents_dir=tmp_path)

        assert isinstance(agent.model_config.tools, tuple)
        assert agent.model_config.tools == ("Read", "Grep")

    def test_permissions_list_becomes_tuple(self, tmp_path: Path) -> None:
        toml_content = """
[agent]
permissions = ["admin", "read"]

[prompt]
system = ""
"""
        (tmp_path / "permtest.toml").write_text(toml_content)
        agent = load_agent_config("permtest", agents_dir=tmp_path)

        assert isinstance(agent.permissions, tuple)
        assert agent.permissions == ("admin", "read")

    def test_agent_is_mutable_for_hot_reload(self, tmp_path: Path) -> None:
        toml_content = """
[prompt]
system = ""
"""
        (tmp_path / "mutable.toml").write_text(toml_content)
        agent = load_agent_config("mutable", agents_dir=tmp_path)
        agent.name = "other"
        assert agent.name == "other"

    def test_commands_enabled_from_toml(self, tmp_path: Path) -> None:
        # Arrange
        toml_content = """
[prompt]
system = "test"

[plugins]
enabled = ["echo"]
"""
        (tmp_path / "testagent.toml").write_text(toml_content)

        # Act
        agent = load_agent_config("testagent", agents_dir=tmp_path)

        # Assert
        assert agent.commands_enabled == ("echo",)

    def test_commands_enabled_defaults_to_empty(self, tmp_path: Path) -> None:
        # Arrange — absent [plugins] section → empty list (default-open)
        toml_content = """
[prompt]
system = "test"
"""
        (tmp_path / "testagent.toml").write_text(toml_content)

        # Act
        agent = load_agent_config("testagent", agents_dir=tmp_path)

        # Assert
        assert agent.commands_enabled == ()

    def test_cwd_absent_defaults_to_none(self, tmp_path: Path) -> None:
        toml_content = """
[prompt]
system = "test"
"""
        (tmp_path / "noagent.toml").write_text(toml_content)
        agent = load_agent_config("noagent", agents_dir=tmp_path)
        assert agent.model_config.cwd is None

    def test_cwd_valid_directory_is_resolved(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        toml_content = f"""
[model]
cwd = "{project_dir}"

[prompt]
system = "test"
"""
        (tmp_path / "cwdagent.toml").write_text(toml_content)
        agent = load_agent_config("cwdagent", agents_dir=tmp_path)
        assert agent.model_config.cwd == project_dir.resolve()

    def test_cwd_tilde_expands(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        subdir = tmp_path / "expanded"
        subdir.mkdir()
        monkeypatch.setenv("HOME", str(tmp_path))
        toml_content = """
[model]
cwd = "~/expanded"

[prompt]
system = "test"
"""
        (tmp_path / "tildeagent.toml").write_text(toml_content)
        agent = load_agent_config("tildeagent", agents_dir=tmp_path)
        assert agent.model_config.cwd == subdir.resolve()

    def test_cwd_nonexistent_warns_and_ignores(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Non-existent cwd is logged as a warning and silently ignored (cwd=None).

        Raising at load time would break CI environments where machine-specific
        paths (e.g. ~/projects) are not present.
        """
        import logging

        toml_content = """
[model]
cwd = "/nonexistent/path/xyz"

[prompt]
system = "test"
"""
        (tmp_path / "badcwd.toml").write_text(toml_content)
        with caplog.at_level(logging.WARNING, logger="lyra.core.agent_loader"):
            cfg = load_agent_config("badcwd", agents_dir=tmp_path)
        assert cfg.model_config.cwd is None
        assert any("not a directory" in r.message for r in caplog.records)

    def test_workspaces_parsed(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        toml_content = f"""
[prompt]
system = "test"

[workspaces]
myproject = "{project_dir}"
"""
        (tmp_path / "wsagent.toml").write_text(toml_content)
        agent = load_agent_config("wsagent", agents_dir=tmp_path)
        assert "myproject" in agent.workspaces
        assert agent.workspaces["myproject"] == project_dir.resolve()

    def test_workspaces_invalid_name_raises(self, tmp_path: Path) -> None:
        toml_content = """
[prompt]
system = "test"

[workspaces]
"bad name!" = "/tmp"
"""
        (tmp_path / "badws.toml").write_text(toml_content)
        with pytest.raises(ValueError, match="Invalid workspace name"):
            load_agent_config("badws", agents_dir=tmp_path)

    def test_workspaces_nonexistent_path_raises(self, tmp_path: Path) -> None:
        toml_content = """
[prompt]
system = "test"

[workspaces]
ghost = "/nonexistent/xyz"
"""
        (tmp_path / "ghostws.toml").write_text(toml_content)
        with pytest.raises(ValueError, match="not a directory"):
            load_agent_config("ghostws", agents_dir=tmp_path)

    def test_workspaces_defaults_to_empty(self, tmp_path: Path) -> None:
        toml_content = """
[prompt]
system = "test"
"""
        (tmp_path / "nows.toml").write_text(toml_content)
        agent = load_agent_config("nows", agents_dir=tmp_path)
        assert agent.workspaces == {}
