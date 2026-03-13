"""Tests for lyra.core.agent: ModelConfig, load_agent_config, persona system."""

from __future__ import annotations

from pathlib import Path

import pytest

from lyra.core.agent import (
    ExpertiseConfig,
    IdentityConfig,
    ModelConfig,
    PersonaConfig,
    PersonalityConfig,
    VoiceConfig,
    compose_system_prompt,
    load_agent_config,
    load_persona,
)


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

    def test_plugins_enabled_from_toml(self, tmp_path: Path) -> None:
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
        assert agent.plugins_enabled == ("echo",)

    def test_plugins_enabled_defaults_to_empty(self, tmp_path: Path) -> None:
        # Arrange — absent [plugins] section → empty list (default-open)
        toml_content = """
[prompt]
system = "test"
"""
        (tmp_path / "testagent.toml").write_text(toml_content)

        # Act
        agent = load_agent_config("testagent", agents_dir=tmp_path)

        # Assert
        assert agent.plugins_enabled == ()

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

    def test_cwd_nonexistent_raises(self, tmp_path: Path) -> None:
        toml_content = """
[model]
cwd = "/nonexistent/path/xyz"

[prompt]
system = "test"
"""
        (tmp_path / "badcwd.toml").write_text(toml_content)
        with pytest.raises(ValueError, match="not a directory"):
            load_agent_config("badcwd", agents_dir=tmp_path)

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


class TestPersonaConfig:
    def test_frozen(self) -> None:
        identity = IdentityConfig(name="TestBot")
        persona = PersonaConfig(identity=identity)
        with pytest.raises(AttributeError):
            persona.identity = IdentityConfig(name="Other")  # type: ignore[misc]

    def test_all_fields_accessible(self) -> None:
        identity = IdentityConfig(
            name="TestBot",
            tagline="a test bot",
            creator="Tester",
            role="assistant",
            goal="Help testing",
        )
        personality = PersonalityConfig(
            traits=("smart", "direct"),
            communication_style="concise",
            tone="professional",
            humor="dry",
        )
        expertise = ExpertiseConfig(
            areas=("Python", "testing"),
            instructions=("Be thorough.",),
        )
        voice = VoiceConfig(
            speaking_style="clear",
            pace="moderate",
            warmth="warm",
        )
        persona = PersonaConfig(
            identity=identity,
            personality=personality,
            expertise=expertise,
            voice=voice,
        )
        assert persona.identity.name == "TestBot"
        assert persona.identity.tagline == "a test bot"
        assert persona.personality.traits == ("smart", "direct")
        assert persona.expertise.areas == ("Python", "testing")
        assert persona.voice.speaking_style == "clear"
        assert persona.voice.pace == "moderate"
        assert persona.voice.warmth == "warm"

    def test_defaults(self) -> None:
        identity = IdentityConfig(name="Minimal")
        persona = PersonaConfig(identity=identity)
        assert persona.personality.traits == ()
        assert persona.expertise.areas == ()
        assert persona.voice.speaking_style == ""


class TestLoadPersona:
    def test_valid_load(self, tmp_path: Path) -> None:
        toml_content = """\
[identity]
name = "TestBot"
tagline = "a test bot"
creator = "Tester"

[personality]
traits = ["direct", "precise"]

[expertise]
areas = ["Python", "testing"]
instructions = ["Be thorough."]

[voice]
speaking_style = "clear"
pace = "moderate"
warmth = "warm"
"""
        (tmp_path / "testbot.persona.toml").write_text(toml_content)
        persona = load_persona("testbot", personas_dir=tmp_path)
        assert persona.identity.name == "TestBot"
        assert persona.identity.tagline == "a test bot"
        assert persona.personality.traits == ("direct", "precise")
        assert persona.expertise.areas == ("Python", "testing")
        assert persona.voice.speaking_style == "clear"

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="Persona config not found"):
            load_persona("nonexistent", personas_dir=tmp_path)

    def test_missing_name(self, tmp_path: Path) -> None:
        toml_content = """\
[identity]
tagline = "no name here"
"""
        (tmp_path / "noname.persona.toml").write_text(toml_content)
        with pytest.raises(ValueError, match="missing required \\[identity\\].name"):
            load_persona("noname", personas_dir=tmp_path)

    @pytest.mark.parametrize(
        "bad_name", ["../etc/passwd", "a b c", "foo/bar", "hello!"]
    )
    def test_invalid_name_rejected(self, tmp_path: Path, bad_name: str) -> None:
        with pytest.raises(ValueError, match="only \\[a-zA-Z0-9_-\\] allowed"):
            load_persona(bad_name, personas_dir=tmp_path)


class TestComposeSystemPrompt:
    def _make_persona(self) -> PersonaConfig:
        return PersonaConfig(
            identity=IdentityConfig(
                name="Lyra",
                tagline="a personal AI assistant",
                creator="Roxabi",
                goal="Be direct and precise.",
            ),
            personality=PersonalityConfig(
                traits=("direct", "precise"),
                communication_style="concise",
                tone="professional",
            ),
            expertise=ExpertiseConfig(
                areas=("Python", "testing"),
                instructions=("Respond in English.", "Be thorough."),
            ),
        )

    def test_contains_all_fields(self) -> None:
        persona = self._make_persona()
        result = compose_system_prompt(persona)
        assert "Lyra" in result
        assert "Roxabi" in result
        assert "Be direct and precise." in result
        assert "direct" in result
        assert "precise" in result
        assert "Python" in result
        assert "testing" in result
        assert "Respond in English." in result
        assert "Be thorough." in result

    def test_natural_prose(self) -> None:
        persona = self._make_persona()
        result = compose_system_prompt(persona)
        assert result.startswith("You are")
        assert "Name:" not in result
        assert "Traits:" not in result

    def test_size_guard(self) -> None:
        persona = PersonaConfig(
            identity=IdentityConfig(name="BigBot"),
            expertise=ExpertiseConfig(
                instructions=tuple(["x" * 1000] * 100),
            ),
        )
        with pytest.raises(ValueError, match="exceeds.*KB"):
            compose_system_prompt(persona)


class TestLoadAgentConfigWithPersona:
    def test_persona_only_composes_prompt(self, tmp_path: Path) -> None:
        """Agent TOML with persona only -> composed prompt from vault."""
        personas_dir = tmp_path / "personas"
        personas_dir.mkdir()
        (personas_dir / "mybot.persona.toml").write_text("""\
[identity]
name = "MyBot"
tagline = "a helpful bot"
creator = "TestCo"
goal = "Help everyone."

[personality]
traits = ["friendly"]
""")
        (tmp_path / "myagent.toml").write_text("""\
[agent]
memory_namespace = "myagent"
persona = "mybot"
""")
        agent = load_agent_config(
            "myagent",
            agents_dir=tmp_path,
            personas_dir=personas_dir,
        )
        assert agent.persona is not None
        assert agent.persona.identity.name == "MyBot"
        assert "MyBot" in agent.system_prompt
        assert agent.system_prompt.startswith("You are")

    def test_persona_plus_raw_prompt_uses_raw(self, tmp_path: Path) -> None:
        """Agent TOML with persona + [prompt].system -> raw prompt wins."""
        personas_dir = tmp_path / "personas"
        personas_dir.mkdir()
        (personas_dir / "mybot.persona.toml").write_text("""\
[identity]
name = "MyBot"
""")
        (tmp_path / "myagent.toml").write_text("""\
[agent]
memory_namespace = "myagent"
persona = "mybot"

[prompt]
system = "You are a custom bot."
""")
        agent = load_agent_config(
            "myagent",
            agents_dir=tmp_path,
            personas_dir=personas_dir,
        )
        assert agent.persona is not None
        assert agent.system_prompt == "You are a custom bot."

    def test_no_persona_uses_raw_prompt(self, tmp_path: Path) -> None:
        """Agent TOML without persona -> persona is None, raw prompt used."""
        (tmp_path / "myagent.toml").write_text("""\
[agent]
memory_namespace = "myagent"

[prompt]
system = "You are a plain bot."
""")
        agent = load_agent_config("myagent", agents_dir=tmp_path)
        assert agent.persona is None
        assert agent.system_prompt == "You are a plain bot."
