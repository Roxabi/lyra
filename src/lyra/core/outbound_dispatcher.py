"""OutboundDispatcher — per-platform outbound queue with circuit breaker ownership.

Each platform adapter gets its own OutboundDispatcher. The dispatcher owns the
platform circuit breaker check: adapter.send() and adapter.send_streaming() no
longer check or record the platform CB — the dispatcher worker loop is the single
owner.

A secondary asyncio.Task drains the outbound queue, checks the circuit breaker,
calls the adapter, and records success/failure.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from lyra.errors import ProviderError

from .circuit_breaker import CircuitBreaker, CircuitRegistry
from .message import (
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundMessage,
    RoutingContext,
)

if TYPE_CHECKING:
    from lyra.core.hub import ChannelAdapter

log = logging.getLogger(__name__)

# Queue item: (kind, msg, payload) for send;
# (kind, msg, chunks, outbound) for streaming; (kind, inbound, audio) for audio;
# (kind, inbound, attachment) for attachment
_ITEM = tuple


class OutboundDispatcher:
    """Queue-backed outbound worker that owns the platform circuit breaker.

    Lifecycle:
        dispatcher = OutboundDispatcher(
            platform_name="telegram",
            adapter=tg_adapter,
            circuit=registry.get("telegram"),
            circuit_registry=registry,  # for Anthropic CB recording
        )
        await dispatcher.start()
        ...
        await dispatcher.stop()
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

    def _verify_routing(self, routing: RoutingContext | None) -> bool:
        """Verify RoutingContext matches this dispatcher's platform + bot_id.

        Returns True if routing is valid (or absent — backward compat).
        Returns False and logs an error if there is a mismatch.
        """
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
        """Enqueue a non-streaming response for delivery.

        Fire-and-forget: returns immediately. The worker task delivers asynchronously.
        """
        self._queue.put_nowait(("send", msg, response))

    def enqueue_streaming(
        self,
        msg: InboundMessage,
        chunks: AsyncIterator[str],
        outbound: OutboundMessage | None = None,
    ) -> None:
        """Enqueue a streaming response for delivery.

        Fire-and-forget: returns immediately. The worker task consumes the
        iterator and calls adapter.send_streaming() asynchronously.
        """
        self._queue.put_nowait(("streaming", msg, chunks, outbound))

    def enqueue_audio(
        self, inbound: InboundMessage, audio: OutboundAudio
    ) -> None:
        """Enqueue an audio response for delivery.

        Fire-and-forget: returns immediately. The worker task calls
        adapter.render_audio() asynchronously with CB ownership.
        """
        self._queue.put_nowait(("audio", inbound, audio))

    def enqueue_attachment(
        self, inbound: InboundMessage, attachment: OutboundAttachment
    ) -> None:
        """Enqueue an attachment for delivery.

        Fire-and-forget: returns immediately. The worker task calls
        adapter.render_attachment() asynchronously with CB ownership.
        """
        self._queue.put_nowait(("attachment", inbound, attachment))

    async def start(self) -> None:
        """Spawn the background worker task."""
        self._worker = asyncio.create_task(
            self._worker_loop(),
            name=f"outbound-dispatcher-{self._platform_name}",
        )

    async def stop(self) -> None:
        """Cancel the worker task and wait for it to finish."""
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None

    def qsize(self) -> int:
        """Return the number of pending items in the outbound queue."""
        return self._queue.qsize()

    async def _worker_loop(self) -> None:  # noqa: C901, PLR0915 — outbound dispatch with circuit-breaker, routing verification, kind branching, and error classification
        """Consume the outbound queue indefinitely."""
        while True:
            item = await self._queue.get()
            try:
                kind = item[0]
                if kind == "streaming":
                    _, msg, payload, outbound = item
                elif kind == "audio":
                    _, msg, payload = item
                    outbound = None
                else:
                    _, msg, payload = item
                    outbound = None
                # Verify routing context matches this dispatcher.
                # "send": check payload (OutboundMessage) routing.
                # "streaming": check outbound routing if provided.
                # "audio"/"attachment": always use msg (InboundMessage) routing.
                if kind == "send":
                    _routing = getattr(payload, "routing", None) or msg.routing
                elif kind in ("audio", "attachment"):
                    _routing = msg.routing
                else:
                    _routing = (
                        outbound.routing
                        if outbound is not None
                        else msg.routing
                    )
                if not self._verify_routing(_routing):
                    if kind == "streaming":
                        async for _ in payload:
                            pass
                    continue

                if self._circuit is not None and self._circuit.is_open():
                    log.warning(
                        '{"event": "%s_circuit_open", "action": "%s", "dropped": true}',
                        self._platform_name,
                        kind,
                    )
                    # Drain streaming iterator to prevent generator leaks
                    if kind == "streaming":
                        async for _ in payload:
                            pass
                    if outbound is not None:
                        outbound.metadata["reply_message_id"] = None
                    continue

                try:
                    if kind == "send":
                        await self._adapter.send(msg, payload)
                    elif kind == "audio":
                        await self._adapter.render_audio(payload, msg)
                    elif kind == "attachment":
                        await self._adapter.render_attachment(payload, msg)
                    else:
                        await self._adapter.send_streaming(msg, payload, outbound)
                    if self._circuit is not None:
                        self._circuit.record_success()
                except BaseException as exc:
                    if self._circuit is not None:
                        self._circuit.record_failure()
                    # Record provider CB failure for LLM provider errors
                    if isinstance(exc, ProviderError) and (
                        self._circuit_registry is not None
                    ):
                        ant_cb = self._circuit_registry.get("anthropic")
                        if ant_cb is not None:
                            ant_cb.record_failure()
                    if not isinstance(exc, Exception):
                        raise  # re-raise CancelledError / KeyboardInterrupt
                    log.exception(
                        "OutboundDispatcher[%s] delivery failed (kind=%s): %s",
                        self._platform_name,
                        kind,
                        exc,
                    )
            finally:
                self._queue.task_done()
