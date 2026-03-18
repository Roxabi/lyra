"""Tests for the search plugin (issue #99 T5, #360)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.commands.search.handlers import cmd_search, set_vault_provider
from lyra.core.message import InboundMessage, Response
from lyra.core.pool import Pool
from lyra.core.trust import TrustLevel


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


def make_mock_vault(return_value: str = "results") -> MagicMock:
    vault = MagicMock()
    vault.search = AsyncMock(return_value=return_value)
    vault.add = AsyncMock()
    return vault


class TestCmdSearch:
    """cmd_search() — VaultProvider injection, edge cases."""

    @pytest.fixture(autouse=True)
    def reset_vault_provider(self):
        """Reset the module-level vault provider after each test."""
        yield
        set_vault_provider(None)

    @pytest.mark.asyncio
    async def test_happy_path_returns_vault_results(self) -> None:
        msg = make_message("/search python asyncio")
        pool = make_pool()
        vault = make_mock_vault("Result 1\nResult 2\n")
        set_vault_provider(vault)

        response = await cmd_search(msg, pool, ["python", "asyncio"])

        assert isinstance(response, Response)
        assert "Result 1" in response.content
        vault.search.assert_called_once_with("python asyncio", timeout=25.0)

    @pytest.mark.asyncio
    async def test_empty_args_returns_usage_hint(self) -> None:
        msg = make_message("/search")
        pool = make_pool()
        response = await cmd_search(msg, pool, [])
        assert isinstance(response, Response)
        assert "Usage:" in response.content

    @pytest.mark.asyncio
    async def test_no_vault_provider_returns_not_available(self) -> None:
        """When vault provider is None (not set), returns a graceful message."""
        msg = make_message("/search query")
        pool = make_pool()
        # Do not call set_vault_provider — it stays None

        response = await cmd_search(msg, pool, ["query"])

        assert isinstance(response, Response)
        assert "vault" in response.content.lower()

    @pytest.mark.asyncio
    async def test_vault_absent_returns_graceful_string(self) -> None:
        msg = make_message("/search query")
        pool = make_pool()
        vault = make_mock_vault("vault CLI not available.")
        set_vault_provider(vault)

        response = await cmd_search(msg, pool, ["query"])

        assert isinstance(response, Response)
        assert "vault" in response.content.lower()

    @pytest.mark.asyncio
    async def test_multi_word_query_joined(self) -> None:
        msg = make_message("/search foo bar baz")
        pool = make_pool()
        vault = make_mock_vault("results")
        set_vault_provider(vault)

        await cmd_search(msg, pool, ["foo", "bar", "baz"])

        vault.search.assert_called_once_with("foo bar baz", timeout=25.0)
