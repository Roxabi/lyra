"""Tests for persona config, load_persona, compose_system_prompt, and agent+persona integration."""  # noqa: E501

from __future__ import annotations

from pathlib import Path

import pytest

from lyra.core.agent_config import (
    ExpertiseConfig,
    IdentityConfig,
    PersonaConfig,
    PersonalityConfig,
    VoiceConfig,
)
from lyra.core.agent_loader import load_agent_config
from lyra.core.persona import compose_system_prompt, load_persona


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


def test_lyra_default_persona_has_turn_closure_instruction() -> None:
    """Composed system prompt includes turn-closure instruction (#373)."""
    from lyra.core.persona import compose_system_prompt, load_persona

    fixture_dir = Path(__file__).parent.parent / "fixtures" / "personas"
    persona = load_persona("lyra_default", personas_dir=fixture_dir)
    prompt = compose_system_prompt(persona)
    assert "close the turn" in prompt, (
        f"Turn-closure instruction missing from composed prompt.\nGot:\n{prompt}"
    )
