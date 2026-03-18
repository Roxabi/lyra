"""Tests for the search session command handler (issue #99 T5, #360).

/search is now a session command (registered via register_session_command),
not a plugin command. It receives SessionTools via injection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.commands.search.handlers import cmd_search
from lyra.core.message import InboundMessage, Response
from lyra.core.trust import TrustLevel
from lyra.integrations.base import SessionTools


def make_message(text: str = "/search hello") -> InboundMessage:
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
    )


def make_tools(return_value: str = "results") -> tuple[SessionTools, AsyncMock]:
    vault_search: AsyncMock = AsyncMock(return_value=return_value)
    vault = MagicMock()
    vault.search = vault_search
    vault.add = AsyncMock()
    scraper = MagicMock()
    scraper.scrape = AsyncMock(return_value="")
    return SessionTools(scraper=scraper, vault=vault), vault_search


def make_driver() -> MagicMock:
    return MagicMock()


class TestCmdSearch:
    """cmd_search() — SessionTools injection, edge cases."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_vault_results(self) -> None:
        msg = make_message("/search python asyncio")
        tools, vault_search = make_tools("Result 1\nResult 2\n")

        response = await cmd_search(
            msg, make_driver(), tools, ["python", "asyncio"], 30.0
        )

        assert isinstance(response, Response)
        assert "Result 1" in response.content
        vault_search.assert_called_once_with("python asyncio", timeout=25.0)

    @pytest.mark.asyncio
    async def test_empty_args_returns_usage_hint(self) -> None:
        msg = make_message("/search")
        tools, _ = make_tools()

        response = await cmd_search(msg, make_driver(), tools, [], 30.0)

        assert isinstance(response, Response)
        assert "Usage:" in response.content

    @pytest.mark.asyncio
    async def test_vault_absent_returns_graceful_string(self) -> None:
        msg = make_message("/search query")
        tools, _ = make_tools("vault CLI not available.")

        response = await cmd_search(msg, make_driver(), tools, ["query"], 30.0)

        assert isinstance(response, Response)
        assert "vault" in response.content.lower()

    @pytest.mark.asyncio
    async def test_multi_word_query_joined(self) -> None:
        msg = make_message("/search foo bar baz")
        tools, vault_search = make_tools("results")

        await cmd_search(msg, make_driver(), tools, ["foo", "bar", "baz"], 30.0)

        vault_search.assert_called_once_with("foo bar baz", timeout=25.0)
