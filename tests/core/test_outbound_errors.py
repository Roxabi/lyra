"""Tests for outbound_errors: _is_transient_error and try_notify_user."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from lyra.core.outbound_errors import _is_transient_error, try_notify_user

from .conftest import make_dispatcher_msg

# ---------------------------------------------------------------------------
# _is_transient_error
# ---------------------------------------------------------------------------


class TestIsTransientError:
    def test_asyncio_timeout_error(self) -> None:
        assert _is_transient_error(asyncio.TimeoutError()) is True

    def test_timeout_error(self) -> None:
        assert _is_transient_error(TimeoutError()) is True

    def test_connection_error(self) -> None:
        assert _is_transient_error(ConnectionError("network reset")) is True

    def test_connection_reset_error(self) -> None:
        assert _is_transient_error(ConnectionResetError("reset")) is True

    def test_connection_refused_error(self) -> None:
        assert _is_transient_error(ConnectionRefusedError("refused")) is True

    def test_value_error_not_transient(self) -> None:
        assert _is_transient_error(ValueError("bad input")) is False

    def test_runtime_error_not_transient(self) -> None:
        assert _is_transient_error(RuntimeError("boom")) is False

    def test_key_error_not_transient(self) -> None:
        assert _is_transient_error(KeyError("missing")) is False

    # aiohttp errors (detected by module name)

    def test_aiohttp_error_transient(self) -> None:
        AiohttpClientError = type(
            "ClientConnectionError",
            (Exception,),
            {"__module__": "aiohttp.client_exceptions"},
        )
        assert _is_transient_error(AiohttpClientError()) is True

    def test_aiohttp_server_error_transient(self) -> None:
        AiohttpServerError = type(
            "ServerDisconnectedError", (Exception,), {"__module__": "aiohttp"}
        )
        assert _is_transient_error(AiohttpServerError()) is True

    # aiogram errors (detected by module name)

    def test_aiogram_network_error_transient(self) -> None:
        AiogramNetworkError = type(
            "TelegramNetworkError", (Exception,), {"__module__": "aiogram.exceptions"}
        )
        assert _is_transient_error(AiogramNetworkError()) is True

    def test_aiogram_server_error_transient(self) -> None:
        AiogramServerError = type(
            "TelegramServerError", (Exception,), {"__module__": "aiogram.exceptions"}
        )
        assert _is_transient_error(AiogramServerError()) is True

    def test_aiogram_retry_after_transient(self) -> None:
        AiogramRetryAfter = type(
            "TelegramRetryAfter", (Exception,), {"__module__": "aiogram.exceptions"}
        )
        assert _is_transient_error(AiogramRetryAfter()) is True

    def test_aiogram_api_error_429_transient(self) -> None:
        cls = type(
            "TelegramAPIError",
            (Exception,),
            {"__module__": "aiogram.exceptions", "status_code": 429},
        )
        assert _is_transient_error(cls()) is True

    def test_aiogram_api_error_500_transient(self) -> None:
        cls = type(
            "TelegramAPIError",
            (Exception,),
            {"__module__": "aiogram.exceptions", "status_code": 500},
        )
        assert _is_transient_error(cls()) is True

    def test_aiogram_api_error_400_not_transient(self) -> None:
        cls = type(
            "TelegramBadRequest",
            (Exception,),
            {"__module__": "aiogram.exceptions", "status_code": 400},
        )
        assert _is_transient_error(cls()) is False

    def test_aiogram_api_error_no_status_not_transient(self) -> None:
        cls = type(
            "TelegramAPIError",
            (Exception,),
            {"__module__": "aiogram.exceptions"},
        )
        assert _is_transient_error(cls()) is False

    # discord.py errors (detected by module name)

    def test_discord_gateway_not_found_transient(self) -> None:
        cls = type("GatewayNotFound", (Exception,), {"__module__": "discord.errors"})
        assert _is_transient_error(cls()) is True

    def test_discord_connection_closed_transient(self) -> None:
        cls = type("ConnectionClosed", (Exception,), {"__module__": "discord.gateway"})
        assert _is_transient_error(cls()) is True

    def test_discord_http_500_transient(self) -> None:
        cls = type(
            "HTTPException",
            (Exception,),
            {"__module__": "discord.errors", "status": 503},
        )
        assert _is_transient_error(cls()) is True

    def test_discord_http_429_transient(self) -> None:
        cls = type(
            "HTTPException",
            (Exception,),
            {"__module__": "discord.errors", "status": 429},
        )
        assert _is_transient_error(cls()) is True

    def test_discord_http_403_not_transient(self) -> None:
        cls = type(
            "HTTPException",
            (Exception,),
            {"__module__": "discord.errors", "status": 403},
        )
        assert _is_transient_error(cls()) is False

    def test_discord_http_no_status_not_transient(self) -> None:
        cls = type("HTTPException", (Exception,), {"__module__": "discord.errors"})
        assert _is_transient_error(cls()) is False


# ---------------------------------------------------------------------------
# try_notify_user
# ---------------------------------------------------------------------------


class TestTryNotifyUser:
    async def test_telegram_sends_message(self) -> None:
        adapter = MagicMock()
        adapter.bot = MagicMock()
        adapter.bot.send_message = AsyncMock()
        msg = make_dispatcher_msg()  # platform_meta has chat_id=123
        await try_notify_user("telegram", adapter, msg, "⚠️ error")
        adapter.bot.send_message.assert_awaited_once_with(chat_id=123, text="⚠️ error")

    async def test_telegram_no_chat_id_returns_silently(self) -> None:
        adapter = MagicMock()
        adapter.bot = MagicMock()
        adapter.bot.send_message = AsyncMock()
        msg = make_dispatcher_msg()
        object.__setattr__(msg, "platform_meta", {})  # no chat_id
        await try_notify_user("telegram", adapter, msg, "⚠️ error")
        adapter.bot.send_message.assert_not_awaited()

    async def test_telegram_exception_swallowed(self) -> None:
        adapter = MagicMock()
        adapter.bot = MagicMock()
        adapter.bot.send_message = AsyncMock(side_effect=Exception("network error"))
        msg = make_dispatcher_msg()
        # Must not propagate the exception
        await try_notify_user("telegram", adapter, msg, "⚠️ error")

    async def test_discord_sends_to_thread_when_present(self) -> None:
        adapter = MagicMock()
        channel = MagicMock()
        channel.send = AsyncMock()
        adapter._resolve_channel = AsyncMock(return_value=channel)

        msg = make_dispatcher_msg()
        object.__setattr__(
            msg,
            "platform_meta",
            {"channel_id": 10, "thread_id": 20, "message_id": 1},
        )
        await try_notify_user("discord", adapter, msg, "⚠️ error")
        adapter._resolve_channel.assert_awaited_once_with(20)
        channel.send.assert_awaited_once_with("⚠️ error")

    async def test_discord_falls_back_to_channel(self) -> None:
        adapter = MagicMock()
        channel = MagicMock()
        channel.send = AsyncMock()
        adapter._resolve_channel = AsyncMock(return_value=channel)

        msg = make_dispatcher_msg()
        object.__setattr__(
            msg,
            "platform_meta",
            {"channel_id": 10, "thread_id": None, "message_id": 1},
        )
        await try_notify_user("discord", adapter, msg, "⚠️ error")
        adapter._resolve_channel.assert_awaited_once_with(10)

    async def test_discord_no_channel_id_returns_silently(self) -> None:
        adapter = MagicMock()
        adapter._resolve_channel = AsyncMock()
        msg = make_dispatcher_msg()
        object.__setattr__(
            msg, "platform_meta", {"thread_id": None, "channel_id": None}
        )
        await try_notify_user("discord", adapter, msg, "⚠️ error")
        adapter._resolve_channel.assert_not_awaited()

    async def test_unknown_platform_no_exception(self) -> None:
        adapter = MagicMock()
        msg = make_dispatcher_msg()
        # Should not raise for unsupported platforms
        await try_notify_user("cli", adapter, msg, "⚠️ error")

    async def test_discord_exception_swallowed(self) -> None:
        adapter = MagicMock()
        adapter._resolve_channel = AsyncMock(side_effect=Exception("connection lost"))
        msg = make_dispatcher_msg()
        object.__setattr__(msg, "platform_meta", {"channel_id": 10, "thread_id": None})
        # Must not propagate
        await try_notify_user("discord", adapter, msg, "⚠️ error")
