"""NatsOutboundListener — NATS outbound subject → adapter dispatch."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any, cast

from nats.aio.client import Client as NATS
from nats.aio.msg import Msg

from lyra.adapters.nats_stream_decoder import (
    decode_stream_events,
)
from lyra.adapters.nats_stream_decoder import (
    handle_stream_error as _handle_stream_error_impl,
)
from lyra.core.message import (
    InboundAudio,
    InboundMessage,
    OutboundAttachment,
    OutboundMessage,
    Platform,
)
from lyra.nats._serialize import deserialize_dict as _deserialize_dict
from lyra.nats._validate import validate_nats_token

if TYPE_CHECKING:
    from lyra.core.hub.hub_protocol import ChannelAdapter

log = logging.getLogger(__name__)

_MAX_CACHE_SIZE = 500
_MAX_STREAMS = 100
_MAX_QUEUE_SIZE = 256
_CACHE_TTL_SECONDS = 120
_REAPER_INTERVAL_SECONDS = 30


class NatsOutboundListener:
    """NATS outbound subscriber → adapter dispatch (send/attachment/stream)."""

    def __init__(
        self,
        nc: NATS,
        platform: Platform,
        bot_id: str,
        adapter: "ChannelAdapter",
        *,
        queue_group: str = "",
    ) -> None:
        validate_nats_token(queue_group, kind="queue_group", allow_empty=True)
        self._nc = nc
        self._platform = platform
        self._bot_id = bot_id
        self._adapter = adapter
        self._queue_group = queue_group
        self._cache: dict[str, InboundMessage | InboundAudio] = {}
        self._cache_ts: dict[str, float] = {}
        self._stream_queues: dict[str, asyncio.Queue[dict]] = {}
        self._stream_tasks: dict[str, asyncio.Task[None]] = {}
        self._stream_outbound: dict[str, OutboundMessage] = {}
        self._sub: Any = None  # nats.aio.subscription.Subscription | None
        self._reaper_task: asyncio.Task[None] | None = None
        self._terminated_streams: set[str] = set()
        self._version_mismatch_drops: dict[str, int] = {}

    def cache_inbound(self, msg: InboundMessage | InboundAudio) -> None:
        """Store msg so it can be retrieved later by stream_id."""
        if len(self._cache) >= _MAX_CACHE_SIZE:
            oldest = next(iter(self._cache))
            self._cache.pop(oldest)
            self._cache_ts.pop(oldest, None)
            log.warning(
                "NatsOutboundListener: _cache full (%d), evicted %r",
                _MAX_CACHE_SIZE, oldest,
            )
        self._cache[msg.id] = msg
        self._cache_ts[msg.id] = time.monotonic()

    def version_mismatch_count(self, envelope_name: str) -> int:
        """Cumulative drops for *envelope_name* (schema version mismatches)."""
        return self._version_mismatch_drops.get(envelope_name, 0)

    async def start(self) -> None:
        """Subscribe to the outbound NATS subject."""
        subject = f"lyra.outbound.{self._platform.value}.{self._bot_id}"
        self._sub = await self._nc.subscribe(
            subject, queue=self._queue_group, cb=self._handle
        )
        self._reaper_task = asyncio.create_task(self._reap_stale())

    async def stop(self) -> None:
        """Unsubscribe from NATS."""
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper_task
            self._reaper_task = None
        if self._sub is not None:
            await self._sub.unsubscribe()
            self._sub = None
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
        elif msg_type == "stream_start":
            self._handle_stream_start(data)
        elif msg_type == "stream_error":
            self._handle_stream_error(data)
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
            outbound = _deserialize_dict(outbound_data, OutboundMessage)
        except Exception:
            log.warning("NatsOutboundListener: failed to deserialize outbound message")
            return
        await self._adapter.send(cast(InboundMessage, original_msg), outbound)
        self._cache.pop(stream_id, None)
        self._cache_ts.pop(stream_id, None)

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
            attachment = _deserialize_dict(attachment_data, OutboundAttachment)
        except Exception:
            log.warning("NatsOutboundListener: failed to deserialize attachment")
            return
        await self._adapter.render_attachment(
            attachment, cast(InboundMessage, original_msg)
        )
        self._cache.pop(stream_id, None)
        self._cache_ts.pop(stream_id, None)

    def _handle_stream_start(self, data: dict) -> None:
        """Store outbound metadata for a streaming session."""
        stream_id = data.get("stream_id")
        outbound_data = data.get("outbound")
        if stream_id is None or outbound_data is None:
            return
        if len(self._stream_outbound) >= _MAX_STREAMS:
            log.warning(
                "NatsOutboundListener: _stream_outbound full"
                " (%d entries), dropping stream_id=%r",
                _MAX_STREAMS,
                stream_id,
            )
            return
        try:
            self._stream_outbound[stream_id] = _deserialize_dict(
                outbound_data, OutboundMessage
            )
        except Exception:
            log.warning("NatsOutboundListener: failed to deserialize stream outbound")

    def _handle_stream_error(self, data: dict) -> None:
        """Dispatch to the stream_error handler in _nats_outbound_stream."""
        _handle_stream_error_impl(self, data)

    async def _handle_chunk(self, data: dict) -> None:
        stream_id = data.get("stream_id")
        if stream_id is None:
            log.warning("NatsOutboundListener: chunk envelope missing stream_id")
            return
        if stream_id in self._terminated_streams:
            log.warning(
                "NatsOutboundListener: chunk rejected for terminated"
                " stream_id=%r",
                stream_id,
            )
            return
        at_limit = len(self._stream_tasks) >= _MAX_STREAMS
        if stream_id not in self._stream_tasks and at_limit:
            log.warning(
                "NatsOutboundListener: _stream_tasks full"
                " (%d streams), dropping stream_id=%r",
                _MAX_STREAMS,
                stream_id,
            )
            return
        q = self._stream_queues.setdefault(
            stream_id, asyncio.Queue(maxsize=_MAX_QUEUE_SIZE),
        )
        if stream_id in self._cache_ts:
            self._cache_ts[stream_id] = time.monotonic()
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            log.warning(
                "NatsOutboundListener: stream queue full"
                " for stream_id=%r, dropping chunk",
                stream_id,
            )
            return
        if stream_id not in self._stream_tasks:
            self._stream_tasks[stream_id] = asyncio.create_task(
                self._drain_stream(stream_id, q)
            )

    async def _drain_stream(self, stream_id: str, q: asyncio.Queue[dict]) -> None:
        """Drain a stream queue and call adapter.send_streaming()."""
        original_msg = self._cache.get(stream_id)
        if original_msg is None:
            drained = 0
            while not q.empty():
                await q.get()
                drained += 1
            log.warning(
                "NatsOutboundListener: drained %d chunk(s) for unknown stream_id=%r",
                drained,
                stream_id,
            )
            self._stream_tasks.pop(stream_id, None)
            self._stream_queues.pop(stream_id, None)
            return

        outbound = self._stream_outbound.pop(stream_id, None)
        try:
            # counter= wires drop tracking; read via version_mismatch_count().
            await self._adapter.send_streaming(
                cast(InboundMessage, original_msg),
                decode_stream_events(
                    stream_id, q, counter=self._version_mismatch_drops
                ),
                outbound,
            )
        except Exception:
            log.exception(
                "NatsOutboundListener: send_streaming failed for stream_id=%r",
                stream_id,
            )
        finally:
            self._cache.pop(stream_id, None)
            self._cache_ts.pop(stream_id, None)
            self._stream_tasks.pop(stream_id, None)
            self._stream_queues.pop(stream_id, None)
            self._terminated_streams.discard(stream_id)

    async def _reap_stale(self) -> None:
        """Periodically evict cache entries that have exceeded the TTL."""
        while True:
            await asyncio.sleep(_REAPER_INTERVAL_SECONDS)
            now = time.monotonic()
            stale = [
                sid
                for sid, ts in list(self._cache_ts.items())
                if now - ts > _CACHE_TTL_SECONDS
            ]
            for stream_id in stale:
                log.warning(
                    "NatsOutboundListener: evicting stale stream_id=%r (TTL exceeded)",
                    stream_id,
                )
                self._cache.pop(stream_id, None)
                self._cache_ts.pop(stream_id, None)
                self._stream_outbound.pop(stream_id, None)
                task = self._stream_tasks.pop(stream_id, None)
                if task is not None:
                    task.cancel()
                self._stream_queues.pop(stream_id, None)
