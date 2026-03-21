"""Integration tests — processor.post(): vault.add() invocation and ordering."""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.command_parser import CommandParser
from lyra.core.message import InboundMessage, Response
from lyra.core.pool import Pool
from lyra.core.processor_registry import registry
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

            async def process(
                self, _msg: InboundMessage, _pool, *, on_intermediate=None
            ):
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

            async def process(
                self, _msg: InboundMessage, _pool, *, on_intermediate=None
            ):
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

            async def process(
                self, _msg: InboundMessage, _pool, *, on_intermediate=None
            ):
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
