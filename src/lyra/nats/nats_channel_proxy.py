"""NatsChannelProxy — ChannelAdapter implementation over NATS.

Publishes outbound messages to NATS subjects instead of calling platform SDKs.
Used by the standalone Hub process to dispatch responses to remote adapters.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import nats.errors
from nats.aio.client import Client as NATS

from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import (
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    OutboundMessage,
    Platform,
)
from lyra.core.messaging.render_events import RenderEvent
from lyra.nats.audio_publish import (
    notify_audio_publish_failed,
    publish_audio_with_retry,
)
from lyra.nats.keepalive import _run_keepalive_loop
from lyra.nats.render_event_codec import NatsRenderEventCodec
from lyra.nats.stream_error import (
    publish_stream_error,
)
from lyra.nats.stream_error import (
    publish_stream_errors as _publish_stream_errors,
)
from lyra.nats.type_registry import TYPE_REGISTRY_RESOLVER
from roxabi_contracts.outbound import OutboundAudioSubjects
from roxabi_nats import TypeHintResolver
from roxabi_nats._serialize import serialize

if TYPE_CHECKING:
    from nats.js.client import JetStreamContext

log = logging.getLogger(__name__)

_NATS_UNSAFE = re.compile(r"[.*> ]")


def _safe_subject_token(value: str) -> str:
    """Sanitize a value for use as a NATS subject token."""
    return _NATS_UNSAFE.sub("_", value)


class NatsChannelProxy:
    """ChannelAdapter that publishes outbound messages to NATS subjects.

    Implements the ChannelAdapter Protocol from hub_protocol.py.
    Inbound normalization is not supported — raises NotImplementedError.
    render_audio() publishes audio bytes as a NATS "audio" envelope.
    render_audio_stream() and render_voice_stream() are not yet implemented (C5).
    """

    def __init__(
        self,
        nc: NATS,
        platform: Platform,
        bot_id: str,
        *,
        resolver: TypeHintResolver = TYPE_REGISTRY_RESOLVER,
    ) -> None:
        """Store nc, platform, bot_id. No I/O."""
        if not re.fullmatch(r"[A-Za-z0-9_-]+", bot_id):
            raise ValueError(
                f"Invalid bot_id for NATS subject: {bot_id!r} — "
                "must match [A-Za-z0-9_-]+"
            )
        self._nc = nc
        # JetStreamContext stores the NATS client by reference (self._nc = conn) and
        # dispatches every publish through it.  The NATS client reconnects in-place
        # (same object, new socket) so this cached context is reconnect-transparent.
        self._js: JetStreamContext = nc.jetstream()
        self._platform = platform
        self._bot_id = bot_id
        self._resolver = resolver
        self._codec = NatsRenderEventCodec(resolver=resolver)
        self._active_streams: set[str] = set()

    def _to_json(self, obj: Any) -> Any:
        return json.loads(serialize(obj, resolver=self._resolver).decode("utf-8"))

    # ------------------------------------------------------------------
    # Inbound normalization — not supported by this proxy
    # ------------------------------------------------------------------

    def normalize(self, raw: Any) -> InboundMessage:
        raise NotImplementedError(
            "NatsChannelProxy does not normalize inbound messages"
        )

    def normalize_audio(
        self,
        raw: Any,
        audio_bytes: bytes,
        mime_type: str,
        *,
        trust_level: TrustLevel,
        pending: Any = None,
    ) -> InboundMessage:
        raise NotImplementedError("NatsChannelProxy does not normalize audio messages")

    # ------------------------------------------------------------------
    # Outbound dispatch
    # ------------------------------------------------------------------

    async def send(
        self, original_msg: InboundMessage, outbound: OutboundMessage
    ) -> None:
        """Publish an outbound text message to NATS."""
        subject = f"lyra.outbound.{self._platform.value}.{self._bot_id}"
        envelope = {
            "type": "send",
            "stream_id": original_msg.id,
            "outbound": self._to_json(outbound),
            "original_msg": self._to_json(original_msg),
        }
        payload = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        await self._nc.publish(subject, payload)

    async def send_streaming(
        self,
        original_msg: InboundMessage,
        events: AsyncIterator[RenderEvent],
        outbound: OutboundMessage | None = None,
    ) -> None:
        """Publish streaming chunks to NATS; keepalive prevents per-chunk timeout (#687)."""  # noqa: E501
        subject = f"lyra.outbound.{self._platform.value}.{self._bot_id}"

        if outbound is not None:
            header = {
                "type": "stream_start",
                "stream_id": original_msg.id,
                "outbound": self._to_json(outbound),
                "original_msg": self._to_json(original_msg),
            }
            await self._nc.publish(
                subject, json.dumps(header, ensure_ascii=False).encode("utf-8")
            )

        # Shared mutable boxes for keepalive coordination (avoids nonlocal in task).
        seq_box: list[int] = [0]
        last_publish_box: list[float] = [time.monotonic()]
        keepalive_task = asyncio.create_task(
            _run_keepalive_loop(
                self._nc, subject, original_msg.id, seq_box, last_publish_box
            )
        )
        try:
            self._active_streams.add(original_msg.id)
            try:
                async for event in events:
                    event_type, payload, is_done = self._codec.encode(event)
                    chunk = {
                        "stream_id": original_msg.id,
                        "seq": seq_box[0],
                        "event_type": event_type,
                        "payload": payload,
                        "done": is_done,
                    }
                    await self._nc.publish(
                        subject,
                        json.dumps(chunk, ensure_ascii=False).encode("utf-8"),
                    )
                    last_publish_box[0] = time.monotonic()
                    seq_box[0] += 1
                # Always publish a terminal sentinel so the adapter's
                # _drain_stream exits cleanly even when the events iterator
                # was empty or the last event did not set is_final=True.
                terminal = {
                    "stream_id": original_msg.id,
                    "seq": seq_box[0],
                    "event_type": "stream_end",
                    "payload": {},
                    "done": True,
                }
                await self._nc.publish(
                    subject,
                    json.dumps(terminal, ensure_ascii=False).encode("utf-8"),
                )
            except Exception as exc:  # noqa: BLE001 — bus boundary, type sanitized
                log.warning(
                    "NatsChannelProxy: NATS publish failed during streaming,"
                    " stream_id=%r type=%s — draining iterator",
                    original_msg.id,
                    type(exc).__name__,
                )
                await publish_stream_error(self._nc, subject, original_msg.id)
                async for _ in events:
                    pass
        finally:
            keepalive_task.cancel()
            try:
                await keepalive_task
            except asyncio.CancelledError:
                pass
            # Ensure stream_id is always removed from the tracking set, regardless
            # of success, streaming exception, or publish failure in the except path.
            self._active_streams.discard(original_msg.id)

    async def publish_stream_errors(self, reason: str = "hub_shutdown") -> None:
        """Publish stream_error for all active streams, then clear the set.

        Uses an atomic swap to capture the snapshot and reset the set in one step,
        eliminating the race window between list() and clear() when a concurrent
        exception-path discard fires mid-iteration.
        """
        subject = f"lyra.outbound.{self._platform.value}.{self._bot_id}"
        stream_ids = self._active_streams
        self._active_streams = set()
        await _publish_stream_errors(self._nc, subject, stream_ids, reason)

    # ------------------------------------------------------------------
    # Audio — not yet implemented (C5)
    # ------------------------------------------------------------------

    async def render_audio(self, msg: OutboundAudio, inbound: InboundMessage) -> None:
        """Publish audio to JetStream (durable).  Fallback notification on failure."""
        stream_id = inbound.id
        subject = OutboundAudioSubjects.audio(self._platform.value, self._bot_id)
        envelope = {
            "type": "audio",
            "stream_id": stream_id,
            "audio": self._to_json(msg),
            "original_msg": self._to_json(inbound),
        }
        payload = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        try:
            await publish_audio_with_retry(self._js, subject, payload, stream_id)
        except (nats.errors.Error, asyncio.TimeoutError) as exc:
            log.error(
                "NatsChannelProxy: audio JetStream publish failed"
                " stream_id=%r exc_type=%s — dispatching undelivered notification",
                stream_id,
                type(exc).__name__,
            )
            await notify_audio_publish_failed(
                self._nc, self._platform, self._bot_id, self._resolver, inbound
            )

    async def render_audio_stream(
        self,
        chunks: AsyncIterator[OutboundAudioChunk],
        inbound: InboundMessage,
    ) -> None:
        """Audio streaming not implemented (C5) — drains iterator."""
        log.warning(
            "audio-stream-over-NATS not implemented (C5) — dropping for msg %s",
            inbound.id,
        )
        async for _ in chunks:
            pass

    async def render_voice_stream(
        self,
        chunks: AsyncIterator[OutboundAudioChunk],
        inbound: InboundMessage,
    ) -> None:
        """Voice streaming not implemented (C5) — drains iterator."""
        log.warning(
            "voice-stream-over-NATS not implemented (C5) — dropping for msg %s",
            inbound.id,
        )
        async for _ in chunks:
            pass

    # ------------------------------------------------------------------
    # Attachment dispatch
    # ------------------------------------------------------------------

    async def render_attachment(
        self, msg: OutboundAttachment, inbound: InboundMessage
    ) -> None:
        """Publish an outbound attachment to NATS."""
        subject = f"lyra.outbound.{self._platform.value}.{self._bot_id}"
        envelope = {
            "type": "attachment",
            "stream_id": inbound.id,
            "attachment": self._to_json(msg),
        }
        payload = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        await self._nc.publish(subject, payload)
