"""Tests for NatsOutboundListener — NATS-to-adapter dispatch."""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.message import InboundMessage, Platform
from lyra.core.trust import TrustLevel
from lyra.nats._serialize import serialize


def _make_tg_msg(msg_id: str = "msg-1") -> InboundMessage:
    return InboundMessage(
        id=msg_id,
        platform="telegram",
        bot_id="main",
        scope_id="chat:42",
        user_id="tg:user:1",
        user_name="Alice",
        is_mention=False,
        text="hi",
        text_raw="hi",
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 42,
            "message_id": 10,
            "topic_id": None,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
    )


def _make_nats_msg(data: dict) -> MagicMock:
    msg = MagicMock()
    msg.data = json.dumps(data).encode("utf-8")
    return msg


@pytest.mark.asyncio
async def test_send_envelope_dispatches_to_adapter_send() -> None:
    """cache_inbound + send envelope -> adapter.send() called once."""
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener

    nc = AsyncMock()
    adapter = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    msg = _make_tg_msg()
    listener.cache_inbound(msg)

    # Serialize outbound using the same approach as NatsChannelProxy
    envelope = {
        "type": "send",
        "stream_id": msg.id,
        "outbound": {
            "content": ["hello"],
            "buttons": [],
            "metadata": {},
        },
    }
    await listener._handle(_make_nats_msg(envelope))

    adapter.send.assert_called_once()
    call_original_msg, _call_outbound = adapter.send.call_args[0]
    assert call_original_msg is msg


@pytest.mark.asyncio
async def test_send_unknown_stream_id_logs_warning_no_crash() -> None:
    """Unknown stream_id in send envelope -> warning logged, no crash."""
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener

    nc = AsyncMock()
    adapter = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    envelope = {
        "type": "send",
        "stream_id": "nonexistent-id",
        "outbound": {"content": ["hi"], "buttons": [], "metadata": {}},
    }
    await listener._handle(_make_nats_msg(envelope))

    adapter.send.assert_not_called()


@pytest.mark.asyncio
async def test_attachment_envelope_dispatches_to_render_attachment() -> None:
    """Attachment envelope -> adapter.render_attachment() called once."""
    import base64

    from lyra.adapters.nats_outbound_listener import NatsOutboundListener

    nc = AsyncMock()
    adapter = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    msg = _make_tg_msg("msg-attach")
    listener.cache_inbound(msg)

    # OutboundAttachment.data is bytes, serialized as b64:<base64> in JSON wire format
    b64_data = "b64:" + base64.b64encode(b"PNG").decode("ascii")
    envelope = {
        "type": "attachment",
        "stream_id": msg.id,
        "attachment": {
            "data": b64_data,
            "type": "image",
            "mime_type": "image/png",
        },
    }
    await listener._handle(_make_nats_msg(envelope))

    adapter.render_attachment.assert_called_once()


@pytest.mark.asyncio
async def test_send_evicts_cache_entry() -> None:
    """send envelope -> cache entry removed after dispatch (eviction is unconditional)."""  # noqa: E501
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener

    nc = AsyncMock()
    adapter = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    msg = _make_tg_msg("msg-evict")
    listener.cache_inbound(msg)

    assert msg.id in listener._cache

    envelope = {
        "type": "send",
        "stream_id": msg.id,
        "outbound": {"content": ["hi"], "buttons": [], "metadata": {}},
    }
    await listener._handle(_make_nats_msg(envelope))

    assert msg.id not in listener._cache


@pytest.mark.asyncio
async def test_chunk_envelope_triggers_send_streaming() -> None:
    """Chunk envelopes reassemble into a stream and call adapter.send_streaming()."""
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener

    nc = AsyncMock()
    adapter = AsyncMock()
    adapter.send_streaming = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    msg = _make_tg_msg("msg-stream")
    listener.cache_inbound(msg)

    # Send a single done chunk
    chunk = {
        "stream_id": msg.id,
        "seq": 0,
        "event_type": "text",
        "payload": {"text": "hello", "is_final": True},
        "done": True,
    }
    await listener._handle(_make_nats_msg(chunk))

    # Await the drain task directly — avoids flaky sleep-based synchronization
    task = listener._stream_tasks.get(msg.id)
    if task:
        await task

    adapter.send_streaming.assert_called_once()


@pytest.mark.asyncio
async def test_start_subscribes_and_stop_unsubscribes() -> None:
    """start() subscribes to NATS subject; stop() unsubscribes."""
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener

    mock_sub = AsyncMock()
    nc = AsyncMock()
    nc.subscribe = AsyncMock(return_value=mock_sub)

    adapter = MagicMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    await listener.start()
    nc.subscribe.assert_called_once()
    subject = nc.subscribe.call_args[0][0]
    assert subject == "lyra.outbound.telegram.main"

    await listener.stop()
    mock_sub.unsubscribe.assert_called_once()


@pytest.mark.asyncio
async def test_stop_cancels_reaper_task() -> None:
    """stop() cancels the reaper task and sets _reaper_task to None."""
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener

    mock_sub = AsyncMock()
    nc = AsyncMock()
    nc.subscribe = AsyncMock(return_value=mock_sub)

    adapter = MagicMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    # Arrange
    await listener.start()
    assert listener._reaper_task is not None

    # Act
    await listener.stop()

    # Assert
    assert listener._reaper_task is None


def test_cache_inbound_drops_when_full(caplog) -> None:
    """cache_inbound evicts oldest and warns when _cache is at max size."""
    import logging

    from lyra.adapters._inbound_cache import MAX_SIZE
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener

    nc = AsyncMock()
    adapter = AsyncMock()
    listener = NatsOutboundListener(
        nc,
        Platform.TELEGRAM,
        "main",
        adapter,
    )

    # Fill cache to the limit using distinct fake entries
    for i in range(MAX_SIZE):
        fake = _make_tg_msg(f"fill-{i}")
        listener._cache._msgs[fake.id] = fake
        listener._cache._ts[fake.id] = 0.0

    overflow_msg = _make_tg_msg("overflow-msg")
    _logger = "lyra.adapters._inbound_cache"
    with caplog.at_level(logging.WARNING, logger=_logger):
        listener.cache_inbound(overflow_msg)

    assert overflow_msg.id in listener._cache
    assert "fill-0" not in listener._cache
    assert len(listener._cache._msgs) == MAX_SIZE
    assert len(listener._cache._ts) == MAX_SIZE
    assert any("full" in r.message for r in caplog.records)
    assert any("evicted" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_stream_drops_when_at_max_streams(caplog) -> None:
    """_handle_chunk drops new stream when _stream_tasks full."""
    import asyncio
    import logging

    from lyra.adapters.nats_outbound_listener import (
        _MAX_STREAMS,
        NatsOutboundListener,
    )

    nc = AsyncMock()
    adapter = AsyncMock()
    listener = NatsOutboundListener(
        nc,
        Platform.TELEGRAM,
        "main",
        adapter,
    )

    # Fill stream_tasks to the limit with mock tasks
    for i in range(_MAX_STREAMS):
        mock_task = MagicMock(spec=asyncio.Task)
        listener._stream_tasks[f"stream-{i}"] = mock_task

    new_stream_id = "overflow-stream"
    chunk = {
        "stream_id": new_stream_id,
        "seq": 0,
        "event_type": "text",
        "payload": {"text": "hello", "is_final": True},
        "done": True,
    }
    _logger = "lyra.adapters.nats_outbound_listener"
    with caplog.at_level(logging.WARNING, logger=_logger):
        await listener._handle(_make_nats_msg(chunk))

    assert new_stream_id not in listener._stream_tasks
    assert new_stream_id not in listener._stream_queues
    assert any("_stream_tasks full" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_existing_stream_receives_chunks_at_capacity(caplog) -> None:
    """Chunk for an existing stream_id is enqueued even when _stream_tasks is full."""
    import asyncio
    import logging

    from lyra.adapters.nats_outbound_listener import (
        _MAX_STREAMS,
        NatsOutboundListener,
    )

    nc = AsyncMock()
    adapter = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    existing_id = "existing-stream"
    existing_msg = _make_tg_msg(existing_id)
    listener.cache_inbound(existing_msg)

    # Create a real queue for the existing stream
    q: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
    listener._stream_queues[existing_id] = q
    existing_task = MagicMock(spec=asyncio.Task)
    listener._stream_tasks[existing_id] = existing_task

    # Fill _stream_tasks to _MAX_STREAMS (existing_id already occupies one slot)
    for i in range(_MAX_STREAMS - 1):
        listener._stream_tasks[f"other-{i}"] = MagicMock(spec=asyncio.Task)

    assert len(listener._stream_tasks) == _MAX_STREAMS

    chunk = {
        "stream_id": existing_id,
        "seq": 0,
        "event_type": "text",
        "payload": {"text": "hello", "is_final": True},
        "done": True,
    }
    _logger = "lyra.adapters.nats_outbound_listener"
    with caplog.at_level(logging.WARNING, logger=_logger):
        await listener._handle(_make_nats_msg(chunk))

    # Chunk must have been enqueued for the existing stream
    assert listener._stream_queues[existing_id].qsize() >= 1
    # No warning about capacity — existing stream is allowed
    assert not any("_stream_tasks full" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_reaper_evicts_stale_entries(caplog) -> None:
    """run_reaper evicts entries exceeding TTL_SECONDS."""
    import asyncio
    import logging
    import time
    import unittest.mock as mock

    from lyra.adapters._inbound_cache import TTL_SECONDS, run_reaper
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener

    nc = AsyncMock()
    adapter = AsyncMock()
    listener = NatsOutboundListener(
        nc,
        Platform.TELEGRAM,
        "main",
        adapter,
    )

    stale_id = "stale-stream"
    fresh_id = "fresh-stream"

    stale_msg = _make_tg_msg(stale_id)
    fresh_msg = _make_tg_msg(fresh_id)

    listener._cache._msgs[stale_id] = stale_msg
    listener._cache._ts[stale_id] = time.monotonic() - (TTL_SECONDS + 1)

    listener._cache._msgs[fresh_id] = fresh_msg
    listener._cache._ts[fresh_id] = time.monotonic()

    # Wire up _stream_outbound and a mock task for the stale entry
    from lyra.core.message import OutboundMessage

    listener._stream_outbound[stale_id] = OutboundMessage(
        content=["x"], buttons=[], metadata={}
    )
    mock_task = MagicMock(spec=asyncio.Task)
    listener._stream_tasks[stale_id] = mock_task

    # run_reaper calls cache._reap() which evicts stale entries,
    # but doesn't know about stream_outbound/tasks (listener's concern).
    # We test the cache layer directly here.
    call_count = 0

    async def _sleep_once(_interval):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise asyncio.CancelledError

    _target = "lyra.adapters._inbound_cache.asyncio.sleep"
    _logger = "lyra.adapters._inbound_cache"
    with mock.patch(_target, side_effect=_sleep_once):
        with caplog.at_level(logging.WARNING, logger=_logger):
            try:
                await run_reaper(listener._cache)
            except asyncio.CancelledError:
                pass

    assert stale_id not in listener._cache
    assert fresh_id in listener._cache
    assert any("evicting stale" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_start_subscribes_with_queue_group() -> None:
    """NatsOutboundListener.start() passes queue_group to nc.subscribe()."""
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener

    # Arrange
    nc = AsyncMock()
    adapter = AsyncMock()
    listener = NatsOutboundListener(
        nc,
        Platform.TELEGRAM,
        "main",
        adapter,
        queue_group="adapter-outbound-telegram-main",
    )

    # Act
    await listener.start()

    # Assert
    nc.subscribe.assert_called_once()
    _, kwargs = nc.subscribe.call_args
    assert kwargs.get("queue") == "adapter-outbound-telegram-main"


@pytest.mark.asyncio
async def test_default_queue_group_is_empty() -> None:
    """Default queue_group is empty string."""
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener

    # Arrange / Act
    nc = AsyncMock()
    adapter = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    # Assert
    assert listener._queue_group == ""


@pytest.mark.asyncio
async def test_stream_error_enqueues_poison_pill() -> None:
    """Verifies _handle routes type=stream_error to _handle_stream_error.

    _handle_stream_error enqueues a poison-pill chunk into the active stream
    queue so _drain_stream terminates immediately.
    """
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener

    nc = AsyncMock()
    adapter = AsyncMock()
    adapter.send_streaming = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    msg = _make_tg_msg("msg-stream-err")
    listener.cache_inbound(msg)

    # stream_start so a queue exists and outbound metadata is cached
    stream_start = {
        "type": "stream_start",
        "stream_id": msg.id,
        "outbound": {"content": [], "buttons": [], "metadata": {}},
    }
    await listener._handle(_make_nats_msg(stream_start))

    # One regular chunk — this starts a drain task
    chunk = {
        "stream_id": msg.id,
        "seq": 0,
        "event_type": "text",
        "payload": {"text": "partial", "is_final": False},
        "done": False,
    }
    await listener._handle(_make_nats_msg(chunk))

    # stream_error — hub crashed mid-stream.
    error_envelope = {
        "type": "stream_error",
        "stream_id": msg.id,
    }
    await listener._handle(_make_nats_msg(error_envelope))

    # _handle_stream_error must enqueue a poison pill so the queue has 2 items:
    # the regular chunk + the stream_error sentinel.
    q = listener._stream_queues.get(msg.id)
    assert q is not None, "_stream_queues must contain an entry for the stream_id"
    assert q.qsize() == 2, (
        "Expected 2 items in queue (1 chunk + 1 stream_error poison pill); "
        f"got {q.qsize() if q else 'no queue'}"
    )


@pytest.mark.asyncio
async def test_stream_error_missing_stream_id_is_noop() -> None:
    """stream_error with no stream_id is a no-op — no crash, no state change."""
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener

    nc = AsyncMock()
    adapter = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    msg = _make_tg_msg("msg-noop")
    listener.cache_inbound(msg)

    # stream_error without stream_id — should silently no-op
    error_envelope = {"type": "stream_error"}
    await listener._handle(_make_nats_msg(error_envelope))

    # State is unchanged
    assert msg.id in listener._cache
    assert len(listener._stream_queues) == 0


@pytest.mark.asyncio
async def test_stream_error_no_queue_cleans_cache() -> None:
    """stream_error with no active queue removes the cache entry."""
    import time

    from lyra.adapters.nats_outbound_listener import NatsOutboundListener
    from lyra.core.message import OutboundMessage

    nc = AsyncMock()
    adapter = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    msg = _make_tg_msg("msg-err-no-queue")
    listener.cache_inbound(msg)

    # Seed _cache_ts and _stream_outbound to verify both are cleaned up
    listener._cache._ts[msg.id] = time.monotonic()
    listener._stream_outbound[msg.id] = OutboundMessage(
        content=["x"], buttons=[], metadata={}
    )

    assert msg.id in listener._cache

    # stream_error arrives with no prior chunks / no queue
    error_envelope = {
        "type": "stream_error",
        "stream_id": msg.id,
    }
    await listener._handle(_make_nats_msg(error_envelope))

    # Cache entry and all related state must be cleaned up
    assert msg.id not in listener._cache
    assert msg.id not in listener._cache._ts
    assert msg.id not in listener._stream_outbound


@pytest.mark.asyncio
async def test_stream_error_unknown_stream_id_is_noop() -> None:
    """stream_error with a stream_id the listener has no state for is a no-op.

    Defense-in-depth: a forged stream_error with a random stream_id must NOT
    pollute the tombstone set or evict unrelated cache entries. Enforcement of
    publisher identity belongs at the NATS auth layer; this test guards the
    code-level blast-radius reduction.
    """
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener

    nc = AsyncMock()
    adapter = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    # A legitimate cached message that must not be disturbed.
    cached = _make_tg_msg("legit-msg-id")
    listener.cache_inbound(cached)

    # Forged stream_error for a stream_id the listener has never seen.
    error_envelope = {
        "type": "stream_error",
        "stream_id": "forged-stream-id-42",
        "reason": "attacker",
    }
    await listener._handle(_make_nats_msg(error_envelope))

    # Nothing in listener state should reference the forged id.
    assert "forged-stream-id-42" not in listener._terminated_streams
    assert "forged-stream-id-42" not in listener._cache
    assert "forged-stream-id-42" not in listener._stream_outbound
    assert "forged-stream-id-42" not in listener._stream_queues
    assert "forged-stream-id-42" not in listener._stream_tasks

    # The legitimate cache entry is untouched.
    assert cached.id in listener._cache


# ---------------------------------------------------------------------------
# #566: check_schema_version on OutboundMessage receive paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_version_mismatch_drops_and_increments_counter() -> None:
    """send envelope with schema_version > expected is dropped; counter incremented."""
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener
    from lyra.core.message import SCHEMA_VERSION_OUTBOUND_MESSAGE

    nc = AsyncMock()
    adapter = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    msg = _make_tg_msg("msg-send-version")
    listener.cache_inbound(msg)

    envelope = {
        "type": "send",
        "stream_id": msg.id,
        "outbound": {
            "schema_version": SCHEMA_VERSION_OUTBOUND_MESSAGE + 1,
            "content": ["hello"],
            "buttons": [],
            "metadata": {},
        },
    }
    await listener._handle(_make_nats_msg(envelope))

    adapter.send.assert_not_called()
    assert listener._version_mismatch_drops == {"OutboundMessage:schema": 1}


@pytest.mark.asyncio
async def test_stream_start_version_mismatch_drops_and_increments_counter() -> None:
    """stream_start envelope with schema_version > expected is dropped; counter incremented."""  # noqa: E501
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener
    from lyra.core.message import SCHEMA_VERSION_OUTBOUND_MESSAGE

    nc = AsyncMock()
    adapter = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    msg = _make_tg_msg("msg-stream-start-version")
    listener.cache_inbound(msg)

    envelope = {
        "type": "stream_start",
        "stream_id": msg.id,
        "outbound": {
            "schema_version": SCHEMA_VERSION_OUTBOUND_MESSAGE + 1,
            "content": [],
            "buttons": [],
            "metadata": {},
        },
    }
    await listener._handle(_make_nats_msg(envelope))

    assert msg.id not in listener._stream_outbound
    assert listener._version_mismatch_drops == {"OutboundMessage:schema": 1}


@pytest.mark.asyncio
async def test_attachment_version_mismatch_drops_and_increments_counter() -> None:
    """attachment envelope with schema_version > expected is dropped; counter incremented."""  # noqa: E501
    import base64

    from lyra.adapters.nats_outbound_listener import NatsOutboundListener
    from lyra.core.message import SCHEMA_VERSION_OUTBOUND_MESSAGE

    nc = AsyncMock()
    adapter = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    msg = _make_tg_msg("msg-attach-version")
    listener.cache_inbound(msg)

    b64_data = "b64:" + base64.b64encode(b"PNG").decode("ascii")
    envelope = {
        "type": "attachment",
        "stream_id": msg.id,
        "attachment": {
            "schema_version": SCHEMA_VERSION_OUTBOUND_MESSAGE + 1,
            "data": b64_data,
            "type": "image",
            "mime_type": "image/png",
        },
    }
    await listener._handle(_make_nats_msg(envelope))

    adapter.render_attachment.assert_not_called()
    assert listener._version_mismatch_drops == {"OutboundAttachment:schema": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("schema_version", [None, 1])
async def test_send_valid_version_is_accepted(schema_version) -> None:
    """send envelope with valid schema_version (absent or == 1) is accepted."""
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener

    nc = AsyncMock()
    adapter = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    msg = _make_tg_msg("msg-send-valid")
    listener.cache_inbound(msg)

    outbound: dict = {"content": ["hello"], "buttons": [], "metadata": {}}
    if schema_version is not None:
        outbound["schema_version"] = schema_version
    envelope = {"type": "send", "stream_id": msg.id, "outbound": outbound}
    await listener._handle(_make_nats_msg(envelope))

    adapter.send.assert_called_once()
    assert listener._version_mismatch_drops == {}


# ---------------------------------------------------------------------------
# MT-14: Listener-level version mismatch counter integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_version_mismatch_counter_flows_from_listener() -> None:
    """version_mismatch_count() reflects drops that occurred inside _drain_stream.

    Strategy: construct a listener and manually call decode_stream_events with
    listener._version_mismatch_drops as the counter, feeding a queue that contains
    one v2 text chunk (should be dropped) followed by a terminal v1 chunk.
    After draining the generator we assert:
    - listener.version_mismatch_count("TextRenderEvent") == 1
    - the terminal v1 chunk was decoded and yielded

    This is a direct integration test: real NatsRenderEventCodec.decode() +
    real decode_stream_events() + real listener counter dict — no mocks on
    the tested path.
    """
    import asyncio
    from unittest.mock import AsyncMock

    from lyra.adapters.nats_outbound_listener import NatsOutboundListener
    from lyra.adapters.nats_stream_decoder import decode_stream_events
    from lyra.core.message import Platform
    from lyra.core.render_events import TextRenderEvent

    # Arrange
    nc = AsyncMock()
    adapter = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    # Initial state: counter is empty
    assert listener.version_mismatch_count("TextRenderEvent") == 0

    stream_id = "test-stream-mt14"
    q: asyncio.Queue[dict] = asyncio.Queue()

    # Chunk 1: v2 payload → should be dropped, counter incremented
    await q.put(
        {
            "stream_id": stream_id,
            "seq": 0,
            "event_type": "text",
            "payload": {"schema_version": 2, "text": "bad", "is_final": False},
            "done": False,
        }
    )
    # Chunk 2: v1 payload → should decode and be yielded; is_final=True → terminal
    await q.put(
        {
            "stream_id": stream_id,
            "seq": 1,
            "event_type": "text",
            "payload": {"schema_version": 1, "text": "good", "is_final": True},
            "done": True,
        }
    )

    # Act — drain the generator using the listener's own counter dict
    yielded: list[object] = []
    async for event in decode_stream_events(
        stream_id, q, counter=listener._version_mismatch_drops
    ):
        yielded.append(event)

    # Assert — v2 chunk was dropped, counter reflects it
    assert listener.version_mismatch_count("TextRenderEvent") == 1

    # Assert — v1 chunk was decoded and yielded
    assert len(yielded) == 1
    assert isinstance(yielded[0], TextRenderEvent)
    assert yielded[0].text == "good"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# #622: streaming cache miss — embedded original_msg fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_cache_miss_with_embedded_original_msg_delivers() -> None:
    """SC-7: cache miss + embedded original_msg -> send_streaming called."""
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener

    # Arrange
    nc = AsyncMock()
    adapter = AsyncMock()
    adapter.send_streaming = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    msg = _make_tg_msg("msg-cache-miss-embed")
    # Do NOT call cache_inbound — simulate cache miss / TTL eviction

    # Confirm cache truly has no entry before dispatch
    assert msg.id not in listener._cache

    serialized_orig = json.loads(serialize(msg).decode("utf-8"))

    stream_start = {
        "type": "stream_start",
        "stream_id": msg.id,
        "outbound": {"content": [], "buttons": [], "metadata": {}},
        "original_msg": serialized_orig,
    }
    await listener._handle(_make_nats_msg(stream_start))

    done_chunk = {
        "stream_id": msg.id,
        "seq": 0,
        "event_type": "text",
        "payload": {"text": "hi", "is_final": True},
        "done": True,
    }
    await listener._handle(_make_nats_msg(done_chunk))

    # Act — await the drain task
    task = listener._stream_tasks.get(msg.id)
    assert task is not None, "drain task was not created"
    await task

    # Assert — message was delivered via embedded fallback
    adapter.send_streaming.assert_called_once()
    assert listener._stream_original_msgs == {}  # proves fallback dict was consumed


@pytest.mark.asyncio
async def test_stream_cache_miss_bad_embedded_original_msg_warns_and_drains(
    caplog,
) -> None:
    """SC-7b: cache miss + malformed embedded original_msg -> warn + drain."""
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener

    nc = AsyncMock()
    adapter = AsyncMock()
    adapter.send_streaming = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    stream_id = "msg-bad-embed"
    # Malformed: id=None fails InboundMessage deserialization
    listener._stream_original_msgs[stream_id] = {"id": None, "platform": "telegram"}

    done_chunk = {
        "stream_id": stream_id,
        "seq": 0,
        "event_type": "text",
        "payload": {"text": "hi", "is_final": True},
        "done": True,
    }

    _logger = "lyra.adapters.nats_outbound_listener"
    with caplog.at_level(logging.WARNING, logger=_logger):
        await listener._handle(_make_nats_msg(done_chunk))
        task = listener._stream_tasks.get(stream_id)
        assert task is not None, "drain task was not created"
        await task

    adapter.send_streaming.assert_not_called()
    assert any(
        "bad embedded" in r.message and r.levelno == logging.WARNING
        for r in caplog.records
    )
    assert any(
        "drained" in r.message and r.levelno == logging.WARNING for r in caplog.records
    )


@pytest.mark.asyncio
async def test_stream_cache_hit_does_not_use_stream_original_msgs() -> None:
    """SC-8: cache hit -> _stream_original_msgs unused and cleaned up after drain."""
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener

    # Arrange
    nc = AsyncMock()
    adapter = AsyncMock()
    adapter.send_streaming = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    msg = _make_tg_msg("msg-cache-hit-orig")
    listener.cache_inbound(msg)  # normal cache path

    serialized_orig = json.loads(serialize(msg).decode("utf-8"))

    stream_start = {
        "type": "stream_start",
        "stream_id": msg.id,
        "outbound": {"content": [], "buttons": [], "metadata": {}},
        "original_msg": serialized_orig,
    }
    await listener._handle(_make_nats_msg(stream_start))

    done_chunk = {
        "stream_id": msg.id,
        "seq": 0,
        "event_type": "text",
        "payload": {"text": "hi", "is_final": True},
        "done": True,
    }
    await listener._handle(_make_nats_msg(done_chunk))

    # Act
    task = listener._stream_tasks.get(msg.id)
    assert task is not None, "drain task was not created"
    await task

    # Assert — delivered via cache (not fallback)
    adapter.send_streaming.assert_called_once()
    # _stream_original_msgs cleaned up in finally block
    assert listener._stream_original_msgs == {}


@pytest.mark.asyncio
async def test_stream_both_missing_warns_and_drains(caplog) -> None:
    """SC-9: no cache entry and no _stream_original_msgs -> warn + drain."""
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener

    # Arrange — no cache_inbound, no stream_start (so _stream_original_msgs is empty)
    nc = AsyncMock()
    adapter = AsyncMock()
    adapter.send_streaming = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    stream_id = "msg-both-missing"

    done_chunk = {
        "stream_id": stream_id,
        "seq": 0,
        "event_type": "text",
        "payload": {"text": "hi", "is_final": True},
        "done": True,
    }

    _logger = "lyra.adapters.nats_outbound_listener"
    with caplog.at_level(logging.WARNING, logger=_logger):
        await listener._handle(_make_nats_msg(done_chunk))

        task = listener._stream_tasks.get(stream_id)
        assert task is not None
        await task

    # Assert — send_streaming never called; warning fired
    adapter.send_streaming.assert_not_called()
    assert any(
        "drained" in r.message and r.levelno == logging.WARNING for r in caplog.records
    )


def test_remember_terminated_evicts_oldest_first(monkeypatch) -> None:
    """#569: FIFO eviction drops the oldest tombstone across the full sequence."""
    from lyra.adapters import nats_stream_decoder as nsd
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener
    from lyra.adapters.nats_stream_decoder import remember_terminated

    # Shrink the cap so the FIFO property is verified against a tiny sequence.
    monkeypatch.setattr(nsd, "_MAX_TERMINATED_STREAMS", 3)

    nc = AsyncMock()
    adapter = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    # Insert in known order: A, B, C (fills cap).
    remember_terminated(listener, "A")
    remember_terminated(listener, "B")
    remember_terminated(listener, "C")
    assert list(listener._terminated_streams.keys()) == ["A", "B", "C"]

    # Insert D → A (oldest) evicted; order is now B, C, D.
    remember_terminated(listener, "D")
    assert list(listener._terminated_streams.keys()) == ["B", "C", "D"]

    # Re-tombstone B → B moves to most-recent position; order now C, D, B.
    remember_terminated(listener, "B")
    assert list(listener._terminated_streams.keys()) == ["C", "D", "B"]

    # Insert E → C (now oldest) evicted.
    remember_terminated(listener, "E")
    assert list(listener._terminated_streams.keys()) == ["D", "B", "E"]


def test_reap_tombstones_evicts_stale_entries(monkeypatch) -> None:
    """#570: reaper removes tombstones older than TTL_SECONDS.

    Uses a frozen monotonic clock so the stale/fresh boundary can't drift
    under CI load.
    """
    from lyra.adapters import nats_stream_decoder as nsd
    from lyra.adapters._inbound_cache import TTL_SECONDS
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener
    from lyra.adapters.nats_stream_decoder import reap_tombstones

    frozen = 1_000_000.0
    monkeypatch.setattr(nsd.time, "monotonic", lambda: frozen)

    nc = AsyncMock()
    adapter = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    stale_id = "tombstone-stale"
    fresh_id = "tombstone-fresh"
    listener._terminated_streams[stale_id] = frozen - (TTL_SECONDS + 1)
    listener._terminated_streams[fresh_id] = frozen

    reap_tombstones(listener, TTL_SECONDS)

    assert stale_id not in listener._terminated_streams
    assert fresh_id in listener._terminated_streams


@pytest.mark.asyncio
async def test_run_reaper_loop_wires_into_start_and_evicts_stale(monkeypatch) -> None:
    """Wiring test: start() launches run_reaper_loop, which clears stale tombstones.

    Patches asyncio.sleep in the decoder module to yield once then raise
    CancelledError so the loop executes exactly one iteration before the
    task is torn down — no real timers involved.
    """
    import asyncio

    from lyra.adapters import nats_stream_decoder as nsd
    from lyra.adapters._inbound_cache import TTL_SECONDS
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener

    frozen = 2_000_000.0
    monkeypatch.setattr(nsd.time, "monotonic", lambda: frozen)

    call_count = 0

    async def _sleep_once(_interval):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise asyncio.CancelledError
        # first call: yield once so the loop body gets to run
        return None

    monkeypatch.setattr(nsd.asyncio, "sleep", _sleep_once)

    mock_sub = AsyncMock()
    nc = AsyncMock()
    nc.subscribe = AsyncMock(return_value=mock_sub)
    adapter = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    # Seed a stale tombstone.
    stale_id = "wired-stale"
    listener._terminated_streams[stale_id] = frozen - (TTL_SECONDS + 1)

    await listener.start()
    # Allow the reaper task to run one iteration, then cancel propagates.
    assert listener._reaper_task is not None
    with contextlib.suppress(asyncio.CancelledError):
        await listener._reaper_task

    assert stale_id not in listener._terminated_streams


@pytest.mark.asyncio
async def test_run_reaper_loop_survives_transient_exception(monkeypatch) -> None:
    """run_reaper_loop catches exceptions from reap calls and keeps running.

    Behavioural assertion: if the except clause did not catch, the task
    would die after the first raise and call_count would never reach 2.
    Reaching call 2 proves the exception was swallowed and the loop
    continued to the next sleep.
    """
    import asyncio

    from lyra.adapters import nats_stream_decoder as nsd
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener

    call_count = 0

    async def _sleep_controlled(_interval):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise asyncio.CancelledError
        return None

    monkeypatch.setattr(nsd.asyncio, "sleep", _sleep_controlled)

    mock_sub = AsyncMock()
    nc = AsyncMock()
    nc.subscribe = AsyncMock(return_value=mock_sub)
    adapter = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    # Make reap_tombstones raise on first iteration.
    raise_once = [True]

    def _boom(*_args, **_kwargs):
        if raise_once[0]:
            raise_once[0] = False
            raise RuntimeError("transient")

    monkeypatch.setattr(nsd, "reap_tombstones", _boom)

    await listener.start()
    assert listener._reaper_task is not None
    with contextlib.suppress(asyncio.CancelledError):
        await listener._reaper_task

    # Body ran once (raised), loop swallowed it, hit sleep again → cancelled.
    assert call_count >= 2
    assert raise_once == [False]  # _boom was actually called


@pytest.mark.asyncio
async def test_stop_clears_stream_original_msgs() -> None:
    """SC-11: stop() clears _stream_original_msgs dict."""
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener

    # Arrange
    mock_sub = AsyncMock()
    nc = AsyncMock()
    nc.subscribe = AsyncMock(return_value=mock_sub)

    adapter = MagicMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    # Manually populate _stream_original_msgs
    listener._stream_original_msgs["test-id"] = {"some": "data"}

    await listener.start()

    # Act
    await listener.stop()

    # Assert
    assert listener._stream_original_msgs == {}
