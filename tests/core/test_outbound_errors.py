"""Tests for outbound_errors: _is_transient_error and try_notify_user."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from lyra.core.hub.outbound.outbound_errors import _is_transient_error, try_notify_user

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
    async def test_sends_via_adapter_send(self) -> None:
        adapter = MagicMock()
        adapter.send = AsyncMock()
        msg = make_dispatcher_msg()
        await try_notify_user("telegram", adapter, msg, "⚠️ error")
        adapter.send.assert_awaited_once()
        _, outbound = adapter.send.call_args.args
        assert outbound.content == ["⚠️ error"]

    async def test_works_for_any_platform(self) -> None:
        adapter = MagicMock()
        adapter.send = AsyncMock()
        msg = make_dispatcher_msg()
        await try_notify_user("cli", adapter, msg, "⚠️ error")
        adapter.send.assert_awaited_once()

    async def test_exception_swallowed(self) -> None:
        adapter = MagicMock()
        adapter.send = AsyncMock(side_effect=Exception("network error"))
        msg = make_dispatcher_msg()
        # Must not propagate the exception
        await try_notify_user("telegram", adapter, msg, "⚠️ error")

    async def test_circuit_open_suppresses(self) -> None:
        adapter = MagicMock()
        adapter.send = AsyncMock()
        circuit = MagicMock()
        circuit.is_open = MagicMock(return_value=True)
        msg = make_dispatcher_msg()
        await try_notify_user("telegram", adapter, msg, "⚠️ error", circuit=circuit)
        adapter.send.assert_not_awaited()
