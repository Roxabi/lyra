"""Tests for the command router (issue #66, updated #106, #99).

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
  Issue #99 — Bare URL detection (TestBareUrlDetection)
  Issue #99 — Session command registry (TestSessionCommands)
"""

from __future__ import annotations

import asyncio
import dataclasses as _dataclasses
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lyra.core.agent import Agent, AgentBase, load_agent_config
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.command_loader import CommandLoader
from lyra.core.command_parser import CommandParser
from lyra.core.command_router import CommandConfig, CommandRouter
from lyra.core.hub import Hub
from lyra.core.message import (
    InboundMessage,
    OutboundMessage,
    Platform,
    Response,
)
from lyra.core.messages import MessageManager
from lyra.core.pool import Pool
from lyra.core.trust import TrustLevel

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
    *,
    is_admin: bool = False,
) -> InboundMessage:
    """Build a minimal InboundMessage for testing.

    Auto-parses CommandContext and attaches it to the message, mirroring
    what the Hub pipeline does, so tests that call dispatch() or is_command()
    directly work without going through Hub.
    """
    _parser = CommandParser()
    cmd_ctx = _parser.parse(content)
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
        is_admin=is_admin,
        command=cmd_ctx,  # type: ignore[call-arg]  # field added in #153
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


def make_router(
    tmp_path: Path,
    enabled: list[str] | None = None,
    patterns: dict | None = None,
) -> CommandRouter:
    """Build a CommandRouter with the echo plugin loaded."""
    plugins_dir = make_echo_plugin_dir(tmp_path)
    loader = CommandLoader(plugins_dir)
    loader.load("echo")
    effective = enabled if enabled is not None else ["echo"]
    _patterns = patterns if patterns is not None else {"bare_url": True}
    return CommandRouter(
        command_loader=loader, enabled_plugins=effective, patterns=_patterns
    )


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
        assert "List available commands" in response.content  # type: ignore[union-attr]


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
        assert "unknown command" in response.content.lower()  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_dispatch_plugin_without_pool_raises(self, tmp_path: Path) -> None:
        # Arrange — pool is omitted; plugin commands require a real Pool
        router = make_router(tmp_path)
        msg = make_message(content="/echo hi")

        # Act / Assert
        with pytest.raises(TypeError, match="requires a Pool"):
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
        agent._command_loader.load("echo")
        agent._effective_plugins = ["echo"]
        agent._plugin_mtimes = agent._record_plugin_mtimes()
        agent.command_router = CommandRouter(
            agent._command_loader, agent._effective_plugins
        )

        # Verify initial state — /echo is reachable
        plugin_cmds = agent._command_loader.get_commands(["echo"])
        assert "/echo" in plugin_cmds

        # Act — touch handlers.py to simulate an mtime change, then reload
        handlers_path = plugins_dir / "echo" / "handlers.py"
        import os

        new_mtime = handlers_path.stat().st_mtime + 1
        os.utime(handlers_path, (new_mtime, new_mtime))
        agent._plugin_mtimes["echo"] = new_mtime - 2  # make it appear stale

        agent._maybe_reload()

        # Assert — command_router was rebuilt and echo plugin commands are available
        plugin_cmds_after = agent._command_loader.get_commands(["echo"])
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
) -> CommandRouter:
    """Build a CommandRouter with circuit_registry set."""
    plugins_dir = Path(tempfile.mkdtemp())
    loader = CommandLoader(plugins_dir)
    return CommandRouter(
        command_loader=loader,
        enabled_plugins=[],
        circuit_registry=registry,
    )


def make_circuit_msg(
    user_id: str = "tg:user:42", *, is_admin: bool = False
) -> InboundMessage:
    """Build a /circuit InboundMessage from a Telegram user."""
    _parser = CommandParser()
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
        is_admin=is_admin,
        command=_parser.parse("/circuit"),  # type: ignore[call-arg]  # field added in #153
    )


class TestCircuitCommandAdminCheck:
    """Non-admin sender is denied; admin sender receives the status table (SC-15)."""

    @pytest.mark.asyncio
    async def test_circuit_command_denied_for_non_admin(self) -> None:
        """SC-15: Non-admin sender → 'This command is admin-only.'"""
        # Arrange
        registry = CircuitRegistry()
        registry.register(CircuitBreaker("anthropic"))
        router = make_circuit_router(registry=registry)
        msg = make_circuit_msg(user_id="tg:user:42")  # is_admin=False by default

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
        router = make_circuit_router(registry=registry)
        msg = make_circuit_msg(user_id="tg:user:42", is_admin=True)

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
    async def test_circuit_command_denied_when_not_admin(
        self,
    ) -> None:
        """SC-15: msg.is_admin=False → /circuit denied (fail-closed)."""
        # Arrange
        registry = CircuitRegistry()
        registry.register(CircuitBreaker("anthropic"))
        router = make_circuit_router(registry=registry)
        msg = make_circuit_msg(user_id="tg:user:42")  # is_admin=False by default

        # Act
        response = await router.dispatch(msg)

        # Assert — non-admin msg.is_admin=False blocks access
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
        router = make_circuit_router(registry=registry)
        msg = make_circuit_msg(user_id="tg:user:42", is_admin=True)

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
        # Arrange — no registry provided; msg.is_admin=True grants access
        router = make_circuit_router(registry=None)
        msg = make_circuit_msg(user_id="tg:user:42", is_admin=True)

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

    def test_commands_enabled_from_toml(self, tmp_path: Path) -> None:
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
        assert agent.commands_enabled == ("echo", "weather")

    def test_commands_enabled_defaults_to_empty(self, tmp_path: Path) -> None:
        # Arrange — no [plugins] section at all
        toml_content = make_minimal_toml()
        (tmp_path / "test_agent.toml").write_text(toml_content)

        # Act
        agent = load_agent_config("test_agent", agents_dir=tmp_path)

        # Assert — absent [plugins] section → empty tuple (default-open)
        assert agent.commands_enabled == ()

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
        loader = CommandLoader(plugins_dir)
        router = CommandRouter(
            command_loader=loader, enabled_plugins=[], msg_manager=mm
        )
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
    with_holder: bool = True,
) -> CommandRouter:
    """Build a CommandRouter with runtime_config_holder set."""
    from lyra.core.runtime_config import RuntimeConfig, RuntimeConfigHolder

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(exist_ok=True)
    loader = CommandLoader(plugins_dir)
    holder = RuntimeConfigHolder(RuntimeConfig()) if with_holder else None
    return CommandRouter(
        command_loader=loader,
        enabled_plugins=[],
        runtime_config_holder=holder,
    )


def make_config_msg(
    user_id: str = "tg:user:42", content: str = "/config", *, is_admin: bool = False
) -> InboundMessage:
    """Build a /config InboundMessage."""
    _parser = CommandParser()
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
        is_admin=is_admin,
        command=_parser.parse(content),  # type: ignore[call-arg]  # field added in #153
    )


class TestConfigCommand:
    """/config builtin — admin gate, show, set, reset (issue #135)."""

    @pytest.mark.asyncio
    async def test_config_non_admin_denied(self, tmp_path: Path) -> None:
        """Non-admin sender → 'This command is admin-only.'"""
        # Arrange — msg.is_admin=False (default) denies access
        router = make_config_router(tmp_path)
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
        router = make_config_router(tmp_path)
        msg = make_config_msg(user_id="tg:user:42", content="/config", is_admin=True)

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
        monkeypatch.setattr("lyra.core.agent_config._AGENTS_DIR", tmp_path)
        router = make_config_router(tmp_path)
        msg = make_config_msg(
            user_id="tg:user:42", content="/config style=detailed", is_admin=True
        )

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
        router = make_config_router(tmp_path)
        msg = make_config_msg(
            user_id="tg:user:42", content="/config foo=bar", is_admin=True
        )

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
        monkeypatch.setattr("lyra.core.agent_config._AGENTS_DIR", tmp_path)
        router = make_config_router(tmp_path)
        msg = make_config_msg(
            user_id="tg:user:42", content="/config reset", is_admin=True
        )

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
        monkeypatch.setattr("lyra.core.agent_config._AGENTS_DIR", tmp_path)
        router = make_config_router(tmp_path)
        msg = make_config_msg(
            user_id="tg:user:42", content="/config reset style", is_admin=True
        )

        # Act
        response = await router.dispatch(msg)

        # Assert
        assert isinstance(response, Response)
        assert "Reset: style = (default). Saved." in response.content

    @pytest.mark.asyncio
    async def test_config_reset_unknown_key(self, tmp_path: Path) -> None:
        """Admin /config reset unknown_key → error with 'Unknown config key'"""
        # Arrange
        router = make_config_router(tmp_path)
        msg = make_config_msg(
            user_id="tg:user:42", content="/config reset unknown_key", is_admin=True
        )

        # Act
        response = await router.dispatch(msg)

        # Assert
        assert isinstance(response, Response)
        assert "Unknown config key" in response.content

    @pytest.mark.asyncio
    async def test_config_no_holder(self, tmp_path: Path) -> None:
        """/config when runtime_config_holder is None → not available message."""
        # Arrange — router built without a holder
        router = make_config_router(tmp_path, with_holder=False)
        msg = make_config_msg(user_id="tg:user:42", content="/config", is_admin=True)

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
    loader = CommandLoader(plugins_dir)
    return CommandRouter(
        command_loader=loader,
        enabled_plugins=[],
        workspaces=workspaces,
    )


class TestWorkspaceCommands:
    """Workspace slash commands switch cwd, clear history, re-submit args."""

    def test_workspace_builtin_registered(self, tmp_path: Path) -> None:
        """/workspace is registered as a builtin."""
        ws_dir = tmp_path / "myws"
        ws_dir.mkdir()
        router = make_workspace_router(tmp_path, {"myws": ws_dir})
        assert "/workspace" in router._builtins

    def test_individual_workspace_names_not_registered_as_builtins(
        self, tmp_path: Path
    ) -> None:
        """Workspace names are NOT individually registered as builtins."""
        ws_dir = tmp_path / "myws"
        ws_dir.mkdir()
        router = make_workspace_router(tmp_path, {"myws": ws_dir})
        assert "/myws" not in router._builtins

    @pytest.mark.asyncio
    async def test_workspace_ls_returns_list(self, tmp_path: Path) -> None:
        """/workspace ls returns the list of configured workspaces."""
        ws_dir = tmp_path / "proj"
        ws_dir.mkdir()
        router = make_workspace_router(tmp_path, {"proj": ws_dir})
        msg = make_message(content="/workspace ls", is_admin=True)

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert "proj" in response.content

    @pytest.mark.asyncio
    async def test_workspace_list_alias(self, tmp_path: Path) -> None:
        """/workspace list is an alias for /workspace ls."""
        ws_dir = tmp_path / "proj"
        ws_dir.mkdir()
        router = make_workspace_router(tmp_path, {"proj": ws_dir})
        msg = make_message(content="/workspace list", is_admin=True)

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert "proj" in response.content

    @pytest.mark.asyncio
    async def test_workspace_no_args_returns_list(self, tmp_path: Path) -> None:
        """/workspace with no args returns the list of workspaces."""
        ws_dir = tmp_path / "proj"
        ws_dir.mkdir()
        router = make_workspace_router(tmp_path, {"proj": ws_dir})
        msg = make_message(content="/workspace", is_admin=True)

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert "proj" in response.content

    @pytest.mark.asyncio
    async def test_workspace_dispatch_returns_context_response(
        self, tmp_path: Path
    ) -> None:
        """Dispatching /workspace <name> switches cwd and returns confirmation."""
        ws_dir = tmp_path / "proj"
        ws_dir.mkdir()
        router = make_workspace_router(tmp_path, {"proj": ws_dir})
        msg = make_message(content="/workspace proj", is_admin=True)

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
        """Remaining text after /workspace <name> is re-submitted via pool.submit."""
        ws_dir = tmp_path / "proj"
        ws_dir.mkdir()
        router = make_workspace_router(tmp_path, {"proj": ws_dir})
        msg = make_message(
            content="/workspace proj what's the last commit?", is_admin=True
        )

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
        """Without a pool, /workspace <name> still returns confirmation."""
        ws_dir = tmp_path / "solo"
        ws_dir.mkdir()
        router = make_workspace_router(tmp_path, {"solo": ws_dir})
        msg = make_message(content="/workspace solo", is_admin=True)

        response = await router.dispatch(msg, pool=None)

        assert isinstance(response, Response)
        assert "Workspace: solo" in response.content

    @pytest.mark.asyncio
    async def test_unknown_workspace_name_returns_error(self, tmp_path: Path) -> None:
        """/workspace <unknown> returns an error listing available names."""
        ws_dir = tmp_path / "myws"
        ws_dir.mkdir()
        router = make_workspace_router(tmp_path, {"myws": ws_dir})
        msg = make_message(content="/workspace otherws", is_admin=True)

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert response.content is not None
        assert "unknown workspace" in response.content.lower()
        assert "myws" in response.content


# ---------------------------------------------------------------------------
# T13 — CommandParser integration: is_command reads msg.command (#153)
# ---------------------------------------------------------------------------


def make_message_with_command(content: str, **kwargs) -> InboundMessage:
    """Build InboundMessage with CommandContext pre-attached (as Hub pipeline would)."""
    parser = CommandParser()
    cmd_ctx = parser.parse(content)
    msg = make_message(content, **kwargs)
    if cmd_ctx is not None:
        msg = _dataclasses.replace(msg, command=cmd_ctx)
    return msg


class TestIsCommandReadsCommandContext:
    """is_command() reads msg.command, not regex on text (#153 S3)."""

    def test_is_command_true_when_command_set(self, tmp_path: Path) -> None:
        # Arrange
        router = make_router(tmp_path)
        msg = make_message_with_command("/help")

        # Act / Assert
        assert router.is_command(msg) is True

    def test_is_command_false_when_no_command(self, tmp_path: Path) -> None:
        # Arrange
        router = make_router(tmp_path)
        msg = make_message("hello world")

        # Act / Assert
        assert router.is_command(msg) is False

    def test_is_command_true_for_bang_prefix(self, tmp_path: Path) -> None:
        # Arrange
        router = make_router(tmp_path)
        msg = make_message_with_command("!test")

        # Act / Assert — ! prefix is a valid command prefix
        assert router.is_command(msg) is True

    def test_get_command_name_returns_full_name(self, tmp_path: Path) -> None:
        # Arrange
        router = make_router(tmp_path)
        msg = make_message_with_command("/imagine cat")

        # Act / Assert
        assert router.get_command_name(msg) == "/imagine"

    def test_get_command_name_bang_prefix(self, tmp_path: Path) -> None:
        # Arrange
        router = make_router(tmp_path)
        msg = make_message_with_command("!help")

        # Act / Assert
        assert router.get_command_name(msg) == "!help"

    def test_get_command_name_none_when_no_command(self, tmp_path: Path) -> None:
        # Arrange
        router = make_router(tmp_path)
        msg = make_message("hello")

        # Act / Assert
        assert router.get_command_name(msg) is None


class TestBangPrefixFallthrough:
    """!-prefixed unknown commands return None sentinel — fallthrough to pool (#153)."""

    @pytest.mark.asyncio
    async def test_unknown_bang_command_returns_none(self, tmp_path: Path) -> None:
        # Arrange
        router = make_router(tmp_path)
        msg = make_message_with_command("!unknown_command_xyz")

        # Act
        result = await router.dispatch(msg, pool=None)

        # Assert — None sentinel triggers pool fallthrough in Hub pipeline
        assert result is None

    @pytest.mark.asyncio
    async def test_unknown_slash_command_returns_response(self, tmp_path: Path) -> None:
        # Arrange — unknown / command still returns an error Response, not None
        router = make_router(tmp_path)
        msg = make_message_with_command("/totally_unknown_xyz123")

        # Act
        result = await router.dispatch(msg, pool=None)

        # Assert — slash unknown: returns a Response (not None)
        assert result is not None
        assert hasattr(result, "content")

    @pytest.mark.asyncio
    async def test_unknown_bang_command_falls_through_to_pool_in_hub(
        self, tmp_path: Path
    ) -> None:
        """Hub integration: !unknown_xyz → dispatch() returns None sentinel →
        _dispatch_command() calls _submit_to_pool() → agent.process() reached."""
        # Arrange — full hub with a capturing agent that has a command router
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
        # Attach a router that knows /echo but has no !-prefixed commands
        agent.command_router = make_router(tmp_path)
        hub.register_agent(agent)

        class SilentAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                pass

            async def send_streaming(
                self, original_msg: InboundMessage, chunks: object
            ) -> None:
                pass

        hub.register_adapter(Platform.TELEGRAM, "main", SilentAdapter())  # type: ignore[arg-type]
        hub.register_binding(
            Platform.TELEGRAM, "main", "chat:42", "lyra", "telegram:main:chat:42"
        )

        # Act — send !unknown_xyz; hub parses CommandContext internally
        await hub.bus.put(make_message(content="!unknown_xyz"))

        try:
            await asyncio.wait_for(hub.run(), timeout=0.3)
        except asyncio.TimeoutError:
            pass

        # Assert — agent.process() called via pool (not "Unknown command" response)
        assert len(process_calls) == 1
        assert process_calls[0].text == "!unknown_xyz"


# ---------------------------------------------------------------------------
# Issue #99 — Bare URL detection (T1)
# ---------------------------------------------------------------------------


class TestBareUrlDetection:
    """prepare() rewrites bare URLs to /add; is_command() detects them."""

    def test_https_url_is_detected(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        msg = make_message(content="https://example.com/article")
        assert router.is_command(msg) is True

    def test_http_url_is_detected(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        msg = make_message(content="http://example.com")
        assert router.is_command(msg) is True

    def test_prepare_returns_add_command(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        msg = make_message(content="https://example.com/page")
        prepared = router.prepare(msg)
        assert prepared.command is not None
        assert prepared.command.name == "add"
        assert prepared.command.prefix == "/"

    def test_prepare_sets_url_as_args(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        url = "https://example.com/page"
        msg = make_message(content=url)
        prepared = router.prepare(msg)
        assert prepared.command is not None
        assert prepared.command.args == url

    def test_url_with_surrounding_text_not_rewritten(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        msg = make_message(content="check this https://example.com")
        # msg.command is None (no slash prefix) and not a bare URL
        prepared = router.prepare(msg)
        assert prepared.command is None

    def test_url_with_surrounding_text_is_not_command(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        msg = make_message(content="check this https://example.com")
        assert router.is_command(msg) is False

    def test_regular_command_unaffected_by_prepare(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        msg = make_message(content="/help")
        prepared = router.prepare(msg)
        assert prepared.command is not None
        assert prepared.command.name == "help"

    def test_get_command_name_returns_add_for_bare_url(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        msg = make_message(content="https://example.com")
        prepared = router.prepare(msg)
        assert router.get_command_name(prepared) == "/add"

    def test_plain_text_not_detected_as_command(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        msg = make_message(content="hello world")
        assert router.is_command(msg) is False

    @pytest.mark.asyncio
    async def test_dispatch_bare_url_routes_to_add_session(
        self, tmp_path: Path
    ) -> None:
        """dispatch() with a bare URL calls the /add session handler."""
        from unittest.mock import AsyncMock

        router = make_router(tmp_path)
        handler = AsyncMock(return_value=Response(content="added"))
        router.register_session_command("add", handler)
        # Provide a mock session driver so the session path is taken
        from unittest.mock import MagicMock as MM

        router._session_driver = MM()

        msg = make_message(content="https://example.com/test")
        pool = MagicMock(spec=Pool)
        response = await router.dispatch(msg, pool=pool)
        assert response is not None
        assert response.content == "added"
        handler.assert_called_once()


class TestBareUrlPatternsDisabled:
    """patterns={"bare_url": False} disables bare URL rewriting (opt-in model)."""

    def test_bare_url_is_not_detected_when_disabled(self, tmp_path: Path) -> None:
        # Arrange — router with bare_url explicitly disabled
        router = make_router(tmp_path, patterns={"bare_url": False})
        msg = make_message(content="https://example.com/article")

        # Act
        result = router.is_command(msg)

        # Assert — bare URL passthrough: is_command returns False
        assert result is False

    def test_prepare_returns_message_unchanged_when_disabled(
        self, tmp_path: Path
    ) -> None:
        # Arrange
        router = make_router(tmp_path, patterns={"bare_url": False})
        url = "https://example.com/page"
        msg = make_message(content=url)

        # Act
        prepared = router.prepare(msg)

        # Assert — no CommandContext added; message passes through
        assert prepared.command is None

    def test_default_patterns_disables_bare_url(self, tmp_path: Path) -> None:
        # Arrange — router with no patterns (empty dict = default-off)
        plugins_dir = make_echo_plugin_dir(tmp_path)
        loader = CommandLoader(plugins_dir)
        loader.load("echo")
        router = CommandRouter(command_loader=loader, enabled_plugins=["echo"])
        msg = make_message(content="https://example.com")

        # Act
        result = router.is_command(msg)

        # Assert — default is opt-in (False): bare URL not treated as command
        assert result is False

    def test_bare_url_enabled_explicitly(self, tmp_path: Path) -> None:
        # Arrange — router with bare_url explicitly enabled
        router = make_router(tmp_path, patterns={"bare_url": True})
        msg = make_message(content="https://example.com")

        # Act
        result = router.is_command(msg)

        # Assert — explicit opt-in: bare URL is a command
        assert result is True


# ---------------------------------------------------------------------------
# Issue #99 — Session command registry (T2)
# ---------------------------------------------------------------------------


class TestSessionCommands:
    """SessionCommandEntry registration, dispatch, timeout, and help listing."""

    def _make_router_with_session(
        self,
        tmp_path: Path,
        driver: object = None,
        handler_response: str = "session result",
    ):
        from unittest.mock import AsyncMock, MagicMock

        router = make_router(tmp_path)
        router._session_driver = driver or MagicMock()

        async_handler = AsyncMock(return_value=Response(content=handler_response))
        router.register_session_command(
            "mysession",
            async_handler,
            description="My session command",
            timeout=30.0,
        )
        return router, async_handler

    @pytest.mark.asyncio
    async def test_session_command_dispatched(self, tmp_path: Path) -> None:
        router, handler = self._make_router_with_session(tmp_path)
        msg = make_message(content="/mysession arg1")
        pool = MagicMock(spec=Pool)
        response = await router.dispatch(msg, pool=pool)
        assert response is not None
        assert response.content == "session result"
        handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_session_command_none_driver_returns_degradation(
        self, tmp_path: Path
    ) -> None:
        router = make_router(tmp_path)
        router._session_driver = None

        from unittest.mock import AsyncMock

        async_handler = AsyncMock(return_value=Response(content="never"))
        router.register_session_command("nosession", async_handler)

        msg = make_message(content="/nosession")
        pool = MagicMock(spec=Pool)
        response = await router.dispatch(msg, pool=pool)
        assert response is not None
        assert (
            "anthropic-sdk" in response.content.lower()
            or "session" in response.content.lower()
        )
        handler = async_handler
        handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_session_command_timeout_returns_timeout_message(
        self, tmp_path: Path
    ) -> None:
        import asyncio as _asyncio

        router = make_router(tmp_path)
        router._session_driver = MagicMock()

        async def slow_handler(msg, driver, args, timeout):
            await _asyncio.sleep(10)
            return Response(content="never")

        router.register_session_command("slow", slow_handler, timeout=0.01)

        msg = make_message(content="/slow")
        pool = MagicMock(spec=Pool)
        response = await router.dispatch(msg, pool=pool)
        assert response is not None
        assert "timed out" in response.content.lower()

    def test_register_conflict_with_builtin_raises(self, tmp_path: Path) -> None:
        from unittest.mock import AsyncMock

        router = make_router(tmp_path)
        handler = AsyncMock(return_value=Response(content="x"))
        with pytest.raises(ValueError, match="clashes with a builtin"):
            router.register_session_command("help", handler)

    def test_register_conflict_with_plugin_raises(self, tmp_path: Path) -> None:
        from unittest.mock import AsyncMock

        router = make_router(tmp_path)  # has echo plugin
        handler = AsyncMock(return_value=Response(content="x"))
        with pytest.raises(ValueError, match="clashes with a plugin"):
            router.register_session_command("echo", handler)

    def test_help_includes_session_commands_section(self, tmp_path: Path) -> None:
        from unittest.mock import AsyncMock

        router = make_router(tmp_path)
        handler = AsyncMock(return_value=Response(content="x"))
        router.register_session_command(
            "mything", handler, description="Does the thing"
        )
        from lyra.core.builtin_commands import help_command

        response = help_command(
            router._builtins,
            router._session_handlers,
            router._command_loader,
            router._enabled_plugins,
            router._msg_manager,
        )
        assert "Commands:" in response.content
        assert "/mything" in response.content
        assert "Does the thing" in response.content

    def test_session_args_passed_to_handler(self, tmp_path: Path) -> None:
        import asyncio as _asyncio
        from unittest.mock import MagicMock

        received_args: list[list[str]] = []

        async def capturing_handler(msg, driver, args, timeout):
            received_args.append(args)
            return Response(content="ok")

        router = make_router(tmp_path)
        router._session_driver = MagicMock()
        router.register_session_command("capture", capturing_handler)

        msg = make_message(content="/capture foo bar")
        pool = MagicMock(spec=Pool)

        _asyncio.get_event_loop().run_until_complete(router.dispatch(msg, pool=pool))

        assert received_args == [["foo", "bar"]]
