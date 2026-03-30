"""Tests for command detection and basic dispatch routing (issue #66, #106).

Covers:
  Slice 1 — Command detection (TestIsCommand)
  Slice 2 — Dispatch to builtins and plugins (TestDispatchHelp,
             TestDispatchUnknownCommand, TestDispatchRoutesToPlugin)
  Slice 3 — Hot-reload + hub passthrough (TestHotReloadUpdatesCommands,
             TestPassthroughNonCommandInHub)
  msg_manager injection (TestMsgManagerInjectionUnknownCommand)
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

if TYPE_CHECKING:
    from lyra.core.hub.hub_protocol import ChannelAdapter

import pytest

from lyra.core.agent import Agent, AgentBase
from lyra.core.commands.command_loader import CommandLoader
from lyra.core.commands.command_router import CommandRouter
from lyra.core.message import (
    InboundMessage,
    OutboundMessage,
    Platform,
    Response,
)
from lyra.core.messages import MessageManager
from lyra.core.pool import Pool

from .conftest import make_echo_plugin_dir, make_message, make_router

TOML_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "lyra"
    / "config"
    / "messages.toml"
)


# ---------------------------------------------------------------------------
# Slice 1 — Command Detection
# ---------------------------------------------------------------------------


class TestIsCommand:
    """is_command() detects slash-prefixed messages (SC-1)."""

    def test_is_command_detects_slash_prefix(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        msg = make_message(content="/help")
        assert router.is_command(msg) is True

    def test_is_command_ignores_plain_text(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        msg = make_message(content="hello")
        assert router.is_command(msg) is False

    def test_is_command_ignores_slash_in_middle(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        msg = make_message(content="not a /command")
        assert router.is_command(msg) is False

    def test_is_command_empty_string_is_not_command(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        msg = make_message(content="")
        assert router.is_command(msg) is False

    def test_is_command_bare_slash_is_not_command(self, tmp_path: Path) -> None:
        # Arrange — a lone "/" with no name is ambiguous; spec requires a name after "/"
        router = make_router(tmp_path)
        msg = make_message(content="/")
        # Assert — "/" alone should NOT be treated as a command (no name follows)
        assert router.is_command(msg) is False


# ---------------------------------------------------------------------------
# Slice 2 — Dispatch to builtins and plugins
# ---------------------------------------------------------------------------


class TestDispatchHelp:
    """dispatch() for /help returns a listing of all registered commands (SC-4)."""

    @pytest.mark.asyncio
    async def test_dispatch_help_lists_commands(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        msg = make_message(content="/help")
        response = await router.dispatch(msg)
        assert isinstance(response, Response)
        assert "/help" in response.content
        assert "/echo" in response.content

    @pytest.mark.asyncio
    async def test_dispatch_help_includes_builtin_description(
        self, tmp_path: Path
    ) -> None:
        router = make_router(tmp_path)
        msg = make_message(content="/help")
        response = await router.dispatch(msg)
        assert response is not None
        assert response.content is not None
        assert "List available commands" in response.content


class TestDispatchUnknownCommand:
    """dispatch() for an unrecognised command returns a helpful error (SC-5)."""

    @pytest.mark.asyncio
    async def test_dispatch_unknown_command(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        msg = make_message(content="/pizza")
        response = await router.dispatch(msg)
        assert isinstance(response, Response)
        assert "unknown command" in response.content.lower()
        assert "/help" in response.content


class TestDispatchRoutesToPlugin:
    """dispatch() routes a plugin command to its handler and returns the result."""

    @pytest.mark.asyncio
    async def test_dispatch_routes_to_plugin(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        msg = make_message(content="/echo hi")
        pool = Pool(pool_id="test", agent_name="test_agent", ctx=MagicMock())
        response = await router.dispatch(msg, pool=pool)
        assert isinstance(response, Response)
        assert response.content == "hi"

    @pytest.mark.asyncio
    async def test_dispatch_routes_multiple_args(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        msg = make_message(content="/echo foo bar baz")
        pool = Pool(pool_id="test", agent_name="test_agent", ctx=MagicMock())
        response = await router.dispatch(msg, pool=pool)
        assert isinstance(response, Response)
        assert response.content == "foo bar baz"

    @pytest.mark.asyncio
    async def test_dispatch_disabled_plugin_returns_unknown(
        self, tmp_path: Path
    ) -> None:
        # echo is loaded but NOT in the enabled list
        router = make_router(tmp_path, enabled=[])
        msg = make_message(content="/echo hi")
        response = await router.dispatch(msg)
        assert response is not None
        assert response.content is not None
        assert "unknown command" in response.content.lower()

    @pytest.mark.asyncio
    async def test_dispatch_plugin_without_pool_raises(self, tmp_path: Path) -> None:
        # pool is omitted; plugin commands require a real Pool
        router = make_router(tmp_path)
        msg = make_message(content="/echo hi")
        with pytest.raises(TypeError, match="requires a Pool"):
            await router.dispatch(msg, pool=None)


# ---------------------------------------------------------------------------
# Slice 3 — Hot-reload + hub passthrough
# ---------------------------------------------------------------------------


class TestHotReloadUpdatesCommands:
    """_maybe_reload() rebuilds command_router when plugin handlers.py changes."""

    def test_hot_reload_updates_plugin_commands(self, tmp_path: Path) -> None:
        class ConcreteAgent(AgentBase):
            async def process(
                self, msg: InboundMessage, pool: Pool, *, on_intermediate=None
            ) -> Response:
                return Response(content="ok")

        from lyra.core.agent_config import ModelConfig

        (tmp_path / "plugins").mkdir()
        plugins_dir = make_echo_plugin_dir(tmp_path / "plugins")

        config = Agent(
            name="test_agent",
            system_prompt="You are a test agent.",
            memory_namespace="test",
            llm_config=ModelConfig(
                backend="claude-cli", model="claude-haiku-4-5-20251001"
            ),
        )
        agent = ConcreteAgent(config, agents_dir=tmp_path, plugins_dir=plugins_dir)

        agent._command_loader.load("echo")
        agent._effective_plugins = ["echo"]
        agent._plugin_mtimes = agent._record_plugin_mtimes()
        agent.command_router = CommandRouter(
            agent._command_loader, agent._effective_plugins
        )

        plugin_cmds = agent._command_loader.get_commands(["echo"])
        assert "/echo" in plugin_cmds

        handlers_path = plugins_dir / "echo" / "handlers.py"
        import os

        new_mtime = handlers_path.stat().st_mtime + 1
        os.utime(handlers_path, (new_mtime, new_mtime))
        agent._plugin_mtimes["echo"] = new_mtime - 2  # make it appear stale

        agent._maybe_reload()

        plugin_cmds_after = agent._command_loader.get_commands(["echo"])
        assert "/echo" in plugin_cmds_after


class TestPassthroughNonCommandInHub:
    """Plain-text messages bypass the router and reach agent.process() (SC-10)."""

    @pytest.mark.asyncio
    async def test_passthrough_non_command_in_hub(self) -> None:
        process_calls: list[InboundMessage] = []

        class CapturingAgent(AgentBase):
            async def process(
                self, msg: InboundMessage, pool: Pool, *, on_intermediate=None
            ) -> Response:
                process_calls.append(msg)
                return Response(content="agent reply")

        hub_mod = __import__("lyra.core.hub", fromlist=["Hub"])
        Hub = hub_mod.Hub
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

        hub.register_adapter(
            Platform.TELEGRAM,
            "main",
            cast("ChannelAdapter", CapturingAdapter()),
        )
        hub.register_binding(
            Platform.TELEGRAM, "main", "chat:42", "lyra", "telegram:main:chat:42"
        )

        plain_msg = make_message(content="hello, how are you?")
        await hub.bus.put(plain_msg)

        try:
            await asyncio.wait_for(hub.run(), timeout=0.3)
        except asyncio.TimeoutError:
            pass

        assert len(process_calls) == 1
        assert process_calls[0].text == "hello, how are you?"

    @pytest.mark.asyncio
    async def test_slash_command_does_not_reach_agent_process(self) -> None:
        """When the router handles a /command, agent.process() is never called."""
        process_calls: list[InboundMessage] = []

        class TrackingAgent(AgentBase):
            async def process(
                self, msg: InboundMessage, pool: Pool, *, on_intermediate=None
            ) -> Response:
                process_calls.append(msg)
                return Response(content="should not be reached")

        hub_mod = __import__("lyra.core.hub", fromlist=["Hub"])
        Hub = hub_mod.Hub
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

        hub.register_adapter(
            Platform.TELEGRAM,
            "main",
            cast("ChannelAdapter", CapturingAdapter()),
        )
        hub.register_binding(
            Platform.TELEGRAM, "main", "chat:42", "lyra", "telegram:main:chat:42"
        )

        command_msg = make_message(content="/help")
        await hub.bus.put(command_msg)

        try:
            await asyncio.wait_for(hub.run(), timeout=0.3)
        except asyncio.TimeoutError:
            pass

        assert len(process_calls) == 0


# ---------------------------------------------------------------------------
# msg_manager injection — unknown command returns TOML string
# ---------------------------------------------------------------------------


class TestMsgManagerInjectionUnknownCommand:
    """CommandRouter with a real MessageManager returns the TOML 'unknown_command'
    string instead of the hardcoded fallback."""

    @pytest.mark.asyncio
    async def test_unknown_command_returns_toml_string(self, tmp_path: Path) -> None:
        mm = MessageManager(TOML_PATH)
        plugins_dir = Path(tempfile.mkdtemp())
        loader = CommandLoader(plugins_dir)
        router = CommandRouter(
            command_loader=loader, enabled_plugins=[], msg_manager=mm
        )
        msg = make_message(content="/unknown_cmd")

        response = await router.dispatch(msg)

        expected = mm.get("unknown_command", command_name="/unknown_cmd")
        assert isinstance(response, Response)
        assert response.content == expected
