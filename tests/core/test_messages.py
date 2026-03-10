"""Tests for MessageManager (SC-11a through SC-11f, SC-6, SC-11b).

Covers:
  V1 (T1.3) — SC-11a: template loading from TOML
               SC-11c: variable substitution ({command_name}, {retry_secs})
               SC-11d: no-raise guarantee (missing key, bad path, wrong kwargs)
  V2 (T2.3) — SC-11e: FR language resolution
               SC-11f: reading [i18n] default_language from agent TOML
  V4 (T4.5) — SC-11b: resolution order (all 4 fallback steps)
               SC-6:  hot-reload preserves msg_manager on CommandRouter rebuild
"""

from __future__ import annotations

from pathlib import Path

from lyra.core.messages import MessageManager

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Absolute path so tests run regardless of cwd
TOML_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "lyra"
    / "config"
    / "messages.toml"
)


# ---------------------------------------------------------------------------
# V1 — SC-11a: Template loading from TOML
# ---------------------------------------------------------------------------


class TestTemplateLoading:
    """SC-11a: MessageManager loads strings from TOML file."""

    def test_loads_generic_error_string(self) -> None:
        # Arrange
        mm = MessageManager(TOML_PATH)

        # Act
        result = mm.get("generic")

        # Assert
        assert result == "Something went wrong. Please try again."

    def test_loads_help_header_string(self) -> None:
        # Arrange
        mm = MessageManager(TOML_PATH)

        # Act
        result = mm.get("help_header")

        # Assert
        assert result == "Available commands:"

    def test_loads_platform_specific_string(self) -> None:
        # Arrange
        mm = MessageManager(TOML_PATH)

        # Act
        result = mm.get("backpressure_ack", platform="telegram")

        # Assert — platform-specific EN string resolves
        assert "Processing" in result
        assert result != ""

    def test_loads_discord_platform_string(self) -> None:
        # Arrange
        mm = MessageManager(TOML_PATH)

        # Act
        result = mm.get("backpressure_ack", platform="discord")

        # Assert
        assert "Processing" in result


# ---------------------------------------------------------------------------
# V1 — SC-11c: Variable substitution
# ---------------------------------------------------------------------------


class TestVariableSubstitution:
    """SC-11c: {command_name} and {retry_secs} placeholders are rendered."""

    def test_substitutes_command_name(self) -> None:
        # Arrange
        mm = MessageManager(TOML_PATH)

        # Act
        result = mm.get("unknown_command", command_name="/foo")

        # Assert — the substituted value appears in the output
        assert "/foo" in result

    def test_substitutes_retry_secs(self) -> None:
        # Arrange
        mm = MessageManager(TOML_PATH)

        # Act
        result = mm.get("unavailable", retry_secs="30")

        # Assert
        assert "30" in result

    def test_substitution_produces_full_sentence(self) -> None:
        # Arrange
        mm = MessageManager(TOML_PATH)

        # Act
        result = mm.get("unknown_command", command_name="/pizza")

        # Assert — full template rendered (not just the substituted fragment)
        assert "/pizza" in result
        assert "/help" in result


# ---------------------------------------------------------------------------
# V1 — SC-11d: No-raise guarantee
# ---------------------------------------------------------------------------


class TestNoRaiseGuarantee:
    """SC-11d: get() never raises regardless of errors at any level."""

    def test_no_raise_missing_key(self) -> None:
        # Arrange
        mm = MessageManager(TOML_PATH)

        # Act — completely unknown key
        result = mm.get("totally.nonexistent.key")

        # Assert — returns str, never raises
        assert isinstance(result, str)

    def test_no_raise_bad_toml_path(self) -> None:
        # Arrange — path does not exist; MessageManager should swallow the error
        mm = MessageManager("/nonexistent/path/messages.toml")

        # Act
        result = mm.get("generic")

        # Assert — returns a fallback string, never raises
        assert isinstance(result, str)

    def test_no_raise_wrong_kwargs_for_no_placeholder_key(self) -> None:
        # Arrange — "generic" has no {placeholders}; extra kwargs are harmless
        mm = MessageManager(TOML_PATH)

        # Act
        result = mm.get("generic", wrong_kwarg="x")

        # Assert — still returns the string without raising
        assert isinstance(result, str)
        assert "Something went wrong" in result

    def test_no_raise_missing_kwargs_for_placeholder_key(self) -> None:
        # Arrange — "unknown_command" requires {command_name}; if omitted the
        # format_map call would raise KeyError — MessageManager must handle it
        mm = MessageManager(TOML_PATH)

        # Act
        result = mm.get("unknown_command")  # intentionally omit command_name=

        # Assert — no raise; returns some string (fallback or partially-rendered)
        assert isinstance(result, str)

    def test_no_raise_empty_key(self) -> None:
        # Arrange
        mm = MessageManager(TOML_PATH)

        # Act
        result = mm.get("")

        # Assert
        assert isinstance(result, str)

    def test_bad_path_still_returns_fallback_for_known_key(self) -> None:
        # Arrange — TOML fails to load, but hardcoded fallbacks exist
        mm = MessageManager("/no/such/file.toml")

        # Act
        result = mm.get("generic")

        # Assert — returns hardcoded fallback
        assert isinstance(result, str)
        # The fallback for "generic" is the same as the TOML value
        assert "Something went wrong" in result


# ---------------------------------------------------------------------------
# V2 — SC-11e: FR language resolution
# ---------------------------------------------------------------------------


class TestFrenchLanguageResolution:
    """SC-11e: language="fr" returns French strings."""

    def test_fr_generic_error(self) -> None:
        # Arrange
        mm = MessageManager(TOML_PATH, language="fr")

        # Act
        result = mm.get("generic")

        # Assert
        assert result == "Une erreur s'est produite. Réessaie."

    def test_fr_help_header(self) -> None:
        # Arrange
        mm = MessageManager(TOML_PATH, language="fr")

        # Act
        result = mm.get("help_header")

        # Assert
        assert result == "Commandes disponibles :"

    def test_fr_telegram_adapter_backpressure(self) -> None:
        # Arrange
        mm = MessageManager(TOML_PATH, language="fr")

        # Act
        result = mm.get("backpressure_ack", platform="telegram")

        # Assert — FR platform-specific string returned
        assert "Traitement" in result

    def test_fr_discord_adapter_backpressure(self) -> None:
        # Arrange
        mm = MessageManager(TOML_PATH, language="fr")

        # Act
        result = mm.get("backpressure_ack", platform="discord")

        # Assert
        assert "Traitement" in result

    def test_fr_stream_interrupted(self) -> None:
        # Arrange
        mm = MessageManager(TOML_PATH, language="fr")

        # Act
        result = mm.get("stream_interrupted", platform="telegram")

        # Assert
        assert "interrompue" in result

    def test_fr_substitution_works(self) -> None:
        # Arrange
        mm = MessageManager(TOML_PATH, language="fr")

        # Act
        result = mm.get("unknown_command", command_name="/foo")

        # Assert — FR template rendered with substitution
        assert "/foo" in result


# ---------------------------------------------------------------------------
# V2 — SC-11f: Reading [i18n] default_language from agent TOML
# ---------------------------------------------------------------------------


class TestAgentI18nLanguage:
    """SC-11f: load_agent_config() reads [i18n] default_language."""

    def test_i18n_language_defaults_to_en(self, tmp_path: Path) -> None:
        # Arrange — minimal TOML without [i18n] section
        toml_content = b"""
[agent]
memory_namespace = "test"

[model]
backend = "claude-cli"

[prompt]
system = "You are a test assistant."
"""
        (tmp_path / "nolangage.toml").write_bytes(toml_content)
        from lyra.core.agent import load_agent_config

        # Act
        cfg = load_agent_config("nolangage", agents_dir=tmp_path)

        # Assert — defaults to "en" when [i18n] section is absent
        assert cfg.i18n_language == "en"

    def test_i18n_language_reads_fr_from_toml(self, tmp_path: Path) -> None:
        # Arrange — TOML with [i18n] default_language = "fr"
        toml_content = b"""
[agent]
memory_namespace = "test"

[model]
backend = "claude-cli"

[prompt]
system = "Tu es un assistant de test."

[i18n]
default_language = "fr"
"""
        (tmp_path / "frenchagent.toml").write_bytes(toml_content)
        from lyra.core.agent import load_agent_config

        # Act
        cfg = load_agent_config("frenchagent", agents_dir=tmp_path)

        # Assert
        assert cfg.i18n_language == "fr"

    def test_i18n_language_explicit_en(self, tmp_path: Path) -> None:
        # Arrange — TOML explicitly sets "en"
        toml_content = b"""
[prompt]
system = "test"

[i18n]
default_language = "en"
"""
        (tmp_path / "enagent.toml").write_bytes(toml_content)
        from lyra.core.agent import load_agent_config

        # Act
        cfg = load_agent_config("enagent", agents_dir=tmp_path)

        # Assert
        assert cfg.i18n_language == "en"

    def test_lyra_default_agent_has_i18n_language(self) -> None:
        # Arrange — load the real default agent config with fixture persona
        from pathlib import Path

        from lyra.core.agent import load_agent_config

        fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "personas"

        # Act
        cfg = load_agent_config("lyra_default", personas_dir=fixtures_dir)

        # Assert — field must exist with a valid value
        assert hasattr(cfg, "i18n_language")
        assert cfg.i18n_language in ("en", "fr")  # or whatever the default is


# ---------------------------------------------------------------------------
# V4 — SC-11b: Resolution order (all 4 fallback steps)
# ---------------------------------------------------------------------------


class TestResolutionOrder:
    """SC-11b: All four resolution steps are reachable independently.

    Resolution order (from spec):
      (a) adapters.{platform}.{lang}.{key}  — platform + language match
      (b) adapters.{platform}.en.{key}      — platform match, EN fallback
      (c) errors.{lang}.{key}               — global key, active language
      (d) errors.en.{key}                   — global key, EN fallback
      (e) _FALLBACKS[key]                   — hardcoded safety net (never raises)
    """

    def test_step_a_platform_lang_match(self) -> None:
        # Step (a): adapters.telegram.fr.backpressure_ack wins when lang="fr"
        mm = MessageManager(TOML_PATH, language="fr")
        result = mm.get("backpressure_ack", platform="telegram")
        assert result == "Traitement de ta requête\u2026"

    def test_step_b_platform_en_fallback(self) -> None:
        # Step (b): lang="de" has no telegram.de entries → falls to telegram.en
        mm = MessageManager(TOML_PATH, language="de")
        result = mm.get("backpressure_ack", platform="telegram")
        assert result == "Processing your request\u2026"

    def test_step_c_global_lang_match(self) -> None:
        # Step (c): "generic" has no adapters.*.* entry → goes to errors.fr
        # Even when platform="telegram" is passed, no adapters.telegram.*.generic
        # exists so resolution falls to errors.fr.generic
        mm = MessageManager(TOML_PATH, language="fr")
        result = mm.get("generic", platform="telegram")
        assert result == "Une erreur s'est produite. Réessaie."

    def test_step_d_global_en_fallback(self) -> None:
        # Step (d): lang="de" + no platform + key is in errors.en only
        mm = MessageManager(TOML_PATH, language="de")
        result = mm.get("generic")
        assert result == "Something went wrong. Please try again."

    def test_step_e_hardcoded_fallback(self) -> None:
        # Step (e): key not present in TOML at all — uses _FALLBACKS
        mm = MessageManager("/nonexistent/path.toml")
        result = mm.get("generic")
        # Hardcoded fallback should match the known value
        assert "Something went wrong" in result

    def test_platform_lang_beats_platform_en(self) -> None:
        # FR string should win over EN for same platform when lang="fr"
        mm_en = MessageManager(TOML_PATH, language="en")
        mm_fr = MessageManager(TOML_PATH, language="fr")
        result_en = mm_en.get("backpressure_ack", platform="telegram")
        result_fr = mm_fr.get("backpressure_ack", platform="telegram")
        assert result_en != result_fr
        assert "Processing" in result_en
        assert "Traitement" in result_fr

    def test_platform_en_beats_global_en(self) -> None:
        # adapters.telegram.en.backpressure_ack exists; errors.en has no
        # backpressure_ack — so platform.en is the only match
        mm = MessageManager(TOML_PATH, language="en")
        result_with_platform = mm.get("backpressure_ack", platform="telegram")
        result_without_platform = mm.get("backpressure_ack")
        # Without platform, there's no errors.en.backpressure_ack entry —
        # it falls through to _FALLBACKS which still contains the same string
        assert "Processing" in result_with_platform
        assert isinstance(result_without_platform, str)


# ---------------------------------------------------------------------------
# V4 — SC-6: Hot-reload preserves msg_manager
# ---------------------------------------------------------------------------


class TestHotReloadPreservesMsgManager:
    """SC-6: CommandRouter retains msg_manager after _maybe_reload() rebuilds it."""

    def test_hotreload_config_change_preserves_msg_manager(
        self, tmp_path: Path
    ) -> None:
        """msg_manager survives CommandRouter rebuild on config hot-reload."""
        from lyra.core.agent import AgentBase, load_agent_config
        from lyra.core.message import Message, Response
        from lyra.core.pool import Pool

        # Arrange — write a minimal agent TOML
        toml_content = b"""
[agent]
memory_namespace = "test"

[model]
backend = "claude-cli"

[prompt]
system = "You are a test assistant."
"""
        toml_path = tmp_path / "reloadtest.toml"
        toml_path.write_bytes(toml_content)

        # Create a concrete subclass of AgentBase for testing
        class ConcreteAgent(AgentBase):
            async def process(self, msg: Message, pool: Pool) -> Response:
                return Response(content="ok")

        config = load_agent_config("reloadtest", agents_dir=tmp_path)
        mm = MessageManager(TOML_PATH)
        agent = ConcreteAgent(
            config,
            agents_dir=tmp_path,
            msg_manager=mm,
        )

        # Verify msg_manager is wired into command_router initially
        assert agent.command_router._msg_manager is mm

        # Act — write a different system prompt so new_config != self.config,
        # which triggers CommandRouter rebuild in _maybe_reload()
        old_cr = agent.command_router
        toml_path.write_bytes(b"""
[agent]
memory_namespace = "test"

[model]
backend = "claude-cli"

[prompt]
system = "You are a test assistant (reloaded)."
""")
        agent._last_mtime = 0.0  # make agent think the file changed

        agent._maybe_reload()

        # Assert — command_router rebuilt (new object); msg_manager preserved
        assert agent.command_router is not old_cr
        assert agent.command_router._msg_manager is not None
        assert agent.command_router._msg_manager is mm

    def test_hotreload_plugin_change_preserves_msg_manager(
        self, tmp_path: Path
    ) -> None:
        """msg_manager survives CommandRouter rebuild on plugin hot-reload."""
        import os

        from lyra.core.agent import AgentBase, load_agent_config
        from lyra.core.command_router import CommandRouter
        from lyra.core.message import Message, Response
        from lyra.core.pool import Pool

        # Arrange — write minimal agent TOML
        toml_content = b"""
[agent]
memory_namespace = "test"

[model]
backend = "claude-cli"

[prompt]
system = "test"
"""
        toml_path = tmp_path / "pluginreload.toml"
        toml_path.write_bytes(toml_content)

        # Create a plugins directory with a minimal echo plugin
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        plugin_dir = plugins_dir / "echo"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            'name = "echo"\n'
            'description = "Echo back"\n'
            "[[commands]]\n"
            'name = "echo"\n'
            'description = "Echo back"\n'
            'handler = "cmd_echo"\n'
        )
        handlers_path = plugin_dir / "handlers.py"
        handlers_path.write_text(
            "from lyra.core.message import Response, Message\n"
            "from lyra.core.pool import Pool\n"
            "async def cmd_echo("
            "msg: Message, pool: Pool, args: list[str]) -> Response:\n"
            '    return Response(content=" ".join(args))\n'
        )

        class ConcreteAgent(AgentBase):
            async def process(self, msg: Message, pool: Pool) -> Response:
                return Response(content="ok")

        config = load_agent_config("pluginreload", agents_dir=tmp_path)
        mm = MessageManager(TOML_PATH)
        agent = ConcreteAgent(
            config,
            agents_dir=tmp_path,
            plugins_dir=plugins_dir,
            msg_manager=mm,
        )

        # Manually load echo plugin and wire it
        agent._plugin_loader.load("echo")
        agent._effective_plugins = ["echo"]
        agent._plugin_mtimes = agent._record_plugin_mtimes()
        agent.command_router = CommandRouter(
            agent._plugin_loader,
            agent._effective_plugins,
            msg_manager=mm,
        )

        # Act — simulate plugin handlers.py mtime change
        old_cr = agent.command_router
        new_mtime = handlers_path.stat().st_mtime + 1
        os.utime(handlers_path, (new_mtime, new_mtime))
        agent._plugin_mtimes["echo"] = new_mtime - 2

        agent._maybe_reload()

        # Assert — command_router rebuilt; msg_manager preserved
        assert agent.command_router is not old_cr
        assert agent.command_router._msg_manager is not None
        assert agent.command_router._msg_manager is mm

    def test_msg_manager_none_when_not_injected(self, tmp_path: Path) -> None:
        """When msg_manager is not passed, command_router._msg_manager is None
        (backward-compatible with existing code paths — SC-9)."""
        from lyra.core.agent import AgentBase, load_agent_config
        from lyra.core.message import Message, Response
        from lyra.core.pool import Pool

        toml_content = b"""
[prompt]
system = "test"
"""
        (tmp_path / "noinjection.toml").write_bytes(toml_content)

        class ConcreteAgent(AgentBase):
            async def process(self, msg: Message, pool: Pool) -> Response:
                return Response(content="ok")

        config = load_agent_config("noinjection", agents_dir=tmp_path)
        # No msg_manager= argument passed — backward-compatible path
        agent = ConcreteAgent(config, agents_dir=tmp_path)

        # Assert — None when not injected (existing tests continue to pass)
        assert agent.command_router._msg_manager is None
