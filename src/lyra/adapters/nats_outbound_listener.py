"""NatsOutboundListener — subscribes to lyra.outbound.{platform}.{bot_id} and dispatches
outbound messages back to the adapter (send / send_streaming / render_attachment).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from nats.aio.client import Client as NATS
from nats.aio.msg import Msg

from lyra.core.message import (
    InboundMessage,
    OutboundAttachment,
    OutboundMessage,
    Platform,
)

if TYPE_CHECKING:
    from lyra.core.hub.hub_protocol import ChannelAdapter

log = logging.getLogger(__name__)


class NatsOutboundListener:
    """Subscribes to NATS outbound subject and dispatches to the platform adapter.

    Three envelope types:
    - send:       {"type": "send", "stream_id": ..., "outbound": {...}}
    - attachment: {"type": "attachment", "stream_id": ..., "attachment": {...}}
    - chunk:      {"stream_id": ..., "seq": N, "event_type": ..., "payload": {...},
                   "done": bool}

    Inbound message cache: populated by cache_inbound() before push_to_hub_guarded.
    Used to correlate stream_id → original InboundMessage for reply routing.
    """

    def __init__(
        self,
        nc: NATS,
        platform: Platform,
        bot_id: str,
        adapter: "ChannelAdapter",
    ) -> None:
        self._nc = nc
        self._platform = platform
        self._bot_id = bot_id
        self._adapter = adapter
        self._cache: dict[str, InboundMessage] = {}
        self._stream_queues: dict[str, asyncio.Queue[dict]] = {}
        self._stream_tasks: dict[str, asyncio.Task[None]] = {}
        self._sub: Any = None  # nats.aio.subscription.Subscription | None

    def cache_inbound(self, msg: InboundMessage) -> None:
        """Store msg so it can be retrieved later by stream_id."""
        self._cache[msg.id] = msg

    async def start(self) -> None:
        """Subscribe to the outbound NATS subject."""
        subject = f"lyra.outbound.{self._platform.value}.{self._bot_id}"
        self._sub = await self._nc.subscribe(subject, cb=self._handle)

    async def stop(self) -> None:
        """Unsubscribe from NATS."""
        if self._sub is not None:
            await self._sub.unsubscribe()
            self._sub = None
        # cancel pending stream tasks
        for task in list(self._stream_tasks.values()):
            task.cancel()
        self._stream_tasks.clear()
        self._stream_queues.clear()

    async def _handle(self, msg: Msg) -> None:
        try:
            data = json.loads(msg.data)
        except Exception:
            log.warning("NatsOutboundListener: failed to decode message")
            return
        msg_type = data.get("type")
        if msg_type == "send":
            await self._handle_send(data)
        elif msg_type == "attachment":
            await self._handle_attachment(data)
        elif "stream_id" in data and "seq" in data:
            await self._handle_chunk(data)
        else:
            log.warning("NatsOutboundListener: unknown envelope type=%r", msg_type)

    async def _handle_send(self, data: dict) -> None:
        stream_id = data.get("stream_id")
        original_msg = self._cache.get(stream_id) if stream_id else None
        if stream_id is None or original_msg is None:
            log.warning(
                "NatsOutboundListener: unknown stream_id=%r for send",
                stream_id,
            )
            return
        outbound_data = data.get("outbound")
        if outbound_data is None:
            log.warning("NatsOutboundListener: missing 'outbound' key in send envelope")
            return
        try:
            outbound = OutboundMessage(**outbound_data)
        except Exception:
            log.warning("NatsOutboundListener: failed to deserialize outbound message")
            return
        await self._adapter.send(original_msg, outbound)
        self._cache.pop(stream_id, None)

    async def _handle_attachment(self, data: dict) -> None:
        stream_id = data.get("stream_id")
        original_msg = self._cache.get(stream_id) if stream_id else None
        if stream_id is None or original_msg is None:
            log.warning(
                "NatsOutboundListener: unknown stream_id=%r for attachment", stream_id
            )
            return
        attachment_data = data.get("attachment")
        if attachment_data is None:
            log.warning("NatsOutboundListener: missing 'attachment' key in envelope")
            return
        try:
            attachment = OutboundAttachment(**attachment_data)
        except Exception:
            log.warning("NatsOutboundListener: failed to deserialize attachment")
            return
        await self._adapter.render_attachment(attachment, original_msg)
        self._cache.pop(stream_id, None)

    async def _handle_chunk(self, data: dict) -> None:
        stream_id = data.get("stream_id")
        if stream_id is None:
            log.warning("NatsOutboundListener: chunk envelope missing stream_id")
            return
        q = self._stream_queues.setdefault(stream_id, asyncio.Queue())
        await q.put(data)
        # Launch a drain task on first chunk; subsequent chunks just enqueue
        if stream_id not in self._stream_tasks:
            self._stream_tasks[stream_id] = asyncio.create_task(
                self._drain_stream(stream_id, q)
            )

    async def _drain_stream(self, stream_id: str, q: asyncio.Queue[dict]) -> None:
        """Drain a stream queue and call adapter.send_streaming()."""
        original_msg = self._cache.get(stream_id)
        if original_msg is None:
            log.warning(
                "NatsOutboundListener: unknown stream_id=%r for streaming", stream_id
            )
            # drain queue silently
            while not q.empty():
                await q.get()
            self._stream_tasks.pop(stream_id, None)
            self._stream_queues.pop(stream_id, None)
            return

        from lyra.core.render_events import TextRenderEvent

        async def _events():
            while True:
                chunk = await q.get()
                event_type = chunk.get("event_type", "text")
                payload = chunk.get("payload", {})
                is_done = chunk.get("done", False)
                if event_type == "text":
                    yield TextRenderEvent(**payload)
                if is_done:
                    break

        try:
            await self._adapter.send_streaming(original_msg, _events())
        except Exception:
            log.exception(
                "NatsOutboundListener: send_streaming failed for stream_id=%r",
                stream_id,
            )
        finally:
            self._cache.pop(stream_id, None)
            self._stream_tasks.pop(stream_id, None)
            self._stream_queues.pop(stream_id, None)
