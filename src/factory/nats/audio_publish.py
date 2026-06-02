"""Audio publish helpers for NatsChannelProxy.

Extracted from nats_channel_proxy.py (#1589) to shrink the class below
300 lines.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import nats.errors
from nats.aio.client import Client as NATS

from factory.core.messaging.message import InboundMessage
from factory.core.messaging.voice_notify import notify_undelivered
from roxabi_nats._serialize import serialize

if TYPE_CHECKING:
    from nats.js.client import JetStreamContext

    from factory.core.messaging.message import Platform
    from roxabi_nats import TypeHintResolver

log = logging.getLogger(__name__)

# Bounded retry for transient JetStream publish failures (leader election, timeout).
# Nats-Msg-Id + the stream's 60 s duplicate window make retried publishes idempotent.
_AUDIO_PUBLISH_MAX_ATTEMPTS = 3
_AUDIO_PUBLISH_BACKOFF_BASE_S = 0.2
_AUDIO_PUBLISH_BACKOFF_CAP_S = 1.0


async def publish_audio_with_retry(
    js: JetStreamContext,
    subject: str,
    payload: bytes,
    stream_id: str,
) -> None:
    """Attempt JetStream publish up to _AUDIO_PUBLISH_MAX_ATTEMPTS times.

    Retries on transient ``nats.errors.Error`` or ``asyncio.TimeoutError``
    with capped exponential backoff.  Re-raises the last exception on
    exhaustion so the caller can fall through to notify_audio_publish_failed.

    Retried publishes are idempotent: the ``Nats-Msg-Id`` header combined
    with the stream's 60 s duplicate window prevents double-delivery.
    """
    last_exc: nats.errors.Error | asyncio.TimeoutError | None = None
    for attempt in range(_AUDIO_PUBLISH_MAX_ATTEMPTS):
        try:
            await js.publish(subject, payload, headers={"Nats-Msg-Id": stream_id})
            return
        except (nats.errors.Error, asyncio.TimeoutError) as exc:
            last_exc = exc
            if attempt < _AUDIO_PUBLISH_MAX_ATTEMPTS - 1:
                delay = min(
                    _AUDIO_PUBLISH_BACKOFF_BASE_S * (2**attempt),
                    _AUDIO_PUBLISH_BACKOFF_CAP_S,
                )
                log.warning(
                    "NatsChannelProxy: audio publish attempt %d/%d failed"
                    " stream_id=%r exc_type=%s — retrying in %.2fs",
                    attempt + 1,
                    _AUDIO_PUBLISH_MAX_ATTEMPTS,
                    stream_id,
                    type(exc).__name__,
                    delay,
                )
                await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]


async def notify_audio_publish_failed(
    nc: NATS,
    platform: Platform,
    bot_id: str,
    resolver: TypeHintResolver,
    inbound: InboundMessage,
) -> None:
    """Best-effort: send voice-undelivered notification via legacy text subject.

    Uses the core-NATS (at-most-once) text subject rather than JetStream — the
    JetStream path is the one that just failed.  Swallows any publish error;
    the failure is logged but never re-raised so the hub loop stays alive.
    No ``str(exc)`` content reaches the bus (SanitizedError discipline).
    """
    text_subject = f"lyra.outbound.{platform.value}.{bot_id}"
    notif = notify_undelivered(context="hub-audio-publish-fail")
    notif_envelope = {
        "type": "send",
        "stream_id": inbound.id,
        "outbound": json.loads(serialize(notif, resolver=resolver).decode("utf-8")),
        "original_msg": json.loads(
            serialize(inbound, resolver=resolver).decode("utf-8")
        ),
    }
    notif_payload = json.dumps(notif_envelope, ensure_ascii=False).encode("utf-8")
    try:
        await nc.publish(text_subject, notif_payload)
    except nats.errors.Error:
        log.warning(
            "NatsChannelProxy: failed to publish audio-undelivered notification"
            " stream_id=%r — user will not be notified",
            inbound.id,
        )
