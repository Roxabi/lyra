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


def _cred_store_patches(token: str, webhook_secret: str = "") -> tuple:
    """Patch for _load_bot_token in adapter_standalone (returns token + webhook)."""
    webhook: str | None = webhook_secret if webhook_secret else None
    return (
        patch(
            "lyra.bootstrap.credentials.load_bot_token",
            return_value=(token, webhook),
        ),
    )


@pytest.mark.asyncio
async def test_telegram_bootstrap_wires_listener_and_calls_astart() -> None:
    """Telegram standalone bootstrap: NatsOutboundListener wired, astart() called."""
    from lyra.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )

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

    (load_token_patch,) = _cred_store_patches("test-token", "webhook-secret")
    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("lyra.nats.nats_bus.NatsBus", return_value=mock_inbound_bus),
        patch("lyra.adapters.telegram.TelegramAdapter", return_value=mock_adapter),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.NatsOutboundListener",
            return_value=mock_listener,
        ),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.wait_for_hub",
            AsyncMock(return_value=True),
        ),
        load_token_patch,
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
    from lyra.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )

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

    (load_token_patch_dc,) = _cred_store_patches("discord-token")
    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("lyra.nats.nats_bus.NatsBus", return_value=mock_inbound_bus_dc),
        patch("lyra.adapters.discord.DiscordAdapter", return_value=mock_adapter_dc),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.NatsOutboundListener",
            return_value=mock_listener_dc,
        ),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.wait_for_hub",
            AsyncMock(return_value=True),
        ),
        load_token_patch_dc,
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
    from lyra.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )

    with (
        patch.dict(os.environ, {}, clear=True),
        pytest.raises(SystemExit),
    ):
        await _bootstrap_adapter_standalone({}, "telegram")


@pytest.mark.asyncio
async def test_nc_close_called_even_on_exception() -> None:
    """nc.close() is called in finally block even when bootstrap raises."""
    from lyra.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )

    mock_nc = AsyncMock()
    (load_token_patch_exc,) = _cred_store_patches("t")
    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("lyra.nats.nats_bus.NatsBus", side_effect=RuntimeError("boom")),
        load_token_patch_exc,
        patch.dict(os.environ, {"NATS_URL": "nats://localhost:4222"}),
        pytest.raises(RuntimeError, match="boom"),
    ):
        await _bootstrap_adapter_standalone(_make_raw_config("telegram"), "telegram")

    mock_nc.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_telegram_astart_failure_cleans_up_wired_resources() -> None:
    """astart() raises mid-loop -> wired + current bot resources cleaned up."""
    from lyra.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )

    raw_config = {
        "telegram": {
            "bots": [{"bot_id": "first"}, {"bot_id": "second"}]
        }
    }

    mock_nc = AsyncMock()

    mock_adapter_first = AsyncMock()
    mock_adapter_first._bot_id = "first"
    mock_adapter_first.astart = AsyncMock()
    mock_adapter_first.close = AsyncMock()

    mock_bus_first = AsyncMock()
    mock_bus_first.register = MagicMock()
    mock_bus_first.start = AsyncMock()
    mock_bus_first.stop = AsyncMock()

    mock_adapter_second = AsyncMock()
    mock_adapter_second._bot_id = "second"
    mock_adapter_second.astart = AsyncMock(side_effect=RuntimeError("boom"))
    mock_adapter_second.close = AsyncMock()

    mock_bus_second = AsyncMock()
    mock_bus_second.register = MagicMock()
    mock_bus_second.start = AsyncMock()
    mock_bus_second.stop = AsyncMock()

    adapter_queue = [mock_adapter_first, mock_adapter_second]
    bus_queue = [mock_bus_first, mock_bus_second]

    def _make_adapter(*args, **kwargs):
        return adapter_queue.pop(0)

    def _make_bus(*args, **kwargs):
        return bus_queue.pop(0)

    (load_token_patch,) = _cred_store_patches("test-token")
    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("lyra.nats.nats_bus.NatsBus", side_effect=_make_bus),
        patch("lyra.adapters.telegram.TelegramAdapter", side_effect=_make_adapter),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.NatsOutboundListener",
            return_value=AsyncMock(),
        ),
        load_token_patch,
        patch.dict(os.environ, {"NATS_URL": "nats://localhost:4222"}),
        pytest.raises(RuntimeError, match="boom"),
    ):
        await _bootstrap_adapter_standalone(raw_config, "telegram")

    mock_adapter_first.close.assert_awaited_once()
    mock_bus_first.stop.assert_awaited_once()
    mock_adapter_second.close.assert_awaited_once()
    mock_bus_second.stop.assert_awaited_once()
    mock_nc.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_discord_astart_failure_cleans_up_wired_resources() -> None:
    """astart() raises mid-loop -> wired + current bot resources cleaned up."""
    from lyra.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )

    raw_config = {
        "discord": {
            "bots": [
                {"bot_id": "first", "auto_thread": False, "thread_hot_hours": 4},
                {"bot_id": "second", "auto_thread": False, "thread_hot_hours": 4},
            ]
        }
    }

    mock_nc = AsyncMock()

    mock_adapter_first = AsyncMock()
    mock_adapter_first._bot_id = "first"
    mock_adapter_first.astart = AsyncMock()
    mock_adapter_first.close = AsyncMock()

    mock_bus_first = AsyncMock()
    mock_bus_first.register = MagicMock()
    mock_bus_first.start = AsyncMock()
    mock_bus_first.stop = AsyncMock()

    mock_adapter_second = AsyncMock()
    mock_adapter_second._bot_id = "second"
    mock_adapter_second.astart = AsyncMock(side_effect=RuntimeError("boom"))
    mock_adapter_second.close = AsyncMock()

    mock_bus_second = AsyncMock()
    mock_bus_second.register = MagicMock()
    mock_bus_second.start = AsyncMock()
    mock_bus_second.stop = AsyncMock()

    adapter_queue = [mock_adapter_first, mock_adapter_second]
    bus_queue = [mock_bus_first, mock_bus_second]

    def _make_adapter(*args, **kwargs):
        return adapter_queue.pop(0)

    def _make_bus(*args, **kwargs):
        return bus_queue.pop(0)

    (load_token_patch,) = _cred_store_patches("discord-token")
    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("lyra.nats.nats_bus.NatsBus", side_effect=_make_bus),
        patch("lyra.adapters.discord.DiscordAdapter", side_effect=_make_adapter),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.NatsOutboundListener",
            return_value=AsyncMock(),
        ),
        load_token_patch,
        patch.dict(os.environ, {"NATS_URL": "nats://localhost:4222"}),
        pytest.raises(RuntimeError, match="boom"),
    ):
        await _bootstrap_adapter_standalone(raw_config, "discord")

    mock_adapter_first.close.assert_awaited_once()
    mock_bus_first.stop.assert_awaited_once()
    mock_adapter_second.close.assert_awaited_once()
    mock_bus_second.stop.assert_awaited_once()
    mock_nc.close.assert_awaited_once()
