"""Tests for the svc plugin command handler (issue #362)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import cast
from unittest.mock import AsyncMock

import pytest

import lyra.commands.svc.handlers as svc_mod
from lyra.commands.svc.handlers import cmd_svc
from lyra.core.message import InboundMessage
from lyra.core.pool import Pool
from lyra.core.trust import TrustLevel
from lyra.integrations.base import ServiceControlFailed


def _make_msg(text: str = "/svc status", *, is_admin: bool = True) -> InboundMessage:
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
        trust_level=TrustLevel.OWNER if is_admin else TrustLevel.TRUSTED,
        is_admin=is_admin,
    )


_POOL = cast(Pool, None)  # pool is unused by cmd_svc


@pytest.fixture(autouse=True)
def _mock_service_manager(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Replace the module-level _service_manager with a mock for all tests."""
    mock_mgr = AsyncMock()
    mock_mgr.control = AsyncMock(return_value="(no output)")
    monkeypatch.setattr(svc_mod, "_service_manager", mock_mgr)
    return mock_mgr


class TestCmdSvcAdminGuard:
    @pytest.mark.asyncio
    async def test_non_admin_rejected(self):
        msg = _make_msg(is_admin=False)
        result = await cmd_svc(msg, _POOL, ["status"])
        assert "admin-only" in result.content.lower()

    @pytest.mark.asyncio
    async def test_admin_allowed(self, _mock_service_manager: AsyncMock):
        msg = _make_msg()
        _mock_service_manager.control = AsyncMock(return_value="lyra RUNNING")
        result = await cmd_svc(msg, _POOL, ["status"])
        assert "lyra" in result.content


class TestCmdSvcValidation:
    @pytest.mark.asyncio
    async def test_empty_args_shows_usage(self):
        msg = _make_msg()
        result = await cmd_svc(msg, _POOL, [])
        assert "Usage:" in result.content

    @pytest.mark.asyncio
    async def test_unknown_action_rejected(self):
        msg = _make_msg()
        result = await cmd_svc(msg, _POOL, ["destroy"])
        assert "Unknown action" in result.content

    @pytest.mark.asyncio
    async def test_unknown_service_rejected(self):
        msg = _make_msg()
        result = await cmd_svc(msg, _POOL, ["restart", "badservice"])
        assert "Unknown service" in result.content

    @pytest.mark.asyncio
    async def test_reload_alias(self, _mock_service_manager: AsyncMock):
        msg = _make_msg()
        _mock_service_manager.control = AsyncMock(return_value="restarted")
        await cmd_svc(msg, _POOL, ["reload", "lyra"])
        _mock_service_manager.control.assert_awaited_once_with("restart", "lyra")

    @pytest.mark.asyncio
    async def test_restart_requires_service(self):
        msg = _make_msg()
        result = await cmd_svc(msg, _POOL, ["restart"])
        assert "Usage:" in result.content


class TestCmdSvcErrorHandling:
    @pytest.mark.asyncio
    async def test_timeout_returns_message(self, _mock_service_manager: AsyncMock):
        msg = _make_msg()
        _mock_service_manager.control = AsyncMock(
            side_effect=ServiceControlFailed("timeout")
        )
        result = await cmd_svc(msg, _POOL, ["status"])
        assert "timed out" in result.content.lower()

    @pytest.mark.asyncio
    async def test_not_available_returns_message(
        self, _mock_service_manager: AsyncMock
    ):
        msg = _make_msg()
        _mock_service_manager.control = AsyncMock(
            side_effect=ServiceControlFailed("not_available")
        )
        result = await cmd_svc(msg, _POOL, ["status"])
        assert "not found" in result.content.lower()

    @pytest.mark.asyncio
    async def test_subprocess_error_returns_message(
        self, _mock_service_manager: AsyncMock
    ):
        msg = _make_msg()
        _mock_service_manager.control = AsyncMock(
            side_effect=ServiceControlFailed("subprocess_error")
        )
        result = await cmd_svc(msg, _POOL, ["restart", "lyra"])
        assert "error" in result.content.lower()
