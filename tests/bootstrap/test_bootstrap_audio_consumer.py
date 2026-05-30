"""Tests for audio consumer wiring in standalone_telegram/standalone_discord (#1482 T8).

Asserts that bootstrap_telegram_standalone / bootstrap_discord_standalone:
  1. Call start_audio_consumer after astart() + typing-listener, passing js from
     nc.jetstream(), the correct platform string, bot_id, and adapter instance.
  2. Pass per-bot durable ("outbound-audio-{platform}-{bot_id}") and filter
     ("lyra.outbound.audio.{platform}.{bot_id}.>") to ensure_consumer + ctor.
  3. Wire adapter.render_audio as send_audio and adapter.send as send_text.
  4. Call consumer.start() and consumer.stop() (via _close_tg/dc_wired).
  5. astart-failure path: start_audio_consumer is never called.

All tests override the autouse _noop_audio_consumer conftest fixture by applying
their own `with patch(...)` blocks inside the test body (innermost patch wins).
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import _LOAD_BOT_TOKEN_PATH

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
            _LOAD_BOT_TOKEN_PATH,
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
# Telegram — provisions and starts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_audio_consumer_telegram_provisions_and_starts() -> None:
    """Telegram: start_audio_consumer called with correct js/platform/bot_id/adapter.

    Patches start_audio_consumer at the wiring module import path (overriding the
    conftest autouse no-op) and captures the call arguments.  Also patches the
    underlying ensure_*/JetStreamAudioConsumer via a side_effect that calls through
    to the real start_audio_consumer so provisioning assertions hold.
    """
    from lyra.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )
    from lyra.bootstrap.standalone.audio_consumer_bootstrap import (
        start_audio_consumer as real_start_audio_consumer,
    )

    stop = asyncio.Event()
    stop.set()

    mock_nc = _make_nc_mock()
    mock_js = mock_nc.jetstream()

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

    captured_calls: list[dict] = []

    async def _capturing_start_audio_consumer(js, platform, bot_id, adapter):
        captured_calls.append(
            {"js": js, "platform": platform, "bot_id": bot_id, "adapter": adapter}
        )
        # Call through to the real function so ensure_*/JetStreamAudioConsumer run.
        return await real_start_audio_consumer(js, platform, bot_id, adapter)

    (load_token,) = _cred_patch()
    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("lyra.nats.nats_bus.NatsBus", return_value=mock_inbound_bus),
        patch("lyra.adapters.telegram.TelegramAdapter", return_value=mock_adapter),
        patch(
            "lyra.bootstrap.wiring.standalone_telegram.NatsOutboundListener",
            return_value=AsyncMock(),
        ),
        patch(
            "lyra.bootstrap.wiring.standalone_telegram.wait_for_hub",
            AsyncMock(return_value=True),
        ),
        # Override the conftest no-op with a capturing call-through.
        patch(
            "lyra.bootstrap.wiring.standalone_telegram.start_audio_consumer",
            side_effect=_capturing_start_audio_consumer,
        ),
        # Intercept NATS provisioning inside start_audio_consumer.
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

    # start_audio_consumer called once with the right arguments
    assert len(captured_calls) == 1
    assert captured_calls[0]["js"] is mock_js
    assert captured_calls[0]["platform"] == "telegram"
    assert captured_calls[0]["bot_id"] == "main"
    assert captured_calls[0]["adapter"] is mock_adapter

    # Provisioning order: stream → kv → consumer with per-bot durable/filter
    mock_ensure_stream.assert_awaited_once_with(mock_js)
    mock_ensure_kv.assert_awaited_once_with(mock_js)
    mock_ensure_consumer.assert_awaited_once_with(
        mock_js,
        durable="outbound-audio-telegram-main",
        filter_subject="lyra.outbound.audio.telegram.main",
    )

    # Constructor: per-bot durable + filter, correct send bindings
    mock_consumer_cls.assert_called_once()
    _, ctor_kwargs = mock_consumer_cls.call_args
    assert ctor_kwargs["durable"] == "outbound-audio-telegram-main"
    assert ctor_kwargs["filter_subject"] == "lyra.outbound.audio.telegram.main"
    assert ctor_kwargs["send_audio"] == mock_adapter.render_audio
    assert ctor_kwargs["send_text"] == mock_adapter.send

    # Consumer lifecycle: started during _wire_bot, stopped via _close_tg_wired
    mock_consumer.start.assert_awaited_once()
    mock_consumer.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_bootstrap_audio_consumer_telegram_stop_called_in_teardown() -> None:
    """Telegram: consumer.stop() called via _close_tg_wired in teardown finally."""
    from lyra.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )

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
            "lyra.bootstrap.wiring.standalone_telegram.NatsOutboundListener",
            return_value=AsyncMock(),
        ),
        patch(
            "lyra.bootstrap.wiring.standalone_telegram.wait_for_hub",
            AsyncMock(return_value=True),
        ),
        patch(
            "lyra.bootstrap.wiring.standalone_telegram.start_audio_consumer",
            AsyncMock(return_value=mock_consumer),
        ),
        load_token,
        patch.dict(os.environ, {"NATS_URL": "nats://localhost:4222"}),
    ):
        await _bootstrap_adapter_standalone(
            _make_raw_config("telegram"), "telegram", _stop=stop
        )

    # Teardown (_close_tg_wired) must stop the consumer.
    mock_consumer.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_bootstrap_audio_consumer_tg_no_consumer_on_astart_failure() -> None:
    """Telegram astart failure: start_audio_consumer never called."""
    from lyra.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )

    mock_nc = _make_nc_mock()
    mock_adapter = AsyncMock()
    mock_adapter._bot_id = "main"
    mock_adapter.resolve_identity = AsyncMock()
    mock_adapter.astart = AsyncMock(side_effect=RuntimeError("astart failed"))
    mock_adapter.close = AsyncMock()

    mock_inbound_bus = AsyncMock()
    mock_inbound_bus.register = MagicMock()

    (load_token,) = _cred_patch()
    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("lyra.nats.nats_bus.NatsBus", return_value=mock_inbound_bus),
        patch("lyra.adapters.telegram.TelegramAdapter", return_value=mock_adapter),
        patch(
            "lyra.bootstrap.wiring.standalone_telegram.NatsOutboundListener",
            return_value=AsyncMock(),
        ),
        patch(
            "lyra.bootstrap.wiring.standalone_telegram.start_audio_consumer",
            new_callable=AsyncMock,
        ) as mock_start_consumer,
        load_token,
        patch.dict(os.environ, {"NATS_URL": "nats://localhost:4222"}),
        pytest.raises(RuntimeError, match="astart failed"),
    ):
        await _bootstrap_adapter_standalone(_make_raw_config("telegram"), "telegram")

    # Consumer must NOT be started when astart raises
    mock_start_consumer.assert_not_awaited()


# ---------------------------------------------------------------------------
# Discord — provisions and starts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_audio_consumer_discord_provisions_and_starts() -> None:
    """Discord: start_audio_consumer called with correct js/platform/bot_id/adapter."""
    from lyra.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )
    from lyra.bootstrap.standalone.audio_consumer_bootstrap import (
        start_audio_consumer as real_start_audio_consumer,
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

    captured_calls: list[dict] = []

    async def _capturing_start_audio_consumer(js, platform, bot_id, adapter):
        captured_calls.append(
            {"js": js, "platform": platform, "bot_id": bot_id, "adapter": adapter}
        )
        return await real_start_audio_consumer(js, platform, bot_id, adapter)

    (load_token_dc,) = _cred_patch("discord-token")
    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("lyra.nats.nats_bus.NatsBus", return_value=mock_inbound_bus_dc),
        patch("lyra.adapters.discord.DiscordAdapter", return_value=mock_adapter_dc),
        patch(
            "lyra.bootstrap.wiring.standalone_discord.NatsOutboundListener",
            return_value=AsyncMock(),
        ),
        patch(
            "lyra.bootstrap.wiring.standalone_discord.wait_for_hub",
            AsyncMock(return_value=True),
        ),
        patch(
            "lyra.bootstrap.wiring.standalone_discord.start_audio_consumer",
            side_effect=_capturing_start_audio_consumer,
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

    assert len(captured_calls) == 1
    assert captured_calls[0]["js"] is mock_js
    assert captured_calls[0]["platform"] == "discord"
    assert captured_calls[0]["bot_id"] == "main"
    assert captured_calls[0]["adapter"] is mock_adapter_dc

    mock_ensure_stream_dc.assert_awaited_once_with(mock_js)
    mock_ensure_kv_dc.assert_awaited_once_with(mock_js)
    mock_ensure_consumer_dc.assert_awaited_once_with(
        mock_js,
        durable="outbound-audio-discord-main",
        filter_subject="lyra.outbound.audio.discord.main",
    )

    mock_consumer_cls_dc.assert_called_once()
    _, ctor_kwargs_dc = mock_consumer_cls_dc.call_args
    assert ctor_kwargs_dc["durable"] == "outbound-audio-discord-main"
    assert ctor_kwargs_dc["filter_subject"] == "lyra.outbound.audio.discord.main"
    assert ctor_kwargs_dc["send_audio"] == mock_adapter_dc.render_audio
    assert ctor_kwargs_dc["send_text"] == mock_adapter_dc.send

    mock_consumer_dc.start.assert_awaited_once()
    mock_consumer_dc.stop.assert_awaited_once()


# ---------------------------------------------------------------------------
# S2 — NullAudioConsumer sentinel (ADR-079 §c)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_audio_consumer_returns_null_on_ensure_stream_failure() -> None:
    """Returns NullAudioConsumer (not None, no raise) when ensure_stream fails."""
    import nats.errors

    from lyra.adapters.nats.null_audio_consumer import NullAudioConsumer
    from lyra.bootstrap.standalone.audio_consumer_bootstrap import start_audio_consumer

    mock_js = MagicMock()

    with patch(
        "lyra.bootstrap.standalone.audio_consumer_bootstrap.ensure_stream",
        new_callable=AsyncMock,
        side_effect=nats.errors.Error("STREAM.CREATE denied"),
    ):
        result = await start_audio_consumer(mock_js, "telegram", "main", MagicMock())

    assert isinstance(result, NullAudioConsumer)


@pytest.mark.asyncio
async def test_start_audio_consumer_returns_null_on_ensure_kv_failure() -> None:
    """start_audio_consumer returns NullAudioConsumer when ensure_kv raises."""
    from lyra.adapters.nats.null_audio_consumer import NullAudioConsumer
    from lyra.bootstrap.standalone.audio_consumer_bootstrap import start_audio_consumer

    mock_js = MagicMock()

    with (
        patch(
            "lyra.bootstrap.standalone.audio_consumer_bootstrap.ensure_stream",
            new_callable=AsyncMock,
        ),
        patch(
            "lyra.bootstrap.standalone.audio_consumer_bootstrap.ensure_kv",
            new_callable=AsyncMock,
            side_effect=RuntimeError("KV create failed"),
        ),
    ):
        result = await start_audio_consumer(mock_js, "discord", "main", MagicMock())

    assert isinstance(result, NullAudioConsumer)


@pytest.mark.asyncio
async def test_start_audio_consumer_returns_real_consumer_on_success() -> None:
    """start_audio_consumer returns the real JetStreamAudioConsumer on happy path."""
    from lyra.adapters.nats.jetstream_audio_consumer import JetStreamAudioConsumer
    from lyra.bootstrap.standalone.audio_consumer_bootstrap import start_audio_consumer

    mock_js = MagicMock()
    mock_kv = MagicMock()
    mock_consumer = AsyncMock(spec=JetStreamAudioConsumer)
    mock_adapter = MagicMock()
    mock_adapter.render_audio = AsyncMock()
    mock_adapter.send = AsyncMock()

    with (
        patch(
            "lyra.bootstrap.standalone.audio_consumer_bootstrap.ensure_stream",
            new_callable=AsyncMock,
        ),
        patch(
            "lyra.bootstrap.standalone.audio_consumer_bootstrap.ensure_kv",
            new_callable=AsyncMock,
            return_value=mock_kv,
        ),
        patch(
            "lyra.bootstrap.standalone.audio_consumer_bootstrap.ensure_consumer",
            new_callable=AsyncMock,
        ),
        patch(
            "lyra.bootstrap.standalone.audio_consumer_bootstrap.JetStreamAudioConsumer",
            return_value=mock_consumer,
        ),
    ):
        result = await start_audio_consumer(mock_js, "telegram", "main", mock_adapter)

    assert result is mock_consumer
    mock_consumer.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_null_audio_consumer_stop_is_awaitable_noop() -> None:
    """NullAudioConsumer.stop() is awaitable and completes without error."""
    from lyra.adapters.nats.null_audio_consumer import NullAudioConsumer

    sentinel = NullAudioConsumer()
    # Must not raise; return value is None.
    result = await sentinel.stop()
    assert result is None


@pytest.mark.asyncio
async def test_teardown_calls_stop_on_null_sentinel_without_error() -> None:
    """Teardown calls .stop() on NullAudioConsumer — no if-guards, no error."""
    from lyra.adapters.nats.null_audio_consumer import NullAudioConsumer
    from lyra.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )

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

    mock_inbound_bus = AsyncMock()
    mock_inbound_bus.register = MagicMock()

    # Return a real NullAudioConsumer (not a mock) to verify the sentinel contract.
    null_consumer = NullAudioConsumer()

    (load_token,) = _cred_patch()
    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("lyra.nats.nats_bus.NatsBus", return_value=mock_inbound_bus),
        patch("lyra.adapters.telegram.TelegramAdapter", return_value=mock_adapter),
        patch(
            "lyra.bootstrap.wiring.standalone_telegram.NatsOutboundListener",
            return_value=AsyncMock(),
        ),
        patch(
            "lyra.bootstrap.wiring.standalone_telegram.wait_for_hub",
            AsyncMock(return_value=True),
        ),
        patch(
            "lyra.bootstrap.wiring.standalone_telegram.start_audio_consumer",
            AsyncMock(return_value=null_consumer),
        ),
        load_token,
        patch.dict(os.environ, {"NATS_URL": "nats://localhost:4222"}),
    ):
        # Should complete without any AttributeError or TypeError from teardown.
        await _bootstrap_adapter_standalone(
            _make_raw_config("telegram"), "telegram", _stop=stop
        )
