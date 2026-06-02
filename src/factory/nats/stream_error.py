"""Stream error publish helpers for NatsChannelProxy.

Extracted from nats_channel_proxy.py (#1589) to shrink the class below
300 lines.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import nats.errors

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

log = logging.getLogger(__name__)


async def publish_stream_error(nc: NATS, subject: str, stream_id: str) -> None:
    """Publish a stream_error envelope, swallowing NATS transport errors."""
    error_envelope = {
        "type": "stream_error",
        "stream_id": stream_id,
        "reason": "streaming_exception",
    }
    try:
        await nc.publish(
            subject,
            json.dumps(error_envelope, ensure_ascii=False).encode("utf-8"),
        )
    except nats.errors.Error:
        log.warning(
            "NatsChannelProxy: failed to publish stream_error for stream_id=%r",
            stream_id,
        )


async def publish_stream_errors(
    nc: NATS,
    subject: str,
    stream_ids: set[str],
    reason: str = "hub_shutdown",
) -> None:
    """Publish stream_error for every stream_id in *stream_ids*."""
    for stream_id in stream_ids:
        envelope = {
            "type": "stream_error",
            "stream_id": stream_id,
            "reason": reason,
        }
        try:
            await nc.publish(
                subject,
                json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
            )
        except nats.errors.Error:
            log.warning(
                "NatsChannelProxy: failed to publish stream_error for stream_id=%r",
                stream_id,
            )
