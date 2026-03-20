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

from .circuit_breaker import CircuitBreaker, CircuitRegistry
from .message import (
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    OutboundMessage,
    RoutingContext,
)
from .outbound_errors import (
    _CIRCUIT_NOTIFY_DEBOUNCE,
    _CIRCUIT_OPEN_MSG,
    _ITEM,
    _SCOPE_REAP_THRESHOLD,
    _SEND_ERROR_MSG,
    _is_transient_error,
    try_notify_user,
)

if TYPE_CHECKING:
    from lyra.core.hub import ChannelAdapter

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
        chunks: AsyncIterator[str],
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

    async def _dispatch_item(self, item: _ITEM) -> None:  # noqa: C901, PLR0915 — outbound dispatch with circuit-breaker, routing verification, kind branching, and error classification
        """Dispatch a single item (routing check, circuit, retry, send, callback)."""
        kind = item[0]
        if kind == "streaming":
            _, msg, payload, outbound = item
        elif kind in (
            "send",
            "audio",
            "audio_stream",
            "voice_stream",
            "attachment",
        ):
            _, msg, payload = item
            outbound = None
        else:
            log.error(
                '{"event": "unknown_kind", "kind": "%s", "action": "skipped"}',
                kind,
            )
            return
        # Verify routing context matches this dispatcher.
        # "send": check payload (OutboundMessage) routing.
        # "streaming": check outbound routing if provided.
        # "audio"/"audio_stream"/"attachment": use msg (InboundMessage) routing.
        # "voice_stream": synthesize from msg when routing absent — TTS callers
        #     may omit explicit routing; synthesizing ensures the routing check
        #     validates platform+bot_id without requiring callers to set it.
        if kind == "send":
            _routing = getattr(payload, "routing", None) or msg.routing
        elif kind == "voice_stream":
            _routing = msg.routing or RoutingContext(
                platform=msg.platform,
                bot_id=msg.bot_id,
                scope_id=msg.scope_id,
            )
        elif kind in ("audio", "audio_stream", "attachment"):
            _routing = msg.routing
        else:
            _routing = outbound.routing if outbound is not None else msg.routing
        if not self._verify_routing(_routing):
            if kind in ("streaming", "audio_stream", "voice_stream"):
                async for _ in payload:
                    pass
            return

        if self._circuit is not None and self._circuit.is_open():
            log.warning(
                '{"event": "%s_circuit_open", "action": "%s", "dropped": true}',
                self._platform_name,
                kind,
            )
            # Drain streaming iterator to prevent generator leaks
            if kind in ("streaming", "audio_stream", "voice_stream"):
                async for _ in payload:
                    pass
            _cb_out = payload if kind == "send" else outbound
            if _cb_out is not None:
                _cb_out.metadata["reply_message_id"] = None
                _cb = _cb_out.metadata.pop("_on_dispatched", None)
                if callable(_cb):
                    _cb(_cb_out)
            # Fix 3: notify user once per chat per debounce window
            scope_key = msg.scope_id or msg.id
            now = time.monotonic()
            last_ts = self._circuit_notify_ts.get(scope_key, 0.0)
            if now - last_ts >= _CIRCUIT_NOTIFY_DEBOUNCE:
                self._circuit_notify_ts[scope_key] = now
                await self._try_notify_user(msg, _CIRCUIT_OPEN_MSG)
            return

        # Fix 1: retry loop with exponential backoff for transient errors
        _backoff_delays = (1.0, 2.0, 4.0)
        _max_attempts = 1 + len(_backoff_delays)  # 1 initial + 3 retries = 4 total
        _last_exc: Exception | None = None
        _attempt = 0
        while _attempt < _max_attempts:
            try:
                if kind == "send":
                    await self._adapter.send(msg, payload)
                elif kind == "audio":
                    await self._adapter.render_audio(payload, msg)
                elif kind == "audio_stream":
                    await self._adapter.render_audio_stream(payload, msg)
                elif kind == "voice_stream":
                    await self._adapter.render_voice_stream(payload, msg)
                elif kind == "attachment":
                    await self._adapter.render_attachment(payload, msg)
                else:
                    # Drain superseded streaming (cancel-in-flight) silently.
                    if outbound is not None and outbound.metadata.get("_superseded"):
                        async for _ in payload:
                            pass
                        break
                    await self._adapter.send_streaming(msg, payload, outbound)
                if self._circuit is not None:
                    self._circuit.record_success()
                _last_exc = None
                break  # success
            except BaseException as exc:
                if not isinstance(exc, Exception):
                    # Re-raise CancelledError / KeyboardInterrupt immediately
                    if self._circuit is not None:
                        self._circuit.record_failure()
                    raise
                is_transient = _is_transient_error(exc)
                retry_possible = (
                    is_transient
                    and _attempt + 1 < _max_attempts
                    and kind == "send"  # streaming iterators cannot be replayed
                )
                if retry_possible:
                    delay = _backoff_delays[_attempt]
                    log.warning(
                        "OutboundDispatcher[%s] delivery attempt %d failed"
                        " (kind=%s, transient), retrying in %.0fs: %s",
                        self._platform_name,
                        _attempt + 1,
                        kind,
                        delay,
                        exc,
                    )
                    _last_exc = exc
                    _attempt += 1
                    await asyncio.sleep(delay)
                else:
                    _last_exc = exc
                    _attempt = _max_attempts  # exit loop
                    break

        # Invoke dispatched callback after send (#316).
        # "send" → payload is the OutboundMessage; else → outbound.
        _out = payload if kind == "send" else outbound
        if _out is not None:
            _dispatched = _out.metadata.pop("_on_dispatched", None)
            if callable(_dispatched):
                _dispatched(_out)

        if _last_exc is not None:
            exc = _last_exc
            if self._circuit is not None:
                self._circuit.record_failure()
            # Drain iterator to prevent generator leaks on delivery failure
            if kind in ("streaming", "audio_stream", "voice_stream"):
                async for _ in payload:
                    pass
            log.error(
                "OutboundDispatcher[%s] delivery failed (kind=%s): %s",
                self._platform_name,
                kind,
                exc,
                exc_info=exc,
            )
            # Fix 1: send user notification after all retries exhausted
            if kind in ("send", "streaming"):
                await self._try_notify_user(msg, _SEND_ERROR_MSG)

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
