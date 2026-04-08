"""NatsChannelProxy — ChannelAdapter implementation over NATS.

Publishes outbound messages to NATS subjects instead of calling platform SDKs.
Used by the standalone Hub process to dispatch responses to remote adapters.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Any

from nats.aio.client import Client as NATS

from lyra.core.message import (
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    OutboundMessage,
    Platform,
)
from lyra.core.render_events import RenderEvent
from lyra.core.trust import TrustLevel
from lyra.nats._serialize import serialize
from lyra.nats.render_event_codec import NatsRenderEventCodec

log = logging.getLogger(__name__)

_NATS_UNSAFE = re.compile(r'[.*> ]')


def _safe_subject_token(value: str) -> str:
    """Sanitize a value for use as a NATS subject token."""
    return _NATS_UNSAFE.sub('_', value)


class NatsChannelProxy:
    """ChannelAdapter that publishes outbound messages to NATS subjects.

    Implements the ChannelAdapter Protocol from hub_protocol.py.
    Inbound normalization is not supported — raises NotImplementedError.
    render_audio() publishes audio bytes as a NATS "audio" envelope.
    render_audio_stream() and render_voice_stream() are not yet implemented (C5).
    """

    def __init__(self, nc: NATS, platform: Platform, bot_id: str) -> None:
        """Store nc, platform, bot_id. No I/O."""
        if not re.fullmatch(r'[A-Za-z0-9_-]+', bot_id):
            raise ValueError(
                f"Invalid bot_id for NATS subject: {bot_id!r} — "
                "must match [A-Za-z0-9_-]+"
            )
        self._nc = nc
        self._platform = platform
        self._bot_id = bot_id
        self._active_streams: set[str] = set()

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
    ) -> InboundMessage:
        raise NotImplementedError(
            "NatsChannelProxy does not normalize audio messages"
        )

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
            "outbound": json.loads(serialize(outbound).decode("utf-8")),
            "original_msg": json.loads(serialize(original_msg).decode("utf-8")),
        }
        payload = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        await self._nc.publish(subject, payload)

    async def send_streaming(
        self,
        original_msg: InboundMessage,
        events: AsyncIterator[RenderEvent],
        outbound: OutboundMessage | None = None,
    ) -> None:
        """Publish streaming render events to NATS as chunked messages."""
        subject = f"lyra.outbound.{self._platform.value}.{self._bot_id}"

        self._active_streams.add(original_msg.id)

        # Send outbound metadata so the adapter can honour the intermediate flag
        # (typing indicator lifecycle) and other outbound fields.
        if outbound is not None:
            header = {
                "type": "stream_start",
                "stream_id": original_msg.id,
                "outbound": json.loads(serialize(outbound).decode("utf-8")),
            }
            await self._nc.publish(
                subject,
                json.dumps(header, ensure_ascii=False).encode("utf-8"),
            )
        seq = 0
        try:
            try:
                async for event in events:
                    event_type, payload, is_done = NatsRenderEventCodec.encode(event)
                    chunk = {
                        "stream_id": original_msg.id,
                        "seq": seq,
                        "event_type": event_type,
                        "payload": payload,
                        "done": is_done,
                    }
                    await self._nc.publish(
                        subject,
                        json.dumps(chunk, ensure_ascii=False).encode("utf-8"),
                    )
                    seq += 1
                # Always publish a terminal sentinel so the adapter's
                # _drain_stream exits cleanly even when the events iterator
                # was empty or the last event did not set is_final=True.
                terminal = {
                    "stream_id": original_msg.id,
                    "seq": seq,
                    "event_type": "stream_end",
                    "payload": {},
                    "done": True,
                }
                await self._nc.publish(
                    subject,
                    json.dumps(terminal, ensure_ascii=False).encode("utf-8"),
                )
            except Exception:
                log.exception(
                    "NatsChannelProxy: NATS publish failed during streaming,"
                    " draining iterator"
                )
                error_envelope = {
                    "type": "stream_error",
                    "stream_id": original_msg.id,
                    "reason": "streaming_exception",
                }
                try:
                    await self._nc.publish(
                        subject,
                        json.dumps(error_envelope, ensure_ascii=False).encode("utf-8"),
                    )
                except Exception:
                    log.warning(
                        "NatsChannelProxy: failed to publish stream_error"
                        " for stream_id=%r",
                        original_msg.id,
                    )
                async for _ in events:
                    pass
        finally:
            # Ensure stream_id is always removed from the tracking set, regardless
            # of success, streaming exception, or publish failure in the except path.
            self._active_streams.discard(original_msg.id)

    async def publish_stream_errors(self, reason: str = "hub_shutdown") -> None:
        """Publish stream_error for all active streams, then clear the set.

        Uses an atomic swap to capture the snapshot and reset the set in one
        step, eliminating the race window between list() and clear() when a
        concurrent exception-path discard fires mid-iteration.
        """
        subject = f"lyra.outbound.{self._platform.value}.{self._bot_id}"
        stream_ids = self._active_streams
        self._active_streams = set()
        for stream_id in stream_ids:
            envelope = {
                "type": "stream_error",
                "stream_id": stream_id,
                "reason": reason,
            }
            try:
                await self._nc.publish(
                    subject,
                    json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
                )
            except Exception:
                log.warning(
                    "NatsChannelProxy: failed to publish stream_error"
                    " for stream_id=%r",
                    stream_id,
                )

    # ------------------------------------------------------------------
    # Audio — not yet implemented (C5)
    # ------------------------------------------------------------------

    async def render_audio(self, msg: OutboundAudio, inbound: InboundMessage) -> None:
        """Publish an outbound audio voice note to NATS."""
        subject = f"lyra.outbound.{self._platform.value}.{self._bot_id}"
        envelope = {
            "type": "audio",
            "stream_id": inbound.id,
            "audio": json.loads(serialize(msg).decode("utf-8")),
            "original_msg": json.loads(serialize(inbound).decode("utf-8")),
        }
        payload = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        await self._nc.publish(subject, payload)

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
            "attachment": json.loads(serialize(msg).decode("utf-8")),
        }
        payload = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        await self._nc.publish(subject, payload)
