"""Tests for the search plugin (issue #99 T5)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.core.auth import TrustLevel
from lyra.core.message import InboundMessage, Response
from lyra.core.pool import Pool
from lyra.plugins.search.handlers import cmd_search


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


def make_pool() -> MagicMock:
    return MagicMock(spec=Pool)


class TestCmdSearch:
    """cmd_search() — vault_search wrapping, edge cases."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_vault_results(self) -> None:
        msg = make_message("/search python asyncio")
        pool = make_pool()

        with patch(
            "lyra.core.session_helpers.vault_search",
            new=AsyncMock(return_value="Result 1\nResult 2\n"),
        ) as mock_search:
            response = await cmd_search(msg, pool, ["python", "asyncio"])

        assert isinstance(response, Response)
        assert "Result 1" in response.content
        mock_search.assert_called_once_with("python asyncio", timeout=25.0)

    @pytest.mark.asyncio
    async def test_empty_args_returns_usage_hint(self) -> None:
        msg = make_message("/search")
        pool = make_pool()
        response = await cmd_search(msg, pool, [])
        assert isinstance(response, Response)
        assert "Usage:" in response.content

    @pytest.mark.asyncio
    async def test_vault_absent_returns_graceful_string(self) -> None:
        msg = make_message("/search query")
        pool = make_pool()

        with patch(
            "lyra.core.session_helpers.vault_search",
            new=AsyncMock(return_value="vault CLI not available."),
        ):
            response = await cmd_search(msg, pool, ["query"])

        assert isinstance(response, Response)
        assert "vault" in response.content.lower()

    @pytest.mark.asyncio
    async def test_multi_word_query_joined(self) -> None:
        msg = make_message("/search foo bar baz")
        pool = make_pool()

        mock_search = AsyncMock(return_value="results")
        with patch("lyra.core.session_helpers.vault_search", new=mock_search):
            await cmd_search(msg, pool, ["foo", "bar", "baz"])

        mock_search.assert_called_once_with("foo bar baz", timeout=25.0)
