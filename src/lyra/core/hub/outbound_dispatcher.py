"""OutboundDispatcher — per-platform outbound queue with circuit breaker ownership.

Each platform adapter gets its own OutboundDispatcher that owns the platform
circuit breaker check. A background asyncio.Task drains the queue, checks the CB,
calls the adapter, and records success/failure.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from ..circuit_breaker import CircuitBreaker, CircuitRegistry
from ..messaging.message import (
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    OutboundMessage,
    RoutingContext,
)
from ._dispatch import dispatch_outbound_item
from .outbound_errors import (
    _CIRCUIT_NOTIFY_DEBOUNCE,
    _ITEM,
    _NOTIFY_TS_REAP_THRESHOLD,
    _SCOPE_REAP_THRESHOLD,
    try_notify_user,
)

if TYPE_CHECKING:
    from lyra.core.hub import ChannelAdapter
    from lyra.core.messaging.render_events import RenderEvent

log = logging.getLogger(__name__)


class OutboundDispatcher:
    """Queue-backed outbound worker that owns the platform circuit breaker.

    Create with platform_name/adapter/circuit, then call start()/stop().
    """

    def __init__(
        self,
        platform_name: str,
        adapter: "ChannelAdapter",
        circuit: CircuitBreaker | None = None,
        circuit_registry: CircuitRegistry | None = None,
        bot_id: str = "main",
    ) -> None:
        self._platform_name = platform_name
        self._bot_id = bot_id
        self._adapter = adapter
        self._circuit = circuit
        self._circuit_registry = circuit_registry
        self._queue: asyncio.Queue[_ITEM] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        # Per-chat last circuit-open notification timestamp for debouncing (Fix 3)
        self._circuit_notify_ts: dict[str, float] = {}
        self._scope_locks: dict[str, asyncio.Lock] = {}
        self._scope_tasks: set[asyncio.Task[None]] = set()

    def _verify_routing(self, routing: RoutingContext | None) -> bool:
        """True if routing matches this dispatcher (or is absent); False on mismatch."""
        if routing is None:
            return True
        if routing.platform != self._platform_name:
            log.error(
                "routing mismatch: expected platform=%r, got %r — message dropped",
                self._platform_name,
                routing.platform,
            )
            return False
        if routing.bot_id != self._bot_id:
            log.error(
                "routing mismatch: expected bot_id=%r, got %r — message dropped",
                self._bot_id,
                routing.bot_id,
            )
            return False
        return True

    def enqueue(self, msg: InboundMessage, response: OutboundMessage) -> None:
        """Enqueue a non-streaming response for delivery (fire-and-forget)."""
        self._queue.put_nowait(("send", msg, response))

    def enqueue_streaming(
        self,
        msg: InboundMessage,
        chunks: AsyncIterator[RenderEvent],
        outbound: OutboundMessage | None = None,
    ) -> None:
        """Enqueue a streaming response for delivery (fire-and-forget)."""
        self._queue.put_nowait(("streaming", msg, chunks, outbound))

    def enqueue_audio(self, inbound: InboundMessage, audio: OutboundAudio) -> None:
        """Enqueue an audio response for delivery (fire-and-forget)."""
        self._queue.put_nowait(("audio", inbound, audio))

    def enqueue_audio_stream(
        self,
        inbound: InboundMessage,
        chunks: AsyncIterator[OutboundAudioChunk],
    ) -> None:
        """Enqueue a streaming audio response for delivery (fire-and-forget)."""
        self._queue.put_nowait(("audio_stream", inbound, chunks))

    def enqueue_voice_stream(
        self,
        inbound: InboundMessage,
        chunks: AsyncIterator[OutboundAudioChunk],
    ) -> None:
        """Enqueue a voice stream for delivery (fire-and-forget)."""
        self._queue.put_nowait(("voice_stream", inbound, chunks))

    def enqueue_attachment(
        self, inbound: InboundMessage, attachment: OutboundAttachment
    ) -> None:
        """Enqueue an attachment for delivery (fire-and-forget)."""
        self._queue.put_nowait(("attachment", inbound, attachment))

    async def start(self) -> None:
        """Spawn the background worker task."""
        self._worker = asyncio.create_task(
            self._worker_loop(),
            name=f"outbound-dispatcher-{self._platform_name}",
        )

    async def stop(self) -> None:
        """Cancel worker, drain in-flight scope tasks, reap idle locks."""
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None
        if self._scope_tasks:
            await asyncio.gather(*self._scope_tasks, return_exceptions=True)
        self._reap_scope_locks()

    def qsize(self) -> int:
        """Return the number of pending items in the outbound queue."""
        return self._queue.qsize()

    async def _try_notify_user(self, msg: InboundMessage, text: str) -> None:
        """Delegate to module-level helper in outbound_errors."""
        await try_notify_user(self._platform_name, self._adapter, msg, text)

    async def _dispatch_item(self, item: _ITEM) -> None:
        """Dispatch a single item (routing check, circuit, retry, send, callback)."""
        await dispatch_outbound_item(
            self._platform_name,
            self._adapter,
            self._circuit,
            item,
            self._verify_routing,
            self._try_notify_user,
            self._circuit_notify_ts,
        )

    async def _worker_loop(self) -> None:
        """Fan-out: dequeue → lock per scope → create_task → task_done immediately."""
        while True:
            item = await self._queue.get()
            try:
                msg = item[1]
                if not isinstance(msg, InboundMessage):
                    log.error(
                        "OutboundDispatcher[%s]: malformed queue item"
                        " (expected InboundMessage at [1], got %r) — skipped",
                        self._platform_name,
                        type(msg),
                    )
                    continue
                scope_id = msg.scope_id or msg.id
                lock = self._scope_locks.setdefault(scope_id, asyncio.Lock())
                task = asyncio.create_task(
                    self._process_locked(lock, item),
                    name=f"outbound-scope-{self._platform_name}-{scope_id}",
                )

                def _on_task_done(t: asyncio.Task[None]) -> None:
                    self._scope_tasks.discard(t)
                    if not t.cancelled() and (exc := t.exception()) is not None:
                        log.error(
                            "OutboundDispatcher[%s] scope task exception: %s",
                            self._platform_name,
                            exc,
                            exc_info=exc,
                        )

                task.add_done_callback(_on_task_done)
                self._scope_tasks.add(task)
                if len(self._scope_locks) > _SCOPE_REAP_THRESHOLD:
                    self._reap_scope_locks()
                if len(self._circuit_notify_ts) > _NOTIFY_TS_REAP_THRESHOLD:
                    self._reap_circuit_notify_ts()
            finally:
                self._queue.task_done()

    async def _process_locked(self, lock: asyncio.Lock, item: _ITEM) -> None:
        """Acquire scope lock, then dispatch. Serializes same-scope messages."""
        async with lock:
            await self._dispatch_item(item)

    def _reap_scope_locks(self) -> None:
        """Remove idle scope locks (not currently held)."""
        self._scope_locks = {
            scope_id: lock
            for scope_id, lock in self._scope_locks.items()
            if lock.locked()
        }

    def _reap_circuit_notify_ts(self) -> None:
        """Remove debounce timestamps older than the debounce window."""
        now = time.monotonic()
        self._circuit_notify_ts = {
            scope_key: ts
            for scope_key, ts in self._circuit_notify_ts.items()
            if now - ts < _CIRCUIT_NOTIFY_DEBOUNCE
        }
