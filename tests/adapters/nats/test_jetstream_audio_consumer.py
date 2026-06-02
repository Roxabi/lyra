"""Tests for JetStreamAudioConsumer and sibling modules (#1482, T5).

Covers the four required scenarios:
  (a) Successful send → ack called, stream_id in sent-set.
  (b) Redelivery of already-sent stream_id → ack, send NOT called twice.
  (c) Terminal (num_delivered >= MAX_DELIVER) failing send → term() called
      + notify_undelivered fired exactly once.
  (d) start/stop cancels the loop cleanly.

Also covers InMemorySentSet directly (T10 swap-target interface) and
decode_audio_envelope / num_delivered (envelope module).

Strategy: no real NATS server — all NATS interactions are mocked via
MagicMock/AsyncMock, mirroring the turn_writer test pattern.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from unittest.mock import AsyncMock, MagicMock

import nats.errors
import pytest

from factory.adapters.nats.jetstream_audio_consumer import (
    MAX_DELIVER,
    JetStreamAudioConsumer,
)
from factory.adapters.nats.jetstream_audio_dedup import (
    DEDUP_MAX_ENTRIES,
    DEDUP_TTL,
    InMemorySentSet,
)
from factory.adapters.nats.jetstream_audio_envelope import (
    decode_audio_envelope,
    num_delivered,
)
from factory.core.auth.trust import TrustLevel
from factory.core.messaging.message import (
    InboundMessage,
    OutboundAudio,
    OutboundMessage,
    Platform,
)
from factory.core.messaging.voice_notify import VOICE_UNDELIVERED_MSG
from roxabi_contracts.blob_ref import BlobRef

# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

_DURABLE = "outbound-audio-telegram"
_FILTER = "lyra.outbound.audio.telegram.>"
_STREAM_ID = "msg-test-001"


def _make_consumer(
    *,
    send_audio: AsyncMock | None = None,
    send_text: AsyncMock | None = None,
    dedup: InMemorySentSet | None = None,
) -> JetStreamAudioConsumer:
    js = MagicMock()
    return JetStreamAudioConsumer(
        js,
        durable=_DURABLE,
        filter_subject=_FILTER,
        send_audio=send_audio or AsyncMock(),
        send_text=send_text or AsyncMock(),
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


def _make_inbound(stream_id: str = _STREAM_ID) -> InboundMessage:
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


def _make_nats_msg(  # noqa: PLR0913
    *,
    stream_id: str = _STREAM_ID,
    audio: OutboundAudio | None = None,
    inbound: InboundMessage | None = None,
    msg_type: str = "audio",
    num_delivered_val: int = 1,
    raw_data: bytes | None = None,
) -> MagicMock:
    """Build a fake NATS JetStream message with metadata and a serialized envelope."""
    from roxabi_nats._serialize import serialize

    resolved_inbound = inbound or _make_inbound(stream_id)
    if raw_data is not None:
        data = raw_data
    else:
        audio = audio or _make_audio()
        envelope = {
            "type": msg_type,
            "stream_id": stream_id,
            "audio": json.loads(serialize(audio).decode("utf-8")),
            "original_msg": json.loads(serialize(resolved_inbound).decode("utf-8")),
        }
        data = json.dumps(envelope, ensure_ascii=False).encode("utf-8")

    msg = MagicMock()
    msg.data = data
    msg.subject = f"lyra.outbound.audio.telegram.{resolved_inbound.bot_id}"
    msg.ack = AsyncMock()
    msg.nak = AsyncMock()
    msg.term = AsyncMock()

    meta = MagicMock()
    meta.num_delivered = num_delivered_val
    msg.metadata = meta
    return msg


# ===========================================================================
# InMemorySentSet — unit tests for the T10 swap-target interface
# ===========================================================================


@pytest.mark.anyio
async def test_in_memory_sent_set_round_trip() -> None:
    """mark_sent + already_sent basic round-trip."""
    s = InMemorySentSet()
    assert not await s.already_sent("sid-a")
    await s.mark_sent("sid-a")
    assert await s.already_sent("sid-a")


@pytest.mark.anyio
async def test_in_memory_sent_set_ttl_expiry() -> None:
    """Expired entry (past TTL) treated as not-sent."""
    s = InMemorySentSet(ttl=10.0)
    await s.mark_sent("sid-old")
    s._sent["sid-old"] = time.monotonic() - 20.0  # backdate past TTL
    assert not await s.already_sent("sid-old")


@pytest.mark.anyio
async def test_in_memory_sent_set_fifo_eviction() -> None:
    """Oldest entry is evicted when cap is exceeded."""
    s = InMemorySentSet(max_entries=3)
    for i in range(3):
        await s.mark_sent(f"sid-{i}")
    await s.mark_sent("sid-overflow")
    assert "sid-0" not in s._sent
    assert "sid-overflow" in s._sent
    assert len(s._sent) == 3


def test_in_memory_sent_set_default_constants() -> None:
    """Default TTL and cap match the documented arithmetic."""
    assert DEDUP_TTL == 900.0
    assert DEDUP_MAX_ENTRIES == 1_000


@pytest.mark.anyio
async def test_in_memory_sent_set_different_ids_independent() -> None:
    """Two distinct stream_ids are tracked independently."""
    s = InMemorySentSet()
    await s.mark_sent("a")
    assert await s.already_sent("a")
    assert not await s.already_sent("b")


# ===========================================================================
# decode_audio_envelope — unit tests for the envelope module
# ===========================================================================


def test_decode_audio_envelope_happy_path() -> None:
    """Valid audio envelope returns (stream_id, audio, inbound)."""
    msg = _make_nats_msg(stream_id="decode-ok")
    sid, audio, inbound = decode_audio_envelope(msg)
    assert sid == "decode-ok"
    assert isinstance(audio, OutboundAudio)
    assert isinstance(inbound, InboundMessage)


def test_decode_audio_envelope_bad_json_returns_none() -> None:
    """Non-JSON payload returns (None, None, None)."""
    msg = _make_nats_msg(raw_data=b"not-json{{")
    result = decode_audio_envelope(msg)
    assert result == (None, None, None)


def test_decode_audio_envelope_wrong_type_returns_none() -> None:
    """Envelope with type != 'audio' returns (None, None, None)."""
    msg = _make_nats_msg(msg_type="send")
    result = decode_audio_envelope(msg)
    assert result == (None, None, None)


def test_decode_audio_envelope_missing_stream_id_returns_none() -> None:
    """Envelope without stream_id returns (None, None, None)."""
    msg = _make_nats_msg(raw_data=json.dumps({"type": "audio"}).encode())
    result = decode_audio_envelope(msg)
    assert result == (None, None, None)


def test_num_delivered_reads_metadata() -> None:
    """num_delivered reads msg.metadata.num_delivered."""
    msg = _make_nats_msg(num_delivered_val=3)
    assert num_delivered(msg) == 3


def test_num_delivered_fallback_when_metadata_raises() -> None:
    """num_delivered returns 1 when metadata property raises."""
    msg = MagicMock()

    def _raise(self: object) -> None:
        raise Exception("no reply")  # noqa: TRY002

    type(msg).metadata = property(_raise)
    assert num_delivered(msg) == 1


# ===========================================================================
# JetStreamAudioConsumer — integration-level tests via _process / _handle_terminal
# ===========================================================================

# ---------------------------------------------------------------------------
# (a) Successful send → ack called, stream_id in sent-set
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_successful_send_acks_and_marks_sent() -> None:
    """Happy path: send_audio succeeds → ack() called, stream_id in dedup."""
    send_audio = AsyncMock()
    consumer = _make_consumer(send_audio=send_audio)
    msg = _make_nats_msg(stream_id=_STREAM_ID)

    await consumer._process(msg)

    send_audio.assert_awaited_once()
    msg.ack.assert_awaited_once()
    msg.nak.assert_not_awaited()
    msg.term.assert_not_awaited()
    assert await consumer._dedup.already_sent(_STREAM_ID)


@pytest.mark.anyio
async def test_successful_send_does_not_call_term_or_notify() -> None:
    """No term() and no user notification on a successful send."""
    send_text = AsyncMock()
    consumer = _make_consumer(send_text=send_text)
    msg = _make_nats_msg(stream_id="stream-success")

    await consumer._process(msg)

    msg.term.assert_not_awaited()
    send_text.assert_not_awaited()


# ---------------------------------------------------------------------------
# (b) Dedup: redelivery of already-sent stream_id → ack, no second send
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_dedup_skips_second_delivery() -> None:
    """Duplicate stream_id → ack on second delivery, send NOT called twice."""
    send_audio = AsyncMock()
    consumer = _make_consumer(send_audio=send_audio)

    msg1 = _make_nats_msg(stream_id=_STREAM_ID)
    msg2 = _make_nats_msg(stream_id=_STREAM_ID)

    await consumer._process(msg1)
    await consumer._process(msg2)

    assert send_audio.await_count == 1
    msg1.ack.assert_awaited_once()
    msg2.ack.assert_awaited_once()


@pytest.mark.anyio
async def test_dedup_does_not_fire_for_different_stream_ids() -> None:
    """Two distinct stream_ids each trigger one send."""
    send_audio = AsyncMock()
    consumer = _make_consumer(send_audio=send_audio)

    await consumer._process(_make_nats_msg(stream_id="stream-a"))
    await consumer._process(_make_nats_msg(stream_id="stream-b"))

    assert send_audio.await_count == 2


# ---------------------------------------------------------------------------
# (c) Terminal: num_delivered >= MAX_DELIVER + failing send → term + notify once
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_terminal_failure_calls_term_and_notifies() -> None:
    """Terminal delivery: term() called + user notified with canonical message."""
    send_audio = AsyncMock(side_effect=RuntimeError("platform unavailable"))
    send_text = AsyncMock()
    consumer = _make_consumer(send_audio=send_audio, send_text=send_text)

    msg = _make_nats_msg(stream_id="stream-term", num_delivered_val=MAX_DELIVER)
    await consumer._process(msg)

    msg.term.assert_awaited_once()
    msg.ack.assert_not_awaited()
    send_text.assert_awaited_once()
    assert send_text.await_args is not None
    outbound_arg: OutboundMessage = send_text.await_args[0][1]
    assert VOICE_UNDELIVERED_MSG in outbound_arg.to_text()


@pytest.mark.anyio
async def test_terminal_handle_calls_term_then_notifies() -> None:
    """_handle_terminal: term() succeeds → notification sent exactly once.

    New contract (post-bc00aa46): notification is sent ONLY after term()
    succeeds.  Asserts call order and call counts on the real mocks.
    """
    # Arrange
    send_text = AsyncMock()
    consumer = _make_consumer(send_text=send_text)
    inbound = _make_inbound()
    msg = MagicMock()
    msg.term = AsyncMock()  # term() succeeds

    # Act
    await consumer._handle_terminal(msg, "stream-term-order", inbound)

    # Assert: term() was called, then notification sent once
    msg.term.assert_awaited_once()
    send_text.assert_awaited_once()
    # Notification content must carry the expected user-facing message
    assert send_text.await_args is not None
    outbound_arg = send_text.await_args[0][1]
    assert VOICE_UNDELIVERED_MSG in outbound_arg.to_text()


@pytest.mark.anyio
async def test_terminal_notification_suppressed_when_term_raises() -> None:
    """New contract: if term() raises, NO notification is sent.

    JetStream will redeliver and we retry on the next delivery rather than
    notifying prematurely.  This test FAILS if the `termed` guard is removed.
    """
    # Arrange
    send_text = AsyncMock()
    consumer = _make_consumer(send_text=send_text)
    inbound = _make_inbound()
    msg = MagicMock()
    msg.term = AsyncMock(side_effect=RuntimeError("term ack transport error"))

    # Act — term() raises, so the consumer must bail out without notifying
    await consumer._handle_terminal(msg, "stream-term-fail", inbound)

    # Assert: term() was attempted but notification must NOT fire
    msg.term.assert_awaited_once()
    send_text.assert_not_awaited()


@pytest.mark.anyio
async def test_terminal_notification_fires_exactly_once() -> None:
    """_notified guard: second _handle_terminal call (term succeeds) does NOT re-notify.

    Verifies the _notified set is load-bearing: deleting it would cause
    send_text to be called twice when _handle_terminal fires for the same
    stream_id on two consecutive terminal deliveries.
    """
    # Arrange
    send_text = AsyncMock()
    consumer = _make_consumer(send_text=send_text)
    inbound = _make_inbound()
    msg1 = MagicMock()
    msg1.term = AsyncMock()
    msg2 = MagicMock()
    msg2.term = AsyncMock()

    # Act — two independent messages with the same stream_id hit terminal
    await consumer._handle_terminal(msg1, "stream-once", inbound)
    await consumer._handle_terminal(msg2, "stream-once", inbound)

    # Assert: term() called on each message, but notify fired only once
    msg1.term.assert_awaited_once()
    msg2.term.assert_awaited_once()
    assert send_text.await_count == 1, (
        f"send_text called {send_text.await_count} times; "
        "expected 1 — _notified guard must suppress the second notification"
    )


@pytest.mark.anyio
async def test_transient_failure_does_not_term() -> None:
    """num_delivered < MAX_DELIVER → no term(), no notify, no ack."""
    send_audio = AsyncMock(side_effect=OSError("timeout"))
    send_text = AsyncMock()
    consumer = _make_consumer(send_audio=send_audio, send_text=send_text)

    msg = _make_nats_msg(stream_id="stream-transient", num_delivered_val=2)
    await consumer._process(msg)

    msg.term.assert_not_awaited()
    msg.ack.assert_not_awaited()
    send_text.assert_not_awaited()


@pytest.mark.anyio
async def test_transient_failure_does_not_ack() -> None:
    """Transient failure: no ack so JetStream redelivers after AckWait."""
    send_audio = AsyncMock(side_effect=OSError("temporary"))
    consumer = _make_consumer(send_audio=send_audio)
    msg = _make_nats_msg(num_delivered_val=1)

    await consumer._process(msg)

    msg.ack.assert_not_awaited()


# ---------------------------------------------------------------------------
# (d) start / stop
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stop_cancels_loop() -> None:
    """start() spawns a task; stop() cancels it cleanly."""
    consumer = _make_consumer()

    async def _forever(*args: object, **kwargs: object) -> None:
        await asyncio.sleep(9999)

    mock_sub = MagicMock()
    mock_sub.fetch = _forever
    consumer._js.pull_subscribe = AsyncMock(return_value=mock_sub)

    await consumer.start()
    assert consumer._task is not None
    assert not consumer._task.done()

    await consumer.stop()
    assert consumer._task is None


@pytest.mark.anyio
async def test_stop_before_start_is_noop() -> None:
    """stop() before start() does not raise."""
    consumer = _make_consumer()
    await consumer.stop()


# ---------------------------------------------------------------------------
# Malformed envelope handling (via consumer._process)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_malformed_json_acks_and_skips() -> None:
    """Non-JSON data → ack to unblock consumer, send NOT called."""
    send_audio = AsyncMock()
    consumer = _make_consumer(send_audio=send_audio)
    msg = _make_nats_msg(raw_data=b"not-json{{{")

    await consumer._process(msg)

    send_audio.assert_not_awaited()
    msg.ack.assert_awaited_once()


@pytest.mark.anyio
async def test_wrong_type_acks_and_skips() -> None:
    """Envelope type != 'audio' → ack + skip."""
    send_audio = AsyncMock()
    consumer = _make_consumer(send_audio=send_audio)
    msg = _make_nats_msg(msg_type="send")

    await consumer._process(msg)

    send_audio.assert_not_awaited()
    msg.ack.assert_awaited_once()


@pytest.mark.anyio
async def test_missing_stream_id_acks_and_skips() -> None:
    """Envelope missing stream_id → ack + skip."""
    send_audio = AsyncMock()
    consumer = _make_consumer(send_audio=send_audio)
    msg = _make_nats_msg(raw_data=json.dumps({"type": "audio"}).encode())

    await consumer._process(msg)

    send_audio.assert_not_awaited()
    msg.ack.assert_awaited_once()


# ---------------------------------------------------------------------------
# I4a — ConnectionClosedError in consume loop
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_connection_closed_error_logs_and_exits(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ConnectionClosedError in _consume_loop logs ERROR and re-raises.

    The done-callback on the background task catches the unexpected exit and
    logs ERROR.  Verifies the loop exits cleanly (task done) without crashing
    the whole process / swallowing the error silently.

    This test FAILS if the ConnectionClosedError branch is removed from the loop,
    because the task would then hang instead of exiting.
    """
    # Arrange
    consumer = _make_consumer()

    async def _fetch_raises(*args: object, **kwargs: object) -> list:
        raise nats.errors.ConnectionClosedError

    mock_sub = MagicMock()
    mock_sub.fetch = _fetch_raises
    consumer._js.pull_subscribe = AsyncMock(return_value=mock_sub)

    # Act — start, then wait for the task to exit (it should exit quickly)
    await consumer.start()
    assert consumer._task is not None
    task = consumer._task

    with caplog.at_level(logging.ERROR):
        # Give the loop time to run and exit due to ConnectionClosedError.
        # We wait on the task directly (it re-raises, so it will finish).
        with pytest.raises((nats.errors.ConnectionClosedError, Exception)):
            await asyncio.wait_for(asyncio.shield(task), timeout=2.0)

    # Task must be done (loop exited, not hung)
    assert task.done()

    # done-callback must have logged an ERROR about unexpected loop exit
    error_messages = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("consume loop exited unexpectedly" in m for m in error_messages), (
        f"Expected 'consume loop exited unexpectedly' in log; got: {error_messages}"
    )

    # Cleanup: stop the consumer to clear _task / _sub references
    consumer._task = None


# ---------------------------------------------------------------------------
# I4b — per-bot filter_subject: exact 5-token subject, no trailing .>
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_start_subscribes_with_exact_5_token_filter_subject() -> None:
    """start() passes the EXACT 5-token filter_subject to pull_subscribe.

    Contract: per-bot consumers use "lyra.outbound.audio.{platform}.{bot_id}"
    with NO trailing '.>'.  A subject ending in '.>' would silently never match
    the 5-token publish target and break audio delivery.

    This test FAILS if filter_subject is changed to end in '.>'.
    """
    # Arrange — use the exact per-bot subject format from bootstrap
    exact_subject = "lyra.outbound.audio.telegram.bot123"
    consumer = _make_consumer()
    consumer._filter_subject = exact_subject

    async def _forever(*args: object, **kwargs: object) -> None:
        await asyncio.sleep(9999)

    mock_sub = MagicMock()
    mock_sub.fetch = _forever
    consumer._js.pull_subscribe = AsyncMock(return_value=mock_sub)

    # Act
    await consumer.start()

    # Assert: pull_subscribe called with the exact 5-token subject
    consumer._js.pull_subscribe.assert_awaited_once()
    called_subject = consumer._js.pull_subscribe.call_args[0][0]
    assert called_subject == exact_subject, (
        f"pull_subscribe called with {called_subject!r}; "
        f"expected exact 5-token subject {exact_subject!r}"
    )
    # Negative guard: must NOT end with '.>' (would never match 5-token publishes)
    assert not called_subject.endswith(".>"), (
        f"filter_subject {called_subject!r} ends with '.>' — "
        "would silently never match 5-token publish subjects"
    )

    await consumer.stop()
