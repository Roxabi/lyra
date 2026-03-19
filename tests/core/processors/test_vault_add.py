"""Tests for VaultAddProcessor and helpers (issue #363)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from lyra.core.command_parser import CommandContext
from lyra.core.message import InboundMessage, Response
from lyra.core.processors._scraping import _SAFE_SCRAPE_MAX_CHARS
from lyra.core.processors.vault_add import VaultAddProcessor, _parse_tags, _parse_title
from lyra.core.trust import TrustLevel
from lyra.integrations.base import ScrapeFailed, SessionTools, VaultWriteFailed

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
# _parse_title()
# ---------------------------------------------------------------------------


class TestParseTitle:
    def test_parses_title_line(self) -> None:
        # Arrange
        text = "Title: My Great Page\nTags: foo, bar\nSome body text."

        # Act
        result = _parse_title(text, fallback="fallback-url")

        # Assert
        assert result == "My Great Page"

    def test_falls_back_when_no_title_line(self) -> None:
        # Arrange
        text = "No title here, just some content."

        # Act
        result = _parse_title(text, fallback="https://example.com")

        # Assert
        assert result == "https://example.com"

    def test_strips_whitespace_from_title(self) -> None:
        # Arrange
        text = "Title:   Trimmed Title   \nBody."

        # Act
        result = _parse_title(text, fallback="fallback")

        # Assert
        assert result == "Trimmed Title"

    def test_handles_empty_text(self) -> None:
        # Arrange / Act
        result = _parse_title("", fallback="fallback-url")

        # Assert
        assert result == "fallback-url"

    def test_multiline_text_matches_first_title(self) -> None:
        # Arrange
        text = "Intro paragraph.\nTitle: Correct Title\nMore text.\nTitle: Second"

        # Act
        result = _parse_title(text, fallback="fallback")

        # Assert
        assert result == "Correct Title"


# ---------------------------------------------------------------------------
# _parse_tags()
# ---------------------------------------------------------------------------


class TestParseTags:
    def test_parses_comma_separated_tags(self) -> None:
        # Arrange
        text = "Title: Page\nTags: foo, bar, baz\nBody."

        # Act
        result = _parse_tags(text)

        # Assert
        assert result == ["foo", "bar", "baz"]

    def test_returns_empty_list_when_no_tags_line(self) -> None:
        # Arrange
        text = "Title: Page\nNo tags here."

        # Act
        result = _parse_tags(text)

        # Assert
        assert result == []

    def test_strips_whitespace_from_each_tag(self) -> None:
        # Arrange
        text = "Tags:  python ,  asyncio ,  testing  "

        # Act
        result = _parse_tags(text)

        # Assert
        assert result == ["python", "asyncio", "testing"]

    def test_handles_empty_text(self) -> None:
        # Arrange / Act
        result = _parse_tags("")

        # Assert
        assert result == []

    def test_ignores_empty_segments(self) -> None:
        # Arrange — trailing comma produces empty segment
        text = "Tags: alpha, beta, "

        # Act
        result = _parse_tags(text)

        # Assert
        assert result == ["alpha", "beta"]

    def test_single_tag(self) -> None:
        # Arrange
        text = "Tags: only-one"

        # Act
        result = _parse_tags(text)

        # Assert
        assert result == ["only-one"]


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
        tools.scraper.scrape.assert_not_called()  # type: ignore[attr-defined]

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
        # Total text length grows by instruction overhead, but raw content is bounded
        assert enriched.text.count("A") <= _SAFE_SCRAPE_MAX_CHARS

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
        assert result is response  # response returned unchanged on success

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
        assert "vault CLI not available" in result.content

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
        assert "vault write failed" in result.content

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

        # Assert — on success, response is returned without modification
        assert result.content == original_content

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
