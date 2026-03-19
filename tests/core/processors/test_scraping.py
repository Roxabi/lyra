"""Tests for _scraping.py — shared URL extraction and scraping helpers (issue #363)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from lyra.core.command_parser import CommandContext
from lyra.core.message import InboundMessage
from lyra.core.processors._scraping import (
    _SAFE_SCRAPE_MAX_CHARS,
    ScrapingProcessor,
    _extract_and_validate_url,
    _scrape_with_fallback,
)
from lyra.core.trust import TrustLevel
from lyra.integrations.base import ScrapeFailed, SessionTools

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_tools(scrape_return: str = "scraped", scrape_side_effect=None) -> SessionTools:
    scraper = MagicMock()
    if scrape_side_effect is not None:
        scraper.scrape = AsyncMock(side_effect=scrape_side_effect)
    else:
        scraper.scrape = AsyncMock(return_value=scrape_return)
    vault = MagicMock()
    vault.add = AsyncMock()
    vault.search = AsyncMock(return_value="")
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


# Concrete subclass of ScrapingProcessor for testing the abstract base
class _ConcreteProcessor(ScrapingProcessor):
    @property
    def instruction(self) -> str:
        return "Test instruction"


# ---------------------------------------------------------------------------
# _extract_and_validate_url()
# ---------------------------------------------------------------------------


class TestExtractAndValidateUrl:
    def test_valid_http_url_returns_url_and_no_error(self) -> None:
        # Arrange
        msg = make_msg(
            text="/explain http://example.com",
            command_name="explain",
            command_args="http://example.com",
        )

        # Act
        url, err = _extract_and_validate_url(msg)

        # Assert
        assert url == "http://example.com"
        assert err is None

    def test_valid_https_url_returns_url_and_no_error(self) -> None:
        # Arrange
        msg = make_msg(
            text="/explain https://example.com/path?q=1",
            command_name="explain",
            command_args="https://example.com/path?q=1",
        )

        # Act
        url, err = _extract_and_validate_url(msg)

        # Assert
        assert url == "https://example.com/path?q=1"
        assert err is None

    def test_file_scheme_returns_error(self) -> None:
        # Arrange
        msg = make_msg(
            text="/explain file:///etc/passwd",
            command_name="explain",
            command_args="file:///etc/passwd",
        )

        # Act
        url, err = _extract_and_validate_url(msg)

        # Assert
        assert url == ""
        assert err is not None
        assert "http://" in err or "https://" in err

    def test_empty_args_returns_usage_error(self) -> None:
        # Arrange
        msg = make_msg(
            text="/explain",
            command_name="explain",
            command_args="",
        )

        # Act
        url, err = _extract_and_validate_url(msg)

        # Assert
        assert url == ""
        assert err is not None
        assert "Usage:" in err

    def test_no_command_falls_back_to_text(self) -> None:
        # Arrange — message with no command but text is a valid URL
        msg = make_msg(text="https://example.com")

        # Act
        url, err = _extract_and_validate_url(msg)

        # Assert
        assert url == "https://example.com"
        assert err is None

    def test_no_command_plain_text_returns_error(self) -> None:
        # Arrange — no command, text is not a URL
        msg = make_msg(text="just some text")

        # Act
        url, err = _extract_and_validate_url(msg)

        # Assert
        assert url == ""
        assert err is not None

    def test_command_name_appears_in_usage_error(self) -> None:
        # Arrange
        msg = make_msg(
            text="/summarize",
            command_name="summarize",
            command_args="",
        )

        # Act
        _, err = _extract_and_validate_url(msg)

        # Assert
        assert err is not None
        assert "/summarize" in err


# ---------------------------------------------------------------------------
# _scrape_with_fallback()
# ---------------------------------------------------------------------------


class TestScrapeWithFallback:
    async def test_happy_path_returns_scraped_content(self) -> None:
        # Arrange
        scraper = MagicMock()
        scraper.scrape = AsyncMock(return_value="page content")

        # Act
        result = await _scrape_with_fallback(
            scraper, "https://example.com", timeout=30.0
        )

        # Assert
        assert result == "page content"
        scraper.scrape.assert_called_once_with("https://example.com", timeout=30.0)

    async def test_not_available_returns_unavailable_fallback(self) -> None:
        # Arrange
        scraper = MagicMock()
        scraper.scrape = AsyncMock(side_effect=ScrapeFailed("not_available"))

        # Act
        result = await _scrape_with_fallback(
            scraper, "https://example.com", timeout=30.0
        )

        # Assert
        assert "[scraping unavailable]" in result

    async def test_timeout_returns_timeout_fallback(self) -> None:
        # Arrange
        scraper = MagicMock()
        scraper.scrape = AsyncMock(side_effect=ScrapeFailed("timeout"))

        # Act
        result = await _scrape_with_fallback(
            scraper, "https://example.com", timeout=30.0
        )

        # Assert
        assert "[scrape timed out]" in result

    async def test_subprocess_error_returns_generic_fallback(self) -> None:
        # Arrange
        scraper = MagicMock()
        scraper.scrape = AsyncMock(side_effect=ScrapeFailed("subprocess_error"))

        # Act
        result = await _scrape_with_fallback(
            scraper, "https://example.com", timeout=30.0
        )

        # Assert
        assert "[scrape failed]" in result

    async def test_url_included_in_fallback_message(self) -> None:
        # Arrange
        scraper = MagicMock()
        url = "https://example.com/some/path"
        scraper.scrape = AsyncMock(side_effect=ScrapeFailed("not_available"))

        # Act
        result = await _scrape_with_fallback(scraper, url, timeout=30.0)

        # Assert
        assert url in result


# ---------------------------------------------------------------------------
# ScrapingProcessor.pre() — truncation
# ---------------------------------------------------------------------------


class TestScrapingProcessorTruncation:
    async def test_pre_truncates_content_exceeding_max_chars(self) -> None:
        # Arrange
        long_content = "x" * (_SAFE_SCRAPE_MAX_CHARS + 500)
        tools = make_tools(scrape_return=long_content)
        proc = _ConcreteProcessor(tools)
        msg = make_msg(
            text="/explain https://example.com",
            command_name="explain",
            command_args="https://example.com",
        )

        # Act
        enriched = await proc.pre(msg)

        # Assert
        assert "[content truncated]" in enriched.text
        # The raw scraped content should be capped
        assert len(enriched.text) < len(long_content)

    async def test_pre_does_not_truncate_content_within_limit(self) -> None:
        # Arrange
        short_content = "page content"
        tools = make_tools(scrape_return=short_content)
        proc = _ConcreteProcessor(tools)
        msg = make_msg(
            text="/explain https://example.com",
            command_name="explain",
            command_args="https://example.com",
        )

        # Act
        enriched = await proc.pre(msg)

        # Assert
        assert "[content truncated]" not in enriched.text
        assert short_content in enriched.text

    async def test_pre_injects_instruction_and_url(self) -> None:
        # Arrange
        tools = make_tools(scrape_return="content")
        proc = _ConcreteProcessor(tools)
        msg = make_msg(
            text="/explain https://example.com",
            command_name="explain",
            command_args="https://example.com",
        )

        # Act
        enriched = await proc.pre(msg)

        # Assert
        assert "Test instruction" in enriched.text
        assert "https://example.com" in enriched.text
        assert "content" in enriched.text

    async def test_pre_returns_error_msg_for_invalid_url(self) -> None:
        # Arrange
        tools = make_tools()
        proc = _ConcreteProcessor(tools)
        msg = make_msg(
            text="/explain",
            command_name="explain",
            command_args="",
        )

        # Act
        result = await proc.pre(msg)

        # Assert
        assert "Usage:" in result.text
        tools.scraper.scrape.assert_not_called()  # type: ignore[attr-defined]
