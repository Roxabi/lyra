"""Tests for bang-prefix fallthrough, bare URL detection, session commands,
and CommandParser integration (issue #99, #153).

Covers:
  TestBangPrefixFallthrough      — !-prefix unknown commands → None sentinel
  TestBareUrlDetection           — prepare() rewrites bare URLs to /add
  TestBareUrlPatternsDisabled    — patterns={"bare_url": False} disables rewriting
  TestSessionCommands            — session command registry, dispatch, timeout, help
  TestIsCommandReadsCommandContext — is_command() reads msg.command (#153 S3)
"""

from __future__ import annotations

import asyncio
import dataclasses as _dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

if TYPE_CHECKING:
    from lyra.core.hub.hub_protocol import ChannelAdapter

import pytest

from lyra.core.agent import Agent, AgentBase
from lyra.core.commands.command_loader import CommandLoader
from lyra.core.commands.command_parser import CommandParser
from lyra.core.commands.command_router import CommandRouter
from lyra.core.message import (
    InboundMessage,
    OutboundMessage,
    Platform,
    Response,
)
from lyra.core.pool import Pool

from .conftest import make_echo_plugin_dir, make_message, make_router, push_to_hub

# ---------------------------------------------------------------------------
# Helpers local to this file
# ---------------------------------------------------------------------------


def make_message_with_command(content: str, **kwargs) -> InboundMessage:
    """Build InboundMessage with CommandContext pre-attached (as Hub pipeline would)."""
    parser = CommandParser()
    cmd_ctx = parser.parse(content)
    msg = make_message(content, **kwargs)
    if cmd_ctx is not None:
        msg = _dataclasses.replace(msg, command=cmd_ctx)
    return msg


# ---------------------------------------------------------------------------
# T13 — CommandParser integration: is_command reads msg.command (#153)
# ---------------------------------------------------------------------------


class TestIsCommandReadsCommandContext:
    """is_command() reads msg.command, not regex on text (#153 S3)."""

    def test_is_command_true_when_command_set(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        msg = make_message_with_command("/help")
        assert router.is_command(msg) is True

    def test_is_command_false_when_no_command(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        msg = make_message("hello world")
        assert router.is_command(msg) is False

    def test_is_command_true_for_bang_prefix(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        msg = make_message_with_command("!test")
        assert router.is_command(msg) is True  # ! prefix is a valid command prefix

    def test_get_command_name_returns_full_name(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        msg = make_message_with_command("/imagine cat")
        assert router.get_command_name(msg) == "/imagine"

    def test_get_command_name_bang_prefix(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        msg = make_message_with_command("!help")
        assert router.get_command_name(msg) == "!help"

    def test_get_command_name_none_when_no_command(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        msg = make_message("hello")
        assert router.get_command_name(msg) is None


# ---------------------------------------------------------------------------
# Bang-prefix fallthrough (#153)
# ---------------------------------------------------------------------------


class TestBangPrefixFallthrough:
    """!-prefixed unknown commands return None sentinel — fallthrough to pool (#153)."""

    @pytest.mark.asyncio
    async def test_unknown_bang_command_returns_none(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        msg = make_message_with_command("!unknown_command_xyz")
        result = await router.dispatch(msg, pool=None)
        assert result is None  # None sentinel triggers pool fallthrough

    @pytest.mark.asyncio
    async def test_unknown_slash_command_returns_response(self, tmp_path: Path) -> None:
        # Unknown / command still returns an error Response, not None
        router = make_router(tmp_path)
        msg = make_message_with_command("/totally_unknown_xyz123")
        result = await router.dispatch(msg, pool=None)
        assert result is not None
        assert hasattr(result, "content")

    @pytest.mark.asyncio
    async def test_unknown_bang_command_falls_through_to_pool_in_hub(
        self, tmp_path: Path
    ) -> None:
        """Hub integration: !unknown_xyz → dispatch() returns None sentinel →
        _dispatch_command() calls _submit_to_pool() → agent.process() reached."""
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

        hub.register_adapter(
            Platform.TELEGRAM,
            "main",
            cast("ChannelAdapter", SilentAdapter()),
        )
        hub.register_binding(
            Platform.TELEGRAM, "main", "chat:42", "lyra", "telegram:main:chat:42"
        )

        await push_to_hub(hub, make_message(content="!unknown_xyz"))

        try:
            await asyncio.wait_for(hub.run(), timeout=0.3)
        except asyncio.TimeoutError:
            pass

        assert len(process_calls) == 1
        assert process_calls[0].text == "!unknown_xyz"


# ---------------------------------------------------------------------------
# Issue #99 — Bare URL detection (T1)
# ---------------------------------------------------------------------------


class TestBareUrlDetection:
    """prepare() rewrites bare URLs to /vault-add; is_command() detects them."""

    def test_https_url_is_detected(self, tmp_path: Path) -> None:
        assert (
            make_router(tmp_path).is_command(
                make_message(content="https://example.com/article")
            )
            is True
        )  # noqa: E501

    def test_http_url_is_detected(self, tmp_path: Path) -> None:
        assert (
            make_router(tmp_path).is_command(make_message(content="http://example.com"))
            is True
        )  # noqa: E501

    def test_prepare_returns_add_command(self, tmp_path: Path) -> None:
        prepared = make_router(tmp_path).prepare(
            make_message(content="https://example.com/page")
        )
        assert prepared.command is not None
        assert prepared.command.name == "vault-add"
        assert prepared.command.prefix == "/"

    def test_prepare_sets_url_as_args(self, tmp_path: Path) -> None:
        url = "https://example.com/page"
        prepared = make_router(tmp_path).prepare(make_message(content=url))
        assert prepared.command is not None
        assert prepared.command.args == url

    def test_url_with_surrounding_text_not_rewritten(self, tmp_path: Path) -> None:
        prepared = make_router(tmp_path).prepare(
            make_message(content="check this https://example.com")
        )
        assert prepared.command is None

    def test_url_with_surrounding_text_is_not_command(self, tmp_path: Path) -> None:
        assert (
            make_router(tmp_path).is_command(
                make_message(content="check this https://example.com")
            )
            is False
        )  # noqa: E501

    def test_regular_command_unaffected_by_prepare(self, tmp_path: Path) -> None:
        prepared = make_router(tmp_path).prepare(make_message(content="/help"))
        assert prepared.command is not None
        assert prepared.command.name == "help"

    def test_get_command_name_returns_add_for_bare_url(self, tmp_path: Path) -> None:
        router = make_router(tmp_path)
        prepared = router.prepare(make_message(content="https://example.com"))
        assert router.get_command_name(prepared) == "/vault-add"

    def test_plain_text_not_detected_as_command(self, tmp_path: Path) -> None:
        assert (
            make_router(tmp_path).is_command(make_message(content="hello world"))
            is False
        )  # noqa: E501

    @pytest.mark.asyncio
    async def test_dispatch_bare_url_routes_to_add_session(
        self, tmp_path: Path
    ) -> None:
        """dispatch() with a bare URL calls the /vault-add session handler."""
        from unittest.mock import AsyncMock
        from unittest.mock import MagicMock as MM

        from lyra.integrations.base import SessionTools

        router = make_router(tmp_path)
        handler = AsyncMock(return_value=Response(content="added"))
        tools = SessionTools(scraper=MM(), vault=MM())
        router.register_session_command("vault-add", handler, tools=tools)
        router._session_driver = MM()

        msg = make_message(content="https://example.com/test")
        pool = MagicMock(spec=Pool)
        response = await router.dispatch(msg, pool=pool)
        assert response is not None
        assert response.content == "added"
        handler.assert_called_once()


# ---------------------------------------------------------------------------
# Issue #99 — Bare URL patterns disabled
# ---------------------------------------------------------------------------


class TestBareUrlPatternsDisabled:
    """patterns={"bare_url": False} disables bare URL rewriting (opt-in model)."""

    def test_bare_url_is_not_detected_when_disabled(self, tmp_path: Path) -> None:
        router = make_router(tmp_path, patterns={"bare_url": False})
        assert (
            router.is_command(make_message(content="https://example.com/article"))
            is False
        )  # noqa: E501

    def test_prepare_returns_message_unchanged_when_disabled(
        self, tmp_path: Path
    ) -> None:
        url = "https://example.com/page"
        prepared = make_router(tmp_path, patterns={"bare_url": False}).prepare(
            make_message(content=url)
        )
        assert prepared.command is None

    def test_default_patterns_disables_bare_url(self, tmp_path: Path) -> None:
        # Router with no patterns (empty dict = default-off)
        plugins_dir = make_echo_plugin_dir(tmp_path)
        loader = CommandLoader(plugins_dir)
        loader.load("echo")
        router = CommandRouter(command_loader=loader, enabled_plugins=["echo"])
        # Default is opt-in (False): bare URL not treated as command
        assert router.is_command(make_message(content="https://example.com")) is False

    def test_bare_url_enabled_explicitly(self, tmp_path: Path) -> None:
        # Explicit opt-in: bare URL is a command
        assert (
            make_router(tmp_path, patterns={"bare_url": True}).is_command(
                make_message(content="https://example.com")
            )
            is True
        )


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

        from lyra.integrations.base import SessionTools

        router = make_router(tmp_path)
        router._session_driver = driver or MagicMock()

        scraper = MagicMock()
        scraper.scrape = AsyncMock(return_value="content")
        vault = MagicMock()
        vault.add = AsyncMock()
        vault.search = AsyncMock(return_value="results")
        tools = SessionTools(scraper=scraper, vault=vault)

        async_handler = AsyncMock(return_value=Response(content=handler_response))
        router.register_session_command(
            "mysession",
            async_handler,
            tools=tools,
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
        from unittest.mock import AsyncMock, MagicMock

        from lyra.integrations.base import SessionTools

        router = make_router(tmp_path)
        router._session_driver = None

        tools = SessionTools(scraper=MagicMock(), vault=MagicMock())
        async_handler = AsyncMock(return_value=Response(content="never"))
        router.register_session_command("nosession", async_handler, tools=tools)

        msg = make_message(content="/nosession")
        pool = MagicMock(spec=Pool)
        response = await router.dispatch(msg, pool=pool)
        assert response is not None
        assert (
            "anthropic-sdk" in response.content.lower()
            or "session" in response.content.lower()
        )
        async_handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_session_command_timeout_returns_timeout_message(
        self, tmp_path: Path
    ) -> None:
        import asyncio as _asyncio

        from lyra.integrations.base import SessionTools

        router = make_router(tmp_path)
        router._session_driver = MagicMock()

        async def slow_handler(msg, driver, tools, args, timeout):
            await _asyncio.sleep(10)
            return Response(content="never")

        tools = SessionTools(scraper=MagicMock(), vault=MagicMock())
        router.register_session_command("slow", slow_handler, tools=tools, timeout=0.01)

        msg = make_message(content="/slow")
        pool = MagicMock(spec=Pool)
        response = await router.dispatch(msg, pool=pool)
        assert response is not None
        assert "timed out" in response.content.lower()

    def test_register_conflict_with_builtin_raises(self, tmp_path: Path) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from lyra.integrations.base import SessionTools

        router = make_router(tmp_path)
        handler = AsyncMock(return_value=Response(content="x"))
        tools = SessionTools(scraper=MagicMock(), vault=MagicMock())
        with pytest.raises(ValueError, match="clashes with a builtin"):
            router.register_session_command("help", handler, tools=tools)

    def test_register_conflict_with_plugin_raises(self, tmp_path: Path) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from lyra.integrations.base import SessionTools

        router = make_router(tmp_path)  # has echo plugin
        handler = AsyncMock(return_value=Response(content="x"))
        tools = SessionTools(scraper=MagicMock(), vault=MagicMock())
        with pytest.raises(ValueError, match="clashes with a plugin"):
            router.register_session_command("echo", handler, tools=tools)

    def test_help_includes_session_commands_section(self, tmp_path: Path) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from lyra.core.builtin_commands import help_command
        from lyra.integrations.base import SessionTools

        router = make_router(tmp_path)
        handler = AsyncMock(return_value=Response(content="x"))
        tools = SessionTools(scraper=MagicMock(), vault=MagicMock())
        router.register_session_command(
            "mything", handler, tools=tools, description="Does the thing"
        )  # noqa: E501
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

    def test_help_hides_processor_commands_without_passthroughs(
        self, tmp_path: Path
    ) -> None:
        """Processor commands only appear in /help when registered as passthroughs (#359)."""
        from tests.helpers import reload_processors

        from lyra.core.builtin_commands import help_command
        from lyra.core.processor_registry import registry as proc_registry

        reload_processors()
        registered = sorted(proc_registry.commands())
        assert registered, "processor registry should have at least one command"
        sample_cmd = registered[0]

        router = make_router(tmp_path)

        # Without passthroughs — processor commands should NOT appear
        response_no_pt = help_command(
            router._builtins,
            router._session_handlers,
            router._command_loader,
            router._enabled_plugins,
            router._msg_manager,
            passthroughs=frozenset(),
        )
        assert sample_cmd not in response_no_pt.content

        # With passthroughs — only registered ones appear
        response_with_pt = help_command(
            router._builtins,
            router._session_handlers,
            router._command_loader,
            router._enabled_plugins,
            router._msg_manager,
            passthroughs=frozenset({sample_cmd}),
        )
        assert sample_cmd in response_with_pt.content

    def test_session_args_passed_to_handler(self, tmp_path: Path) -> None:
        import asyncio as _asyncio
        from unittest.mock import MagicMock

        from lyra.integrations.base import SessionTools

        received_args: list[list[str]] = []

        async def capturing_handler(msg, driver, tools, args, timeout):
            received_args.append(args)
            return Response(content="ok")

        router = make_router(tmp_path)
        router._session_driver = MagicMock()
        tools = SessionTools(scraper=MagicMock(), vault=MagicMock())
        router.register_session_command("capture", capturing_handler, tools=tools)
        msg = make_message(content="/capture foo bar")
        pool = MagicMock(spec=Pool)
        _asyncio.get_event_loop().run_until_complete(router.dispatch(msg, pool=pool))
        assert received_args == [["foo", "bar"]]
