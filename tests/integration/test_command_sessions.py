"""Integration smoke tests for hub command sessions (issue #99 T7).

End-to-end tests using a mock LlmProvider and patched subprocess calls.
Tests cover AC-1 through AC-11 at the integration level.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.command_loader import CommandLoader
from lyra.core.command_parser import CommandParser
from lyra.core.command_router import CommandRouter
from lyra.core.message import InboundMessage, Response
from lyra.core.pool import Pool
from lyra.core.trust import TrustLevel
from lyra.integrations.base import SessionTools
from lyra.llm.base import LlmProvider, LlmResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_message(text: str) -> InboundMessage:
    parser = CommandParser()
    cmd_ctx = parser.parse(text)
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
        command=cmd_ctx,
    )


def make_mock_driver(
    result_text: str = "Title: X\nSummary: Y\nTags: a, b",
) -> MagicMock:
    driver = MagicMock()
    driver.capabilities = {"default_model": "claude-haiku-4-5-20251001"}
    driver.complete = AsyncMock(return_value=LlmResult(result=result_text))
    return driver


def make_mock_tools() -> tuple[SessionTools, MagicMock, MagicMock]:
    """Return (tools, mock_scraper, mock_vault) for session command tests."""
    scraper = MagicMock()
    scraper.scrape = AsyncMock(return_value="page content")
    vault = MagicMock()
    vault.add = AsyncMock()
    vault.search = AsyncMock(return_value="results")
    tools = SessionTools(scraper=scraper, vault=vault)
    return tools, scraper, vault


async def _stub_vault_add(msg, driver, tools, args, timeout):  # noqa: ARG001
    """Minimal stub replacing cmd_add for router-contract tests."""
    if not args:
        return Response(content="Usage: /vault-add <url>")
    url = args[0]
    content = await tools.scraper.scrape(url, timeout=timeout / 3)
    from lyra.core.agent_config import ModelConfig
    model_cfg = ModelConfig(backend="anthropic-sdk", model="claude-haiku-4-5-20251001")
    result = await driver.complete(
        "session:vault-add", "", model_cfg, "You are a helper.",
        messages=[{"role": "user", "content": content}],
    )
    return Response(content=f"Saved: {url}\n\n{result.result}")


async def _stub_explain(msg, driver, tools, args, timeout):  # noqa: ARG001
    """Minimal stub replacing cmd_explain for router-contract tests."""
    if not args:
        return Response(content="Usage: /explain <url>")
    url = args[0]
    content = await tools.scraper.scrape(url, timeout=timeout / 3)
    from lyra.core.agent_config import ModelConfig
    model_cfg = ModelConfig(backend="anthropic-sdk", model="claude-haiku-4-5-20251001")
    result = await driver.complete(
        "session:explain", "", model_cfg, "You are a helper.",
        messages=[{"role": "user", "content": content}],
    )
    return Response(content=result.result)


async def _stub_summarize(msg, driver, tools, args, timeout):  # noqa: ARG001
    """Minimal stub replacing cmd_summarize for router-contract tests."""
    if not args:
        return Response(content="Usage: /summarize <url>")
    url = args[0]
    content = await tools.scraper.scrape(url, timeout=timeout / 3)
    from lyra.core.agent_config import ModelConfig
    model_cfg = ModelConfig(backend="anthropic-sdk", model="claude-haiku-4-5-20251001")
    result = await driver.complete(
        "session:summarize", "", model_cfg, "You are a helper.",
        messages=[{"role": "user", "content": content}],
    )
    return Response(content=result.result)


def make_router_with_session(
    tmp_path: Path,
    driver: "LlmProvider | None" = None,
    tools: "SessionTools | None" = None,
) -> CommandRouter:
    """Build a CommandRouter with stub session commands for router-contract tests.

    The old session_commands module was deleted in issue #363 (processor pipeline
    migration). These stubs preserve the router-level contracts without depending
    on the removed module.
    """
    _tools = tools
    if _tools is None:
        _tools, _, _ = make_mock_tools()

    # Minimal plugins dir (no real plugins needed for session command tests)
    loader = CommandLoader(tmp_path)
    router = CommandRouter(
        command_loader=loader,
        enabled_plugins=[],
        session_driver=driver,
        patterns={"bare_url": True},
    )
    router.register_session_command(
        "vault-add", _stub_vault_add, tools=_tools, description="Save URL", timeout=60.0
    )
    router.register_session_command(
        "explain", _stub_explain, tools=_tools, description="Explain URL", timeout=60.0
    )
    router.register_session_command(
        "summarize", _stub_summarize, tools=_tools, description="Summarize URL",
        timeout=60.0
    )
    return router


def make_pool() -> MagicMock:
    pool = MagicMock(spec=Pool)
    pool.sdk_history = []
    pool.history = []
    return pool


# ---------------------------------------------------------------------------
# AC-1: Bare URL → /vault-add dispatch
# ---------------------------------------------------------------------------


class TestBareUrlToAdd:
    """AC-1: A bare URL message is dispatched to /vault-add."""

    @pytest.mark.asyncio
    async def test_bare_url_dispatched_to_add(self, tmp_path: Path) -> None:
        driver = make_mock_driver()
        tools, mock_scraper, mock_vault = make_mock_tools()
        router = make_router_with_session(tmp_path, driver=driver, tools=tools)
        pool = make_pool()

        msg = make_message("https://example.com/article")
        # prepare() rewrites the bare URL to /vault-add
        msg = router.prepare(msg)
        assert msg.command is not None
        assert msg.command.name == "vault-add"

        response = await router.dispatch(msg, pool=pool)

        assert response is not None
        assert isinstance(response, Response)
        driver.complete.assert_called_once()
        mock_scraper.scrape.assert_called_once()

    @pytest.mark.asyncio
    async def test_is_command_returns_true_for_bare_url(self, tmp_path: Path) -> None:
        router = make_router_with_session(tmp_path)
        msg = make_message("https://example.com")
        assert router.is_command(msg) is True


# ---------------------------------------------------------------------------
# AC-4: /explain dispatched as session command
# ---------------------------------------------------------------------------


class TestExplainSessionCommand:
    """AC-4: /explain <url> → session command response."""

    @pytest.mark.asyncio
    async def test_explain_returns_llm_response(self, tmp_path: Path) -> None:
        driver = make_mock_driver("This article explains Python async patterns.")
        tools, mock_scraper, _ = make_mock_tools()
        mock_scraper.scrape = AsyncMock(return_value="article content")
        router = make_router_with_session(tmp_path, driver=driver, tools=tools)
        pool = make_pool()

        msg = make_message("/explain https://example.com/async")
        response = await router.dispatch(msg, pool=pool)

        assert response is not None
        assert "async" in response.content.lower()
        driver.complete.assert_called_once()


# ---------------------------------------------------------------------------
# AC-6: /search plugin command
# ---------------------------------------------------------------------------


class TestSearchPlugin:
    """AC-6: /search <query> dispatched as session command (promoted from plugin)."""

    @pytest.mark.asyncio
    async def test_search_uses_vault_search(self, tmp_path: Path) -> None:
        from lyra.commands.search.handlers import cmd_search

        tools, _, vault = make_mock_tools()
        vault_search: AsyncMock = AsyncMock(return_value="Result: python basics")
        vault.search = vault_search

        driver = make_mock_driver()
        router = make_router_with_session(tmp_path, driver=driver, tools=tools)
        router.register_session_command(
            "search", cmd_search, tools=tools,
            description="Search the vault: /search <query>",
            timeout=30.0,
        )
        pool = make_pool()

        msg = make_message("/search python basics")
        response = await router.dispatch(msg, pool=pool)

        assert response is not None
        assert "python" in response.content.lower()
        vault_search.assert_called_once_with("python basics", timeout=25.0)


# ---------------------------------------------------------------------------
# AC-7: Pool history unchanged after session commands
# ---------------------------------------------------------------------------


class TestPoolHistoryUnchanged:
    """AC-7: Session commands do not modify pool.sdk_history or pool.history."""

    @pytest.mark.asyncio
    async def test_sdk_history_unchanged_after_session_command(
        self, tmp_path: Path
    ) -> None:
        driver = make_mock_driver()
        tools, _, _ = make_mock_tools()
        router = make_router_with_session(tmp_path, driver=driver, tools=tools)
        pool = make_pool()
        pool.sdk_history = [{"role": "user", "content": "earlier msg"}]
        pool.history = ["earlier msg"]

        msg = make_message("/vault-add https://example.com")
        await router.dispatch(msg, pool=pool)

        # Pool history must be identical to before the command
        assert pool.sdk_history == [{"role": "user", "content": "earlier msg"}]
        assert pool.history == ["earlier msg"]


# ---------------------------------------------------------------------------
# AC-9: session_driver=None → degradation message
# ---------------------------------------------------------------------------


class TestSessionDriverNone:
    """AC-9: session_driver=None returns a clear error message."""

    @pytest.mark.asyncio
    async def test_no_driver_returns_degradation_message(self, tmp_path: Path) -> None:
        router = make_router_with_session(tmp_path, driver=None)
        pool = make_pool()

        msg = make_message("/vault-add https://example.com")
        response = await router.dispatch(msg, pool=pool)

        assert response is not None
        assert (
            "session" in response.content.lower()
            or "anthropic" in response.content.lower()
        )


# ---------------------------------------------------------------------------
# AC-8: Timeout returns user-friendly message
# ---------------------------------------------------------------------------


class TestSessionTimeout:
    """AC-8: asyncio.wait_for timeout returns a timeout response."""

    @pytest.mark.asyncio
    async def test_timeout_returns_timeout_message(self, tmp_path: Path) -> None:
        loader = CommandLoader(tmp_path)
        driver = make_mock_driver()

        async def slow_handler(msg, driver, tools, args, timeout):  # noqa: ARG001
            await asyncio.sleep(10)
            return Response(content="never")

        router = CommandRouter(
            command_loader=loader,
            enabled_plugins=[],
            session_driver=driver,
        )
        tools = SessionTools(scraper=MagicMock(), vault=MagicMock())
        router.register_session_command("slow", slow_handler, tools=tools, timeout=0.01)

        msg = make_message("/slow")
        pool = make_pool()
        response = await router.dispatch(msg, pool=pool)

        assert response is not None
        assert "timed out" in response.content.lower()
