"""Tests for session_commands.py (issue #99 T4, #360)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.message import InboundMessage, Response
from lyra.core.session_commands import cmd_add, cmd_explain, cmd_summarize
from lyra.core.trust import TrustLevel
from lyra.integrations.base import ScrapeFailed, SessionTools, VaultWriteFailed
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


def make_tools(
    scrape_return: str = "scraped content",
    scrape_side_effect=None,
    vault_side_effect=None,
) -> SessionTools:
    """Build a SessionTools with mock scraper and vault."""
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


class TestCmdAdd:
    """cmd_add() — scrape → LLM → vault write."""

    @pytest.mark.asyncio
    async def test_happy_path_calls_scrape_driver_vault(self) -> None:
        driver = make_driver("Title: My Page\nSummary: Great page.\nTags: tech, news")
        tools = make_tools(scrape_return="scraped content")
        msg = make_message()

        response = await cmd_add(msg, driver, tools, ["https://example.com"], 60.0)

        assert isinstance(response, Response)
        assert "Saved:" in response.content
        tools.scraper.scrape.assert_called_once_with(  # type: ignore[attr-defined]
            "https://example.com", timeout=20.0
        )
        driver.complete.assert_called_once()
        tools.vault.add.assert_called_once()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_missing_arg_returns_usage_hint(self) -> None:
        driver = make_driver()
        tools = make_tools()
        msg = make_message()
        response = await cmd_add(msg, driver, tools, [], 60.0)
        assert "Usage:" in response.content
        driver.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_scrape_failure_uses_fallback_and_still_calls_driver(
        self,
    ) -> None:
        driver = make_driver("Title: Fallback\nSummary: No scrape.\nTags: x")
        tools = make_tools(scrape_side_effect=ScrapeFailed("not_available"))
        msg = make_message()

        response = await cmd_add(msg, driver, tools, ["https://example.com"], 60.0)

        assert isinstance(response, Response)
        driver.complete.assert_called_once()
        # Fallback text was passed via messages — not "scraped content"
        call_kwargs = driver.complete.call_args
        messages = call_kwargs.kwargs["messages"]
        content = messages[0]["content"]
        assert "[scraping unavailable]" in content

    @pytest.mark.asyncio
    async def test_vault_failure_returns_summary_with_note(self) -> None:
        driver = make_driver("Title: Test\nSummary: X.\nTags: a")
        tools = make_tools(vault_side_effect=VaultWriteFailed("not_available"))
        msg = make_message()

        response = await cmd_add(msg, driver, tools, ["https://example.com"], 60.0)

        assert isinstance(response, Response)
        # Summary is returned even when vault fails
        assert "Title: Test" in response.content
        # Vault failure noted
        assert "vault" in response.content.lower()

    @pytest.mark.asyncio
    async def test_scrape_subprocess_error_uses_fallback(self) -> None:
        driver = make_driver("Title: T\nSummary: S.\nTags: t")
        tools = make_tools(scrape_side_effect=ScrapeFailed("subprocess_error"))
        msg = make_message()

        await cmd_add(msg, driver, tools, ["https://example.com"], 60.0)

        driver.complete.assert_called_once()
        messages = driver.complete.call_args.kwargs["messages"]
        assert "[scrape failed]" in messages[0]["content"]


class TestCmdExplain:
    """cmd_explain() — scrape → LLM explanation."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_explanation(self) -> None:
        driver = make_driver("This page is about asyncio in Python.")
        tools = make_tools(scrape_return="some content")
        msg = make_message()

        response = await cmd_explain(msg, driver, tools, ["https://example.com"], 60.0)

        assert isinstance(response, Response)
        assert "asyncio" in response.content
        driver.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_arg_returns_usage_hint(self) -> None:
        driver = make_driver()
        tools = make_tools()
        msg = make_message()
        response = await cmd_explain(msg, driver, tools, [], 60.0)
        assert "Usage:" in response.content
        driver.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_scrape_failure_still_calls_driver(self) -> None:
        driver = make_driver("Explanation text.")
        tools = make_tools(scrape_side_effect=ScrapeFailed("not_available"))
        msg = make_message()

        response = await cmd_explain(msg, driver, tools, ["https://example.com"], 60.0)

        driver.complete.assert_called_once()
        assert isinstance(response, Response)


class TestCmdSummarize:
    """cmd_summarize() — scrape → LLM bullet summary."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_summary(self) -> None:
        driver = make_driver("- Point one\n- Point two\n- Point three")
        tools = make_tools(scrape_return="content")
        msg = make_message()

        response = await cmd_summarize(
            msg, driver, tools, ["https://example.com"], 60.0
        )

        assert isinstance(response, Response)
        assert "Point one" in response.content
        driver.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_arg_returns_usage_hint(self) -> None:
        driver = make_driver()
        tools = make_tools()
        msg = make_message()
        response = await cmd_summarize(msg, driver, tools, [], 60.0)
        assert "Usage:" in response.content
        driver.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_uses_summarize_system_prompt(self) -> None:
        """driver.complete receives SUMMARIZE_SYSTEM_PROMPT."""
        from lyra.core.session_commands import SUMMARIZE_SYSTEM_PROMPT

        driver = make_driver("summary")
        tools = make_tools(scrape_return="content")
        msg = make_message()

        await cmd_summarize(msg, driver, tools, ["https://example.com"], 60.0)

        call_args = driver.complete.call_args
        # system_prompt is the 4th positional argument
        system_prompt = call_args.args[3]
        assert system_prompt == SUMMARIZE_SYSTEM_PROMPT

    # AC-7 (pool history isolation) is covered by the integration test
    # tests/integration/test_command_sessions.py::TestPoolHistoryUnchanged which
    # pre-seeds a real pool and asserts sdk_history and history are unchanged.
