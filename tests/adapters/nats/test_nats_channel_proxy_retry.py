"""Tests for NatsChannelProxy._publish_audio_with_retry (#1503).

Covers:
  R1  transient-then-success: publish raises once then succeeds
      → publish called twice, _notify_audio_publish_failed NOT called
  R2  two-transient-then-success: publish raises twice then succeeds
      → publish called three times, _notify_audio_publish_failed NOT called
  R3  persistent failure: publish always raises
      → publish called exactly _AUDIO_PUBLISH_MAX_ATTEMPTS times,
         _notify_audio_publish_failed called once
  R4  reconnect-safe context: JetStreamContext dispatches through self._nc
      → nc.jetstream() is called during __init__; the returned context routes
         through the live nc reference (reconnect-transparent by object identity)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import nats.errors
import pytest

from factory.core.auth.trust import TrustLevel
from factory.core.messaging.message import InboundMessage, OutboundAudio, Platform
from factory.nats.audio_publish import _AUDIO_PUBLISH_MAX_ATTEMPTS
from factory.nats.nats_channel_proxy import NatsChannelProxy
from factory.nats.type_registry import TYPE_REGISTRY_RESOLVER
from roxabi_contracts.blob_ref import BlobRef

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_nc() -> MagicMock:
    """Return a mock NATS client with async publish and a JetStream context mock."""
    nc = MagicMock()
    nc.publish = AsyncMock()
    js = MagicMock()
    js.publish = AsyncMock(return_value=MagicMock())
    nc.jetstream = MagicMock(return_value=js)
    return nc


def _make_inbound(stream_id: str = "retry-test-001") -> InboundMessage:
    return InboundMessage(
        id=stream_id,
        platform=Platform.TELEGRAM.value,
        bot_id="main",
        scope_id="scope:test:1",
        user_id="u:test:1",
        user_name="testuser",
        is_mention=False,
        text="voice",
        text_raw="voice",
        trust_level=TrustLevel.PUBLIC,
    )


def _make_audio() -> OutboundAudio:
    return OutboundAudio(
        blob_ref=BlobRef(
            store_key="key/audio-retry.ogg",
            content_hash="abc123",
            mime="audio/ogg",
            size=512,
            source="voicecli",
        ),
        mime_type="audio/ogg",
    )


# ---------------------------------------------------------------------------
# R1 — transient-then-success (one failure)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_r1_transient_then_success_no_notification() -> None:
    """R1: js.publish fails once with TimeoutError then succeeds.

    Asserts:
      - publish called exactly 2 times (1 failure + 1 success)
      - _notify_audio_publish_failed NOT called
      - asyncio.sleep called once (backoff between attempts)
    """
    nc = _make_nc()
    js = nc.jetstream()
    js.publish = AsyncMock(side_effect=[asyncio.TimeoutError(), MagicMock()])

    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("r1-transient-001")
    audio = _make_audio()

    _sleep_patch = patch(
        "factory.nats.audio_publish.asyncio.sleep", new_callable=AsyncMock
    )
    _notify_patch = patch(
        "factory.nats.nats_channel_proxy.notify_audio_publish_failed",
        new_callable=AsyncMock,
    )
    with _sleep_patch as mock_sleep, _notify_patch as mock_notify:
        await proxy.render_audio(audio, inbound)

    assert js.publish.await_count == 2, (
        f"Expected 2 publish calls (1 fail + 1 success), got {js.publish.await_count}"
    )
    mock_notify.assert_not_awaited()
    # One sleep between attempt 0 and attempt 1
    assert mock_sleep.await_count == 1


# ---------------------------------------------------------------------------
# R2 — two transient failures then success
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_r2_two_transient_then_success_no_notification() -> None:
    """R2: js.publish fails twice (nats.errors.Error, TimeoutError) then succeeds.

    Asserts:
      - publish called exactly 3 times
      - _notify_audio_publish_failed NOT called
      - asyncio.sleep called twice (backoffs for attempts 0 and 1)
    """
    nc = _make_nc()
    js = nc.jetstream()
    js.publish = AsyncMock(
        side_effect=[
            nats.errors.Error("leader election"),
            asyncio.TimeoutError(),
            MagicMock(),
        ]
    )

    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("r2-two-transient-001")
    audio = _make_audio()

    _sleep_patch = patch(
        "factory.nats.audio_publish.asyncio.sleep", new_callable=AsyncMock
    )
    _notify_patch = patch(
        "factory.nats.nats_channel_proxy.notify_audio_publish_failed",
        new_callable=AsyncMock,
    )
    with _sleep_patch as mock_sleep, _notify_patch as mock_notify:
        await proxy.render_audio(audio, inbound)

    assert js.publish.await_count == 3, (
        f"Expected 3 publish calls (2 fail + 1 success), got {js.publish.await_count}"
    )
    mock_notify.assert_not_awaited()
    assert mock_sleep.await_count == 2


# ---------------------------------------------------------------------------
# R3 — persistent failure (all attempts exhausted)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_r3_persistent_failure_exhausts_attempts_and_notifies() -> None:
    """R3: js.publish always raises nats.errors.Error.

    Asserts:
      - publish called exactly _AUDIO_PUBLISH_MAX_ATTEMPTS times
      - _notify_audio_publish_failed called exactly once
      - render_audio does NOT re-raise the exception
    """
    nc = _make_nc()
    js = nc.jetstream()
    js.publish = AsyncMock(side_effect=nats.errors.Error("stream unavailable"))

    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("r3-persist-001")
    audio = _make_audio()

    _sleep_patch = patch(
        "factory.nats.audio_publish.asyncio.sleep", new_callable=AsyncMock
    )
    _notify_patch = patch(
        "factory.nats.nats_channel_proxy.notify_audio_publish_failed",
        new_callable=AsyncMock,
    )
    with _sleep_patch, _notify_patch as mock_notify:
        # Must not raise
        await proxy.render_audio(audio, inbound)

    assert js.publish.await_count == _AUDIO_PUBLISH_MAX_ATTEMPTS, (
        f"Expected {_AUDIO_PUBLISH_MAX_ATTEMPTS} publish attempts, "
        f"got {js.publish.await_count}"
    )
    mock_notify.assert_awaited_once_with(
        nc, Platform.TELEGRAM, "main", TYPE_REGISTRY_RESOLVER, inbound
    )


# ---------------------------------------------------------------------------
# R4 — reconnect-safe: cached JetStreamContext routes through live nc
# ---------------------------------------------------------------------------


def test_r4_jetstream_context_obtained_at_init_routes_through_live_nc() -> None:
    """R4: nc.jetstream() is called once during __init__.

    The returned JetStreamContext stores the NATS client by reference and
    dispatches every publish through it.  The cached context is therefore
    reconnect-transparent: reconnect mutates the nc object in-place (new socket,
    same Python object).  This test asserts nc.jetstream() is called exactly
    once at construction time, confirming the cache-on-init pattern is in use.
    """
    nc = _make_nc()
    # Reset call count after the MagicMock was constructed
    nc.jetstream.reset_mock()

    _proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")

    nc.jetstream.assert_called_once()
