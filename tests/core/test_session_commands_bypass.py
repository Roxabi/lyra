"""Integration tests — bypass: agents without session tools skip processor pipeline."""

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


def _make_plain_msg(text: str = "hello") -> InboundMessage:
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

            async def process(
                self, _msg: InboundMessage, _pool, *, on_intermediate=None
            ):
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
