"""Tests for SearchProcessor (issue #363)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from lyra.core.commands.command_parser import CommandContext
from lyra.core.message import InboundMessage
from lyra.core.processors.search import SearchProcessor
from lyra.core.trust import TrustLevel
from lyra.integrations.base import SessionTools

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_tools(search_return: str = "result 1\nresult 2") -> SessionTools:
    scraper = MagicMock()
    scraper.scrape = AsyncMock(return_value="")
    vault = MagicMock()
    vault.add = AsyncMock()
    vault.search = AsyncMock(return_value=search_return)
    return SessionTools(scraper=scraper, vault=vault)


def make_msg(
    text: str = "hello",
    command_name: str | None = None,
    command_args: str = "",
) -> InboundMessage:
    cmd = (
        CommandContext(prefix="/", name=command_name, args=command_args, raw=text)
        if command_name
        else None
    )
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
        trust_level=TrustLevel.TRUSTED,
        command=cmd,
    )


# ---------------------------------------------------------------------------
# SearchProcessor.pre()
# ---------------------------------------------------------------------------


class TestSearchProcessorPre:
    async def test_happy_path_enriches_msg_with_query_and_results(self) -> None:
        # Arrange
        tools = make_tools(search_return="Found: article about asyncio")
        proc = SearchProcessor(tools)
        msg = make_msg(
            text="/search asyncio",
            command_name="search",
            command_args="asyncio",
        )

        # Act
        enriched = await proc.pre(msg)

        # Assert
        assert "asyncio" in enriched.text
        assert "Found: article about asyncio" in enriched.text
        cast(Any, tools.vault).search.assert_called_once_with("asyncio", timeout=25.0)
        assert enriched.processor_enriched is True

    async def test_empty_results_produces_no_results_message(self) -> None:
        # Arrange — vault returns empty string
        tools = make_tools(search_return="")
        proc = SearchProcessor(tools)
        msg = make_msg(
            text="/search missing-topic",
            command_name="search",
            command_args="missing-topic",
        )

        # Act
        enriched = await proc.pre(msg)

        # Assert
        assert "no results found" in enriched.text

    async def test_whitespace_only_results_produces_no_results_message(self) -> None:
        # Arrange — vault returns whitespace string
        tools = make_tools(search_return="   \n  ")
        proc = SearchProcessor(tools)
        msg = make_msg(
            text="/search blank",
            command_name="search",
            command_args="blank",
        )

        # Act
        enriched = await proc.pre(msg)

        # Assert
        assert "no results found" in enriched.text

    async def test_empty_query_returns_usage_message(self) -> None:
        # Arrange — no command and empty text so query resolves to ""
        tools = make_tools()
        proc = SearchProcessor(tools)
        msg = make_msg(text="")

        # Act
        result = await proc.pre(msg)

        # Assert
        assert "Usage:" in result.text
        cast(Any, tools.vault).search.assert_not_called()

    async def test_query_from_text_when_no_command(self) -> None:
        # Arrange — message with no parsed command, query comes from text field
        tools = make_tools(search_return="some result")
        proc = SearchProcessor(tools)
        msg = make_msg(text="python asyncio")

        # Act
        enriched = await proc.pre(msg)

        # Assert
        assert "python asyncio" in enriched.text
        cast(Any, tools.vault).search.assert_called_once_with(
            "python asyncio", timeout=25.0
        )

    async def test_enriched_msg_contains_formatting_instruction(self) -> None:
        # Arrange
        tools = make_tools(search_return="article one")
        proc = SearchProcessor(tools)
        msg = make_msg(
            text="/search python",
            command_name="search",
            command_args="python",
        )

        # Act
        enriched = await proc.pre(msg)

        # Assert — the processor injects a formatting instruction for the LLM
        text = enriched.text.lower()
        assert "helpful" in text or "format" in text or "present" in text

    async def test_whitespace_only_query_returns_usage_message(self) -> None:
        # Arrange — no command, text is whitespace only so query resolves to ""
        tools = make_tools()
        proc = SearchProcessor(tools)
        msg = make_msg(text="   ")

        # Act
        result = await proc.pre(msg)

        # Assert
        assert "Usage:" in result.text
        cast(Any, tools.vault).search.assert_not_called()
