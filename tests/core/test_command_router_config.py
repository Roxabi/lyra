"""Tests for /config, /clear, and /stop builtins (issue #127, #135).

Covers:
  TestConfigCommand — /config admin gate, show, set, reset
  TestClearCommand  — /clear and /new history management
  TestStopCommand   — /stop calls pool.cancel()
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lyra.core.commands.command_loader import CommandLoader
from lyra.core.commands.command_parser import CommandParser
from lyra.core.commands.command_router import CommandRouter
from lyra.core.message import InboundMessage, Response
from lyra.core.pool import Pool
from lyra.core.trust import TrustLevel

from .conftest import make_message, make_router

# ---------------------------------------------------------------------------
# Helpers local to this file
# ---------------------------------------------------------------------------


def make_config_router(
    tmp_path: Path,
    with_holder: bool = True,
) -> CommandRouter:
    """Build a CommandRouter with runtime_config_holder set."""
    from lyra.core.runtime_config import RuntimeConfig, RuntimeConfigHolder

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(exist_ok=True)
    loader = CommandLoader(plugins_dir)
    holder = RuntimeConfigHolder(RuntimeConfig()) if with_holder else None
    return CommandRouter(
        command_loader=loader,
        enabled_plugins=[],
        runtime_config_holder=holder,
        runtime_config_path=tmp_path / "lyra_runtime.toml",
    )


def make_config_msg(
    user_id: str = "tg:user:42", content: str = "/config", *, is_admin: bool = False
) -> InboundMessage:
    """Build a /config InboundMessage."""
    _parser = CommandParser()
    return InboundMessage(
        id="msg-config-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:42",
        user_id=user_id,
        user_name="Admin",
        is_mention=False,
        text=content,
        text_raw=content,
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 42,
            "topic_id": None,
            "message_id": None,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
        is_admin=is_admin,
        command=_parser.parse(content),  # type: ignore[call-arg]
    )


# ---------------------------------------------------------------------------
# /config command (issue #135)
# ---------------------------------------------------------------------------


class TestConfigCommand:
    """/config builtin — admin gate, show, set, reset (issue #135)."""

    @pytest.mark.asyncio
    async def test_config_non_admin_denied(self, tmp_path: Path) -> None:
        """Non-admin sender → 'This command is admin-only.'"""
        router = make_config_router(tmp_path)
        msg = make_config_msg(user_id="tg:user:42")

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert "admin-only" in response.content.lower()

    @pytest.mark.asyncio
    async def test_config_show_all_fields(self, tmp_path: Path) -> None:
        """Admin /config (no args) → table showing all 6 config fields."""
        router = make_config_router(tmp_path)
        msg = make_config_msg(user_id="tg:user:42", content="/config", is_admin=True)

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert "style" in response.content
        assert "language" in response.content
        assert "temperature" in response.content
        assert "model" in response.content
        assert "max_steps" in response.content
        assert "extra_instructions" in response.content

    @pytest.mark.asyncio
    async def test_config_set_valid_param(self, tmp_path: Path) -> None:
        """Admin /config style=detailed → sets param, returns 'Updated:...'"""
        router = make_config_router(tmp_path)
        msg = make_config_msg(
            user_id="tg:user:42", content="/config style=detailed", is_admin=True
        )

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert "Updated:" in response.content
        assert "style" in response.content

    @pytest.mark.asyncio
    async def test_config_set_unknown_key(self, tmp_path: Path) -> None:
        """Admin /config foo=bar → 'Unknown config key'"""
        router = make_config_router(tmp_path)
        msg = make_config_msg(
            user_id="tg:user:42", content="/config foo=bar", is_admin=True
        )

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert "Unknown config key" in response.content

    @pytest.mark.asyncio
    async def test_config_reset_all(self, tmp_path: Path) -> None:
        """Admin /config reset → 'Runtime config reset to defaults.'"""
        router = make_config_router(tmp_path)
        msg = make_config_msg(
            user_id="tg:user:42", content="/config reset", is_admin=True
        )

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert "Runtime config reset to defaults." in response.content

    @pytest.mark.asyncio
    async def test_config_reset_single_key(self, tmp_path: Path) -> None:
        """Admin /config reset style → 'Reset: style = (default). Saved.'"""
        router = make_config_router(tmp_path)
        msg = make_config_msg(
            user_id="tg:user:42", content="/config reset style", is_admin=True
        )

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert "Reset: style = (default). Saved." in response.content

    @pytest.mark.asyncio
    async def test_config_reset_unknown_key(self, tmp_path: Path) -> None:
        """Admin /config reset unknown_key → error with 'Unknown config key'"""
        router = make_config_router(tmp_path)
        msg = make_config_msg(
            user_id="tg:user:42", content="/config reset unknown_key", is_admin=True
        )

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert "Unknown config key" in response.content

    @pytest.mark.asyncio
    async def test_config_no_holder(self, tmp_path: Path) -> None:
        """/config when runtime_config_holder is None → not available message."""
        router = make_config_router(tmp_path, with_holder=False)
        msg = make_config_msg(user_id="tg:user:42", content="/config", is_admin=True)

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert "not available" in response.content.lower()


# ---------------------------------------------------------------------------
# /clear and /new commands
# ---------------------------------------------------------------------------


class TestClearCommand:
    """/clear and /new clear history and call pool.reset_session()."""

    @pytest.mark.asyncio
    async def test_clear_clears_sdk_history(self, tmp_path: Path) -> None:
        """/clear empties pool.sdk_history."""
        router = make_router(tmp_path)
        msg = make_message(content="/clear")
        pool_mock = MagicMock(spec=Pool)
        pool_mock.sdk_history = MagicMock()
        pool_mock.history = MagicMock()

        async def _noop() -> None:
            pass

        pool_mock.reset_session = _noop
        await router.dispatch(msg, pool=pool_mock)
        pool_mock.sdk_history.clear.assert_called_once()
        pool_mock.history.clear.assert_called_once()

    @pytest.mark.asyncio
    async def test_clear_calls_reset_session(self, tmp_path: Path) -> None:
        """/clear awaits pool.reset_session() to flush the backend session."""
        router = make_router(tmp_path)
        msg = make_message(content="/clear")
        reset_called = False

        async def _reset() -> None:
            nonlocal reset_called
            reset_called = True

        pool_mock = MagicMock(spec=Pool)
        pool_mock.sdk_history = MagicMock()
        pool_mock.history = MagicMock()
        pool_mock.reset_session = _reset
        await router.dispatch(msg, pool=pool_mock)
        assert reset_called, "pool.reset_session() was not awaited by /clear"

    @pytest.mark.asyncio
    async def test_new_alias_calls_reset_session(self, tmp_path: Path) -> None:
        """/new is an alias for /clear — also calls pool.reset_session()."""
        router = make_router(tmp_path)
        msg = make_message(content="/new")
        reset_called = False

        async def _reset() -> None:
            nonlocal reset_called
            reset_called = True

        pool_mock = MagicMock(spec=Pool)
        pool_mock.sdk_history = MagicMock()
        pool_mock.history = MagicMock()
        pool_mock.reset_session = _reset
        await router.dispatch(msg, pool=pool_mock)
        assert reset_called

    @pytest.mark.asyncio
    async def test_clear_with_no_pool_safe(self, tmp_path: Path) -> None:
        """/clear with pool=None returns a safe response without raising."""
        router = make_router(tmp_path)
        msg = make_message(content="/clear")
        response = await router.dispatch(msg, pool=None)
        assert isinstance(response, Response)
        assert "no active session" in response.content.lower()

    @pytest.mark.asyncio
    async def test_clear_returns_confirmation(self, tmp_path: Path) -> None:
        """/clear response confirms history was cleared."""
        router = make_router(tmp_path)
        msg = make_message(content="/clear")

        async def _noop() -> None:
            pass

        pool_mock = MagicMock(spec=Pool)
        pool_mock.sdk_history = MagicMock()
        pool_mock.history = MagicMock()
        pool_mock.reset_session = _noop
        response = await router.dispatch(msg, pool=pool_mock)
        assert isinstance(response, Response)
        assert "cleared" in response.content.lower()


# ---------------------------------------------------------------------------
# /stop command (issue #127)
# ---------------------------------------------------------------------------


class TestStopCommand:
    """/stop builtin calls pool.cancel() and returns an empty Response (S4-5)."""

    @pytest.mark.asyncio
    async def test_stop_command_calls_pool_cancel(self, tmp_path: Path) -> None:
        """/stop dispatched with a pool calls pool.cancel()."""
        router = make_router(tmp_path)
        msg = make_message(content="/stop")
        pool_mock = MagicMock(spec=Pool)
        pool_mock.cancel = MagicMock()

        response = await router.dispatch(msg, pool=pool_mock)

        pool_mock.cancel.assert_called_once()
        assert isinstance(response, Response)

    @pytest.mark.asyncio
    async def test_stop_command_returns_empty_response(self, tmp_path: Path) -> None:
        """/stop returns Response(content='') — hub skips the duplicate reply."""
        router = make_router(tmp_path)
        msg = make_message(content="/stop")
        pool_mock = MagicMock(spec=Pool)
        pool_mock.cancel = MagicMock()

        response = await router.dispatch(msg, pool=pool_mock)

        assert isinstance(response, Response)
        assert response.content == ""

    @pytest.mark.asyncio
    async def test_stop_command_with_none_pool_safe(self, tmp_path: Path) -> None:
        """Dispatching /stop with pool=None does not raise."""
        router = make_router(tmp_path)
        msg = make_message(content="/stop")
        response = await router.dispatch(msg, pool=None)
        assert isinstance(response, Response)
