"""NatsOutboundListener — NATS outbound subject → adapter dispatch."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from nats.aio.client import Client as NATS

from lyra.adapters._inbound_cache import InboundCache
from lyra.adapters.nats_envelope_handlers import handle_raw_message
from lyra.adapters.nats_stream_decoder import (
    decode_stream_events,
    run_reaper_loop,
)
from lyra.adapters.nats_stream_decoder import (
    handle_stream_error as _handle_stream_error_impl,
)
from lyra.core.message import InboundMessage, OutboundMessage, Platform
from lyra.nats.type_registry import TYPE_REGISTRY_RESOLVER
from roxabi_nats._serialize import _TypeHintResolver
from roxabi_nats._serialize import deserialize_dict as _deserialize_dict
from roxabi_nats._validate import validate_nats_token

if TYPE_CHECKING:
    from lyra.core.hub.hub_protocol import ChannelAdapter

log = logging.getLogger(__name__)

_MAX_STREAMS = 100
_MAX_QUEUE_SIZE = 256


class NatsOutboundListener:
    """NATS outbound subscriber → adapter dispatch (send/attachment/stream)."""

    def __init__(  # noqa: PLR0913
        self,
        nc: NATS,
        platform: Platform,
        bot_id: str,
        adapter: "ChannelAdapter",
        *,
        queue_group: str = "",
        resolver: _TypeHintResolver = TYPE_REGISTRY_RESOLVER,
    ) -> None:
        validate_nats_token(queue_group, kind="queue_group", allow_empty=True)
        self._nc = nc
        self._platform = platform
        self._bot_id = bot_id
        self._adapter = adapter
        self._queue_group = queue_group
        self._resolver = resolver
        self._subject = f"lyra.outbound.{platform.value}.{bot_id}"
        self._cache = InboundCache(resolver=resolver)
        self._stream_queues: dict[str, asyncio.Queue[dict]] = {}
        self._stream_tasks: dict[str, asyncio.Task[None]] = {}
        self._stream_outbound: dict[str, OutboundMessage] = {}
        self._stream_original_msgs: dict[str, dict] = {}
        self._sub: Any = None  # nats.aio.subscription.Subscription | None
        self._reaper_task: asyncio.Task[None] | None = None
        # OrderedDict[stream_id, ts]: FIFO eviction + TTL reaping. See #569, #570.
        self._terminated_streams: OrderedDict[str, float] = OrderedDict()
        self._version_mismatch_drops: dict[str, int] = {}

    def cache_inbound(self, msg: InboundMessage) -> None:
        """Store msg so it can be retrieved later by stream_id."""
        self._cache.put(msg)

    def version_mismatch_count(self, envelope_name: str) -> int:
        """Cumulative drops for *envelope_name* — summed across all check kinds."""
        prefix = f"{envelope_name}:"
        return sum(
            count
            for key, count in self._version_mismatch_drops.items()
            if key.startswith(prefix)
        )

    async def start(self) -> None:
        """Subscribe to the outbound NATS subject."""
        self._sub = await self._nc.subscribe(
            self._subject, queue=self._queue_group, cb=self._handle
        )
        self._reaper_task = asyncio.create_task(run_reaper_loop(self))

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
        self._stream_original_msgs.clear()
        self._stream_outbound.clear()

    async def _handle(self, msg: Any) -> None:
        """Dispatch raw NATS message to envelope handlers."""
        await handle_raw_message(self, msg, resolver=self._resolver)

    def _handle_stream_error(self, data: dict) -> None:
        """Dispatch to the stream_error handler in nats_stream_decoder."""
        _handle_stream_error_impl(self, data)

    async def _drain_stream(self, stream_id: str, q: asyncio.Queue[dict]) -> None:
        """Drain a stream queue and call adapter.send_streaming()."""
        original_msg = self._cache.get(stream_id)
        if original_msg is None:
            raw = self._stream_original_msgs.pop(stream_id, None)
            if raw is not None:
                try:
                    original_msg = _deserialize_dict(
                        raw, InboundMessage, resolver=self._resolver
                    )
                except Exception:
                    log.warning(
                        "NatsOutboundListener: bad embedded original_msg"
                        " for stream_id=%r",
                        stream_id,
                    )
                else:
                    log.debug(
                        "NatsOutboundListener: cache miss, recovered"
                        " original_msg from embedded payload"
                        " for stream_id=%r",
                        stream_id,
                    )
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
                original_msg,
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
            self._cache.pop(stream_id)
            self._stream_tasks.pop(stream_id, None)
            self._stream_queues.pop(stream_id, None)
            self._terminated_streams.pop(stream_id, None)
            self._stream_original_msgs.pop(stream_id, None)
