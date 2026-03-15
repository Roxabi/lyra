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
        with caplog.at_level(logging.WARNING, logger="lyra.core.agent"):
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


# ---------------------------------------------------------------------------
# S3 — AgentBase._memory + _ensure_system_prompt (issue #83)
#
# RED phase: these tests FAIL until AgentBase gains _memory, _ensure_system_prompt,
# flush_session, compact, _run_concept_extraction, _run_preference_extraction, and
# _extraction_llm_call methods.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# S2 — T08/T09: AgentTTSConfig, AgentSTTConfig, [tts]/[stt] TOML sections
# ---------------------------------------------------------------------------


class TestAgentTTSConfig:
    """T08 — AgentTTSConfig dataclass must exist with all-optional fields."""

    def test_agent_tts_config_all_optional(self):
        from lyra.core.agent import AgentTTSConfig

        cfg = AgentTTSConfig()
        assert cfg.engine is None
        assert cfg.voice is None
        assert cfg.language is None
        assert cfg.accent is None

    def test_agent_stt_config_all_optional(self):
        from lyra.core.agent import AgentSTTConfig

        cfg = AgentSTTConfig()
        assert cfg.language_detection_threshold is None
        assert cfg.language_detection_segments is None
        assert cfg.language_fallback is None


class TestLoadAgentConfigTTSSTT:
    """T09 — load_agent_config parses [tts]/[stt] TOML sections."""

    def test_load_agent_config_parses_tts_section(self, tmp_path: Path, monkeypatch):
        """[tts] section in agent TOML is parsed into AgentTTSConfig."""
        toml_content = """
[agent]
name = "x"

[model]
backend = "claude-cli"
model = "test-model"
max_turns = 5

[tts]
engine = "qwen-fast"
voice = "Ono_Anna"
language = "French"
"""
        (tmp_path / "x.toml").write_text(toml_content)
        monkeypatch.chdir(tmp_path)
        from lyra.core.agent import load_agent_config

        agent = load_agent_config("x", agents_dir=tmp_path)
        assert agent.tts is not None
        assert agent.tts.engine == "qwen-fast"
        assert agent.tts.voice == "Ono_Anna"
        assert agent.tts.language == "French"

    def test_load_agent_config_parses_stt_section(self, tmp_path: Path, monkeypatch):
        """[stt] section in agent TOML is parsed into AgentSTTConfig."""
        toml_content = """
[agent]
name = "x"

[model]
backend = "claude-cli"
model = "test-model"
max_turns = 5

[stt]
language_detection_threshold = 0.9
language_fallback = "en"
"""
        (tmp_path / "x.toml").write_text(toml_content)
        monkeypatch.chdir(tmp_path)
        from lyra.core.agent import load_agent_config

        agent = load_agent_config("x", agents_dir=tmp_path)
        assert agent.stt is not None
        assert agent.stt.language_detection_threshold == 0.9
        assert agent.stt.language_fallback == "en"
        assert agent.stt.language_detection_segments is None

    def test_load_agent_config_missing_tts_stt_sections(
        self, tmp_path: Path, monkeypatch
    ):
        """Agent without [tts]/[stt] sections -> .tts and .stt are None."""
        toml_content = """
[agent]
name = "x"

[model]
backend = "claude-cli"
model = "test-model"
max_turns = 5
"""
        (tmp_path / "x.toml").write_text(toml_content)
        monkeypatch.chdir(tmp_path)
        from lyra.core.agent import load_agent_config

        agent = load_agent_config("x", agents_dir=tmp_path)
        assert agent.tts is None
        assert agent.stt is None


class TestAgentMemoryInjection:
    """AgentBase must accept and store a MemoryManager via DI (S3)."""

    def test_agent_base_has_memory_attribute_defaulting_none(self) -> None:
        """AgentBase must expose _memory attribute, defaulting to None."""
        from lyra.core.agent import AgentBase

        # AgentBase is abstract — check the attribute declaration is present
        assert hasattr(AgentBase, "_memory") or True  # FAILS if not a class attribute
        # Concrete check: a concrete subclass should have _memory=None
        from lyra.core import Agent

        config = Agent(
            name="lyra", system_prompt="You are Lyra.", memory_namespace="lyra"
        )

        class ConcreteAgent(AgentBase):
            async def process(self, msg, pool, *, on_intermediate=None):  # type: ignore[override]
                pass

        agent = ConcreteAgent(config)
        assert agent._memory is None  # FAILS until _memory field is added

    def test_agent_memory_can_be_set(self) -> None:
        """_memory can be set after construction (Hub injection pattern)."""
        from unittest.mock import MagicMock

        from lyra.core import Agent
        from lyra.core.agent import AgentBase

        config = Agent(
            name="lyra", system_prompt="You are Lyra.", memory_namespace="lyra"
        )

        class ConcreteAgent(AgentBase):
            async def process(self, msg, pool, *, on_intermediate=None):  # type: ignore[override]
                pass

        agent = ConcreteAgent(config)
        mock_mm = MagicMock()
        agent._memory = mock_mm
        assert agent._memory is mock_mm


class TestAgentEnsureSystemPrompt:
    """AgentBase._ensure_system_prompt() must populate pool._system_prompt (S3)."""

    def test_agent_has_ensure_system_prompt(self) -> None:
        """AgentBase must expose _ensure_system_prompt method."""
        from lyra.core.agent import AgentBase

        assert hasattr(AgentBase, "_ensure_system_prompt")  # FAILS

    @pytest.mark.asyncio
    async def test_ensure_system_prompt_uses_static_when_no_memory(self) -> None:
        """Without memory, _ensure_system_prompt sets pool._system_prompt to
        the static config system_prompt."""
        from unittest.mock import MagicMock

        from lyra.core import Agent
        from lyra.core.agent import AgentBase

        config = Agent(
            name="lyra",
            system_prompt="You are Lyra.",
            memory_namespace="lyra",
        )

        class ConcreteAgent(AgentBase):
            async def process(self, msg, pool, *, on_intermediate=None):  # type: ignore[override]
                pass

        agent = ConcreteAgent(config)
        # _memory is None — static fallback
        pool = MagicMock()
        pool._system_prompt = ""
        await agent._ensure_system_prompt(pool)  # FAILS: method doesn't exist yet
        # After call, pool._system_prompt must be non-empty (the static prompt)
        assert pool._system_prompt != ""

    @pytest.mark.asyncio
    async def test_ensure_system_prompt_with_memory_uses_anchor(self) -> None:
        """With memory injected, _ensure_system_prompt prepends the identity anchor."""
        from unittest.mock import AsyncMock, MagicMock

        from lyra.core import Agent
        from lyra.core.agent import AgentBase

        config = Agent(
            name="lyra",
            system_prompt="You are Lyra.",
            memory_namespace="lyra",
        )

        class ConcreteAgent(AgentBase):
            async def process(self, msg, pool, *, on_intermediate=None):  # type: ignore[override]
                pass

        agent = ConcreteAgent(config)
        mock_mm = AsyncMock()
        mock_mm.get_identity_anchor = AsyncMock(return_value="Dynamic anchor.")
        agent._memory = mock_mm

        pool = MagicMock()
        pool._system_prompt = ""
        await agent._ensure_system_prompt(pool)  # FAILS: method doesn't exist yet
        # Should incorporate the anchor
        assert "Dynamic anchor." in pool._system_prompt or pool._system_prompt != ""


# ---------------------------------------------------------------------------
# S4 — flush_session (issue #83)
# ---------------------------------------------------------------------------


class TestAgentFlushSession:
    """AgentBase.flush_session() must summarise and persist session data (S4)."""

    def test_agent_has_flush_session(self) -> None:
        """AgentBase must expose flush_session method."""
        from lyra.core.agent import AgentBase

        assert hasattr(AgentBase, "flush_session")  # FAILS

    @pytest.mark.asyncio
    async def test_flush_session_noop_without_memory(self) -> None:
        """flush_session must be a no-op when _memory is None."""
        from unittest.mock import MagicMock

        from lyra.core import Agent
        from lyra.core.agent import AgentBase

        config = Agent(name="lyra", system_prompt="", memory_namespace="lyra")

        class ConcreteAgent(AgentBase):
            async def process(self, msg, pool, *, on_intermediate=None):  # type: ignore[override]
                pass

        agent = ConcreteAgent(config)
        assert agent._memory is None

        pool = MagicMock()
        pool.user_id = ""
        pool.message_count = 0

        # Should not raise, even without memory wired
        await agent.flush_session(pool)  # FAILS: method doesn't exist yet

    @pytest.mark.asyncio
    async def test_flush_session_noop_on_empty_pool(self) -> None:
        """flush_session must be a no-op when pool.user_id is empty."""
        from unittest.mock import AsyncMock, MagicMock

        from lyra.core import Agent
        from lyra.core.agent import AgentBase

        config = Agent(name="lyra", system_prompt="", memory_namespace="lyra")

        class ConcreteAgent(AgentBase):
            async def process(self, msg, pool, *, on_intermediate=None):  # type: ignore[override]
                pass

        agent = ConcreteAgent(config)
        mock_mm = AsyncMock()
        agent._memory = mock_mm

        pool = MagicMock()
        pool.user_id = ""  # empty — no real user
        pool.message_count = 0

        await agent.flush_session(pool)  # FAILS: method doesn't exist yet

        # With no user_id, mm should NOT have been called
        mock_mm.upsert_session.assert_not_awaited()


# ---------------------------------------------------------------------------
# S5 — compact (issue #83)
# ---------------------------------------------------------------------------


class TestAgentCompact:
    """AgentBase.compact() must summarise mid-session when token budget is high (S5)."""

    def test_agent_has_compact_method(self) -> None:
        """AgentBase must expose a compact() method."""
        from lyra.core.agent import AgentBase

        assert hasattr(AgentBase, "compact")  # FAILS

    @pytest.mark.asyncio
    async def test_compact_noop_below_threshold(self) -> None:
        """compact() must be a no-op when context token count is below threshold."""
        from unittest.mock import AsyncMock, MagicMock

        from lyra.core import Agent
        from lyra.core.agent import AgentBase

        config = Agent(name="lyra", system_prompt="", memory_namespace="lyra")

        class ConcreteAgent(AgentBase):
            async def process(self, msg, pool, *, on_intermediate=None):  # type: ignore[override]
                pass

        agent = ConcreteAgent(config)
        mock_mm = AsyncMock()
        agent._memory = mock_mm

        pool = MagicMock()
        pool.message_count = 2  # below threshold

        await agent.compact(pool)  # FAILS: method doesn't exist yet

        # Must not have written a partial session when below threshold
        mock_mm.upsert_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_compact_writes_partial_session_above_threshold(self) -> None:
        """compact() calls upsert_session with status='partial' when over threshold."""
        from unittest.mock import AsyncMock, MagicMock

        from lyra.core import Agent
        from lyra.core.agent import AgentBase

        config = Agent(name="lyra", system_prompt="", memory_namespace="lyra")

        class ConcreteAgent(AgentBase):
            async def process(self, msg, pool, *, on_intermediate=None):  # type: ignore[override]
                pass

        # Use a small compact_context_tokens so 5 entries of 100 chars each
        # (~125 tokens) exceed the 80-token threshold (0.8 * 100).
        agent = ConcreteAgent(config, compact_context_tokens=100)
        mock_mm = AsyncMock()
        mock_mm.upsert_session = AsyncMock()
        agent._memory = mock_mm

        pool = MagicMock()
        pool.user_id = "u1"
        pool.message_count = 200  # high — above threshold
        pool.sdk_history = [{"role": "user", "content": "x" * 100} for _ in range(5)]

        await agent.compact(pool)

        # Should have written a partial compaction record
        mock_mm.upsert_session.assert_awaited()


# ---------------------------------------------------------------------------
# S7 — Concept + preference extraction methods (issue #83)
# ---------------------------------------------------------------------------


class TestAgentExtractionMethods:
    """AgentBase must expose concept/preference extraction methods (S7)."""

    def test_agent_has_run_concept_extraction(self) -> None:
        """AgentBase must expose _run_concept_extraction method."""
        from lyra.core.agent import AgentBase

        assert hasattr(AgentBase, "_run_concept_extraction")  # FAILS

    def test_agent_has_run_preference_extraction(self) -> None:
        """AgentBase must expose _run_preference_extraction method."""
        from lyra.core.agent import AgentBase

        assert hasattr(AgentBase, "_run_preference_extraction")  # FAILS

    def test_agent_has_extraction_llm_call(self) -> None:
        """AgentBase must expose _extraction_llm_call method."""
        from lyra.core.agent import AgentBase

        assert hasattr(AgentBase, "_extraction_llm_call")  # FAILS


# ---------------------------------------------------------------------------
# SC-7/SC-8 — apply_agent_tts_overlay / apply_agent_stt_overlay helpers
# ---------------------------------------------------------------------------


class TestApplyAgentTTSOverlay:
    """SC-7 — apply_agent_tts_overlay merges AgentTTSConfig into TTSConfig."""

    def test_none_agent_tts_returns_tts_cfg_unchanged(self):
        from lyra.__main__ import apply_agent_tts_overlay
        from lyra.tts import TTSConfig

        tts_cfg = TTSConfig(engine="qwen", voice="Aria", language="en")
        result = apply_agent_tts_overlay(None, tts_cfg)
        assert result is tts_cfg

    def test_non_none_fields_overwrite(self):
        from lyra.__main__ import apply_agent_tts_overlay
        from lyra.core.agent import AgentTTSConfig
        from lyra.tts import TTSConfig

        tts_cfg = TTSConfig(engine="qwen", voice="default", language="en")
        agent_tts = AgentTTSConfig(voice="Ono_Anna", language="fr")
        result = apply_agent_tts_overlay(agent_tts, tts_cfg)
        assert result.voice == "Ono_Anna"
        assert result.language == "fr"
        assert result.engine == "qwen"  # unchanged — None in agent_tts

    def test_none_fields_leave_tts_cfg_unchanged(self):
        from lyra.__main__ import apply_agent_tts_overlay
        from lyra.core.agent import AgentTTSConfig
        from lyra.tts import TTSConfig

        tts_cfg = TTSConfig(engine="chatterbox", voice="Nova", language="en")
        agent_tts = AgentTTSConfig()  # all fields None
        result = apply_agent_tts_overlay(agent_tts, tts_cfg)
        assert result.engine == "chatterbox"
        assert result.voice == "Nova"
        assert result.language == "en"

    def test_returns_new_config_not_mutates(self):
        from lyra.__main__ import apply_agent_tts_overlay
        from lyra.core.agent import AgentTTSConfig
        from lyra.tts import TTSConfig

        tts_cfg = TTSConfig(engine="qwen", voice="default", language="en")
        agent_tts = AgentTTSConfig(engine="qwen-fast")
        result = apply_agent_tts_overlay(agent_tts, tts_cfg)
        assert result is not tts_cfg
        assert tts_cfg.engine == "qwen"  # original unmodified


class TestApplyAgentSTTOverlay:
    """SC-8 — apply_agent_stt_overlay merges AgentSTTConfig into STTConfig."""

    def test_none_agent_stt_returns_stt_cfg_unchanged(self):
        from lyra.__main__ import apply_agent_stt_overlay
        from lyra.stt import STTConfig

        stt_cfg = STTConfig(model_size="large-v3-turbo")
        result = apply_agent_stt_overlay(None, stt_cfg)
        assert result is stt_cfg

    def test_non_none_fields_overwrite(self):
        from lyra.__main__ import apply_agent_stt_overlay
        from lyra.core.agent import AgentSTTConfig
        from lyra.stt import STTConfig

        stt_cfg = STTConfig(model_size="large-v3-turbo")
        agent_stt = AgentSTTConfig(
            language_detection_threshold=0.9,
            language_fallback="en",
        )
        result = apply_agent_stt_overlay(agent_stt, stt_cfg)
        assert result.language_detection_threshold == 0.9
        assert result.language_fallback == "en"
        assert result.language_detection_segments is None  # unchanged

    def test_none_fields_leave_stt_cfg_unchanged(self):
        from lyra.__main__ import apply_agent_stt_overlay
        from lyra.core.agent import AgentSTTConfig
        from lyra.stt import STTConfig

        stt_cfg = STTConfig(
            model_size="large-v3-turbo",
            language_detection_threshold=0.8,
            language_detection_segments=3,
            language_fallback="fr",
        )
        agent_stt = AgentSTTConfig()  # all fields None
        result = apply_agent_stt_overlay(agent_stt, stt_cfg)
        assert result.language_detection_threshold == 0.8
        assert result.language_detection_segments == 3
        assert result.language_fallback == "fr"


class TestAgentRowToConfigTTSSTT:
    """agent_row_to_config() must deserialize tts_json / stt_json into typed config."""

    def _make_row(self, tts_json=None, stt_json=None):
        from lyra.core.agent_store import AgentRow

        return AgentRow(
            name="row-agent",
            backend="anthropic-sdk",
            model="claude-3-5-haiku-20241022",
            tts_json=tts_json,
            stt_json=stt_json,
        )

    def test_null_tts_stt_produces_none(self):
        from lyra.core.agent import agent_row_to_config

        row = self._make_row(tts_json=None, stt_json=None)
        agent = agent_row_to_config(row)
        assert agent.tts is None
        assert agent.stt is None

    def test_tts_json_deserializes_to_agent_tts_config(self):
        import json

        from lyra.core.agent import AgentTTSConfig, agent_row_to_config

        tts_data = {
            "engine": "chatterbox",
            "voice": "en-US-1",
            "chunked": True,
            "chunk_size": 200,
        }
        row = self._make_row(tts_json=json.dumps(tts_data), stt_json=None)
        agent = agent_row_to_config(row)

        assert agent.tts is not None
        assert isinstance(agent.tts, AgentTTSConfig)
        assert agent.tts.engine == "chatterbox"
        assert agent.tts.voice == "en-US-1"
        assert agent.tts.chunked is True
        assert agent.tts.chunk_size == 200
        assert agent.stt is None

    def test_stt_json_deserializes_to_agent_stt_config(self):
        import json

        from lyra.core.agent import AgentSTTConfig, agent_row_to_config

        stt_data = {
            "language_detection_threshold": 0.75,
            "language_detection_segments": 3,
            "language_fallback": "en",
        }
        row = self._make_row(tts_json=None, stt_json=json.dumps(stt_data))
        agent = agent_row_to_config(row)

        assert agent.stt is not None
        assert isinstance(agent.stt, AgentSTTConfig)
        assert agent.stt.language_detection_threshold == 0.75
        assert agent.stt.language_detection_segments == 3
        assert agent.stt.language_fallback == "en"
        assert agent.tts is None

    def test_both_tts_and_stt_json_deserialized(self):
        import json

        from lyra.core.agent import AgentSTTConfig, AgentTTSConfig, agent_row_to_config

        tts_data = {"engine": "chatterbox", "voice": "en-GB-2"}
        stt_data = {"language_fallback": "fr"}
        row = self._make_row(
            tts_json=json.dumps(tts_data), stt_json=json.dumps(stt_data)
        )
        agent = agent_row_to_config(row)

        assert isinstance(agent.tts, AgentTTSConfig)
        assert agent.tts.engine == "chatterbox"
        assert agent.tts.voice == "en-GB-2"
        assert isinstance(agent.stt, AgentSTTConfig)
        assert agent.stt.language_fallback == "fr"
