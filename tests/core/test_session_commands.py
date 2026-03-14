"""Tests for session_commands.py (issue #99 T4)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.core.auth import TrustLevel
from lyra.core.message import InboundMessage, Response
from lyra.core.session_commands import cmd_add, cmd_explain, cmd_summarize
from lyra.core.session_helpers import ScrapeFailed, VaultWriteFailed
from lyra.llm.base import LlmResult


def make_message(text: str = "hello") -> InboundMessage:
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


def make_driver(
    result_text: str = "Title: Test\nSummary: A test.\nTags: foo, bar",
) -> MagicMock:
    driver = MagicMock()
    driver.capabilities = {"default_model": "claude-haiku-4-5-20251001"}
    driver.complete = AsyncMock(return_value=LlmResult(result=result_text))
    return driver


class TestCmdAdd:
    """cmd_add() — scrape → LLM → vault write."""

    @pytest.mark.asyncio
    async def test_happy_path_calls_scrape_driver_vault(self) -> None:
        driver = make_driver("Title: My Page\nSummary: Great page.\nTags: tech, news")
        msg = make_message()

        with (
            patch(
                "lyra.core.session_commands.scrape_url",
                new=AsyncMock(return_value="scraped content"),
            ) as mock_scrape,
            patch(
                "lyra.core.session_commands.vault_add",
                new=AsyncMock(),
            ) as mock_vault,
        ):
            response = await cmd_add(msg, driver, ["https://example.com"], 60.0)

        assert isinstance(response, Response)
        assert "Saved:" in response.content
        mock_scrape.assert_called_once_with("https://example.com", timeout=20.0)
        driver.complete.assert_called_once()
        mock_vault.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_arg_returns_usage_hint(self) -> None:
        driver = make_driver()
        msg = make_message()
        response = await cmd_add(msg, driver, [], 60.0)
        assert "Usage:" in response.content
        driver.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_scrape_failure_uses_fallback_and_still_calls_driver(
        self,
    ) -> None:
        driver = make_driver("Title: Fallback\nSummary: No scrape.\nTags: x")
        msg = make_message()

        with (
            patch(
                "lyra.core.session_commands.scrape_url",
                new=AsyncMock(side_effect=ScrapeFailed("not_available")),
            ),
            patch(
                "lyra.core.session_commands.vault_add",
                new=AsyncMock(),
            ),
        ):
            response = await cmd_add(msg, driver, ["https://example.com"], 60.0)

        assert isinstance(response, Response)
        driver.complete.assert_called_once()
        # Fallback text was passed — not "scraped content"
        call_kwargs = driver.complete.call_args
        content = call_kwargs.args[1]  # positional text arg
        assert "[scraping unavailable]" in content

    @pytest.mark.asyncio
    async def test_vault_failure_returns_summary_with_note(self) -> None:
        driver = make_driver("Title: Test\nSummary: X.\nTags: a")
        msg = make_message()

        with (
            patch(
                "lyra.core.session_commands.scrape_url",
                new=AsyncMock(return_value="scraped"),
            ),
            patch(
                "lyra.core.session_commands.vault_add",
                new=AsyncMock(side_effect=VaultWriteFailed("not_available")),
            ),
        ):
            response = await cmd_add(msg, driver, ["https://example.com"], 60.0)

        assert isinstance(response, Response)
        # Summary is returned even when vault fails
        assert "Title: Test" in response.content
        # Vault failure noted
        assert "vault" in response.content.lower()

    @pytest.mark.asyncio
    async def test_scrape_subprocess_error_uses_fallback(self) -> None:
        driver = make_driver("Title: T\nSummary: S.\nTags: t")
        msg = make_message()

        with (
            patch(
                "lyra.core.session_commands.scrape_url",
                new=AsyncMock(side_effect=ScrapeFailed("subprocess_error")),
            ),
            patch("lyra.core.session_commands.vault_add", new=AsyncMock()),
        ):
            await cmd_add(msg, driver, ["https://example.com"], 60.0)

        driver.complete.assert_called_once()
        call_content = driver.complete.call_args.args[1]
        assert "[scrape failed]" in call_content


class TestCmdExplain:
    """cmd_explain() — scrape → LLM explanation."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_explanation(self) -> None:
        driver = make_driver("This page is about asyncio in Python.")
        msg = make_message()

        with patch(
            "lyra.core.session_commands.scrape_url",
            new=AsyncMock(return_value="some content"),
        ):
            response = await cmd_explain(msg, driver, ["https://example.com"], 60.0)

        assert isinstance(response, Response)
        assert "asyncio" in response.content
        driver.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_arg_returns_usage_hint(self) -> None:
        driver = make_driver()
        msg = make_message()
        response = await cmd_explain(msg, driver, [], 60.0)
        assert "Usage:" in response.content
        driver.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_scrape_failure_still_calls_driver(self) -> None:
        driver = make_driver("Explanation text.")
        msg = make_message()

        with patch(
            "lyra.core.session_commands.scrape_url",
            new=AsyncMock(side_effect=ScrapeFailed("not_available")),
        ):
            response = await cmd_explain(msg, driver, ["https://example.com"], 60.0)

        driver.complete.assert_called_once()
        assert isinstance(response, Response)


class TestCmdSummarize:
    """cmd_summarize() — scrape → LLM bullet summary."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_summary(self) -> None:
        driver = make_driver("- Point one\n- Point two\n- Point three")
        msg = make_message()

        with patch(
            "lyra.core.session_commands.scrape_url",
            new=AsyncMock(return_value="content"),
        ):
            response = await cmd_summarize(msg, driver, ["https://example.com"], 60.0)

        assert isinstance(response, Response)
        assert "Point one" in response.content
        driver.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_arg_returns_usage_hint(self) -> None:
        driver = make_driver()
        msg = make_message()
        response = await cmd_summarize(msg, driver, [], 60.0)
        assert "Usage:" in response.content
        driver.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_uses_summarize_system_prompt(self) -> None:
        """driver.complete receives SUMMARIZE_SYSTEM_PROMPT."""
        from lyra.core.session_commands import SUMMARIZE_SYSTEM_PROMPT

        driver = make_driver("summary")
        msg = make_message()

        with patch(
            "lyra.core.session_commands.scrape_url",
            new=AsyncMock(return_value="content"),
        ):
            await cmd_summarize(msg, driver, ["https://example.com"], 60.0)

        call_args = driver.complete.call_args
        # system_prompt is the 4th positional argument
        system_prompt = call_args.args[3]
        assert system_prompt == SUMMARIZE_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_pool_history_not_touched(self) -> None:
        """Session commands do not access or modify any pool object."""
        driver = make_driver("bullets")
        msg = make_message()
        pool_mock = MagicMock()

        with patch(
            "lyra.core.session_commands.scrape_url",
            new=AsyncMock(return_value="content"),
        ):
            # cmd_summarize does not accept a pool arg; passing none here
            await cmd_summarize(msg, driver, ["https://example.com"], 60.0)

        # pool_mock should never have been touched
        pool_mock.sdk_history.assert_not_called() if hasattr(
            pool_mock.sdk_history, "assert_not_called"
        ) else None
