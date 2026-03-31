"""NatsChannelProxy — ChannelAdapter implementation over NATS.

Publishes outbound messages to NATS subjects instead of calling platform SDKs.
Used by the standalone Hub process to dispatch responses to remote adapters.
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from nats.aio.client import Client as NATS

from lyra.core.message import (
    InboundAudio,
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    OutboundMessage,
    Platform,
)
from lyra.core.render_events import RenderEvent, TextRenderEvent
from lyra.core.trust import TrustLevel
from lyra.nats._serialize import serialize

log = logging.getLogger(__name__)


class NatsChannelProxy:
    """ChannelAdapter that publishes outbound messages to NATS subjects.

    Implements the ChannelAdapter Protocol from hub_protocol.py.
    Inbound normalization is not supported — raises NotImplementedError.
    Audio-over-NATS (C5) is not yet implemented — audio methods log a warning
    and drain iterators without publishing.
    """

    def __init__(self, nc: NATS, platform: Platform, bot_id: str) -> None:
        """Store nc, platform, bot_id. No I/O."""
        self._nc = nc
        self._platform = platform
        self._bot_id = bot_id

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
    ) -> InboundAudio:
        raise NotImplementedError("NatsChannelProxy does not normalize inbound audio")

    # ------------------------------------------------------------------
    # Outbound dispatch
    # ------------------------------------------------------------------

    async def send(
        self, original_msg: InboundMessage, outbound: OutboundMessage
    ) -> None:
        """Publish an outbound text message to NATS."""
        subject = f"lyra.outbound.{self._platform.value}.{self._bot_id}"
        envelope = {
            "type": "outbound",
            "msg_id": original_msg.id,
            "outbound": json.loads(serialize(outbound).decode("utf-8")),
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
        subject = (
            f"lyra.outbound.stream.{self._platform.value}.{self._bot_id}.{original_msg.id}"
        )
        seq = 0
        try:
            async for event in events:
                event_type = (
                    "text" if isinstance(event, TextRenderEvent) else "tool_summary"
                )
                chunk = {
                    "type": "stream_chunk",
                    "msg_id": original_msg.id,
                    "seq": seq,
                    "event_type": event_type,
                    "payload": json.loads(serialize(event).decode("utf-8")),
                    "done": (
                        getattr(event, "is_final", False)
                        or getattr(event, "is_complete", False)
                    ),
                }
                await self._nc.publish(
                    subject,
                    json.dumps(chunk, ensure_ascii=False).encode("utf-8"),
                )
                seq += 1
        except Exception:
            log.exception(
                "NatsChannelProxy: NATS publish failed during streaming,"
                " draining iterator"
            )
            async for _ in events:
                pass

    # ------------------------------------------------------------------
    # Audio — not yet implemented (C5)
    # ------------------------------------------------------------------

    async def render_audio(self, msg: OutboundAudio, inbound: InboundMessage) -> None:
        """Audio-over-NATS not implemented (C5) — drops audio silently."""
        log.warning(
            "audio-over-NATS not implemented (C5) — dropping audio for msg %s",
            inbound.id,
        )

    async def render_audio_stream(
        self,
        chunks: AsyncIterator[OutboundAudioChunk],
        inbound: InboundMessage,
    ) -> None:
        """Audio streaming not implemented (C5) — drains iterator."""
        log.warning(
            "audio-over-NATS not implemented (C5) — dropping audio stream for msg %s",
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
            "audio-over-NATS not implemented (C5) — dropping voice stream for msg %s",
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
            "msg_id": inbound.id,
            "attachment": json.loads(serialize(msg).decode("utf-8")),
        }
        payload = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        await self._nc.publish(subject, payload)
