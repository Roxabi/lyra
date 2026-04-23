"""Tests for MessageManager -- i18n language resolution and hot-reload
(SC-11e, SC-11f, SC-6).

Covers:
  V2 (T2.3) -- SC-11e: FR language resolution
               SC-11f: reading fallback_language from AgentRow
  V4 (T4.5) -- SC-6:  hot-reload preserves msg_manager on CommandRouter rebuild

The TOML loading path (load_agent_config) was removed in #346.
Tests that exercised TOML loading have been rewritten to use AgentRow +
agent_row_to_config.
"""

from __future__ import annotations

from pathlib import Path

from lyra.core.messaging.messages import MessageManager

from .conftest import MESSAGES_TOML_PATH

# ---------------------------------------------------------------------------
# V2 -- SC-11e: FR language resolution
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

        # Assert -- FR platform-specific string returned
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

        # Assert -- FR template rendered with substitution
        assert "/foo" in result


# ---------------------------------------------------------------------------
# V2 -- SC-11f: Reading fallback_language from AgentRow
# ---------------------------------------------------------------------------


class TestAgentI18nLanguage:
    """SC-11f: agent_row_to_config() reads fallback_language from AgentRow."""

    def test_i18n_language_defaults_to_en(self) -> None:
        # Arrange -- AgentRow without explicit fallback_language (defaults to "en")
        from lyra.core.agent.agent_db_loader import agent_row_to_config
        from lyra.core.agent.agent_models import AgentRow

        row = AgentRow(
            name="nolangage",
            backend="claude-cli",
            model="claude-3-5-haiku-20241022",
        )

        # Act
        cfg = agent_row_to_config(row)

        # Assert -- defaults to "en" when fallback_language is default
        assert cfg.i18n_language == "en"

    def test_i18n_language_reads_fr(self) -> None:
        # Arrange -- AgentRow with fallback_language = "fr"
        from lyra.core.agent.agent_db_loader import agent_row_to_config
        from lyra.core.agent.agent_models import AgentRow

        row = AgentRow(
            name="frenchagent",
            backend="claude-cli",
            model="claude-3-5-haiku-20241022",
            fallback_language="fr",
        )

        # Act
        cfg = agent_row_to_config(row)

        # Assert
        assert cfg.i18n_language == "fr"

    def test_i18n_language_explicit_en(self) -> None:
        # Arrange -- AgentRow explicitly sets "en"
        from lyra.core.agent.agent_db_loader import agent_row_to_config
        from lyra.core.agent.agent_models import AgentRow

        row = AgentRow(
            name="enagent",
            backend="claude-cli",
            model="claude-3-5-haiku-20241022",
            fallback_language="en",
        )

        # Act
        cfg = agent_row_to_config(row)

        # Assert
        assert cfg.i18n_language == "en"


# ---------------------------------------------------------------------------
# V4 -- SC-6: Hot-reload preserves msg_manager
# ---------------------------------------------------------------------------


class TestHotReloadPreservesMsgManager:
    """SC-6: CommandRouter retains msg_manager after _maybe_reload() rebuilds it."""

    def test_hotreload_config_change_preserves_msg_manager(
        self, tmp_path: Path
    ) -> None:
        """msg_manager survives CommandRouter rebuild on config hot-reload."""
        from unittest.mock import MagicMock

        from lyra.core.agent import Agent, AgentBase
        from lyra.core.agent.agent_config import ModelConfig
        from lyra.core.agent.agent_models import AgentRow
        from lyra.core.messaging.message import InboundMessage, Response
        from lyra.core.pool import Pool

        # Create a concrete subclass of AgentBase for testing
        class ConcreteAgent(AgentBase):
            async def process(
                self, msg: InboundMessage, pool: Pool, *, on_intermediate=None
            ) -> Response:
                return Response(content="ok")

        config = Agent(
            name="reloadtest",
            system_prompt="You are a test assistant.",
            memory_namespace="test",
            llm_config=ModelConfig(backend="claude-cli"),
        )
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

        # Assert -- command_router rebuilt (new object); msg_manager preserved
        assert agent.command_router is not old_cr
        assert agent.command_router._msg_manager is not None
        assert agent.command_router._msg_manager is mm

    def test_hotreload_plugin_change_preserves_msg_manager(
        self, tmp_path: Path
    ) -> None:
        """msg_manager survives CommandRouter rebuild on plugin hot-reload."""
        import os

        from lyra.core.agent import Agent, AgentBase
        from lyra.core.agent.agent_config import ModelConfig
        from lyra.core.commands.command_router import CommandRouter
        from lyra.core.messaging.message import InboundMessage, Response
        from lyra.core.pool import Pool

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
            "from lyra.core.messaging.message import Response, InboundMessage\n"
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

        config = Agent(
            name="pluginreload",
            system_prompt="test",
            memory_namespace="test",
            llm_config=ModelConfig(backend="claude-cli"),
        )
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

        # Act -- simulate plugin handlers.py change (content + mtime)
        old_cr = agent.command_router
        handlers_path.write_text(
            "from lyra.core.messaging.message import Response, InboundMessage\n"
            "from lyra.core.pool import Pool\n"
            "async def cmd_echo("
            "msg: InboundMessage, pool: Pool, args: list[str]) -> Response:\n"
            '    return Response(content="v2: " + " ".join(args))\n'
        )
        new_mtime = handlers_path.stat().st_mtime + 1
        os.utime(handlers_path, (new_mtime, new_mtime))
        agent._plugin_mtimes["echo"] = new_mtime - 2

        agent._maybe_reload()

        # Assert -- command_router rebuilt; msg_manager preserved
        assert agent.command_router is not old_cr
        assert agent.command_router._msg_manager is not None
        assert agent.command_router._msg_manager is mm

    def test_msg_manager_none_when_not_injected(self, tmp_path: Path) -> None:
        """When msg_manager is not passed, command_router._msg_manager is None
        (backward-compatible with existing code paths -- SC-9)."""
        from lyra.core.agent import Agent, AgentBase
        from lyra.core.agent.agent_config import ModelConfig
        from lyra.core.messaging.message import InboundMessage, Response
        from lyra.core.pool import Pool

        class ConcreteAgent(AgentBase):
            async def process(
                self, msg: InboundMessage, pool: Pool, *, on_intermediate=None
            ) -> Response:
                return Response(content="ok")

        config = Agent(
            name="noinjection",
            system_prompt="test",
            memory_namespace="noinjection",
            llm_config=ModelConfig(backend="claude-cli"),
        )
        # No msg_manager= argument passed -- backward-compatible path
        agent = ConcreteAgent(config, agents_dir=tmp_path)

        # Assert -- None when not injected (existing tests continue to pass)
        assert agent.command_router._msg_manager is None
