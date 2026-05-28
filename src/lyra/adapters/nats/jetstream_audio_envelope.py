"""Envelope decode + delivery-count helpers for JetStreamAudioConsumer.

Stateless module-level functions — no class needed. Extracted from
jetstream_audio_consumer.py to keep that file under the 300-line cap.

Public surface consumed by JetStreamAudioConsumer._process():
  - decode_audio_envelope(msg) → (stream_id, audio, inbound) | (None, None, None)
  - num_delivered(msg) → int
"""

from __future__ import annotations

import json
import logging
from typing import Any

from lyra.core.messaging.message import InboundMessage, OutboundAudio
from lyra.nats.type_registry import TYPE_REGISTRY_RESOLVER
from roxabi_nats._serialize import deserialize_dict as _deserialize_dict

log = logging.getLogger(__name__)

_EXPECTED_TYPE = "audio"


def decode_audio_envelope(
    msg: Any,
) -> tuple[str | None, OutboundAudio | None, InboundMessage | None]:
    """Decode a JetStream audio envelope into (stream_id, audio, inbound).

    Returns ``(None, None, None)`` on any parse error so the caller can
    ack-and-skip without blocking the consumer.

    Expected wire format (published by NatsChannelProxy.render_audio)::

        {
            "type": "audio",
            "stream_id": "<str>",
            "audio": { ...OutboundAudio... },
            "original_msg": { ...InboundMessage... }
        }
    """
    try:
        data: dict = json.loads(msg.data)
    except (json.JSONDecodeError, ValueError):
        log.warning(
            "jetstream-audio: JSON decode failed on subject=%s",
            msg.subject,
        )
        return None, None, None

    msg_type = data.get("type")
    if msg_type != _EXPECTED_TYPE:
        log.warning(
            "jetstream-audio: unexpected envelope type=%r on subject=%s",
            msg_type,
            msg.subject,
        )
        return None, None, None

    stream_id: str | None = data.get("stream_id")
    if not stream_id:
        log.warning("jetstream-audio: missing stream_id in envelope")
        return None, None, None

    audio_data = data.get("audio")
    original_data = data.get("original_msg")
    if audio_data is None or original_data is None:
        log.warning(
            "jetstream-audio: missing audio or original_msg for stream_id=%r",
            stream_id,
        )
        return None, None, None

    try:
        audio = _deserialize_dict(
            audio_data, OutboundAudio, resolver=TYPE_REGISTRY_RESOLVER
        )
    except (ValueError, TypeError):
        log.warning(
            "jetstream-audio: failed to deserialize OutboundAudio for stream_id=%r",
            stream_id,
        )
        return None, None, None

    try:
        inbound = _deserialize_dict(
            original_data, InboundMessage, resolver=TYPE_REGISTRY_RESOLVER
        )
    except (ValueError, TypeError):
        log.warning(
            "jetstream-audio: failed to deserialize InboundMessage for stream_id=%r",
            stream_id,
        )
        return None, None, None

    return stream_id, audio, inbound


def num_delivered(msg: Any) -> int:
    """Extract num_delivered from JetStream message metadata.

    Returns 1 (safe default) when metadata is unavailable — e.g. the
    reply subject is absent or not a valid JetStream reply token.
    """
    try:
        return msg.metadata.num_delivered  # type: ignore[no-any-return]
    except Exception:  # noqa: BLE001 — metadata parse: varied exception types
        return 1
