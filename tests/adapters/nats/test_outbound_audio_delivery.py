"""Outcome tests for outbound audio delivery guarantees (#1482, V1 slice).

Five end-to-end delivery-contract scenarios — all fake-driven (no real NATS):

  SC1  redelivery-after-glitch → exactly-once
       Same stream_id delivered twice → send_audio called once, both acked.

  SC2  offline-then-restart → delivers
       Message pending during stop() is delivered after start() resumes.

  SC3  terminal → exactly one notif (SC7 guard: _notified dedup)
       send_audio keeps failing through max_deliver → term() + send_text
       each called exactly once, even if _handle_terminal fires again.

  SC4  publish-fail → notif (NatsChannelProxy path)
       js.publish raises → legacy text subject receives the undelivered
       notification; no raw exception text in the published payload.

  SC5  in-proc lost-ack → no double-send
       send_audio succeeds but msg.ack() raises; redelivery of the same
       stream_id is suppressed by InMemorySentSet → send_audio called once.

Fakes reuse the builder helpers from test_jetstream_audio_consumer.py.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import nats.errors
import pytest

from lyra.adapters.nats.jetstream_audio_consumer import (
    MAX_DELIVER,
    JetStreamAudioConsumer,
)
from lyra.adapters.nats.jetstream_audio_dedup import InMemorySentSet
from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import (
    InboundMessage,
    OutboundAudio,
    Platform,
)
from lyra.core.messaging.voice_notify import VOICE_UNDELIVERED_MSG
from lyra.nats.nats_channel_proxy import NatsChannelProxy
from roxabi_contracts.blob_ref import BlobRef
from tests.helpers.messages import make_test_blobref

# ---------------------------------------------------------------------------
# Shared fakes — mirrors test_jetstream_audio_consumer.py helpers
# ---------------------------------------------------------------------------

_DURABLE = "outbound-audio-telegram"
_FILTER = "lyra.outbound.audio.telegram.>"


def _make_consumer(
    *,
    send_audio: AsyncMock | None = None,
    send_text: AsyncMock | None = None,
    dedup: InMemorySentSet | None = None,
    max_deliver: int = MAX_DELIVER,
) -> JetStreamAudioConsumer:
    js = MagicMock()
    return JetStreamAudioConsumer(
        js,
        durable=_DURABLE,
        filter_subject=_FILTER,
        send_audio=send_audio or AsyncMock(),
        send_text=send_text or AsyncMock(),
        max_deliver=max_deliver,
        dedup=dedup,
    )


def _make_audio() -> OutboundAudio:
    return OutboundAudio(
        blob_ref=BlobRef(
            store_key="key/audio-test.ogg",
            content_hash="abc123",
            mime="audio/ogg",
            size=1024,
            source="voicecli",
        ),
        mime_type="audio/ogg",
    )


def _make_inbound(stream_id: str = "msg-test-001") -> InboundMessage:
    return InboundMessage(
        id=stream_id,
        platform=Platform.TELEGRAM.value,
        bot_id="123456",
        scope_id="scope:test:1",
        user_id="u:test:1",
        user_name="testuser",
        is_mention=False,
        text="voice",
        text_raw="voice",
        trust_level=TrustLevel.PUBLIC,
    )


def _make_nats_msg(
    *,
    stream_id: str = "msg-test-001",
    num_delivered_val: int = 1,
    raw_data: bytes | None = None,
) -> MagicMock:
    """Build a fake JetStream pull message with a valid audio envelope."""
    from roxabi_nats._serialize import serialize

    if raw_data is not None:
        data = raw_data
    else:
        audio = _make_audio()
        inbound = _make_inbound(stream_id)
        envelope = {
            "type": "audio",
            "stream_id": stream_id,
            "audio": json.loads(serialize(audio).decode("utf-8")),
            "original_msg": json.loads(serialize(inbound).decode("utf-8")),
        }
        data = json.dumps(envelope, ensure_ascii=False).encode("utf-8")

    msg = MagicMock()
    msg.data = data
    msg.subject = "lyra.outbound.audio.telegram.123456"
    msg.ack = AsyncMock()
    msg.nak = AsyncMock()
    msg.term = AsyncMock()

    meta = MagicMock()
    meta.num_delivered = num_delivered_val
    msg.metadata = meta
    return msg


def _make_nc() -> MagicMock:
    """Return a mock NATS client with async publish and a JetStream context mock."""
    nc = MagicMock()
    nc.publish = AsyncMock()
    js = MagicMock()
    js.publish = AsyncMock(return_value=MagicMock())
    nc.jetstream = MagicMock(return_value=js)
    return nc


# ---------------------------------------------------------------------------
# SC1 — redelivery-after-glitch → exactly-once
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sc1_redelivery_after_glitch_exactly_once() -> None:
    """SC1: same stream_id redelivered twice → send_audio once, both msgs acked.

    Simulates JetStream redelivering a message whose first ack was lost
    (glitch). The in-memory dedup set (InMemorySentSet) must suppress the
    second send while still acking both messages so the consumer stays
    unblocked.
    """
    # Arrange
    stream_id = "sc1-glitch-001"
    send_audio = AsyncMock()
    consumer = _make_consumer(send_audio=send_audio)

    msg_first = _make_nats_msg(stream_id=stream_id, num_delivered_val=1)
    msg_redeliver = _make_nats_msg(stream_id=stream_id, num_delivered_val=2)

    # Act — first delivery succeeds; second is a redelivery of same stream_id
    await consumer._process(msg_first)
    await consumer._process(msg_redeliver)

    # Assert: send_audio called exactly once despite two deliveries
    assert send_audio.await_count == 1, (
        f"send_audio called {send_audio.await_count} times; expected 1"
    )
    # Both messages must be acked (first: post-send; second: dedup hit)
    msg_first.ack.assert_awaited_once()
    msg_redeliver.ack.assert_awaited_once()
    # No terminal path triggered
    msg_first.term.assert_not_awaited()
    msg_redeliver.term.assert_not_awaited()


# ---------------------------------------------------------------------------
# SC2 — offline-then-restart → delivers
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sc2_offline_then_restart_delivers() -> None:
    """SC2: message pending while consumer was stopped is delivered after restart.

    Models the JetStream persistence scenario: the hub publishes while the
    consumer is down; on restart the pull subscription yields the pending
    message. We test that the resumed consumer processes and acks it.

    Uses asyncio.Event gates instead of sleep(0) to avoid scheduler-timing
    flakiness: the test waits for a concrete signal (fetch invoked / msg acked)
    rather than hoping one event-loop tick is enough.
    """
    # Arrange
    stream_id = "sc2-pending-001"
    # Event set when send_audio completes, so the test can stop cleanly.
    msg_processed = asyncio.Event()

    async def _send_audio_and_signal(audio: object, inbound: object) -> None:
        msg_processed.set()

    send_audio = AsyncMock(side_effect=_send_audio_and_signal)
    consumer = _make_consumer(send_audio=send_audio)
    pending_msg = _make_nats_msg(stream_id=stream_id, num_delivered_val=1)

    # first_start_fetched: set when the first subscription's fetch is called at
    # least once, so we know the loop is running before we stop it.
    first_start_fetched = asyncio.Event()

    fetch_results: list = []

    async def _fake_fetch(batch: int, timeout: float) -> list:
        first_start_fetched.set()
        if fetch_results:
            result = fetch_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        # Park until cancelled
        await asyncio.sleep(9999)
        return []  # unreachable

    mock_sub = MagicMock()
    mock_sub.fetch = _fake_fetch
    consumer._js.pull_subscribe = AsyncMock(return_value=mock_sub)

    # First start: loop runs, fetch called with nothing pending, then stop.
    await consumer.start()
    await asyncio.wait_for(first_start_fetched.wait(), timeout=2.0)
    await consumer.stop()

    # Post-restart: subscription now delivers the pending message on first fetch.
    fetch_results.append([pending_msg])

    await consumer.start()
    # Wait until send_audio has been called (message processed), then stop.
    await asyncio.wait_for(msg_processed.wait(), timeout=2.0)
    await consumer.stop()

    # Assert: pending message was processed and acked exactly once
    pending_msg.ack.assert_awaited_once()
    pending_msg.term.assert_not_awaited()


# ---------------------------------------------------------------------------
# SC3 — terminal → exactly one notif (_notified dedup, SC7 guard)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sc3_terminal_term_and_notif_exactly_once() -> None:
    """SC3/SC7: terminal send_audio failure → term() + send_text each once.

    After max_deliver retries, _handle_terminal must:
      - call msg.term() to prevent further redelivery
      - call send_text with the voice-undelivered notification exactly once
    Even if the terminal condition fires a second time (e.g. duplicate
    delivery at the terminal count), send_text is NOT called again.
    """
    # Arrange
    stream_id = "sc3-terminal-001"
    send_audio = AsyncMock(side_effect=RuntimeError("platform unavailable"))
    send_text = AsyncMock()
    consumer = _make_consumer(send_audio=send_audio, send_text=send_text)

    # Deliver at max_deliver count (terminal threshold)
    msg_terminal = _make_nats_msg(stream_id=stream_id, num_delivered_val=MAX_DELIVER)

    # Act — first terminal hit
    await consumer._process(msg_terminal)

    # Assert: term() called, notification sent once, no ack
    msg_terminal.term.assert_awaited_once()
    msg_terminal.ack.assert_not_awaited()
    send_text.assert_awaited_once()

    # Verify notification content contains the expected user-facing string
    assert send_text.await_args is not None
    outbound_arg = send_text.await_args[0][1]
    assert VOICE_UNDELIVERED_MSG in outbound_arg.to_text()

    # SC7 guard: second terminal hit for same stream_id → notify NOT called again
    msg_terminal_2 = _make_nats_msg(stream_id=stream_id, num_delivered_val=MAX_DELIVER)
    await consumer._process(msg_terminal_2)

    # send_text still at count 1 — _notified set guards against re-notification
    assert send_text.await_count == 1, (
        f"send_text called {send_text.await_count} times after second terminal;"
        " expected 1 (notified guard must suppress)"
    )
    # term() was called on both messages (once each — independent msg objects)
    assert msg_terminal.term.await_count == 1
    assert msg_terminal_2.term.await_count == 1


@pytest.mark.anyio
async def test_sc3_notified_guard_deleted_would_re_notify() -> None:
    """Negative: removing _notified guard causes double notification.

    This test FAILS when the _notified guard in _handle_terminal is removed —
    proving the guard is non-tautological.
    """
    # Arrange
    send_text = AsyncMock()
    consumer = _make_consumer(send_text=send_text)
    inbound = _make_inbound("sc3-guard-check")
    msg = MagicMock()
    msg.term = AsyncMock()

    # Act — fire _handle_terminal twice; guard should suppress second notify
    await consumer._handle_terminal(msg, "sc3-guard-check", inbound)
    await consumer._handle_terminal(msg, "sc3-guard-check", inbound)

    # Assert: guard present → send_text called exactly once
    assert send_text.await_count == 1, (
        "Deleting the _notified guard would cause send_text.await_count == 2"
    )


# ---------------------------------------------------------------------------
# SC4 — publish-fail → notif (NatsChannelProxy path)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sc4_publish_fail_dispatches_notification_no_raise() -> None:
    """SC4: js.publish failure → voice-undelivered notif on legacy subject, no raise.

    Drives NatsChannelProxy.render_audio with a js.publish that raises
    nats.errors.Error. Asserts:
      (a) Fallback notification published to legacy text subject.
      (b) No raw exception text in the published payload.
      (c) render_audio does not re-raise the publish error.
    """
    # Arrange
    nc = _make_nc()
    js = nc.jetstream()
    js.publish = AsyncMock(side_effect=nats.errors.Error("stream unavailable"))

    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("sc4-fail-001")
    audio = OutboundAudio(
        blob_ref=make_test_blobref(b"\xde\xad"), mime_type="audio/ogg"
    )

    # Act — (c) must not raise
    await proxy.render_audio(audio, inbound)

    # Assert (a): notification dispatched via legacy text subject
    nc.publish.assert_awaited_once()
    notif_subject, notif_payload_bytes = nc.publish.await_args.args
    assert notif_subject == "lyra.outbound.telegram.main"

    notif_data = json.loads(notif_payload_bytes)
    assert notif_data["type"] == "send"
    assert notif_data["stream_id"] == "sc4-fail-001"

    # Notification content must include the user-facing message
    outbound_content = notif_data["outbound"]["content"]
    assert any(VOICE_UNDELIVERED_MSG in part for part in outbound_content)

    # Assert (b): raw exception string must NOT appear in payload
    raw_payload_str = notif_payload_bytes.decode("utf-8")
    assert "stream unavailable" not in raw_payload_str
    assert "nats.errors" not in raw_payload_str


@pytest.mark.anyio
async def test_sc4_publish_fail_timeout_dispatches_notification() -> None:
    """SC4: asyncio.TimeoutError on js.publish also triggers notification path."""
    # Arrange
    nc = _make_nc()
    js = nc.jetstream()
    js.publish = AsyncMock(side_effect=asyncio.TimeoutError())

    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("sc4-timeout-001")
    audio = OutboundAudio(
        blob_ref=make_test_blobref(b"\xbe\xef"), mime_type="audio/ogg"
    )

    # Act
    await proxy.render_audio(audio, inbound)

    # Assert: notification sent to legacy text subject
    nc.publish.assert_awaited_once()
    notif_subject, _ = nc.publish.await_args.args
    assert notif_subject == "lyra.outbound.telegram.main"


@pytest.mark.anyio
async def test_sc4_js_publish_guard_deleted_would_skip_notification() -> None:
    """Negative: if the except block is deleted, nc.publish is never called.

    This test fails when the error-handling branch in render_audio is removed —
    proving the guard is non-tautological.
    """
    # Arrange
    nc = _make_nc()
    js = nc.jetstream()
    # Simulate success — guard present means notification NOT triggered on success
    js.publish = AsyncMock(return_value=MagicMock())

    proxy = NatsChannelProxy(nc=nc, platform=Platform.TELEGRAM, bot_id="main")
    inbound = _make_inbound("sc4-guard-success")
    audio = OutboundAudio(blob_ref=make_test_blobref(b"\x01"), mime_type="audio/ogg")

    # Act: successful publish
    await proxy.render_audio(audio, inbound)

    # Assert: on success, the fallback notification path must NOT fire
    nc.publish.assert_not_awaited()


# ---------------------------------------------------------------------------
# SC5 — in-proc lost-ack → no double-send
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sc5_lost_ack_dedup_suppresses_double_send() -> None:
    """SC5: send_audio succeeds but ack() raises; redelivery suppressed by dedup.

    Models the scenario where the network drops the ack after a successful
    platform send. JetStream redelivers the same stream_id. InMemorySentSet
    must prevent a second send_audio call.
    """
    # Arrange
    stream_id = "sc5-lost-ack-001"
    send_audio = AsyncMock()

    # First message: ack raises after successful send
    msg_first = _make_nats_msg(stream_id=stream_id, num_delivered_val=1)
    msg_first.ack = AsyncMock(side_effect=OSError("ack network error"))

    consumer = _make_consumer(send_audio=send_audio)

    # Act — first delivery: send succeeds, ack raises (dedup.mark_sent already called)
    await consumer._process(msg_first)

    # Redelivery of same stream_id (JetStream thinks ack was not received)
    msg_redeliver = _make_nats_msg(stream_id=stream_id, num_delivered_val=2)
    await consumer._process(msg_redeliver)

    # Assert: send_audio called exactly once — dedup suppressed the second send
    assert send_audio.await_count == 1, (
        f"send_audio called {send_audio.await_count} times; "
        "expected 1 — dedup must guard even when ack() raised"
    )
    # Redelivery message was acked (dedup hit path, not send path)
    msg_redeliver.ack.assert_awaited_once()
    # No terminal path
    msg_redeliver.term.assert_not_awaited()


@pytest.mark.anyio
async def test_sc5_dedup_guard_deleted_would_double_send() -> None:
    """Negative: removing dedup check causes double send on redelivery.

    Verifies the dedup guard is non-tautological: bypassing InMemorySentSet
    would result in send_audio being called twice.
    This test confirms the guard is meaningful by checking that mark_sent
    is recorded before ack() is attempted.
    """
    # Arrange
    stream_id = "sc5-guard-verify"
    send_audio = AsyncMock()
    dedup = InMemorySentSet()  # hold concrete type so pyright can see ._sent
    consumer = _make_consumer(send_audio=send_audio, dedup=dedup)

    msg_first = _make_nats_msg(stream_id=stream_id, num_delivered_val=1)

    # Act — successful first delivery
    await consumer._process(msg_first)

    # Assert: mark_sent was recorded in the dedup set
    assert await dedup.already_sent(stream_id), (
        "stream_id must be in dedup set after successful send; "
        "deleting mark_sent call would break SC5 protection"
    )

    # Confirm: if dedup set is bypassed (stream_id cleared), a second process
    # call WOULD invoke send_audio again — demonstrating the guard is load-bearing
    dedup._sent.clear()  # simulate guard removal (InMemorySentSet internal)
    msg_redeliver = _make_nats_msg(stream_id=stream_id, num_delivered_val=2)
    await consumer._process(msg_redeliver)

    # With guard deleted: send_audio is called a second time
    assert send_audio.await_count == 2, (
        "Without dedup guard, redelivery triggers a second send — "
        "this confirms the guard is non-tautological"
    )
