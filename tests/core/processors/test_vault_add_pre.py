"""Tests for VaultAddProcessor.pre() — scraping enrichment and edge cases."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from lyra.core.commands.command_parser import CommandContext
from lyra.core.message import InboundMessage
from lyra.core.processors._scraping import _SAFE_SCRAPE_MAX_CHARS
from lyra.core.processors.vault_add import VaultAddProcessor
from lyra.core.trust import TrustLevel
from lyra.integrations.base import ScrapeFailed, SessionTools


def make_tools(
    scrape_return: str = "scraped content",
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
    vault.search = AsyncMock(return_value="results")
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
# VaultAddProcessor.pre()
# ---------------------------------------------------------------------------


class TestVaultAddProcessorPre:
    async def test_happy_path_enriches_msg_with_instruction_and_content(self) -> None:
        # Arrange
        tools = make_tools(scrape_return="scraped page content")
        proc = VaultAddProcessor(tools)
        msg = make_msg(
            text="/vault-add https://example.com",
            command_name="vault-add",
            command_args="https://example.com",
        )

        # Act
        enriched = await proc.pre(msg)

        # Assert
        assert "https://example.com" in enriched.text
        assert "scraped page content" in enriched.text
        # Instruction prompt is injected
        assert "Title:" in enriched.text or "summary" in enriched.text.lower()

    async def test_scrape_not_available_injects_unavailable_placeholder(self) -> None:
        # Arrange
        tools = make_tools(scrape_side_effect=ScrapeFailed("not_available"))
        proc = VaultAddProcessor(tools)
        msg = make_msg(
            text="/vault-add https://example.com",
            command_name="vault-add",
            command_args="https://example.com",
        )

        # Act
        enriched = await proc.pre(msg)

        # Assert
        assert "[scraping unavailable]" in enriched.text

    async def test_scrape_timeout_injects_timeout_placeholder(self) -> None:
        # Arrange
        tools = make_tools(scrape_side_effect=ScrapeFailed("timeout"))
        proc = VaultAddProcessor(tools)
        msg = make_msg(
            text="/vault-add https://example.com",
            command_name="vault-add",
            command_args="https://example.com",
        )

        # Act
        enriched = await proc.pre(msg)

        # Assert
        assert "[scrape timed out]" in enriched.text

    async def test_missing_url_returns_usage_message(self) -> None:
        # Arrange
        tools = make_tools()
        proc = VaultAddProcessor(tools)
        msg = make_msg(
            text="/vault-add",
            command_name="vault-add",
            command_args="",
        )

        # Act
        result = await proc.pre(msg)

        # Assert
        assert "Usage:" in result.text
        cast(Any, tools.scraper).scrape.assert_not_called()

    async def test_truncates_scraped_content_exceeding_max_chars(self) -> None:
        # Arrange
        long_content = "A" * (_SAFE_SCRAPE_MAX_CHARS + 1000)
        tools = make_tools(scrape_return=long_content)
        proc = VaultAddProcessor(tools)
        msg = make_msg(
            text="/vault-add https://example.com",
            command_name="vault-add",
            command_args="https://example.com",
        )

        # Act
        enriched = await proc.pre(msg)

        # Assert
        assert "[content truncated]" in enriched.text
        # Raw scraped payload (the contiguous run of A's) is bounded. Robust
        # against instruction text changes that may themselves contain "A".
        assert "A" * (_SAFE_SCRAPE_MAX_CHARS + 1) not in enriched.text

    async def test_does_not_truncate_content_within_limit(self) -> None:
        # Arrange
        content = "Short content."
        tools = make_tools(scrape_return=content)
        proc = VaultAddProcessor(tools)
        msg = make_msg(
            text="/vault-add https://example.com",
            command_name="vault-add",
            command_args="https://example.com",
        )

        # Act
        enriched = await proc.pre(msg)

        # Assert
        assert "[content truncated]" not in enriched.text
        assert content in enriched.text
