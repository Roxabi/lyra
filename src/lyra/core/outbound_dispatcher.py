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

from anthropic import APIError as AnthropicAPIError

from .circuit_breaker import CircuitBreaker, CircuitRegistry
from .message import InboundMessage, Response

if TYPE_CHECKING:
    from lyra.core.hub import ChannelAdapter

log = logging.getLogger(__name__)

# Queue item: (kind: "send"|"streaming", msg: InboundMessage, payload)
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
    ) -> None:
        self._platform_name = platform_name
        self._adapter = adapter
        self._circuit = circuit
        self._circuit_registry = circuit_registry
        self._queue: asyncio.Queue[_ITEM] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None

    def enqueue(self, msg: InboundMessage, response: Response) -> None:
        """Enqueue a non-streaming response for delivery.

        Fire-and-forget: returns immediately. The worker task delivers asynchronously.
        """
        self._queue.put_nowait(("send", msg, response))

    def enqueue_streaming(
        self, msg: InboundMessage, chunks: AsyncIterator[str]
    ) -> None:
        """Enqueue a streaming response for delivery.

        Fire-and-forget: returns immediately. The worker task consumes the
        iterator and calls adapter.send_streaming() asynchronously.
        """
        self._queue.put_nowait(("streaming", msg, chunks))

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

    async def _worker_loop(self) -> None:
        """Consume the outbound queue indefinitely."""
        while True:
            item = await self._queue.get()
            try:
                kind, msg, payload = item
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
                    continue

                try:
                    if kind == "send":
                        await self._adapter.send(msg, payload)
                    else:
                        await self._adapter.send_streaming(msg, payload)
                    if self._circuit is not None:
                        self._circuit.record_success()
                except BaseException as exc:
                    if self._circuit is not None:
                        self._circuit.record_failure()
                    # Record Anthropic CB failure for Anthropic API errors
                    if isinstance(exc, AnthropicAPIError) and (
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
