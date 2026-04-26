"""NatsDriverBase — reusable hub-side NATS request-dispatch base.

Provides:
- Heartbeat subscription and worker-freshness tracking
- Ephemeral-inbox streaming (_stream_gen)
- Simple request-reply (_request)

Subclass this to build hub-side drivers (e.g. CliNatsDriver, NatsLlmDriver).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS
    from nats.aio.subscription import Subscription

__all__ = ["NatsDriverBase"]
log = logging.getLogger(__name__)


class NatsDriverBase:
    HB_TTL: float = 30.0
    # Subclasses set this to the heartbeat subject they subscribe to.
    # NatsDriverBase uses it in start()/stop() only if set.
    HB_SUBJECT: str = ""

    def __init__(self, nc: "NATS", *, timeout: float = 120.0) -> None:
        self._nc = nc
        self._timeout = timeout
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
            except Exception:
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
        except Exception:
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

    async def _stream_gen(
        self, subject: str, payload_dict: dict, *, timeout: float | None = None
    ) -> AsyncIterator[dict]:
        """Publish to subject with ephemeral inbox reply, yield raw dict chunks."""
        timeout = timeout if timeout is not None else self._timeout
        inbox = self._nc.new_inbox()
        queue: asyncio.Queue = asyncio.Queue(maxsize=512)

        async def _on_msg(msg: Any) -> None:
            await queue.put(msg)

        sub = await self._nc.subscribe(inbox, cb=_on_msg)
        payload = json.dumps(payload_dict, ensure_ascii=False).encode("utf-8")
        try:
            await self._nc.publish(subject, payload, reply=inbox)
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=timeout)
                except TimeoutError:
                    log.warning("nats_driver_base: _stream_gen timeout on %s", subject)
                    return
                try:
                    chunk: dict = json.loads(msg.data)
                except Exception:
                    log.debug("nats_driver_base: chunk parse error", exc_info=True)
                    continue
                yield chunk
                if chunk.get("done", False):
                    return
        finally:
            try:
                await sub.unsubscribe()
            except Exception:
                log.debug("nats_driver_base: error unsubscribing inbox", exc_info=True)

    async def _request(
        self, subject: str, payload_dict: dict, *, timeout: float | None = None
    ) -> dict:
        """Simple request-reply, returns parsed JSON dict."""
        timeout = timeout if timeout is not None else self._timeout
        payload = json.dumps(payload_dict, ensure_ascii=False).encode("utf-8")
        reply = await self._nc.request(subject, payload, timeout=timeout)
        return json.loads(reply.data)
