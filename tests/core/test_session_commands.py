"""Integration tests for the processor pre→LLM→post pipeline (issue #363).

Validates the full command message lifecycle:
  processor.pre() → LLM (agent.process()) → processor.post() → response dispatched

These are integration tests: real Pool, real PoolProcessor, real ProcessorRegistry,
real VaultAddProcessor — only external I/O (scraper, vault, agent.process) is mocked.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.command_parser import CommandParser
from lyra.core.message import InboundMessage, Response
from lyra.core.pool import Pool
from lyra.core.processor_registry import BaseProcessor, registry
from lyra.core.trust import TrustLevel
from lyra.integrations.base import SessionTools
from tests.helpers import reload_processors

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_registry() -> Generator[None, None, None]:
    """Reload processors before each test and clear registry after.

    autouse ensures the global singleton always has /vault-add registered,
    and no cross-test contamination from local @register calls.
    """
    reload_processors()
    yield
    registry.clear()


def _make_tools(
    scrape_return: str = "scraped page content",
    scrape_side_effect=None,
    vault_side_effect=None,
) -> SessionTools:
    scraper = MagicMock()
    if scrape_side_effect is not None:
        scraper.scrape = AsyncMock(side_effect=scrape_side_effect)
    else:
        scraper.scrape = AsyncMock(return_value=scrape_return)

    vault = MagicMock()
    if vault_side_effect is not None:
        vault.add = AsyncMock(side_effect=vault_side_effect)
    else:
        vault.add = AsyncMock()
    vault.search = AsyncMock(return_value="")
    return SessionTools(scraper=scraper, vault=vault)


def _make_command_msg(text: str) -> InboundMessage:
    """Build an InboundMessage with a parsed CommandContext, mirroring Hub pipeline."""
    parser = CommandParser()
    cmd_ctx = parser.parse(text)
    return InboundMessage(
        id="msg-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:42",
        user_id="tg:user:alice",
        user_name="Alice",
        is_mention=False,
        text=text,
        text_raw=text,
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 42,
            "topic_id": None,
            "message_id": None,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
        command=cmd_ctx,
    )


def _make_plain_msg(text: str = "hello") -> InboundMessage:
    """Build a plain InboundMessage with no command context."""
    return InboundMessage(
        id="msg-2",
        platform="telegram",
        bot_id="main",
        scope_id="chat:42",
        user_id="tg:user:alice",
        user_name="Alice",
        is_mention=False,
        text=text,
        text_raw=text,
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 42,
            "topic_id": None,
            "message_id": None,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
        command=None,
    )


def _make_ctx(agent) -> MagicMock:
    """Build a minimal PoolContext mock wired to the given agent."""
    ctx = MagicMock()
    ctx.get_agent = MagicMock(return_value=agent)
    ctx.get_message = MagicMock(return_value=None)
    ctx.dispatch_response = AsyncMock(return_value=None)
    ctx.dispatch_streaming = AsyncMock(return_value=None)
    ctx.record_circuit_success = MagicMock()
    ctx.record_circuit_failure = MagicMock()
    return ctx


async def _drain(pool: Pool, *, timeout: float = 3.0) -> None:
    """Yield to the event loop then wait for the processing task to finish."""
    import asyncio

    await asyncio.sleep(0)
    if pool._current_task is not None:
        await asyncio.wait_for(pool._current_task, timeout=timeout)


# ---------------------------------------------------------------------------
# Test 1 — processor.pre() is called before LLM; LLM sees enriched content
# ---------------------------------------------------------------------------


class TestProcessorPreCalledBeforeLLM:
    """pre() runs before agent.process(); the LLM receives enriched text."""

    async def test_llm_receives_enriched_content_not_raw_command(self) -> None:
        """Submit /vault-add → LLM must see scraped content, NOT the raw URL command."""
        # Arrange
        scraped_body = "This is the scraped article content."
        tools = _make_tools(scrape_return=scraped_body)

        received_texts: list[str] = []

        class RecordingAgent:
            name = "test_agent"
            _session_tools = tools

            async def process(self, msg: InboundMessage, pool, *, on_intermediate=None):
                received_texts.append(msg.text)
                return Response(
                    content="Title: Example\nTags: test\n\nA nice summary."
                )

        agent = RecordingAgent()
        ctx = _make_ctx(agent)
        pool = Pool(
            pool_id="test:main:chat:42",
            agent_name="test_agent",
            ctx=ctx,
            turn_timeout=10.0,
            debounce_ms=0,
        )
        msg = _make_command_msg("/vault-add https://example.com")

        # Act
        pool.submit(msg)
        await _drain(pool)

        # Assert — LLM received the enriched version, not the raw command
        assert len(received_texts) == 1
        enriched = received_texts[0]
        assert scraped_body in enriched, (
            f"Expected scraped content in enriched text, got: {enriched!r}"
        )
        assert "/vault-add https://example.com" not in enriched, (
            "LLM should NOT see the raw command string — pre() must have replaced it"
        )

    async def test_enriched_text_contains_url_and_instruction(self) -> None:
        """Enriched message includes the URL and the extraction instruction."""
        # Arrange
        tools = _make_tools(scrape_return="article body")
        received_texts: list[str] = []

        class RecordingAgent:
            name = "test_agent"
            _session_tools = tools

            async def process(self, msg: InboundMessage, pool, *, on_intermediate=None):
                received_texts.append(msg.text)
                return Response(content="Title: T\nTags: a\n\nSummary.")

        agent = RecordingAgent()
        ctx = _make_ctx(agent)
        pool = Pool(
            pool_id="test:main:chat:42",
            agent_name="test_agent",
            ctx=ctx,
            turn_timeout=10.0,
            debounce_ms=0,
        )
        msg = _make_command_msg("/vault-add https://example.com")

        # Act
        pool.submit(msg)
        await _drain(pool)

        # Assert
        enriched = received_texts[0]
        assert "https://example.com" in enriched
        assert "article body" in enriched
        # Instruction prompt should be present
        assert "Title:" in enriched or "summary" in enriched.lower()


# ---------------------------------------------------------------------------
# Test 2 — processor.post() is called after LLM; vault.add() is invoked
# ---------------------------------------------------------------------------


class TestProcessorPostCalledAfterLLM:
    """post() runs after agent.process(); vault.add() is called with the correct URL."""

    async def test_vault_add_called_after_llm_response(self) -> None:
        """After LLM replies, vault.add() must be called with the original URL."""
        # Arrange
        tools = _make_tools(scrape_return="some content")

        class FixedAgent:
            name = "test_agent"
            _session_tools = tools

            async def process(self, msg: InboundMessage, pool, *, on_intermediate=None):
                return Response(
                    content="Title: My Page\nTags: tech, web\n\nA helpful summary."
                )

        agent = FixedAgent()
        ctx = _make_ctx(agent)
        pool = Pool(
            pool_id="test:main:chat:42",
            agent_name="test_agent",
            ctx=ctx,
            turn_timeout=10.0,
            debounce_ms=0,
        )
        msg = _make_command_msg("/vault-add https://example.com")

        # Act
        pool.submit(msg)
        await _drain(pool)

        # Assert — vault.add was called exactly once
        tools.vault.add.assert_called_once()  # type: ignore[attr-defined]
        call_args = tools.vault.add.call_args  # type: ignore[attr-defined]
        assert call_args.args[2] == "https://example.com"  # url positional arg

    async def test_vault_add_receives_parsed_title_and_tags(self) -> None:
        """vault.add() receives the title and tags parsed from the LLM response."""
        # Arrange
        tools = _make_tools(scrape_return="some content")

        class FixedAgent:
            name = "test_agent"
            _session_tools = tools

            async def process(self, msg: InboundMessage, pool, *, on_intermediate=None):
                return Response(
                    content="Title: My Page\nTags: tech, web\n\nA helpful summary."
                )

        agent = FixedAgent()
        ctx = _make_ctx(agent)
        pool = Pool(
            pool_id="test:main:chat:42",
            agent_name="test_agent",
            ctx=ctx,
            turn_timeout=10.0,
            debounce_ms=0,
        )
        msg = _make_command_msg("/vault-add https://example.com")

        # Act
        pool.submit(msg)
        await _drain(pool)

        # Assert — title and tags come from the LLM response
        call_args = tools.vault.add.call_args  # type: ignore[attr-defined]
        assert call_args.args[0] == "My Page"  # title
        assert "tech" in call_args.args[1]  # tags
        assert "web" in call_args.args[1]

    async def test_llm_called_before_vault_add(self) -> None:
        """agent.process() must complete before vault.add() is called."""
        # Arrange
        call_order: list[str] = []
        tools = _make_tools(scrape_return="content")
        original_vault_add = tools.vault.add

        async def tracking_vault_add(*args, **kwargs):
            call_order.append("vault.add")
            return await original_vault_add(*args, **kwargs)

        tools.vault.add = AsyncMock(side_effect=tracking_vault_add)

        class TrackingAgent:
            name = "test_agent"
            _session_tools = tools

            async def process(self, msg: InboundMessage, pool, *, on_intermediate=None):
                call_order.append("llm")
                return Response(content="Title: T\nTags: a\n\nSummary.")

        agent = TrackingAgent()
        ctx = _make_ctx(agent)
        pool = Pool(
            pool_id="test:main:chat:42",
            agent_name="test_agent",
            ctx=ctx,
            turn_timeout=10.0,
            debounce_ms=0,
        )
        msg = _make_command_msg("/vault-add https://example.com")

        # Act
        pool.submit(msg)
        await _drain(pool)

        # Assert — LLM was called first, then vault.add
        assert call_order == ["llm", "vault.add"], (
            f"Expected ['llm', 'vault.add'], got {call_order}"
        )


# ---------------------------------------------------------------------------
# Test 3 — pre() exception dispatches error; LLM is NOT called
# ---------------------------------------------------------------------------


class TestPreExceptionDispatchesErrorResponse:
    """When pre() raises, an error response is dispatched and the LLM is skipped."""

    async def test_unexpected_pre_exception_skips_llm(self) -> None:
        """If scraper raises an unexpected exception, the LLM must NOT be called."""
        # Arrange — scraper raises a generic (non-ScrapeFailed) exception
        tools = _make_tools(scrape_side_effect=RuntimeError("network down"))

        llm_called = False

        class WatchingAgent:
            name = "test_agent"
            _session_tools = tools

            async def process(self, msg: InboundMessage, pool, *, on_intermediate=None):
                nonlocal llm_called
                llm_called = True
                return Response(content="should not be reached")

        agent = WatchingAgent()
        ctx = _make_ctx(agent)
        pool = Pool(
            pool_id="test:main:chat:42",
            agent_name="test_agent",
            ctx=ctx,
            turn_timeout=10.0,
            debounce_ms=0,
        )
        msg = _make_command_msg("/vault-add https://example.com")

        # Act
        pool.submit(msg)
        await _drain(pool)

        # Assert — LLM was never called
        assert not llm_called, "LLM must not be called when pre() raises"

    async def test_unexpected_pre_exception_dispatches_error_response(self) -> None:
        """If pre() raises, an error response is dispatched to the user."""
        # Arrange
        tools = _make_tools(scrape_side_effect=RuntimeError("network down"))

        class WatchingAgent:
            name = "test_agent"
            _session_tools = tools

            async def process(self, msg: InboundMessage, pool, *, on_intermediate=None):
                return Response(content="should not reach user")

        agent = WatchingAgent()
        ctx = _make_ctx(agent)
        pool = Pool(
            pool_id="test:main:chat:42",
            agent_name="test_agent",
            ctx=ctx,
            turn_timeout=10.0,
            debounce_ms=0,
        )
        msg = _make_command_msg("/vault-add https://example.com")

        # Act
        pool.submit(msg)
        await _drain(pool)

        # Assert — dispatch_response was called with an error/warning message
        ctx.dispatch_response.assert_called_once()
        call_args = ctx.dispatch_response.call_args
        dispatched: Response = call_args.args[1]
        assert isinstance(dispatched, Response)
        # The error message should mention the failing command
        assert (
            "/vault-add" in dispatched.content
            or "failed" in dispatched.content.lower()
        )

    async def test_unexpected_pre_exception_does_not_call_vault(self) -> None:
        """If pre() raises, vault.add() must not be invoked (no side effects)."""
        # Arrange
        tools = _make_tools(scrape_side_effect=RuntimeError("network down"))

        class WatchingAgent:
            name = "test_agent"
            _session_tools = tools

            async def process(self, msg: InboundMessage, pool, *, on_intermediate=None):
                return Response(content="should not reach post")

        agent = WatchingAgent()
        ctx = _make_ctx(agent)
        pool = Pool(
            pool_id="test:main:chat:42",
            agent_name="test_agent",
            ctx=ctx,
            turn_timeout=10.0,
            debounce_ms=0,
        )
        msg = _make_command_msg("/vault-add https://example.com")

        # Act
        pool.submit(msg)
        await _drain(pool)

        # Assert — vault.add never called
        tools.vault.add.assert_not_called()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Test 4 — plain messages do not trigger processors
# ---------------------------------------------------------------------------


class TestNoProcessorForRegularMessage:
    """Plain text messages (no command) never trigger the processor pipeline."""

    async def test_plain_message_does_not_call_pre(self) -> None:
        """A non-command message bypasses the registry; pre() is never invoked."""
        # Arrange — register a spy processor under a command that will NOT be sent
        pre_call_count = 0

        class SpyProcessor(BaseProcessor):
            async def pre(self, msg: InboundMessage) -> InboundMessage:
                nonlocal pre_call_count
                pre_call_count += 1
                return msg

        # Register on a fresh local registry entry (the real registry is loaded by
        # reload_processors() in the autouse fixture, so /vault-add is present;
        # we add a fresh command to confirm the registry itself is not consulted)
        registry.register("/spy-cmd")(SpyProcessor)

        tools = _make_tools()

        class SimpleAgent:
            name = "test_agent"
            _session_tools = tools

            async def process(self, msg: InboundMessage, pool, *, on_intermediate=None):
                return Response(content="plain reply")

        agent = SimpleAgent()
        ctx = _make_ctx(agent)
        pool = Pool(
            pool_id="test:main:chat:42",
            agent_name="test_agent",
            ctx=ctx,
            turn_timeout=10.0,
            debounce_ms=0,
        )
        msg = _make_plain_msg("tell me a joke")

        # Act
        pool.submit(msg)
        await _drain(pool)

        # Assert — pre() was never called
        assert pre_call_count == 0, (
            f"pre() was called {pre_call_count} time(s) for a plain text message"
        )

    async def test_plain_message_reaches_llm_normally(self) -> None:
        """A plain text message is passed directly to the LLM without modification."""
        # Arrange
        tools = _make_tools()
        received_texts: list[str] = []

        class RecordingAgent:
            name = "test_agent"
            _session_tools = tools

            async def process(self, msg: InboundMessage, pool, *, on_intermediate=None):
                received_texts.append(msg.text)
                return Response(content="plain reply")

        agent = RecordingAgent()
        ctx = _make_ctx(agent)
        pool = Pool(
            pool_id="test:main:chat:42",
            agent_name="test_agent",
            ctx=ctx,
            turn_timeout=10.0,
            debounce_ms=0,
        )
        original_text = "tell me a joke"
        msg = _make_plain_msg(original_text)

        # Act
        pool.submit(msg)
        await _drain(pool)

        # Assert — LLM received the original text unchanged
        assert received_texts == [original_text]

    async def test_plain_message_does_not_call_vault(self) -> None:
        """A plain text message must never trigger vault.add() as a side effect."""
        # Arrange
        tools = _make_tools()

        class SimpleAgent:
            name = "test_agent"
            _session_tools = tools

            async def process(self, msg: InboundMessage, pool, *, on_intermediate=None):
                return Response(content="ok")

        agent = SimpleAgent()
        ctx = _make_ctx(agent)
        pool = Pool(
            pool_id="test:main:chat:42",
            agent_name="test_agent",
            ctx=ctx,
            turn_timeout=10.0,
            debounce_ms=0,
        )
        msg = _make_plain_msg("hello world")

        # Act
        pool.submit(msg)
        await _drain(pool)

        # Assert
        tools.vault.add.assert_not_called()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Test 5 — agent without _session_tools skips the processor pipeline entirely
# ---------------------------------------------------------------------------


class TestAgentWithoutSessionToolsSkipsProcessors:
    """When an agent has no _session_tools, the registry is never consulted."""

    async def test_command_msg_skips_processor_when_no_session_tools(self) -> None:
        """If agent._session_tools is absent, the LLM receives the raw command text."""
        # Arrange — agent intentionally has no _session_tools attribute
        received_texts: list[str] = []

        class NoToolsAgent:
            name = "test_agent"
            # deliberately no _session_tools

            async def process(self, msg: InboundMessage, pool, *, on_intermediate=None):
                received_texts.append(msg.text)
                return Response(content="fallback reply")

        agent = NoToolsAgent()
        ctx = _make_ctx(agent)
        pool = Pool(
            pool_id="test:main:chat:42",
            agent_name="test_agent",
            ctx=ctx,
            turn_timeout=10.0,
            debounce_ms=0,
        )
        raw_command = "/vault-add https://example.com"
        msg = _make_command_msg(raw_command)

        # Act
        pool.submit(msg)
        await _drain(pool)

        # Assert — LLM saw the original command text (not enriched)
        assert received_texts == [raw_command]
