"""Tests for bootstrap_audio_consumer wiring (#1482 T8).

Asserts that _bootstrap_adapter_standalone:
  1. Calls ensure_stream / ensure_kv / ensure_consumer before constructing the consumer.
  2. Constructs JetStreamAudioConsumer with the correct durable + filter_subject.
  3. Wires adapter.render_audio as send_audio and adapter.send as send_text.
  4. Calls consumer.start() during bootstrap.
  5. Calls consumer.stop() during teardown.

Both telegram and discord platforms are exercised.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_raw_config(platform: str) -> dict:
    if platform == "telegram":
        return {"telegram": {"bots": [{"bot_id": "main"}]}}
    return {
        "discord": {
            "bots": [{"bot_id": "main", "auto_thread": False, "thread_hot_hours": 4}]
        }
    }


def _cred_patch(token: str = "tok", webhook: str = "") -> tuple:
    return (
        patch(
            "lyra.bootstrap.credentials.load_bot_token",
            return_value=(token, webhook or None),
        ),
    )


def _make_nc_mock() -> AsyncMock:
    """Build a NATS connection mock with a synchronous jetstream() method."""
    mock_nc = AsyncMock()
    mock_js = MagicMock()  # JetStreamContext — nc.jetstream() is sync, not awaited
    mock_nc.jetstream = MagicMock(return_value=mock_js)
    return mock_nc


# ---------------------------------------------------------------------------
# Telegram bootstrap_audio_consumer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_audio_consumer_telegram_provisions_and_starts() -> None:
    """Telegram: ensure_stream/kv/consumer called, consumer.start() called."""
    from lyra.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )

    stop = asyncio.Event()
    stop.set()

    mock_nc = _make_nc_mock()
    mock_js = mock_nc.jetstream()  # capture the same object the bootstrap will see

    mock_adapter = AsyncMock()
    mock_adapter._bot_id = "main"
    mock_adapter.resolve_identity = AsyncMock()
    mock_adapter.astart = AsyncMock()
    mock_adapter.close = AsyncMock()
    mock_adapter.dp.start_polling = AsyncMock(return_value=None)
    mock_adapter.dp.stop_polling = AsyncMock()
    mock_adapter.render_audio = AsyncMock()
    mock_adapter.send = AsyncMock()

    mock_consumer = AsyncMock()
    mock_inbound_bus = AsyncMock()
    mock_inbound_bus.register = MagicMock()

    (load_token,) = _cred_patch()
    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("lyra.nats.nats_bus.NatsBus", return_value=mock_inbound_bus),
        patch("lyra.adapters.telegram.TelegramAdapter", return_value=mock_adapter),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.NatsOutboundListener",
            return_value=AsyncMock(),
        ),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.wait_for_hub",
            AsyncMock(return_value=True),
        ),
        patch(
            "lyra.bootstrap.standalone.audio_consumer_bootstrap.ensure_stream",
            new_callable=AsyncMock,
        ) as mock_ensure_stream,
        patch(
            "lyra.bootstrap.standalone.audio_consumer_bootstrap.ensure_kv",
            new_callable=AsyncMock,
        ) as mock_ensure_kv,
        patch(
            "lyra.bootstrap.standalone.audio_consumer_bootstrap.ensure_consumer",
            new_callable=AsyncMock,
        ) as mock_ensure_consumer,
        patch(
            "lyra.bootstrap.standalone.audio_consumer_bootstrap.JetStreamAudioConsumer",
            return_value=mock_consumer,
        ) as mock_consumer_cls,
        load_token,
        patch.dict(os.environ, {"NATS_URL": "nats://localhost:4222"}),
    ):
        await _bootstrap_adapter_standalone(
            _make_raw_config("telegram"), "telegram", _stop=stop
        )

    # Provisioning order: stream → kv → consumer
    mock_ensure_stream.assert_awaited_once_with(mock_js)
    mock_ensure_kv.assert_awaited_once_with(mock_js)
    mock_ensure_consumer.assert_awaited_once_with(
        mock_js,
        durable="outbound-audio-telegram",
        filter_subject="lyra.outbound.audio.telegram.>",
    )

    # Constructor called with correct durable + filter
    mock_consumer_cls.assert_called_once()
    _, ctor_kwargs = mock_consumer_cls.call_args
    assert ctor_kwargs["durable"] == "outbound-audio-telegram"
    assert ctor_kwargs["filter_subject"] == "lyra.outbound.audio.telegram.>"

    # send_audio / send_text wired to adapter bound methods
    assert ctor_kwargs["send_audio"] == mock_adapter.render_audio
    assert ctor_kwargs["send_text"] == mock_adapter.send

    # Consumer lifecycle: started during bootstrap, stopped in teardown
    mock_consumer.start.assert_awaited_once()
    mock_consumer.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_bootstrap_audio_consumer_telegram_stop_called_in_teardown() -> None:
    """Telegram: consumer.stop() is called in the teardown finally block."""
    from lyra.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )

    # Use a stop event that immediately fires — teardown path is exercised.
    stop = asyncio.Event()
    stop.set()

    mock_nc = _make_nc_mock()

    mock_adapter = AsyncMock()
    mock_adapter._bot_id = "main"
    mock_adapter.resolve_identity = AsyncMock()
    mock_adapter.astart = AsyncMock()
    mock_adapter.close = AsyncMock()
    mock_adapter.dp.start_polling = AsyncMock(return_value=None)
    mock_adapter.dp.stop_polling = AsyncMock()
    mock_adapter.render_audio = AsyncMock()
    mock_adapter.send = AsyncMock()

    mock_consumer = AsyncMock()
    mock_inbound_bus = AsyncMock()
    mock_inbound_bus.register = MagicMock()

    (load_token,) = _cred_patch()
    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("lyra.nats.nats_bus.NatsBus", return_value=mock_inbound_bus),
        patch("lyra.adapters.telegram.TelegramAdapter", return_value=mock_adapter),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.NatsOutboundListener",
            return_value=AsyncMock(),
        ),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.wait_for_hub",
            AsyncMock(return_value=True),
        ),
        patch(
            "lyra.bootstrap.standalone.audio_consumer_bootstrap.ensure_stream",
            new_callable=AsyncMock,
        ),
        patch(
            "lyra.bootstrap.standalone.audio_consumer_bootstrap.ensure_kv",
            new_callable=AsyncMock,
        ),
        patch(
            "lyra.bootstrap.standalone.audio_consumer_bootstrap.ensure_consumer",
            new_callable=AsyncMock,
        ),
        patch(
            "lyra.bootstrap.standalone.audio_consumer_bootstrap.JetStreamAudioConsumer",
            return_value=mock_consumer,
        ),
        load_token,
        patch.dict(os.environ, {"NATS_URL": "nats://localhost:4222"}),
    ):
        await _bootstrap_adapter_standalone(
            _make_raw_config("telegram"), "telegram", _stop=stop
        )

    # Teardown must stop the audio consumer.
    mock_consumer.stop.assert_awaited_once()


# ---------------------------------------------------------------------------
# Discord bootstrap_audio_consumer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_audio_consumer_discord_provisions_and_starts() -> None:
    """Discord: ensure_stream/kv/consumer called, consumer.start() called."""
    from lyra.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )

    stop = asyncio.Event()
    stop.set()

    mock_nc = _make_nc_mock()
    mock_js = mock_nc.jetstream()

    mock_adapter_dc = AsyncMock()
    mock_adapter_dc._bot_id = "main"
    mock_adapter_dc.astart = AsyncMock()
    mock_adapter_dc.close = AsyncMock()
    mock_adapter_dc.start = AsyncMock(return_value=None)
    mock_adapter_dc.render_audio = AsyncMock()
    mock_adapter_dc.send = AsyncMock()

    mock_consumer_dc = AsyncMock()
    mock_inbound_bus_dc = AsyncMock()
    mock_inbound_bus_dc.register = MagicMock()

    (load_token_dc,) = _cred_patch("discord-token")
    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("lyra.nats.nats_bus.NatsBus", return_value=mock_inbound_bus_dc),
        patch(
            "lyra.adapters.discord.DiscordAdapter", return_value=mock_adapter_dc
        ),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.NatsOutboundListener",
            return_value=AsyncMock(),
        ),
        patch(
            "lyra.bootstrap.standalone.adapter_standalone.wait_for_hub",
            AsyncMock(return_value=True),
        ),
        patch(
            "lyra.bootstrap.standalone.audio_consumer_bootstrap.ensure_stream",
            new_callable=AsyncMock,
        ) as mock_ensure_stream_dc,
        patch(
            "lyra.bootstrap.standalone.audio_consumer_bootstrap.ensure_kv",
            new_callable=AsyncMock,
        ) as mock_ensure_kv_dc,
        patch(
            "lyra.bootstrap.standalone.audio_consumer_bootstrap.ensure_consumer",
            new_callable=AsyncMock,
        ) as mock_ensure_consumer_dc,
        patch(
            "lyra.bootstrap.standalone.audio_consumer_bootstrap.JetStreamAudioConsumer",
            return_value=mock_consumer_dc,
        ) as mock_consumer_cls_dc,
        load_token_dc,
        patch.dict(os.environ, {"NATS_URL": "nats://localhost:4222"}),
    ):
        await _bootstrap_adapter_standalone(
            _make_raw_config("discord"), "discord", _stop=stop
        )

    mock_ensure_stream_dc.assert_awaited_once_with(mock_js)
    mock_ensure_kv_dc.assert_awaited_once_with(mock_js)
    mock_ensure_consumer_dc.assert_awaited_once_with(
        mock_js,
        durable="outbound-audio-discord",
        filter_subject="lyra.outbound.audio.discord.>",
    )

    mock_consumer_cls_dc.assert_called_once()
    _, ctor_kwargs_dc = mock_consumer_cls_dc.call_args
    assert ctor_kwargs_dc["durable"] == "outbound-audio-discord"
    assert ctor_kwargs_dc["filter_subject"] == "lyra.outbound.audio.discord.>"
    assert ctor_kwargs_dc["send_audio"] == mock_adapter_dc.render_audio
    assert ctor_kwargs_dc["send_text"] == mock_adapter_dc.send

    mock_consumer_dc.start.assert_awaited_once()
    mock_consumer_dc.stop.assert_awaited_once()
