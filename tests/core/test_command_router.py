"""Tests for the command router (issue #66, updated #106).

Tests the plugin-based CommandRouter: PluginLoader integration, dispatch,
built-in /help, hot-reload, and hub passthrough behaviour.

Covers:
  Slice 1 — Command detection (TestIsCommand)
  Slice 2 — Dispatch to builtins and plugins (TestDispatchHelp,
             TestDispatchUnknownCommand, TestDispatchRoutesToPlugin)
  Slice 3 — Hot-reload + hub passthrough (TestHotReloadUpdatesCommands,
             TestPassthroughNonCommandInHub)
  Slice 4 — /circuit command (SC-15, TestCircuitCommandAdminCheck,
             TestCircuitCommandOpenState, TestCircuitCommandNoRegistry)
  Slice 5 — TOML plugins config parsing (TestPluginsConfigFromToml)
"""

from __future__ import annotations

import asyncio
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lyra.core.agent import Agent, AgentBase, load_agent_config
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.command_router import CommandConfig, CommandRouter
from lyra.core.hub import Hub
from lyra.core.message import Message, MessageType, Platform, Response, TelegramContext
from lyra.core.messages import MessageManager
from lyra.core.plugin_loader import PluginLoader
from lyra.core.pool import Pool

TOML_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "lyra"
    / "config"
    / "messages.toml"
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def make_message(
    content: str = "hello",
    platform: Platform = Platform.TELEGRAM,
    bot_id: str = "main",
    user_id: str = "alice",
) -> Message:
    """Build a minimal Message for testing. Uses direct construction (not
    from_adapter) so that content can be a plain string as the router receives."""
    return Message(
        id="msg-test-1",
        platform=platform,
        bot_id=bot_id,
        user_id=user_id,
        user_name="Alice",
        is_mention=False,
        is_from_bot=False,
        content=content,
        type=MessageType.TEXT,
        timestamp=datetime.now(timezone.utc),
        platform_context=TelegramContext(chat_id=42),
    )


def make_echo_plugin_dir(tmpdir: Path) -> Path:
    """Create a minimal echo plugin in tmpdir/echo/."""
    plugin_dir = tmpdir / "echo"
    plugin_dir.mkdir(exist_ok=True)
    (plugin_dir / "plugin.toml").write_text(
        'name = "echo"\n'
        'description = "Echo back"\n'
        "[[commands]]\n"
        'name = "echo"\n'
        'description = "Echo back the message (test command)"\n'
        'handler = "cmd_echo"\n'
    )
    (plugin_dir / "handlers.py").write_text(
        "from lyra.core.message import Response\n"
        "from lyra.core.pool import Pool\n"
        "from lyra.core.message import Message\n"
        "async def cmd_echo(msg: Message, pool: Pool, args: list[str]) -> Response:\n"
        '    return Response(content=" ".join(args))\n'
    )
    return tmpdir


def make_router(tmp_path: Path, enabled: list[str] | None = None) -> CommandRouter:
    """Build a CommandRouter with the echo plugin loaded."""
    plugins_dir = make_echo_plugin_dir(tmp_path)
    loader = PluginLoader(plugins_dir)
    loader.load("echo")
    effective = enabled if enabled is not None else ["echo"]
    return CommandRouter(plugin_loader=loader, enabled_plugins=effective)


def make_minimal_toml(extra: str = "") -> str:
    """Return a minimal valid agent TOML string."""
    return textwrap.dedent(
        f"""
        [agent]
        name = "test_agent"
        memory_namespace = "test"
        permissions = []

        [model]
        backend = "claude-cli"
        model = "claude-haiku-4-5-20251001"
        max_turns = 10
        tools = []

        [prompt]
        system = "You are a test agent."

        {extra}
        """
    ).strip()


# ---------------------------------------------------------------------------
# Slice 1 — Command Detection
# ---------------------------------------------------------------------------


class TestIsCommand:
    """is_command() detects slash-prefixed messages (SC-1)."""

    def test_is_command_detects_slash_prefix(self, tmp_path: Path) -> None:
        # Arrange
        router = make_router(tmp_path)
        msg = make_message(content="/help")

        # Act
        result = router.is_command(msg)

        # Assert
        assert result is True

    def test_is_command_ignores_plain_text(self, tmp_path: Path) -> None:
        # Arrange
        router = make_router(tmp_path)
        msg = make_message(content="hello")

        # Act
        result = router.is_command(msg)

        # Assert
        assert result is False

    def test_is_command_ignores_slash_in_middle(self, tmp_path: Path) -> None:
        # Arrange
        router = make_router(tmp_path)
        msg = make_message(content="not a /command")

        # Act
        result = router.is_command(msg)

        # Assert
        assert result is False

    def test_is_command_empty_string_is_not_command(self, tmp_path: Path) -> None:
        # Arrange
        router = make_router(tmp_path)
        msg = make_message(content="")

        # Act
        result = router.is_command(msg)

        # Assert
        assert result is False

    def test_is_command_bare_slash_is_not_command(self, tmp_path: Path) -> None:
        # Arrange — a lone "/" with no name is ambiguous; spec requires a name after "/"
        router = make_router(tmp_path)
        msg = make_message(content="/")

        # Act
        result = router.is_command(msg)

        # Assert — "/" alone should NOT be treated as a command (no name follows)
        assert result is False


# ---------------------------------------------------------------------------
# Slice 2 — Dispatch to builtins and plugins
# ---------------------------------------------------------------------------


class TestDispatchHelp:
    """dispatch() for /help returns a listing of all registered commands (SC-4)."""

    @pytest.mark.asyncio
    async def test_dispatch_help_lists_commands(self, tmp_path: Path) -> None:
        # Arrange
        router = make_router(tmp_path)
        msg = make_message(content="/help")

        # Act
        response = await router.dispatch(msg)

        # Assert — response text must mention both /help and /echo
        assert isinstance(response, Response)
        assert "/help" in response.content
        assert "/echo" in response.content

    @pytest.mark.asyncio
    async def test_dispatch_help_includes_builtin_description(
        self, tmp_path: Path
    ) -> None:
        # Arrange
        router = make_router(tmp_path)
        msg = make_message(content="/help")

        # Act
        response = await router.dispatch(msg)

        # Assert — builtin description appears in the listing
        assert "List available commands" in response.content


class TestDispatchUnknownCommand:
    """dispatch() for an unrecognised command returns a helpful error (SC-5)."""

    @pytest.mark.asyncio
    async def test_dispatch_unknown_command(self, tmp_path: Path) -> None:
        # Arrange
        router = make_router(tmp_path)
        msg = make_message(content="/pizza")

        # Act
        response = await router.dispatch(msg)

        # Assert — "Unknown command" and /help hint in the reply
        assert isinstance(response, Response)
        assert "unknown command" in response.content.lower()
        assert "/help" in response.content


class TestDispatchRoutesToPlugin:
    """dispatch() routes a plugin command to its handler and returns the result."""

    @pytest.mark.asyncio
    async def test_dispatch_routes_to_plugin(self, tmp_path: Path) -> None:
        # Arrange
        router = make_router(tmp_path)
        msg = make_message(content="/echo hi")
        pool = Pool(pool_id="test", agent_name="test_agent")

        # Act — call the real echo plugin handler
        response = await router.dispatch(msg, pool=pool)

        # Assert — plugin handler was invoked and result surfaces in Response
        assert isinstance(response, Response)
        assert response.content == "hi"

    @pytest.mark.asyncio
    async def test_dispatch_routes_multiple_args(self, tmp_path: Path) -> None:
        # Arrange
        router = make_router(tmp_path)
        msg = make_message(content="/echo foo bar baz")
        pool = Pool(pool_id="test", agent_name="test_agent")

        # Act
        response = await router.dispatch(msg, pool=pool)

        # Assert
        assert isinstance(response, Response)
        assert response.content == "foo bar baz"

    @pytest.mark.asyncio
    async def test_dispatch_disabled_plugin_returns_unknown(
        self, tmp_path: Path
    ) -> None:
        # Arrange — echo is loaded but NOT in the enabled list
        router = make_router(tmp_path, enabled=[])
        msg = make_message(content="/echo hi")

        # Act
        response = await router.dispatch(msg)

        # Assert — unknown command because echo is not enabled
        assert "unknown command" in response.content.lower()

    @pytest.mark.asyncio
    async def test_dispatch_plugin_without_pool_raises(self, tmp_path: Path) -> None:
        # Arrange — pool is omitted; plugin commands require a real Pool
        router = make_router(tmp_path)
        msg = make_message(content="/echo hi")

        # Act / Assert
        with pytest.raises(TypeError, match="requires a real Pool"):
            await router.dispatch(msg, pool=None)


# ---------------------------------------------------------------------------
# Slice 3 — Hot-reload + hub passthrough
# ---------------------------------------------------------------------------


class TestHotReloadUpdatesCommands:
    """_maybe_reload() rebuilds command_router when plugin handlers.py changes."""

    def test_hot_reload_updates_plugin_commands(self, tmp_path: Path) -> None:
        # Arrange — create a concrete AgentBase subclass for testing
        class ConcreteAgent(AgentBase):
            async def process(self, msg: Message, pool: Pool) -> Response:
                return Response(content="ok")

        # Build a plugins directory with the echo plugin
        (tmp_path / "plugins").mkdir()
        plugins_dir = make_echo_plugin_dir(tmp_path / "plugins")

        # Minimal agent TOML (no plugins section — default-open, but plugin dir empty
        # at agent load time, so we wire plugins manually)
        toml_content = make_minimal_toml()
        toml_path = tmp_path / "test_agent.toml"
        toml_path.write_text(toml_content)

        config = load_agent_config("test_agent", agents_dir=tmp_path)
        agent = ConcreteAgent(config, agents_dir=tmp_path, plugins_dir=plugins_dir)

        # Force-load echo into the agent's plugin_loader
        agent._plugin_loader.load("echo")
        agent._effective_plugins = ["echo"]
        agent._plugin_mtimes = agent._record_plugin_mtimes()
        agent.command_router = CommandRouter(
            agent._plugin_loader, agent._effective_plugins
        )

        # Verify initial state — /echo is reachable
        plugin_cmds = agent._plugin_loader.get_commands(["echo"])
        assert "/echo" in plugin_cmds

        # Act — touch handlers.py to simulate an mtime change, then reload
        handlers_path = plugins_dir / "echo" / "handlers.py"
        import os

        new_mtime = handlers_path.stat().st_mtime + 1
        os.utime(handlers_path, (new_mtime, new_mtime))
        agent._plugin_mtimes["echo"] = new_mtime - 2  # make it appear stale

        # Force config mtime to appear changed so _maybe_reload checks plugins
        new_toml_mtime = toml_path.stat().st_mtime + 1
        os.utime(toml_path, (new_toml_mtime, new_toml_mtime))
        agent._last_mtime = new_toml_mtime - 2

        agent._maybe_reload()

        # Assert — command_router was rebuilt and echo plugin commands are available
        plugin_cmds_after = agent._plugin_loader.get_commands(["echo"])
        assert "/echo" in plugin_cmds_after


class TestPassthroughNonCommandInHub:
    """Plain-text messages bypass the router and reach agent.process() (SC-10)."""

    @pytest.mark.asyncio
    async def test_passthrough_non_command_in_hub(self) -> None:
        # Arrange — wire up a full hub with a capturing agent
        process_calls: list[Message] = []

        class CapturingAgent(AgentBase):
            async def process(self, msg: Message, pool: Pool) -> Response:
                process_calls.append(msg)
                return Response(content="agent reply")

        hub = Hub()

        config = Agent(name="lyra", system_prompt="", memory_namespace="lyra")
        agent = CapturingAgent(config)
        hub.register_agent(agent)

        class CapturingAdapter:
            async def send(self, original_msg: Message, response: Response) -> None:
                pass

            async def send_streaming(
                self, original_msg: Message, chunks: object
            ) -> None:
                pass

        hub.register_adapter(Platform.TELEGRAM, "main", CapturingAdapter())
        hub.register_binding(
            Platform.TELEGRAM, "main", "alice", "lyra", "telegram:main:alice"
        )

        plain_msg = make_message(content="hello, how are you?")
        await hub.bus.put(plain_msg)

        # Act — run the hub for one iteration
        try:
            await asyncio.wait_for(hub.run(), timeout=0.3)
        except asyncio.TimeoutError:
            pass  # expected — hub.run() never returns on its own

        # Assert — agent.process() was called with the plain-text message
        assert len(process_calls) == 1
        assert process_calls[0].content == "hello, how are you?"

    @pytest.mark.asyncio
    async def test_slash_command_does_not_reach_agent_process(self) -> None:
        """When the router handles a /command, agent.process() is never called."""
        # Arrange
        process_calls: list[Message] = []

        class TrackingAgent(AgentBase):
            async def process(self, msg: Message, pool: Pool) -> Response:
                process_calls.append(msg)
                return Response(content="should not be reached")

        hub = Hub()
        config = Agent(name="lyra", system_prompt="", memory_namespace="lyra")
        agent = TrackingAgent(config)
        hub.register_agent(agent)

        class CapturingAdapter:
            async def send(self, original_msg: Message, response: Response) -> None:
                pass

            async def send_streaming(
                self, original_msg: Message, chunks: object
            ) -> None:
                pass

        hub.register_adapter(Platform.TELEGRAM, "main", CapturingAdapter())
        hub.register_binding(
            Platform.TELEGRAM, "main", "alice", "lyra", "telegram:main:alice"
        )

        command_msg = make_message(content="/help")
        await hub.bus.put(command_msg)

        # Act
        try:
            await asyncio.wait_for(hub.run(), timeout=0.3)
        except asyncio.TimeoutError:
            pass

        # Assert — command router intercepted the message; agent.process() was skipped
        assert len(process_calls) == 0


# ---------------------------------------------------------------------------
# Slice 4 — /circuit command (SC-15)
# ---------------------------------------------------------------------------


def make_registry_with_open(open_service: str) -> CircuitRegistry:
    """Build a CircuitRegistry where one named circuit has been tripped open."""
    registry = CircuitRegistry()
    for name in ("anthropic", "telegram", "discord", "hub"):
        cb = CircuitBreaker(name, failure_threshold=1, recovery_timeout=60)
        if name == open_service:
            cb.record_failure()
        registry.register(cb)
    return registry


def make_circuit_router(
    registry: CircuitRegistry | None = None,
    admin_ids: set[str] | None = None,
) -> CommandRouter:
    """Build a CommandRouter with circuit_registry and admin_user_ids set."""
    plugins_dir = Path(tempfile.mkdtemp())
    loader = PluginLoader(plugins_dir)
    return CommandRouter(
        plugin_loader=loader,
        enabled_plugins=[],
        circuit_registry=registry,
        admin_user_ids=admin_ids,
    )


def make_circuit_msg(user_id: str = "tg:user:42") -> Message:
    """Build a /circuit Message from a Telegram user."""
    return Message(
        id="msg-circuit-1",
        platform=Platform.TELEGRAM,
        bot_id="main",
        user_id=user_id,
        user_name="Admin",
        is_mention=False,
        is_from_bot=False,
        content="/circuit",
        type=MessageType.TEXT,
        timestamp=datetime.now(timezone.utc),
        platform_context=TelegramContext(chat_id=42),
    )


class TestCircuitCommandAdminCheck:
    """Non-admin sender is denied; admin sender receives the status table (SC-15)."""

    @pytest.mark.asyncio
    async def test_circuit_command_denied_for_non_admin(self) -> None:
        """SC-15: Non-admin sender → 'This command is admin-only.'"""
        # Arrange
        registry = CircuitRegistry()
        registry.register(CircuitBreaker("anthropic"))
        # Admin is a different user; msg sender is tg:user:42
        router = make_circuit_router(
            registry=registry,
            admin_ids={"tg:user:999"},  # different user from sender
        )
        msg = make_circuit_msg(user_id="tg:user:42")

        # Act
        response = await router.dispatch(msg)

        # Assert
        assert isinstance(response, Response)
        assert "admin-only" in response.content.lower()

    @pytest.mark.asyncio
    async def test_circuit_command_returns_table_for_admin(self) -> None:
        """SC-15: Admin sender → gets formatted status table with all circuits."""
        # Arrange
        registry = CircuitRegistry()
        for name in ("anthropic", "telegram", "discord", "hub"):
            registry.register(CircuitBreaker(name))
        admin_id = "tg:user:42"  # msg.user_id format — no platform prefix
        router = make_circuit_router(registry=registry, admin_ids={admin_id})
        msg = make_circuit_msg(user_id="tg:user:42")

        # Act
        response = await router.dispatch(msg)

        # Assert — header and all four service names present
        assert isinstance(response, Response)
        assert "Circuit Status" in response.content
        assert "anthropic" in response.content
        assert "telegram" in response.content
        assert "discord" in response.content
        assert "hub" in response.content

    @pytest.mark.asyncio
    async def test_circuit_command_denied_when_admin_ids_empty(
        self,
    ) -> None:
        """SC-15: Empty admin_user_ids → /circuit denied for all users."""
        # Arrange
        registry = CircuitRegistry()
        registry.register(CircuitBreaker("anthropic"))
        router = make_circuit_router(registry=registry, admin_ids=set())
        msg = make_circuit_msg(user_id="tg:user:42")

        # Act
        response = await router.dispatch(msg)

        # Assert — empty admin set blocks all users (fail-closed)
        assert isinstance(response, Response)
        assert "admin-only" in response.content.lower()


class TestCircuitCommandOpenState:
    """OPEN circuits surface a 'retry in Xs' hint in the status table (SC-15)."""

    @pytest.mark.asyncio
    async def test_circuit_command_shows_retry_after_for_open_circuit(
        self,
    ) -> None:
        """SC-15: OPEN circuit shows 'retry in Xs' in the table."""
        # Arrange
        registry = make_registry_with_open("anthropic")
        admin_id = "tg:user:42"  # msg.user_id format — no platform prefix
        router = make_circuit_router(registry=registry, admin_ids={admin_id})
        msg = make_circuit_msg(user_id="tg:user:42")

        # Act
        response = await router.dispatch(msg)

        # Assert — anthropic row present; retry hint visible
        assert isinstance(response, Response)
        assert "anthropic" in response.content
        assert "retry in" in response.content.lower()


class TestCircuitCommandNoRegistry:
    """Missing registry → informational 'not configured' message (SC-15)."""

    @pytest.mark.asyncio
    async def test_circuit_command_no_registry(self) -> None:
        """No circuit_registry → 'Circuit breaker not configured.'"""
        # Arrange — admin is set but no registry provided
        router = make_circuit_router(registry=None, admin_ids={"tg:user:42"})
        msg = make_circuit_msg(user_id="tg:user:42")

        # Act
        response = await router.dispatch(msg)

        # Assert
        assert isinstance(response, Response)
        assert "not configured" in response.content.lower()


# ---------------------------------------------------------------------------
# Slice 5 — TOML plugins config parsing
# ---------------------------------------------------------------------------


class TestPluginsConfigFromToml:
    """load_agent_config() parses the [plugins] TOML section."""

    def test_plugins_enabled_from_toml(self, tmp_path: Path) -> None:
        # Arrange — write a TOML with [plugins] enabled list
        toml_content = make_minimal_toml(
            extra=textwrap.dedent(
                """
                [plugins]
                enabled = ["echo", "weather"]
                """
            )
        )
        (tmp_path / "test_agent.toml").write_text(toml_content)

        # Act
        agent = load_agent_config("test_agent", agents_dir=tmp_path)

        # Assert
        assert agent.plugins_enabled == ("echo", "weather")

    def test_plugins_enabled_defaults_to_empty(self, tmp_path: Path) -> None:
        # Arrange — no [plugins] section at all
        toml_content = make_minimal_toml()
        (tmp_path / "test_agent.toml").write_text(toml_content)

        # Act
        agent = load_agent_config("test_agent", agents_dir=tmp_path)

        # Assert — absent [plugins] section → empty tuple (default-open)
        assert agent.plugins_enabled == ()

    def test_command_config_still_parsed_for_builtins(self, tmp_path: Path) -> None:
        # Arrange — [commands] section is still supported for builtin overrides
        toml_content = make_minimal_toml(
            extra=textwrap.dedent(
                """
                [commands."/help"]
                builtin = true
                description = "List available commands"
                """
            )
        )
        (tmp_path / "test_agent.toml").write_text(toml_content)

        # Act
        agent = load_agent_config("test_agent", agents_dir=tmp_path)

        # Assert — commands dict contains the builtin entry
        assert hasattr(agent, "commands")
        assert "/help" in agent.commands
        help_cfg = agent.commands["/help"]
        assert isinstance(help_cfg, CommandConfig)
        assert help_cfg.builtin is True
        assert help_cfg.description == "List available commands"


# ---------------------------------------------------------------------------
# msg_manager injection — unknown command returns TOML string
# ---------------------------------------------------------------------------


class TestMsgManagerInjectionUnknownCommand:
    """CommandRouter with a real MessageManager returns the TOML 'unknown_command'
    string instead of the hardcoded fallback."""

    @pytest.mark.asyncio
    async def test_unknown_command_returns_toml_string(self, tmp_path: Path) -> None:
        # Arrange
        mm = MessageManager(TOML_PATH)
        plugins_dir = Path(tempfile.mkdtemp())
        loader = PluginLoader(plugins_dir)
        router = CommandRouter(
            plugin_loader=loader, enabled_plugins=[], msg_manager=mm
        )
        msg = make_message(content="/unknown_cmd")

        # Act
        response = await router.dispatch(msg)

        # Assert — response matches the TOML template (with command_name substituted)
        expected = mm.get("unknown_command", command_name="/unknown_cmd")
        assert isinstance(response, Response)
        assert response.content == expected
