"""NATS envelope handlers — standalone functions for outbound message dispatch.

Extracted from NatsOutboundListener for single-responsity and file size constraints.
All handlers receive the listener instance and data dict, operating on listener state.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from nats.aio.msg import Msg

from lyra.core.message import (
    SCHEMA_VERSION_OUTBOUND_MESSAGE,
    OutboundAttachment,
    OutboundAudio,
    OutboundMessage,
)
from roxabi_nats._serialize import deserialize_dict as _deserialize_dict
from roxabi_nats._version_check import check_schema_version

if TYPE_CHECKING:
    from lyra.adapters.nats_outbound_listener import NatsOutboundListener

log = logging.getLogger(__name__)

_MAX_STREAMS = 100
_MAX_QUEUE_SIZE = 256


async def handle_send(listener: "NatsOutboundListener", data: dict) -> None:
    """Handle 'send' envelope — resolve cached msg, deserialize, dispatch."""
    resolved = listener._cache.resolve(data, "send")
    if resolved is None:
        return
    stream_id, original_msg = resolved
    outbound_data = data.get("outbound")
    if outbound_data is None:
        log.warning("NatsOutboundListener: missing 'outbound' key in send envelope")
        return
    if not _check_outbound_version(listener, outbound_data, "OutboundMessage"):
        return
    try:
        outbound = _deserialize_dict(outbound_data, OutboundMessage)
    except Exception:
        log.warning("NatsOutboundListener: failed to deserialize outbound message")
        return
    await listener._adapter.send(original_msg, outbound)
    listener._cache.pop(stream_id)


async def handle_attachment(listener: "NatsOutboundListener", data: dict) -> None:
    """Handle 'attachment' envelope type — resolve, deserialize, dispatch to adapter."""
    resolved = listener._cache.resolve(data, "attachment")
    if resolved is None:
        return
    stream_id, original_msg = resolved
    attachment_data = data.get("attachment")
    if attachment_data is None:
        log.warning("NatsOutboundListener: missing 'attachment' key in envelope")
        return
    if not _check_outbound_version(listener, attachment_data, "OutboundAttachment"):
        return
    try:
        attachment = _deserialize_dict(attachment_data, OutboundAttachment)
    except Exception:
        log.warning("NatsOutboundListener: failed to deserialize attachment")
        return
    await listener._adapter.render_attachment(attachment, original_msg)
    listener._cache.pop(stream_id)


async def handle_audio(listener: "NatsOutboundListener", data: dict) -> None:
    """Handle 'audio' envelope type — resolve, deserialize, dispatch to adapter."""
    resolved = listener._cache.resolve(data, "audio")
    if resolved is None:
        return
    stream_id, original_msg = resolved
    audio_data = data.get("audio")
    if audio_data is None:
        log.warning("NatsOutboundListener: missing 'audio' key in envelope")
        return
    try:
        audio = _deserialize_dict(audio_data, OutboundAudio)
    except Exception:
        log.warning("NatsOutboundListener: failed to deserialize audio")
        return
    await listener._adapter.render_audio(audio, original_msg)
    listener._cache.pop(stream_id)


def handle_stream_start(listener: "NatsOutboundListener", data: dict) -> None:
    """Handle 'stream_start' envelope — store outbound metadata for streaming."""
    stream_id = data.get("stream_id")
    outbound_data = data.get("outbound")
    if stream_id is None or outbound_data is None:
        return
    if not _check_outbound_version(listener, outbound_data, "OutboundMessage"):
        return
    if len(listener._stream_outbound) >= _MAX_STREAMS:
        log.warning(
            "NatsOutboundListener: _stream_outbound full"
            " (%d entries), dropping stream_id=%r",
            _MAX_STREAMS,
            stream_id,
        )
        return
    try:
        listener._stream_outbound[stream_id] = _deserialize_dict(
            outbound_data, OutboundMessage
        )
        raw_orig = data.get("original_msg")  # bounded by _MAX_STREAMS guard above
        if raw_orig is not None:
            listener._stream_original_msgs[stream_id] = raw_orig
    except Exception:
        log.warning("NatsOutboundListener: failed to deserialize stream outbound")


async def handle_chunk(listener: "NatsOutboundListener", data: dict) -> None:
    """Handle chunk envelope (stream_id + seq) — queue chunk for streaming dispatch."""
    stream_id = data.get("stream_id")
    if stream_id is None:
        log.warning("NatsOutboundListener: chunk envelope missing stream_id")
        return
    if stream_id in listener._terminated_streams:
        log.warning(
            "NatsOutboundListener: chunk rejected for terminated stream_id=%r",
            stream_id,
        )
        return
    at_limit = len(listener._stream_tasks) >= _MAX_STREAMS
    if stream_id not in listener._stream_tasks and at_limit:
        log.warning(
            "NatsOutboundListener: _stream_tasks full"
            " (%d streams), dropping stream_id=%r",
            _MAX_STREAMS,
            stream_id,
        )
        return
    q = listener._stream_queues.setdefault(
        stream_id,
        asyncio.Queue(maxsize=_MAX_QUEUE_SIZE),
    )
    if stream_id in listener._cache:
        listener._cache.touch(stream_id)
    try:
        q.put_nowait(data)
    except asyncio.QueueFull:
        log.warning(
            "NatsOutboundListener: stream queue full for stream_id=%r, dropping chunk",
            stream_id,
        )
        return
    if stream_id not in listener._stream_tasks:
        listener._stream_tasks[stream_id] = asyncio.create_task(
            listener._drain_stream(stream_id, q)
        )


async def handle_raw_message(listener: "NatsOutboundListener", msg: Msg) -> None:
    """Parse raw NATS message and dispatch to appropriate envelope handler."""
    try:
        data = json.loads(msg.data)
    except Exception:
        log.warning("NatsOutboundListener: failed to decode message")
        return
    msg_type = data.get("type")
    if msg_type == "send":
        await handle_send(listener, data)
    elif msg_type == "stream_start":
        handle_stream_start(listener, data)
    elif msg_type == "stream_error":
        listener._handle_stream_error(data)
    elif msg_type == "attachment":
        await handle_attachment(listener, data)
    elif msg_type == "audio":
        await handle_audio(listener, data)
    elif "stream_id" in data and "seq" in data:
        await handle_chunk(listener, data)
    else:
        log.warning("NatsOutboundListener: unknown envelope type=%r", msg_type)


def _check_outbound_version(
    listener: "NatsOutboundListener", payload: dict, envelope_name: str
) -> bool:
    """Check schema version for outbound envelope, tracking mismatches on listener."""
    return check_schema_version(
        payload,
        envelope_name=envelope_name,
        expected=SCHEMA_VERSION_OUTBOUND_MESSAGE,
        subject=listener._subject,
        counter=listener._version_mismatch_drops,
    )
