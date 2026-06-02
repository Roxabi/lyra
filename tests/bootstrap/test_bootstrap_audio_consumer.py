"""Tests for audio consumer wiring in standalone_telegram/standalone_discord (#1482 T8).

Asserts that bootstrap_telegram_standalone / bootstrap_discord_standalone:
  1. Call start_audio_consumer after astart() + typing-listener, passing js from
     nc.jetstream(), the correct platform string, bot_id, and adapter instance.
  2. Pass per-bot durable ("outbound-audio-{platform}-{bot_id}") and filter
     ("lyra.outbound.audio.{platform}.{bot_id}.>") to ensure_consumer + ctor.
  3. Wire adapter.render_audio as send_audio and adapter.send as send_text.
  4. Call consumer.start() and consumer.stop() (via _close_tg/dc_wired).
  5. astart-failure path: start_audio_consumer is never called.

Also asserts ADR-079 S3 bind-only and ordering invariants:
  6. start_audio_consumer does NOT import ensure_stream/ensure_kv (sole-provisioner).
  7. start_audio_consumer uses js.key_value(KV_BUCKET) (bind-only) not ensure_kv.
  8. wait_for_hub completes BEFORE start_audio_consumer is invoked (ordering barrier).

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
    from factory.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )
    from factory.bootstrap.standalone.audio_consumer_bootstrap import (
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

    # S3 bind-only: js.key_value() is the bind path (hub already provisioned).
    mock_kv = MagicMock()
    mock_js.key_value = AsyncMock(return_value=mock_kv)

    (load_token,) = _cred_patch()
    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("factory.nats.nats_bus.NatsBus", return_value=mock_inbound_bus),
        patch("factory.adapters.telegram.TelegramAdapter", return_value=mock_adapter),
        patch(
            "factory.bootstrap.wiring.standalone_telegram.NatsOutboundListener",
            return_value=AsyncMock(),
        ),
        patch(
            "factory.bootstrap.wiring.standalone_telegram.wait_for_hub",
            AsyncMock(return_value=True),
        ),
        # Override the conftest no-op with a capturing call-through.
        patch(
            "factory.bootstrap.wiring.standalone_telegram.start_audio_consumer",
            side_effect=_capturing_start_audio_consumer,
        ),
        # S3: ensure_consumer still called per-bot; ensure_stream/ensure_kv removed.
        patch(
            "factory.bootstrap.standalone.audio_consumer_bootstrap.ensure_consumer",
            new_callable=AsyncMock,
        ) as mock_ensure_consumer,
        patch(
            "factory.bootstrap.standalone.audio_consumer_bootstrap.JetStreamAudioConsumer",
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

    # S3 bind-only: js.key_value() called with KV_BUCKET (hub already provisioned).
    mock_js.key_value.assert_awaited_once_with("lyra_outbound_audio_sent")
    # Consumer created with per-bot durable/filter
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
    from factory.bootstrap.standalone.adapter_standalone import (
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
        patch("factory.nats.nats_bus.NatsBus", return_value=mock_inbound_bus),
        patch("factory.adapters.telegram.TelegramAdapter", return_value=mock_adapter),
        patch(
            "factory.bootstrap.wiring.standalone_telegram.NatsOutboundListener",
            return_value=AsyncMock(),
        ),
        patch(
            "factory.bootstrap.wiring.standalone_telegram.wait_for_hub",
            AsyncMock(return_value=True),
        ),
        patch(
            "factory.bootstrap.wiring.standalone_telegram.start_audio_consumer",
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
    from factory.bootstrap.standalone.adapter_standalone import (
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
        patch("factory.nats.nats_bus.NatsBus", return_value=mock_inbound_bus),
        patch("factory.adapters.telegram.TelegramAdapter", return_value=mock_adapter),
        patch(
            "factory.bootstrap.wiring.standalone_telegram.NatsOutboundListener",
            return_value=AsyncMock(),
        ),
        # ADR-079 S3: wait_for_hub now precedes the wiring loop; must be patched
        # so this test doesn't attempt real NATS KV operations on the mock nc.
        patch(
            "factory.bootstrap.wiring.standalone_telegram.wait_for_hub",
            AsyncMock(return_value=None),
        ),
        patch(
            "factory.bootstrap.wiring.standalone_telegram.start_audio_consumer",
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
    from factory.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )
    from factory.bootstrap.standalone.audio_consumer_bootstrap import (
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

    # S3 bind-only: js.key_value() is the bind path (hub already provisioned).
    mock_kv_dc = MagicMock()
    mock_js.key_value = AsyncMock(return_value=mock_kv_dc)

    (load_token_dc,) = _cred_patch("discord-token")
    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("factory.nats.nats_bus.NatsBus", return_value=mock_inbound_bus_dc),
        patch("factory.adapters.discord.DiscordAdapter", return_value=mock_adapter_dc),
        patch(
            "factory.bootstrap.wiring.standalone_discord.NatsOutboundListener",
            return_value=AsyncMock(),
        ),
        patch(
            "factory.bootstrap.wiring.standalone_discord.wait_for_hub",
            AsyncMock(return_value=True),
        ),
        patch(
            "factory.bootstrap.wiring.standalone_discord.start_audio_consumer",
            side_effect=_capturing_start_audio_consumer,
        ),
        # S3: ensure_consumer still called per-bot; ensure_stream/ensure_kv removed.
        patch(
            "factory.bootstrap.standalone.audio_consumer_bootstrap.ensure_consumer",
            new_callable=AsyncMock,
        ) as mock_ensure_consumer_dc,
        patch(
            "factory.bootstrap.standalone.audio_consumer_bootstrap.JetStreamAudioConsumer",
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

    # S3 bind-only: js.key_value() called with KV_BUCKET (hub already provisioned).
    mock_js.key_value.assert_awaited_once_with("lyra_outbound_audio_sent")
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
async def test_start_audio_consumer_returns_null_on_key_value_failure() -> None:
    """Returns NullAudioConsumer (not None, no raise) when js.key_value() fails.

    S3: adapter uses js.key_value() (bind-only); hub provisions the bucket.
    If js.key_value() fails (e.g. bucket not yet provisioned), the adapter
    degrades to NullAudioConsumer instead of raising.
    """
    import nats.errors

    from factory.adapters.nats.null_audio_consumer import NullAudioConsumer
    from factory.bootstrap.standalone.audio_consumer_bootstrap import (
        start_audio_consumer,
    )

    mock_js = MagicMock()
    mock_js.key_value = AsyncMock(side_effect=nats.errors.Error("bucket not found"))

    result = await start_audio_consumer(mock_js, "telegram", "main", MagicMock())

    assert isinstance(result, NullAudioConsumer)


@pytest.mark.asyncio
async def test_start_audio_consumer_returns_null_on_ensure_consumer_failure() -> None:
    """start_audio_consumer returns NullAudioConsumer when ensure_consumer raises."""
    from factory.adapters.nats.null_audio_consumer import NullAudioConsumer
    from factory.bootstrap.standalone.audio_consumer_bootstrap import (
        start_audio_consumer,
    )

    mock_js = MagicMock()
    mock_js.key_value = AsyncMock(return_value=MagicMock())

    with patch(
        "factory.bootstrap.standalone.audio_consumer_bootstrap.ensure_consumer",
        new_callable=AsyncMock,
        side_effect=RuntimeError("consumer create failed"),
    ):
        result = await start_audio_consumer(mock_js, "discord", "main", MagicMock())

    assert isinstance(result, NullAudioConsumer)


@pytest.mark.asyncio
async def test_start_audio_consumer_returns_real_consumer_on_success() -> None:
    """start_audio_consumer returns the real JetStreamAudioConsumer on happy path.

    S3: bind-only path — js.key_value() called with KV_BUCKET, not ensure_kv.
    """
    from factory.adapters.nats.jetstream_audio_consumer import JetStreamAudioConsumer
    from factory.bootstrap.standalone.audio_consumer_bootstrap import (
        start_audio_consumer,
    )

    mock_js = MagicMock()
    mock_kv = MagicMock()
    mock_js.key_value = AsyncMock(return_value=mock_kv)
    mock_consumer = AsyncMock(spec=JetStreamAudioConsumer)
    mock_adapter = MagicMock()
    mock_adapter.render_audio = AsyncMock()
    mock_adapter.send = AsyncMock()

    with (
        patch(
            "factory.bootstrap.standalone.audio_consumer_bootstrap.ensure_consumer",
            new_callable=AsyncMock,
        ),
        patch(
            "factory.bootstrap.standalone.audio_consumer_bootstrap.JetStreamAudioConsumer",
            return_value=mock_consumer,
        ),
    ):
        result = await start_audio_consumer(mock_js, "telegram", "main", mock_adapter)

    assert result is mock_consumer
    mock_consumer.start.assert_awaited_once()
    # S3 bind-only: js.key_value called with KV_BUCKET
    mock_js.key_value.assert_awaited_once_with("lyra_outbound_audio_sent")


# ---------------------------------------------------------------------------
# S3 — Adapter bind-only (ADR-079 sole-provisioner)
# ---------------------------------------------------------------------------


def test_start_audio_consumer_does_not_import_ensure_stream() -> None:
    """audio_consumer_bootstrap must NOT import ensure_stream or ensure_kv (S3).

    Structural invariant: after the sole-provisioner migration (ADR-079 S3), the
    adapter bootstrap module is bind-only and must not carry provisioning imports.
    This test fails immediately if anyone re-adds ensure_stream or ensure_kv to
    audio_consumer_bootstrap, making it impossible for the adapter to accidentally
    provision the stream/KV.

    NOTE on prior tautological version: the previous test patched
    ``factory.infrastructure.outbound_audio.stream_setup.ensure_stream`` — a path that
    audio_consumer_bootstrap never imports. ``assert_not_awaited()`` on that mock
    could never fail regardless of what the module does, so it provided zero
    protection. This structural ``hasattr`` check directly tests the invariant.
    """
    import factory.bootstrap.standalone.audio_consumer_bootstrap as acb

    assert not hasattr(acb, "ensure_stream"), (
        "audio_consumer_bootstrap must not import ensure_stream — "
        "stream provisioning belongs to the hub sole-provisioner (ADR-079 S3)."
    )
    assert not hasattr(acb, "ensure_kv"), (
        "audio_consumer_bootstrap must not import ensure_kv — "
        "KV provisioning belongs to the hub sole-provisioner (ADR-079 S3)."
    )


@pytest.mark.asyncio
async def test_start_audio_consumer_uses_key_value_bind_not_ensure_kv() -> None:
    """start_audio_consumer uses js.key_value() (bind) not ensure_kv() (S3).

    Verifies that:
    - js.key_value is awaited with KV_BUCKET ("lyra_outbound_audio_sent")
    - ensure_kv is NOT in the call chain
    """
    from factory.bootstrap.standalone.audio_consumer_bootstrap import (
        KV_BUCKET,
        start_audio_consumer,
    )

    mock_js = MagicMock()
    mock_kv = MagicMock()
    mock_js.key_value = AsyncMock(return_value=mock_kv)

    with (
        patch(
            "factory.bootstrap.standalone.audio_consumer_bootstrap.ensure_consumer",
            new_callable=AsyncMock,
        ),
        patch(
            "factory.bootstrap.standalone.audio_consumer_bootstrap.JetStreamAudioConsumer",
            return_value=AsyncMock(),
        ),
    ):
        await start_audio_consumer(mock_js, "telegram", "main", MagicMock())

    mock_js.key_value.assert_awaited_once_with(KV_BUCKET)


@pytest.mark.asyncio
async def test_null_audio_consumer_stop_is_awaitable_noop() -> None:
    """NullAudioConsumer.stop() is awaitable and completes without error."""
    from factory.adapters.nats.null_audio_consumer import NullAudioConsumer

    sentinel = NullAudioConsumer()
    # Must not raise; return value is None.
    result = await sentinel.stop()
    assert result is None


@pytest.mark.asyncio
async def test_teardown_calls_stop_on_null_sentinel_without_error() -> None:
    """Teardown calls .stop() on NullAudioConsumer — no if-guards, no error."""
    from factory.adapters.nats.null_audio_consumer import NullAudioConsumer
    from factory.bootstrap.standalone.adapter_standalone import (
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
        patch("factory.nats.nats_bus.NatsBus", return_value=mock_inbound_bus),
        patch("factory.adapters.telegram.TelegramAdapter", return_value=mock_adapter),
        patch(
            "factory.bootstrap.wiring.standalone_telegram.NatsOutboundListener",
            return_value=AsyncMock(),
        ),
        patch(
            "factory.bootstrap.wiring.standalone_telegram.wait_for_hub",
            AsyncMock(return_value=True),
        ),
        patch(
            "factory.bootstrap.wiring.standalone_telegram.start_audio_consumer",
            AsyncMock(return_value=null_consumer),
        ),
        load_token,
        patch.dict(os.environ, {"NATS_URL": "nats://localhost:4222"}),
    ):
        # Should complete without any AttributeError or TypeError from teardown.
        await _bootstrap_adapter_standalone(
            _make_raw_config("telegram"), "telegram", _stop=stop
        )


# ---------------------------------------------------------------------------
# ADR-079 S3 — Ordering: wait_for_hub before start_audio_consumer (B3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_hub_called_before_start_audio_consumer_telegram() -> None:
    """Telegram: wait_for_hub completes before start_audio_consumer is invoked.

    This is the regression test for B1 (ADR-079 S3 ordering fix). It MUST fail
    against the pre-B1 code (where wait_for_hub came AFTER the wiring loop) and
    pass after the fix (where wait_for_hub precedes the loop).

    Method: record call order via a shared list with side_effect callbacks on
    both mocks. Assert wait_for_hub index < start_audio_consumer index.
    """
    from factory.bootstrap.standalone.adapter_standalone import (
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

    call_order: list[str] = []

    async def _recording_wait_for_hub(*_args, **_kwargs):
        call_order.append("wait_for_hub")

    async def _recording_start_audio_consumer(*_args, **_kwargs):
        call_order.append("start_audio_consumer")
        return mock_consumer

    (load_token,) = _cred_patch()
    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("factory.nats.nats_bus.NatsBus", return_value=mock_inbound_bus),
        patch("factory.adapters.telegram.TelegramAdapter", return_value=mock_adapter),
        patch(
            "factory.bootstrap.wiring.standalone_telegram.NatsOutboundListener",
            return_value=AsyncMock(),
        ),
        patch(
            "factory.bootstrap.wiring.standalone_telegram.wait_for_hub",
            side_effect=_recording_wait_for_hub,
        ),
        patch(
            "factory.bootstrap.wiring.standalone_telegram.start_audio_consumer",
            side_effect=_recording_start_audio_consumer,
        ),
        load_token,
        patch.dict(os.environ, {"NATS_URL": "nats://localhost:4222"}),
    ):
        await _bootstrap_adapter_standalone(
            _make_raw_config("telegram"), "telegram", _stop=stop
        )

    assert "wait_for_hub" in call_order, "wait_for_hub was never called"
    assert "start_audio_consumer" in call_order, "start_audio_consumer was never called"
    wfh_idx = call_order.index("wait_for_hub")
    sac_idx = call_order.index("start_audio_consumer")
    assert wfh_idx < sac_idx, (
        f"wait_for_hub (pos {wfh_idx}) must precede start_audio_consumer "
        f"(pos {sac_idx}) — ADR-079 S3 ordering invariant violated. "
        "This indicates the pre-B1 regression: move wait_for_hub before the "
        "wiring loop in standalone_telegram.py."
    )


@pytest.mark.asyncio
async def test_wait_for_hub_called_before_start_audio_consumer_discord() -> None:
    """Discord: wait_for_hub completes before start_audio_consumer is invoked.

    Mirror of the Telegram ordering test (ADR-079 S3 / B1 regression guard).
    """
    from factory.bootstrap.standalone.adapter_standalone import (
        _bootstrap_adapter_standalone,
    )

    stop = asyncio.Event()
    stop.set()

    mock_nc = _make_nc_mock()
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

    call_order: list[str] = []

    async def _recording_wait_for_hub(*_args, **_kwargs):
        call_order.append("wait_for_hub")

    async def _recording_start_audio_consumer(*_args, **_kwargs):
        call_order.append("start_audio_consumer")
        return mock_consumer_dc

    (load_token_dc,) = _cred_patch("discord-token")
    with (
        patch("nats.connect", AsyncMock(return_value=mock_nc)),
        patch("factory.nats.nats_bus.NatsBus", return_value=mock_inbound_bus_dc),
        patch("factory.adapters.discord.DiscordAdapter", return_value=mock_adapter_dc),
        patch(
            "factory.bootstrap.wiring.standalone_discord.NatsOutboundListener",
            return_value=AsyncMock(),
        ),
        patch(
            "factory.bootstrap.wiring.standalone_discord.wait_for_hub",
            side_effect=_recording_wait_for_hub,
        ),
        patch(
            "factory.bootstrap.wiring.standalone_discord.start_audio_consumer",
            side_effect=_recording_start_audio_consumer,
        ),
        load_token_dc,
        patch.dict(os.environ, {"NATS_URL": "nats://localhost:4222"}),
    ):
        await _bootstrap_adapter_standalone(
            _make_raw_config("discord"), "discord", _stop=stop
        )

    assert "wait_for_hub" in call_order, "wait_for_hub was never called"
    assert "start_audio_consumer" in call_order, "start_audio_consumer was never called"
    wfh_idx = call_order.index("wait_for_hub")
    sac_idx = call_order.index("start_audio_consumer")
    assert wfh_idx < sac_idx, (
        f"wait_for_hub (pos {wfh_idx}) must precede start_audio_consumer "
        f"(pos {sac_idx}) — ADR-079 S3 ordering invariant violated. "
        "This indicates the pre-B1 regression: move wait_for_hub before the "
        "wiring loop in standalone_discord.py."
    )
