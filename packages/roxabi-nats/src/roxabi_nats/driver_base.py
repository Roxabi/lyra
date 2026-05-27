"""NatsDriverBase — reusable hub-side NATS request-dispatch base.

Provides:
- Heartbeat subscription and worker-freshness tracking
- Ephemeral-inbox streaming (_dict_stream_gen)
- Simple request-reply (_request)

Subclass this to build hub-side drivers (e.g. CliNatsDriver).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import nats.errors

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS
    from nats.aio.subscription import Subscription

__all__ = ["NatsDriverBase", "WorkerUnavailableError"]
log = logging.getLogger(__name__)


class WorkerUnavailableError(RuntimeError):
    """Raised when the worker's heartbeat stops during an active stream."""


class NatsDriverBase:
    HB_TTL: float = 30.0
    # Subclasses set this to the heartbeat subject they subscribe to.
    # NatsDriverBase uses it in start()/stop() only if set.
    HB_SUBJECT: str = ""
    # Absolute upper bound on a single _dict_stream_gen call. Backstop against a
    # hung worker that keeps emitting keepalive chunks forever; the per-chunk
    # `timeout` is a liveness check, not a duration cap.
    DEFAULT_MAX_TOTAL_DURATION: float = 1800.0
    # How often to poll the queue before checking worker liveness.
    LIVENESS_POLL_INTERVAL: float = 5.0

    def __init__(
        self,
        nc: "NATS",
        *,
        timeout: float = 120.0,
        max_total_duration: float | None = None,
    ) -> None:
        self._nc = nc
        self._timeout = timeout
        self._max_total_duration = (
            max_total_duration
            if max_total_duration is not None
            else self.DEFAULT_MAX_TOTAL_DURATION
        )
        self._worker_freshness: dict[str, float] = {}
        self._hb_sub: "Subscription | None" = None

    async def start(self) -> None:
        """Subscribe to HB_SUBJECT. Idempotent."""
        if self._hb_sub is None and self.HB_SUBJECT:
            self._hb_sub = await self._nc.subscribe(
                self.HB_SUBJECT, cb=self._on_heartbeat
            )

    async def stop(self) -> None:
        """Unsubscribe from HB_SUBJECT. Idempotent."""
        if self._hb_sub is not None:
            try:
                await self._hb_sub.unsubscribe()
            except nats.errors.Error:
                log.debug(
                    "NatsDriverBase: error unsubscribing heartbeat", exc_info=True
                )
            finally:
                self._hb_sub = None

    async def _on_heartbeat(self, msg: Any) -> None:
        try:
            data = json.loads(msg.data)
            worker_id = data.get("worker_id")
            if not worker_id:
                log.warning("nats_driver_base: heartbeat missing worker_id, ignoring")
                return
            self._worker_freshness[worker_id] = time.monotonic()
        except (json.JSONDecodeError, ValueError):
            log.debug("nats_driver_base: heartbeat parse error", exc_info=True)

    def _any_worker_alive(self) -> bool:
        now = time.monotonic()
        self._worker_freshness = {
            k: v
            for k, v in self._worker_freshness.items()
            if now - v <= self.HB_TTL * 2
        }
        return any(now - ts <= self.HB_TTL for ts in self._worker_freshness.values())

    def is_alive(self, pool_id: str) -> bool:
        del pool_id
        return self._nc.is_connected and self._any_worker_alive()

    async def _wait_for_chunk(
        self,
        queue: asyncio.Queue,
        effective_timeout: float,
        deadline: float,
        subject: str,
    ) -> Any | None:
        """Poll *queue* in short intervals, checking worker liveness between polls.

        Returns the next message, or ``None`` if the per-chunk timeout elapsed
        (last-resort backstop path).  Raises :exc:`WorkerUnavailableError` when
        ``HB_SUBJECT`` is set and no live worker is detected.
        """
        poll = min(self.LIVENESS_POLL_INTERVAL, effective_timeout)
        elapsed_idle = 0.0
        while True:
            try:
                return await asyncio.wait_for(queue.get(), timeout=poll)
            except TimeoutError:
                elapsed_idle += poll
                if self.HB_SUBJECT and not self._any_worker_alive():
                    log.warning(
                        "nats_driver_base: worker unavailable, aborting stream on %s",
                        subject,
                    )
                    raise WorkerUnavailableError(
                        f"worker heartbeat stopped during stream on {subject!r}"
                    )
                if elapsed_idle >= effective_timeout:
                    # Last-resort: 120s backstop (no liveness signal)
                    log.warning(
                        "nats_driver_base: _dict_stream_gen timeout on %s", subject
                    )
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                poll = min(self.LIVENESS_POLL_INTERVAL, remaining)

    async def _dict_stream_gen(
        self,
        subject: str,
        payload_dict: dict,
        *,
        timeout: float | None = None,
        max_total_duration: float | None = None,
    ) -> AsyncIterator[dict]:
        """Publish to subject with ephemeral inbox reply, yield raw dict chunks.

        Two timers govern stream lifetime:
        - ``timeout`` (per-chunk): max idle time between chunks. Reset on every
          arrival, including keepalive chunks (e.g. ``event_type="tool_use"``).
        - ``max_total_duration`` (absolute): hard cap on total stream duration
          regardless of activity. Backstop against a worker that keeps emitting
          keepalives but never reaches a terminal chunk.
        """
        timeout = timeout if timeout is not None else self._timeout
        max_total = (
            max_total_duration
            if max_total_duration is not None
            else self._max_total_duration
        )
        inbox = self._nc.new_inbox()
        queue: asyncio.Queue = asyncio.Queue(maxsize=512)

        async def _on_msg(msg: Any) -> None:
            try:
                queue.put_nowait(msg)
            except asyncio.QueueFull:
                log.warning(
                    "nats_driver_base: _dict_stream_gen inbox queue full,"
                    " dropping chunk on %s",
                    subject,
                )

        sub = await self._nc.subscribe(inbox, cb=_on_msg)
        payload = json.dumps(payload_dict, ensure_ascii=False).encode("utf-8")
        deadline = time.monotonic() + max_total
        try:
            await self._nc.publish(subject, payload, reply=inbox)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    log.warning(
                        "nats_driver_base: _dict_stream_gen absolute deadline (%.0fs) "
                        "exceeded on %s",
                        max_total,
                        subject,
                    )
                    return
                effective_timeout = min(timeout, remaining)
                msg = await self._wait_for_chunk(
                    queue, effective_timeout, deadline, subject
                )
                if msg is None:
                    return
                try:
                    chunk: dict = json.loads(msg.data)
                except (json.JSONDecodeError, ValueError):
                    log.debug("nats_driver_base: chunk parse error", exc_info=True)
                    continue
                yield chunk
                if chunk.get("done", False):
                    return
        finally:
            try:
                await sub.unsubscribe()
            except nats.errors.Error:
                log.debug("nats_driver_base: error unsubscribing inbox", exc_info=True)

    async def _request(
        self, subject: str, payload_dict: dict, *, timeout: float | None = None
    ) -> dict:
        """Simple request-reply, returns parsed JSON dict."""
        timeout = timeout if timeout is not None else self._timeout
        payload = json.dumps(payload_dict, ensure_ascii=False).encode("utf-8")
        reply = await self._nc.request(subject, payload, timeout=timeout)
        return json.loads(reply.data)
