"""Tests for _bootstrap_adapter_standalone — NATS-mode adapter process bootstrap."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_raw_config(platform: str) -> dict:
    if platform == "telegram":
        return {"telegram": {"bots": [{"bot_id": "main"}]}}
    return {
        "discord": {
            "bots": [{"bot_id": "main", "auto_thread": False, "thread_hot_hours": 4}]
        }
    }


def _mock_cred_store(token: str, webhook_secret: str = "") -> MagicMock:
    """Return a mock CredentialStore that returns the given token."""
    mock = MagicMock()
    mock.connect = AsyncMock()
    mock.close = AsyncMock()
    mock.get_full = AsyncMock(return_value=(token, webhook_secret))
    return mock


def _cred_store_patches(token: str, webhook_secret: str = "") -> tuple:
    """Patches for LyraKeyring + CredentialStore constructor."""
    mock_store = _mock_cred_store(token, webhook_secret)
    return (
        patch(
            "lyra.bootstrap.adapter_standalone.LyraKeyring.load_or_create",
            return_value=MagicMock(),
        ),
        patch(
            "lyra.bootstrap.adapter_standalone.CredentialStore", return_value=mock_store
        ),
    )


@pytest.mark.asyncio
async def test_telegram_bootstrap_wires_listener_and_calls_astart() -> None:
    """Telegram standalone bootstrap: NatsOutboundListener wired, astart() called."""
    from lyra.bootstrap.adapter_standalone import _bootstrap_adapter_standalone

    stop = asyncio.Event()
    stop.set()  # return immediately

    mock_nc = AsyncMock()
    mock_nc.subscribe = AsyncMock(return_value=AsyncMock())

    mock_adapter = AsyncMock()
    mock_adapter._bot_id = "main"
    mock_adapter.resolve_identity = AsyncMock()
    mock_adapter.astart = AsyncMock()
    mock_adapter.close = AsyncMock()
    mock_adapter.dp.start_polling = AsyncMock(return_value=None)
    mock_adapter.dp.stop_polling = AsyncMock(return_value=None)

    mock_listener = AsyncMock()
    mock_inbound_bus = AsyncMock()
    mock_inbound_bus.register = MagicMock()
    mock_inbound_bus.start = AsyncMock()
    mock_inbound_bus.stop = AsyncMock()

    keyring_patch, cred_patch = _cred_store_patches("test-token", "webhook-secret")
    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("lyra.nats.nats_bus.NatsBus", return_value=mock_inbound_bus),
        patch("lyra.adapters.telegram.TelegramAdapter", return_value=mock_adapter),
        patch(
            "lyra.bootstrap.adapter_standalone.NatsOutboundListener",
            return_value=mock_listener,
        ),
        keyring_patch,
        cred_patch,
        patch.dict(os.environ, {"NATS_URL": "nats://localhost:4222"}),
    ):
        await _bootstrap_adapter_standalone(
            _make_raw_config("telegram"), "telegram", _stop=stop
        )

    mock_adapter.astart.assert_awaited_once()
    mock_nc.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_discord_bootstrap_wires_listener_and_calls_astart() -> None:
    """Discord standalone bootstrap: NatsOutboundListener wired, astart() called."""
    from lyra.bootstrap.adapter_standalone import _bootstrap_adapter_standalone

    stop = asyncio.Event()
    stop.set()

    mock_nc = AsyncMock()
    mock_adapter_dc = AsyncMock()
    mock_adapter_dc._bot_id = "main"
    mock_adapter_dc.astart = AsyncMock()
    mock_adapter_dc.close = AsyncMock()
    mock_adapter_dc.start = AsyncMock(return_value=None)
    mock_listener_dc = AsyncMock()
    mock_inbound_bus_dc = AsyncMock()
    mock_inbound_bus_dc.register = MagicMock()
    mock_inbound_bus_dc.start = AsyncMock()
    mock_inbound_bus_dc.stop = AsyncMock()

    keyring_patch, cred_patch = _cred_store_patches("discord-token")
    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("lyra.nats.nats_bus.NatsBus", return_value=mock_inbound_bus_dc),
        patch("lyra.adapters.discord.DiscordAdapter", return_value=mock_adapter_dc),
        patch(
            "lyra.bootstrap.adapter_standalone.NatsOutboundListener",
            return_value=mock_listener_dc,
        ),
        keyring_patch,
        cred_patch,
        patch.dict(os.environ, {"NATS_URL": "nats://localhost:4222"}),
    ):
        await _bootstrap_adapter_standalone(
            _make_raw_config("discord"), "discord", _stop=stop
        )

    mock_adapter_dc.astart.assert_awaited_once()
    mock_nc.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_nats_url_missing_exits() -> None:
    """Missing NATS_URL env var → sys.exit before any NATS connection."""
    from lyra.bootstrap.adapter_standalone import _bootstrap_adapter_standalone

    with (
        patch.dict(os.environ, {}, clear=True),
        pytest.raises(SystemExit),
    ):
        await _bootstrap_adapter_standalone({}, "telegram")


@pytest.mark.asyncio
async def test_nc_close_called_even_on_exception() -> None:
    """nc.close() is called in finally block even when bootstrap raises."""
    from lyra.bootstrap.adapter_standalone import _bootstrap_adapter_standalone

    mock_nc = AsyncMock()
    keyring_patch, cred_patch = _cred_store_patches("t")
    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("lyra.nats.nats_bus.NatsBus", side_effect=RuntimeError("boom")),
        keyring_patch,
        cred_patch,
        patch.dict(os.environ, {"NATS_URL": "nats://localhost:4222"}),
        pytest.raises(RuntimeError, match="boom"),
    ):
        await _bootstrap_adapter_standalone(_make_raw_config("telegram"), "telegram")

    mock_nc.close.assert_awaited_once()
