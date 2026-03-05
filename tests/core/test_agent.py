"""Tests for lyra.core.agent: ModelConfig defaults and load_agent_config."""

from __future__ import annotations

from pathlib import Path

import pytest

from lyra.core.agent import ModelConfig, load_agent_config


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
