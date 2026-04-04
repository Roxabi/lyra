"""Tests for NatsOutboundListener — NATS-to-adapter dispatch."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.message import InboundMessage, Platform
from lyra.core.trust import TrustLevel


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
            "chat_id": 42, "message_id": 10, "topic_id": None, "is_group": False
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
async def test_cache_inbound_drops_when_full(caplog) -> None:
    """cache_inbound() drops the entry and logs a warning when _cache is at max size."""
    import logging

    from lyra.adapters.nats_outbound_listener import NatsOutboundListener, _MAX_CACHE_SIZE

    nc = AsyncMock()
    adapter = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    # Fill cache to the limit using distinct fake entries
    for i in range(_MAX_CACHE_SIZE):
        fake = _make_tg_msg(f"fill-{i}")
        listener._cache[fake.id] = fake
        listener._cache_ts[fake.id] = 0.0

    overflow_msg = _make_tg_msg("overflow-msg")
    with caplog.at_level(logging.WARNING, logger="lyra.adapters.nats_outbound_listener"):
        listener.cache_inbound(overflow_msg)

    assert overflow_msg.id not in listener._cache
    assert len(listener._cache) == _MAX_CACHE_SIZE
    assert any("_cache full" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_stream_drops_when_at_max_streams(caplog) -> None:
    """_handle_chunk drops a new stream and logs a warning when _stream_tasks is at max."""
    import asyncio
    import logging

    from lyra.adapters.nats_outbound_listener import NatsOutboundListener, _MAX_STREAMS

    nc = AsyncMock()
    adapter = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

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
    with caplog.at_level(logging.WARNING, logger="lyra.adapters.nats_outbound_listener"):
        await listener._handle(_make_nats_msg(chunk))

    assert new_stream_id not in listener._stream_tasks
    assert new_stream_id not in listener._stream_queues
    assert any("_stream_tasks full" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_reaper_evicts_stale_entries(caplog) -> None:
    """_reap_stale() evicts entries whose timestamp exceeds _CACHE_TTL_SECONDS."""
    import asyncio
    import logging

    from lyra.adapters.nats_outbound_listener import (
        NatsOutboundListener,
        _CACHE_TTL_SECONDS,
    )

    nc = AsyncMock()
    adapter = AsyncMock()
    listener = NatsOutboundListener(nc, Platform.TELEGRAM, "main", adapter)

    stale_id = "stale-stream"
    fresh_id = "fresh-stream"

    stale_msg = _make_tg_msg(stale_id)
    fresh_msg = _make_tg_msg(fresh_id)

    import time

    listener._cache[stale_id] = stale_msg
    listener._cache_ts[stale_id] = time.monotonic() - (_CACHE_TTL_SECONDS + 1)

    listener._cache[fresh_id] = fresh_msg
    listener._cache_ts[fresh_id] = time.monotonic()

    # Patch sleep: return normally on the first call (so the reap body runs),
    # then raise CancelledError on the second call to stop the loop.
    call_count = 0

    async def _sleep_once(_interval):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise asyncio.CancelledError

    import unittest.mock as mock

    with mock.patch("lyra.adapters.nats_outbound_listener.asyncio.sleep", side_effect=_sleep_once):
        with caplog.at_level(logging.WARNING, logger="lyra.adapters.nats_outbound_listener"):
            try:
                await listener._reap_stale()
            except asyncio.CancelledError:
                pass

    assert stale_id not in listener._cache
    assert stale_id not in listener._cache_ts
    assert fresh_id in listener._cache
    assert any("evicting stale" in record.message for record in caplog.records)
