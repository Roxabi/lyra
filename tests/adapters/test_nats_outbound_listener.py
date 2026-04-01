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
