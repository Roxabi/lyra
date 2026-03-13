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
from unittest.mock import MagicMock

import pytest

from lyra.core.agent import Agent, AgentBase, load_agent_config
from lyra.core.auth import TrustLevel
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.command_router import CommandConfig, CommandRouter
from lyra.core.hub import Hub
from lyra.core.message import (
    InboundMessage,
    OutboundMessage,
    Platform,
    Response,
)
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
    platform: str = "telegram",
    bot_id: str = "main",
    user_id: str = "alice",
) -> InboundMessage:
    """Build a minimal InboundMessage for testing."""
    return InboundMessage(
        id="msg-test-1",
        platform=platform,
        bot_id=bot_id,
        scope_id="chat:42",
        user_id=user_id,
        user_name="Alice",
        is_mention=False,
        text=content,
        text_raw=content,
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 42,
            "topic_id": None,
            "message_id": None,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
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
        "from lyra.core.message import Response, InboundMessage\n"
        "from lyra.core.pool import Pool\n"
        "async def cmd_echo(\n"
        "    msg: InboundMessage, pool: Pool, args: list[str]\n"
        ") -> Response:\n"
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
        pool = Pool(pool_id="test", agent_name="test_agent", ctx=MagicMock())

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
        pool = Pool(pool_id="test", agent_name="test_agent", ctx=MagicMock())

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
            async def process(
                self, msg: InboundMessage, pool: Pool, *, on_intermediate=None
            ) -> Response:
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
        process_calls: list[InboundMessage] = []

        class CapturingAgent(AgentBase):
            async def process(
                self, msg: InboundMessage, pool: Pool, *, on_intermediate=None
            ) -> Response:
                process_calls.append(msg)
                return Response(content="agent reply")

        hub = Hub()

        config = Agent(name="lyra", system_prompt="", memory_namespace="lyra")
        agent = CapturingAgent(config)
        hub.register_agent(agent)

        class CapturingAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                pass

            async def send_streaming(
                self, original_msg: InboundMessage, chunks: object
            ) -> None:
                pass

        hub.register_adapter(Platform.TELEGRAM, "main", CapturingAdapter())  # type: ignore[arg-type]
        hub.register_binding(
            Platform.TELEGRAM, "main", "chat:42", "lyra", "telegram:main:chat:42"
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
        assert process_calls[0].text == "hello, how are you?"

    @pytest.mark.asyncio
    async def test_slash_command_does_not_reach_agent_process(self) -> None:
        """When the router handles a /command, agent.process() is never called."""
        # Arrange
        process_calls: list[InboundMessage] = []

        class TrackingAgent(AgentBase):
            async def process(
                self, msg: InboundMessage, pool: Pool, *, on_intermediate=None
            ) -> Response:
                process_calls.append(msg)
                return Response(content="should not be reached")

        hub = Hub()
        config = Agent(name="lyra", system_prompt="", memory_namespace="lyra")
        agent = TrackingAgent(config)
        hub.register_agent(agent)

        class CapturingAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                pass

            async def send_streaming(
                self, original_msg: InboundMessage, chunks: object
            ) -> None:
                pass

        hub.register_adapter(Platform.TELEGRAM, "main", CapturingAdapter())  # type: ignore[arg-type]
        hub.register_binding(
            Platform.TELEGRAM, "main", "chat:42", "lyra", "telegram:main:chat:42"
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


def make_circuit_msg(user_id: str = "tg:user:42") -> InboundMessage:
    """Build a /circuit InboundMessage from a Telegram user."""
    return InboundMessage(
        id="msg-circuit-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:42",
        user_id=user_id,
        user_name="Admin",
        is_mention=False,
        text="/circuit",
        text_raw="/circuit",
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 42,
            "topic_id": None,
            "message_id": None,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
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
        router = CommandRouter(plugin_loader=loader, enabled_plugins=[], msg_manager=mm)
        msg = make_message(content="/unknown_cmd")

        # Act
        response = await router.dispatch(msg)

        # Assert — response matches the TOML template (with command_name substituted)
        expected = mm.get("unknown_command", command_name="/unknown_cmd")
        assert isinstance(response, Response)
        assert response.content == expected


# ---------------------------------------------------------------------------
# Tests for /stop command (issue #127)
# ---------------------------------------------------------------------------


class TestStopCommand:
    """/stop builtin calls pool.cancel() and returns an empty Response (S4-5)."""

    @pytest.mark.asyncio
    async def test_stop_command_calls_pool_cancel(self, tmp_path: Path) -> None:
        """/stop dispatched with a pool calls pool.cancel()."""
        # Arrange
        router = make_router(tmp_path)
        msg = make_message(content="/stop")

        pool_mock = MagicMock(spec=Pool)
        pool_mock.cancel = MagicMock()

        # Act
        response = await router.dispatch(msg, pool=pool_mock)

        # Assert
        pool_mock.cancel.assert_called_once()
        assert isinstance(response, Response)

    @pytest.mark.asyncio
    async def test_stop_command_returns_empty_response(self, tmp_path: Path) -> None:
        """/stop returns Response(content='') — hub skips the duplicate reply."""
        # Arrange
        router = make_router(tmp_path)
        msg = make_message(content="/stop")

        pool_mock = MagicMock(spec=Pool)
        pool_mock.cancel = MagicMock()

        # Act
        response = await router.dispatch(msg, pool=pool_mock)

        # Assert
        assert isinstance(response, Response)
        assert response.content == ""

    @pytest.mark.asyncio
    async def test_stop_command_with_none_pool_safe(self, tmp_path: Path) -> None:
        """Dispatching /stop with pool=None does not raise."""
        # Arrange
        router = make_router(tmp_path)
        msg = make_message(content="/stop")

        # Act / Assert — must not raise even when pool is absent
        response = await router.dispatch(msg, pool=None)
        assert isinstance(response, Response)


# ---------------------------------------------------------------------------
# Slice 6 — /config command (issue #135)
# ---------------------------------------------------------------------------


def make_config_router(
    tmp_path: Path,
    admin_ids: set[str] | None = None,
    with_holder: bool = True,
) -> CommandRouter:
    """Build a CommandRouter with runtime_config_holder and admin_user_ids set."""
    from lyra.core.runtime_config import RuntimeConfig, RuntimeConfigHolder

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(exist_ok=True)
    loader = PluginLoader(plugins_dir)
    holder = RuntimeConfigHolder(RuntimeConfig()) if with_holder else None
    return CommandRouter(
        plugin_loader=loader,
        enabled_plugins=[],
        admin_user_ids=admin_ids,
        runtime_config_holder=holder,
    )


def make_config_msg(
    user_id: str = "tg:user:42", content: str = "/config"
) -> InboundMessage:
    """Build a /config InboundMessage."""
    return InboundMessage(
        id="msg-config-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:42",
        user_id=user_id,
        user_name="Admin",
        is_mention=False,
        text=content,
        text_raw=content,
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 42,
            "topic_id": None,
            "message_id": None,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
    )


class TestConfigCommand:
    """/config builtin — admin gate, show, set, reset (issue #135)."""

    @pytest.mark.asyncio
    async def test_config_non_admin_denied(self, tmp_path: Path) -> None:
        """Non-admin sender → 'This command is admin-only.'"""
        # Arrange — admin is a different user
        router = make_config_router(tmp_path, admin_ids={"tg:user:999"})
        msg = make_config_msg(user_id="tg:user:42")

        # Act
        response = await router.dispatch(msg)

        # Assert
        assert isinstance(response, Response)
        assert "admin-only" in response.content.lower()

    @pytest.mark.asyncio
    async def test_config_show_all_fields(self, tmp_path: Path) -> None:
        """Admin /config (no args) → table showing all 6 config fields."""
        # Arrange
        router = make_config_router(tmp_path, admin_ids={"tg:user:42"})
        msg = make_config_msg(user_id="tg:user:42", content="/config")

        # Act
        response = await router.dispatch(msg)

        # Assert — all 6 field names appear in the output
        assert isinstance(response, Response)
        assert "style" in response.content
        assert "language" in response.content
        assert "temperature" in response.content
        assert "model" in response.content
        assert "max_steps" in response.content
        assert "extra_instructions" in response.content

    @pytest.mark.asyncio
    async def test_config_set_valid_param(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Admin /config style=detailed → sets param, returns 'Updated:...'"""
        # Arrange — monkeypatch _AGENTS_DIR so save() writes into tmp_path
        monkeypatch.setattr("lyra.core.agent._AGENTS_DIR", tmp_path)
        router = make_config_router(tmp_path, admin_ids={"tg:user:42"})
        msg = make_config_msg(user_id="tg:user:42", content="/config style=detailed")

        # Act
        response = await router.dispatch(msg)

        # Assert
        assert isinstance(response, Response)
        assert "Updated:" in response.content
        assert "style" in response.content

    @pytest.mark.asyncio
    async def test_config_set_unknown_key(self, tmp_path: Path) -> None:
        """Admin /config foo=bar → 'Unknown config key'"""
        # Arrange
        router = make_config_router(tmp_path, admin_ids={"tg:user:42"})
        msg = make_config_msg(user_id="tg:user:42", content="/config foo=bar")

        # Act
        response = await router.dispatch(msg)

        # Assert
        assert isinstance(response, Response)
        assert "Unknown config key" in response.content

    @pytest.mark.asyncio
    async def test_config_reset_all(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Admin /config reset → 'Runtime config reset to defaults.'"""
        # Arrange
        monkeypatch.setattr("lyra.core.agent._AGENTS_DIR", tmp_path)
        router = make_config_router(tmp_path, admin_ids={"tg:user:42"})
        msg = make_config_msg(user_id="tg:user:42", content="/config reset")

        # Act
        response = await router.dispatch(msg)

        # Assert
        assert isinstance(response, Response)
        assert "Runtime config reset to defaults." in response.content

    @pytest.mark.asyncio
    async def test_config_reset_single_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Admin /config reset style → 'Reset: style = (default). Saved.'"""
        # Arrange
        monkeypatch.setattr("lyra.core.agent._AGENTS_DIR", tmp_path)
        router = make_config_router(tmp_path, admin_ids={"tg:user:42"})
        msg = make_config_msg(user_id="tg:user:42", content="/config reset style")

        # Act
        response = await router.dispatch(msg)

        # Assert
        assert isinstance(response, Response)
        assert "Reset: style = (default). Saved." in response.content

    @pytest.mark.asyncio
    async def test_config_reset_unknown_key(self, tmp_path: Path) -> None:
        """Admin /config reset unknown_key → error with 'Unknown config key'"""
        # Arrange
        router = make_config_router(tmp_path, admin_ids={"tg:user:42"})
        msg = make_config_msg(user_id="tg:user:42", content="/config reset unknown_key")

        # Act
        response = await router.dispatch(msg)

        # Assert
        assert isinstance(response, Response)
        assert "Unknown config key" in response.content

    @pytest.mark.asyncio
    async def test_config_no_holder(self, tmp_path: Path) -> None:
        """/config when runtime_config_holder is None → not available message."""
        # Arrange — router built without a holder
        router = make_config_router(
            tmp_path, admin_ids={"tg:user:42"}, with_holder=False
        )
        msg = make_config_msg(user_id="tg:user:42", content="/config")

        # Act
        response = await router.dispatch(msg)

        # Assert
        assert isinstance(response, Response)
        assert "not available" in response.content.lower()


# ---------------------------------------------------------------------------
# Slice — /clear and /new commands
# ---------------------------------------------------------------------------


class TestClearCommand:
    """/clear and /new clear history and call pool.reset_session()."""

    @pytest.mark.asyncio
    async def test_clear_clears_sdk_history(self, tmp_path: Path) -> None:
        """/clear empties pool.sdk_history."""
        router = make_router(tmp_path)
        msg = make_message(content="/clear")

        pool_mock = MagicMock(spec=Pool)
        pool_mock.sdk_history = MagicMock()
        pool_mock.history = MagicMock()

        async def _noop() -> None:
            pass

        pool_mock.reset_session = _noop

        await router.dispatch(msg, pool=pool_mock)

        pool_mock.sdk_history.clear.assert_called_once()
        pool_mock.history.clear.assert_called_once()

    @pytest.mark.asyncio
    async def test_clear_calls_reset_session(self, tmp_path: Path) -> None:
        """/clear awaits pool.reset_session() to flush the backend session."""
        router = make_router(tmp_path)
        msg = make_message(content="/clear")

        reset_called = False

        async def _reset() -> None:
            nonlocal reset_called
            reset_called = True

        pool_mock = MagicMock(spec=Pool)
        pool_mock.sdk_history = MagicMock()
        pool_mock.history = MagicMock()
        pool_mock.reset_session = _reset

        await router.dispatch(msg, pool=pool_mock)

        assert reset_called, "pool.reset_session() was not awaited by /clear"

    @pytest.mark.asyncio
    async def test_new_alias_calls_reset_session(self, tmp_path: Path) -> None:
        """/new is an alias for /clear — also calls pool.reset_session()."""
        router = make_router(tmp_path)
        msg = make_message(content="/new")

        reset_called = False

        async def _reset() -> None:
            nonlocal reset_called
            reset_called = True

        pool_mock = MagicMock(spec=Pool)
        pool_mock.sdk_history = MagicMock()
        pool_mock.history = MagicMock()
        pool_mock.reset_session = _reset

        await router.dispatch(msg, pool=pool_mock)

        assert reset_called

    @pytest.mark.asyncio
    async def test_clear_with_no_pool_safe(self, tmp_path: Path) -> None:
        """/clear with pool=None returns a safe response without raising."""
        router = make_router(tmp_path)
        msg = make_message(content="/clear")

        response = await router.dispatch(msg, pool=None)

        assert isinstance(response, Response)
        assert "no active session" in response.content.lower()

    @pytest.mark.asyncio
    async def test_clear_returns_confirmation(self, tmp_path: Path) -> None:
        """/clear response confirms history was cleared."""
        router = make_router(tmp_path)
        msg = make_message(content="/clear")

        async def _noop() -> None:
            pass

        pool_mock = MagicMock(spec=Pool)
        pool_mock.sdk_history = MagicMock()
        pool_mock.history = MagicMock()
        pool_mock.reset_session = _noop

        response = await router.dispatch(msg, pool=pool_mock)

        assert isinstance(response, Response)
        assert "cleared" in response.content.lower()


# ---------------------------------------------------------------------------
# Slice — workspace commands
# ---------------------------------------------------------------------------


def make_workspace_router(tmp_path: Path, workspaces: dict[str, Path]) -> CommandRouter:
    """Build a CommandRouter with workspace commands registered."""
    import tempfile as _tempfile

    plugins_dir = Path(_tempfile.mkdtemp())
    loader = PluginLoader(plugins_dir)
    return CommandRouter(
        plugin_loader=loader,
        enabled_plugins=[],
        workspaces=workspaces,
        admin_user_ids={"alice"},  # make_message() default user_id
    )


class TestWorkspaceCommands:
    """Workspace slash commands switch cwd, clear history, re-submit args."""

    def test_workspace_commands_appear_in_builtins(self, tmp_path: Path) -> None:
        """Workspace names are registered in _builtins."""
        ws_dir = tmp_path / "myws"
        ws_dir.mkdir()
        router = make_workspace_router(tmp_path, {"myws": ws_dir})
        assert "/myws" in router._builtins

    @pytest.mark.asyncio
    async def test_workspace_dispatch_returns_context_response(
        self, tmp_path: Path
    ) -> None:
        """Dispatching a workspace command returns 'Context: <path>'."""
        ws_dir = tmp_path / "proj"
        ws_dir.mkdir()
        router = make_workspace_router(tmp_path, {"proj": ws_dir})
        msg = make_message(content="/proj")

        switch_called_with: list[Path] = []

        async def _fake_switch(cwd: Path) -> None:
            switch_called_with.append(cwd)

        pool_mock = MagicMock(spec=Pool)
        pool_mock.switch_workspace = _fake_switch
        pool_mock.submit = MagicMock()

        response = await router.dispatch(msg, pool=pool_mock)

        assert isinstance(response, Response)
        assert "Workspace: proj" in response.content
        assert switch_called_with == [ws_dir]

    @pytest.mark.asyncio
    async def test_workspace_dispatch_resubmits_remaining_args(
        self, tmp_path: Path
    ) -> None:
        """Remaining args after workspace command are re-submitted via pool.submit."""
        ws_dir = tmp_path / "proj"
        ws_dir.mkdir()
        router = make_workspace_router(tmp_path, {"proj": ws_dir})
        msg = make_message(content="/proj what's the last commit?")

        async def _fake_switch(cwd: Path) -> None:
            pass

        submitted: list = []
        pool_mock = MagicMock(spec=Pool)
        pool_mock.switch_workspace = _fake_switch
        pool_mock.submit = MagicMock(side_effect=submitted.append)

        await router.dispatch(msg, pool=pool_mock)

        pool_mock.submit.assert_called_once()
        submitted_msg = submitted[0]
        assert submitted_msg.text == "what's the last commit?"

    @pytest.mark.asyncio
    async def test_workspace_dispatch_no_pool_still_returns_context(
        self, tmp_path: Path
    ) -> None:
        """Without a pool, workspace command still returns 'Context: ...'."""
        ws_dir = tmp_path / "solo"
        ws_dir.mkdir()
        router = make_workspace_router(tmp_path, {"solo": ws_dir})
        msg = make_message(content="/solo")

        response = await router.dispatch(msg, pool=None)

        assert isinstance(response, Response)
        assert "Workspace: solo" in response.content

    @pytest.mark.asyncio
    async def test_unknown_command_not_treated_as_workspace(
        self, tmp_path: Path
    ) -> None:
        """Commands not in workspaces dict fall through to unknown-command handling."""
        ws_dir = tmp_path / "myws"
        ws_dir.mkdir()
        router = make_workspace_router(tmp_path, {"myws": ws_dir})
        msg = make_message(content="/otherws")

        response = await router.dispatch(msg)

        assert "unknown command" in response.content.lower()
