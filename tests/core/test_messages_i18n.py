"""Tests for MessageManager — i18n language resolution and hot-reload
(SC-11e, SC-11f, SC-6).

Covers:
  V2 (T2.3) — SC-11e: FR language resolution
               SC-11f: reading [i18n] default_language from agent TOML
  V4 (T4.5) — SC-6:  hot-reload preserves msg_manager on CommandRouter rebuild
"""

from __future__ import annotations

from pathlib import Path

from lyra.core.messages import MessageManager

from .conftest import MESSAGES_TOML_PATH

# ---------------------------------------------------------------------------
# V2 — SC-11e: FR language resolution
# ---------------------------------------------------------------------------


class TestFrenchLanguageResolution:
    """SC-11e: language="fr" returns French strings."""

    def test_fr_generic_error(self) -> None:
        # Arrange
        mm = MessageManager(MESSAGES_TOML_PATH, language="fr")

        # Act
        result = mm.get("generic")

        # Assert
        assert result == "Une erreur s'est produite. R\u00e9essaie."

    def test_fr_help_header(self) -> None:
        # Arrange
        mm = MessageManager(MESSAGES_TOML_PATH, language="fr")

        # Act
        result = mm.get("help_header")

        # Assert
        assert result == "Commandes disponibles :"

    def test_fr_telegram_adapter_backpressure(self) -> None:
        # Arrange
        mm = MessageManager(MESSAGES_TOML_PATH, language="fr")

        # Act
        result = mm.get("backpressure_ack", platform="telegram")

        # Assert — FR platform-specific string returned
        assert "Traitement" in result

    def test_fr_discord_adapter_backpressure(self) -> None:
        # Arrange
        mm = MessageManager(MESSAGES_TOML_PATH, language="fr")

        # Act
        result = mm.get("backpressure_ack", platform="discord")

        # Assert
        assert "Traitement" in result

    def test_fr_stream_interrupted(self) -> None:
        # Arrange
        mm = MessageManager(MESSAGES_TOML_PATH, language="fr")

        # Act
        result = mm.get("stream_interrupted", platform="telegram")

        # Assert
        assert "interrompue" in result

    def test_fr_substitution_works(self) -> None:
        # Arrange
        mm = MessageManager(MESSAGES_TOML_PATH, language="fr")

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
        from lyra.core.agent_loader import load_agent_config

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
        from lyra.core.agent_loader import load_agent_config

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
        from lyra.core.agent_loader import load_agent_config

        # Act
        cfg = load_agent_config("enagent", agents_dir=tmp_path)

        # Assert
        assert cfg.i18n_language == "en"

    def test_lyra_default_agent_has_i18n_language(self) -> None:
        # Arrange — load the real default agent config with fixture persona
        from pathlib import Path

        from lyra.core.agent_loader import load_agent_config

        fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "personas"

        # Act
        cfg = load_agent_config("lyra_default", personas_dir=fixtures_dir)

        # Assert — field must exist with a valid value
        assert hasattr(cfg, "i18n_language")
        assert cfg.i18n_language in ("en", "fr")  # or whatever the default is


# ---------------------------------------------------------------------------
# V4 — SC-6: Hot-reload preserves msg_manager
# ---------------------------------------------------------------------------


class TestHotReloadPreservesMsgManager:
    """SC-6: CommandRouter retains msg_manager after _maybe_reload() rebuilds it."""

    def test_hotreload_config_change_preserves_msg_manager(
        self, tmp_path: Path
    ) -> None:
        """msg_manager survives CommandRouter rebuild on config hot-reload."""
        from unittest.mock import MagicMock

        from lyra.core.agent import AgentBase, load_agent_config
        from lyra.core.agent_models import AgentRow
        from lyra.core.message import InboundMessage, Response
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
            async def process(
                self, msg: InboundMessage, pool: Pool, *, on_intermediate=None
            ) -> Response:
                return Response(content="ok")

        config = load_agent_config("reloadtest", agents_dir=tmp_path)
        mm = MessageManager(MESSAGES_TOML_PATH)

        # Mock agent_store to simulate DB-based hot-reload (#343)
        mock_store = MagicMock()
        changed_row = AgentRow(
            name="reloadtest",
            backend="claude-cli",
            model="claude-sonnet-4-5",
            updated_at="2026-01-01T00:00:01",
        )
        mock_store.get.return_value = changed_row

        agent = ConcreteAgent(
            config,
            agents_dir=tmp_path,
            msg_manager=mm,
            agent_store=mock_store,
        )

        # Verify msg_manager is wired into command_router initially
        assert agent.command_router._msg_manager is mm

        old_cr = agent.command_router
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
        from lyra.core.commands.command_router import CommandRouter
        from lyra.core.message import InboundMessage, Response
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
            "from lyra.core.message import Response, InboundMessage\n"
            "from lyra.core.pool import Pool\n"
            "async def cmd_echo("
            "msg: InboundMessage, pool: Pool, args: list[str]) -> Response:\n"
            '    return Response(content=" ".join(args))\n'
        )

        class ConcreteAgent(AgentBase):
            async def process(
                self, msg: InboundMessage, pool: Pool, *, on_intermediate=None
            ) -> Response:
                return Response(content="ok")

        config = load_agent_config("pluginreload", agents_dir=tmp_path)
        mm = MessageManager(MESSAGES_TOML_PATH)
        agent = ConcreteAgent(
            config,
            agents_dir=tmp_path,
            plugins_dir=plugins_dir,
            msg_manager=mm,
        )

        # Manually load echo plugin and wire it
        agent._command_loader.load("echo")
        agent._effective_plugins = ["echo"]
        agent._plugin_mtimes = agent._record_plugin_mtimes()
        agent._command_mgr.command_hashes = agent._command_mgr._record_command_hashes()
        agent.command_router = CommandRouter(
            agent._command_loader,
            agent._effective_plugins,
            msg_manager=mm,
        )

        # Act — simulate plugin handlers.py change (content + mtime)
        old_cr = agent.command_router
        handlers_path.write_text(
            "from lyra.core.message import Response, InboundMessage\n"
            "from lyra.core.pool import Pool\n"
            "async def cmd_echo("
            "msg: InboundMessage, pool: Pool, args: list[str]) -> Response:\n"
            '    return Response(content="v2: " + " ".join(args))\n'
        )
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
        from lyra.core.message import InboundMessage, Response
        from lyra.core.pool import Pool

        toml_content = b"""
[prompt]
system = "test"
"""
        (tmp_path / "noinjection.toml").write_bytes(toml_content)

        class ConcreteAgent(AgentBase):
            async def process(
                self, msg: InboundMessage, pool: Pool, *, on_intermediate=None
            ) -> Response:
                return Response(content="ok")

        config = load_agent_config("noinjection", agents_dir=tmp_path)
        # No msg_manager= argument passed — backward-compatible path
        agent = ConcreteAgent(config, agents_dir=tmp_path)

        # Assert — None when not injected (existing tests continue to pass)
        assert agent.command_router._msg_manager is None
