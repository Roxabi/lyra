"""Tests for VaultAddProcessor.post() — vault persistence and concurrency."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from lyra.core.commands.command_parser import CommandContext
from lyra.core.message import InboundMessage, Response
from lyra.core.processors.vault_add import VaultAddProcessor
from lyra.core.trust import TrustLevel
from lyra.integrations.base import SessionTools, VaultWriteFailed


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
# VaultAddProcessor.post()
# ---------------------------------------------------------------------------


class TestVaultAddProcessorPost:
    async def test_happy_path_calls_vault_add_with_parsed_title_and_tags(self) -> None:
        # Arrange
        tools = make_tools()
        proc = VaultAddProcessor(tools)
        msg = make_msg(
            text="/vault-add https://example.com",
            command_name="vault-add",
            command_args="https://example.com",
        )
        response = Response(
            content="Title: My Page\nTags: tech, python\n\nA helpful summary."
        )

        # Act
        result = await proc.post(msg, response)

        # Assert
        tools.vault.add.assert_called_once()  # type: ignore[attr-defined]
        call_kwargs = tools.vault.add.call_args  # type: ignore[attr-defined]
        assert call_kwargs.args[0] == "My Page"  # title
        assert "tech" in call_kwargs.args[1]  # tags list
        assert "python" in call_kwargs.args[1]
        assert call_kwargs.args[2] == "https://example.com"  # url
        assert "✅ Saved to vault." in result.content  # success recap appended

    async def test_vault_not_available_appends_note_to_response(self) -> None:
        # Arrange
        tools = make_tools(vault_side_effect=VaultWriteFailed("not_available"))
        proc = VaultAddProcessor(tools)
        msg = make_msg(
            text="/vault-add https://example.com",
            command_name="vault-add",
            command_args="https://example.com",
        )
        response = Response(content="Title: Page\nTags: a\n\nSummary.")

        # Act
        result = await proc.post(msg, response)

        # Assert
        assert "⚠️" in result.content
        assert "Vault CLI not available" in result.content

    async def test_vault_write_failed_appends_generic_note(self) -> None:
        # Arrange
        tools = make_tools(vault_side_effect=VaultWriteFailed("other"))
        proc = VaultAddProcessor(tools)
        msg = make_msg(
            text="/vault-add https://example.com",
            command_name="vault-add",
            command_args="https://example.com",
        )
        response = Response(content="Title: Page\nTags: a\n\nSummary.")

        # Act
        result = await proc.post(msg, response)

        # Assert
        assert "⚠️" in result.content
        assert "Vault write failed" in result.content

    async def test_unexpected_exception_appends_fail_note(self) -> None:
        # Arrange — vault.add raises something other than VaultWriteFailed
        tools = make_tools(vault_side_effect=RuntimeError("disk full"))
        proc = VaultAddProcessor(tools)
        msg = make_msg(
            text="/vault-add https://example.com",
            command_name="vault-add",
            command_args="https://example.com",
        )
        response = Response(content="Title: Page\nTags: a\n\nSummary.")

        # Act
        result = await proc.post(msg, response)

        # Assert — unexpected exception is caught and a fail note is still shown
        assert "⚠️" in result.content
        assert "Vault write failed" in result.content

    async def test_empty_url_in_msg_returns_response_unchanged(self) -> None:
        # Arrange
        tools = make_tools()
        proc = VaultAddProcessor(tools)
        # Message with no URL (command args empty)
        msg = make_msg(
            text="/vault-add",
            command_name="vault-add",
            command_args="",
        )
        response = Response(content="Some LLM response.")

        # Act
        result = await proc.post(msg, response)

        # Assert
        assert result is response
        tools.vault.add.assert_not_called()  # type: ignore[attr-defined]

    async def test_response_content_preserved_on_vault_success(self) -> None:
        # Arrange
        tools = make_tools()
        proc = VaultAddProcessor(tools)
        msg = make_msg(
            text="/vault-add https://example.com",
            command_name="vault-add",
            command_args="https://example.com",
        )
        original_content = "Title: Page\nTags: a\n\nThis is the summary."
        response = Response(content=original_content)

        # Act
        result = await proc.post(msg, response)

        # Assert — success recap is appended to original content
        assert result.content.startswith(original_content)
        assert "✅ Saved to vault." in result.content

    async def test_falls_back_to_url_as_title_when_no_title_line(self) -> None:
        # Arrange
        tools = make_tools()
        proc = VaultAddProcessor(tools)
        url = "https://example.com"
        msg = make_msg(
            text=f"/vault-add {url}",
            command_name="vault-add",
            command_args=url,
        )
        response = Response(content="No title line here, just content.")

        # Act
        await proc.post(msg, response)

        # Assert — URL used as fallback title
        call_args = tools.vault.add.call_args  # type: ignore[attr-defined]
        assert call_args.args[0] == url

    async def test_empty_tags_when_no_tags_line(self) -> None:
        # Arrange
        tools = make_tools()
        proc = VaultAddProcessor(tools)
        msg = make_msg(
            text="/vault-add https://example.com",
            command_name="vault-add",
            command_args="https://example.com",
        )
        response = Response(content="Title: Page\nNo tags line here.")

        # Act
        await proc.post(msg, response)

        # Assert — empty tags list passed
        call_args = tools.vault.add.call_args  # type: ignore[attr-defined]
        assert call_args.args[1] == []


# ---------------------------------------------------------------------------
# VaultAddProcessor — concurrent isolation (B3 verification)
# ---------------------------------------------------------------------------


class TestVaultAddProcessorConcurrency:
    async def test_concurrent_post_uses_correct_urls(self) -> None:
        """Two concurrent post() calls with different URLs use the correct original msg
        URLs.

        Verifies the B3 fix: URL is re-extracted from the original InboundMessage
        in post() rather than stored as mutable instance state, so concurrent
        execution never mixes up URLs between requests.
        """
        # Arrange — separate tools per processor to track calls independently
        tools_a = make_tools()
        tools_b = make_tools()

        proc_a = VaultAddProcessor(tools_a)
        proc_b = VaultAddProcessor(tools_b)

        msg_a = make_msg(
            text="/vault-add https://example-a.com",
            command_name="vault-add",
            command_args="https://example-a.com",
        )
        msg_b = make_msg(
            text="/vault-add https://example-b.com",
            command_name="vault-add",
            command_args="https://example-b.com",
        )
        response_a = Response(content="Title: Page A\nTags: alpha\n\nSummary A.")
        response_b = Response(content="Title: Page B\nTags: beta\n\nSummary B.")

        # Act — run both post() calls concurrently
        await asyncio.gather(
            proc_a.post(msg_a, response_a),
            proc_b.post(msg_b, response_b),
        )

        # Assert — each vault received exactly one call with the correct URL
        tools_a.vault.add.assert_called_once()  # type: ignore[attr-defined]
        tools_b.vault.add.assert_called_once()  # type: ignore[attr-defined]

        call_a = tools_a.vault.add.call_args  # type: ignore[attr-defined]
        call_b = tools_b.vault.add.call_args  # type: ignore[attr-defined]

        assert call_a.args[2] == "https://example-a.com"
        assert call_b.args[2] == "https://example-b.com"

    async def test_concurrent_post_does_not_mix_titles(self) -> None:
        """Concurrent post() calls parse titles from their own response,
        not each other's.
        """
        # Arrange
        tools_a = make_tools()
        tools_b = make_tools()

        proc_a = VaultAddProcessor(tools_a)
        proc_b = VaultAddProcessor(tools_b)

        msg_a = make_msg(
            text="/vault-add https://example-a.com",
            command_name="vault-add",
            command_args="https://example-a.com",
        )
        msg_b = make_msg(
            text="/vault-add https://example-b.com",
            command_name="vault-add",
            command_args="https://example-b.com",
        )
        response_a = Response(content="Title: Alpha Title\nTags: a\n\nContent A.")
        response_b = Response(content="Title: Beta Title\nTags: b\n\nContent B.")

        # Act
        await asyncio.gather(
            proc_a.post(msg_a, response_a),
            proc_b.post(msg_b, response_b),
        )

        # Assert — titles come from each processor's own response
        call_a = tools_a.vault.add.call_args  # type: ignore[attr-defined]
        call_b = tools_b.vault.add.call_args  # type: ignore[attr-defined]

        assert call_a.args[0] == "Alpha Title"
        assert call_b.args[0] == "Beta Title"
