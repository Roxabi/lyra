"""Integration tests — processor.pre(): enrichment and pre() exception handling."""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.auth.trust import TrustLevel
from lyra.core.commands.command_parser import CommandParser
from lyra.core.messaging.message import InboundMessage, Response
from lyra.core.pool import Pool
from lyra.core.processors.processor_registry import registry
from lyra.integrations.base import SessionTools
from tests.helpers import reload_processors


@pytest.fixture(autouse=True)
def _restore_registry() -> Generator[None, None, None]:  # noqa: F841  # autouse fixture
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


def _make_ctx(agent) -> MagicMock:
    ctx = MagicMock()
    ctx.get_agent = MagicMock(return_value=agent)
    ctx.get_message = MagicMock(return_value=None)
    ctx.dispatch_response = AsyncMock(return_value=None)
    ctx.dispatch_streaming = AsyncMock(return_value=None)
    ctx.record_circuit_success = MagicMock()
    ctx.record_circuit_failure = MagicMock()
    return ctx


async def _drain(pool: Pool, *, timeout: float = 3.0) -> None:
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

            async def process(
                self,
                msg: InboundMessage,
                _pool,
                *,
                _on_intermediate=None,
            ):
                received_texts.append(msg.text)
                return Response(content="Title: Example\nTags: test\n\nA nice summary.")

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

            async def process(
                self, msg: InboundMessage, _pool, *, on_intermediate=None
            ):
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

            async def process(
                self, _msg: InboundMessage, _pool, *, on_intermediate=None
            ):
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

            async def process(
                self, _msg: InboundMessage, _pool, *, on_intermediate=None
            ):
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
            "/vault-add" in dispatched.content or "failed" in dispatched.content.lower()
        )

    async def test_unexpected_pre_exception_does_not_call_vault(self) -> None:
        """If pre() raises, vault.add() must not be invoked (no side effects)."""
        # Arrange
        tools = _make_tools(scrape_side_effect=RuntimeError("network down"))

        class WatchingAgent:
            name = "test_agent"
            _session_tools = tools

            async def process(
                self, _msg: InboundMessage, _pool, *, on_intermediate=None
            ):
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
        cast("AsyncMock", tools.vault.add).assert_not_called()
