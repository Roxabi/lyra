"""Integration smoke tests for hub command sessions (issue #99 T7).

End-to-end tests using a mock LlmProvider and patched subprocess calls.
Tests cover AC-1 through AC-11 at the integration level.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.core.command_loader import CommandLoader
from lyra.core.command_parser import CommandParser
from lyra.core.command_router import CommandRouter
from lyra.core.message import InboundMessage, Response
from lyra.core.pool import Pool
from lyra.core.trust import TrustLevel
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


def make_router_with_session(
    tmp_path: Path,
    driver: "LlmProvider | None" = None,
) -> CommandRouter:
    """Build a CommandRouter with session commands and a mock search plugin."""
    from lyra.core.session_commands import cmd_add, cmd_explain, cmd_summarize

    # Minimal plugins dir (no real plugins needed for session command tests)
    loader = CommandLoader(tmp_path)
    router = CommandRouter(
        command_loader=loader,
        enabled_plugins=[],
        session_driver=driver,
        patterns={"bare_url": True},
    )
    router.register_session_command(
        "add", cmd_add, description="Save URL", timeout=60.0
    )
    router.register_session_command(
        "explain", cmd_explain, description="Explain URL", timeout=60.0
    )
    router.register_session_command(
        "summarize", cmd_summarize, description="Summarize URL", timeout=60.0
    )
    return router


def make_pool() -> MagicMock:
    pool = MagicMock(spec=Pool)
    pool.sdk_history = []
    pool.history = []
    return pool


# ---------------------------------------------------------------------------
# AC-1: Bare URL → /add dispatch
# ---------------------------------------------------------------------------


class TestBareUrlToAdd:
    """AC-1: A bare URL message is dispatched to /add."""

    @pytest.mark.asyncio
    async def test_bare_url_dispatched_to_add(self, tmp_path: Path) -> None:
        driver = make_mock_driver()
        router = make_router_with_session(tmp_path, driver=driver)
        pool = make_pool()

        with (
            patch(
                "lyra.core.session_commands.scrape_url",
                new=AsyncMock(return_value="page content"),
            ),
            patch("lyra.core.session_commands.vault_add", new=AsyncMock()),
        ):
            msg = make_message("https://example.com/article")
            # prepare() rewrites the bare URL to /add
            msg = router.prepare(msg)
            assert msg.command is not None
            assert msg.command.name == "add"

            response = await router.dispatch(msg, pool=pool)

        assert response is not None
        assert isinstance(response, Response)
        driver.complete.assert_called_once()

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
        router = make_router_with_session(tmp_path, driver=driver)
        pool = make_pool()

        with patch(
            "lyra.core.session_commands.scrape_url",
            new=AsyncMock(return_value="article content"),
        ):
            msg = make_message("/explain https://example.com/async")
            response = await router.dispatch(msg, pool=pool)

        assert response is not None
        assert "async" in response.content.lower()
        driver.complete.assert_called_once()


# ---------------------------------------------------------------------------
# AC-6: /search plugin command
# ---------------------------------------------------------------------------


class TestSearchPlugin:
    """AC-6: /search <query> dispatched as plugin command."""

    @pytest.mark.asyncio
    async def test_search_uses_vault_search(self, tmp_path: Path) -> None:
        # Build a router with the real search plugin
        search_plugin_dir = (
            Path(__file__).resolve().parent.parent.parent / "src" / "lyra" / "commands"
        )
        loader = CommandLoader(search_plugin_dir)
        loader.load("search")

        driver = make_mock_driver()
        router = CommandRouter(
            command_loader=loader,
            enabled_plugins=["search"],
            session_driver=driver,
        )
        pool = make_pool()

        with patch(
            "lyra.core.session_helpers.vault_search",
            new=AsyncMock(return_value="Result: python basics"),
        ) as mock_search:
            msg = make_message("/search python basics")
            response = await router.dispatch(msg, pool=pool)

        assert response is not None
        assert "python" in response.content.lower()
        mock_search.assert_called_once_with("python basics", timeout=25.0)


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
        router = make_router_with_session(tmp_path, driver=driver)
        pool = make_pool()
        pool.sdk_history = [{"role": "user", "content": "earlier msg"}]
        pool.history = ["earlier msg"]

        with (
            patch(
                "lyra.core.session_commands.scrape_url",
                new=AsyncMock(return_value="content"),
            ),
            patch("lyra.core.session_commands.vault_add", new=AsyncMock()),
        ):
            msg = make_message("/add https://example.com")
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

        msg = make_message("/add https://example.com")
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

        async def slow_handler(msg, driver, args, timeout):  # noqa: ARG001
            await asyncio.sleep(10)
            return Response(content="never")

        router = CommandRouter(
            command_loader=loader,
            enabled_plugins=[],
            session_driver=driver,
        )
        router.register_session_command("slow", slow_handler, timeout=0.01)

        msg = make_message("/slow")
        pool = make_pool()
        response = await router.dispatch(msg, pool=pool)

        assert response is not None
        assert "timed out" in response.content.lower()
