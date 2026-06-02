from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from lyra.transport.typing_event import TypingEvent
from lyra.transport.work_scope import WorkScope

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

log = logging.getLogger(__name__)


def is_typing_enabled() -> bool:
    return os.getenv("LYRA_TYPING_ENABLED", "true").lower() == "true"


class TypingPublisher:
    def __init__(self, nc: "NATS", *, enabled: bool | None = None) -> None:
        self._nc = nc
        self._enabled = enabled if enabled is not None else is_typing_enabled()
        self._refcount: dict[tuple[str, str, int], int] = {}
        self._lock = asyncio.Lock()

    async def publish_started(self, scope: WorkScope) -> None:
        if not self._enabled:
            return
        async with self._lock:
            key = (scope.platform, scope.bot_id, scope.scope_id)
            new = self._refcount.get(key, 0) + 1
            if new != 1:
                self._refcount[key] = new
                return  # sub-scope, no wire emit (AC2 ref-count)
            event = TypingEvent(kind="started", scope=scope, ts=time.time())
            ok = await self._publish(event)
            if ok:
                self._refcount[key] = new
            # publish failed: leave refcount at 0 so a later ended() no-ops cleanly

    async def publish_ended(self, scope: WorkScope) -> None:
        if not self._enabled:
            return
        async with self._lock:
            key = (scope.platform, scope.bot_id, scope.scope_id)
            current = self._refcount.get(key, 0)
            if current <= 0:
                log.debug("typing_publisher: ended for absent scope key=%r", key)
                return  # AC3 idempotent no-op (also covers post-restart resilience)
            self._refcount[key] = current - 1
            if self._refcount[key] != 0:
                return
            del self._refcount[key]
            ok = await self._publish(
                TypingEvent(kind="ended", scope=scope, ts=time.time())
            )
            if not ok:
                log.warning(
                    "typing_publisher: ended publish failed, state may diverge key=%r",
                    key,
                )

    @asynccontextmanager
    async def scope(self, work_scope: WorkScope):
        try:
            await self.publish_started(work_scope)
            yield
        finally:
            await self.publish_ended(work_scope)

    async def _publish(self, event: TypingEvent) -> bool:
        subject = f"lyra.typing.{event.scope.platform}.{event.scope.bot_id}"
        try:
            await self._nc.publish(subject, event.model_dump_json().encode("utf-8"))
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("typing_publisher: publish failed: %s", exc)  # AC5 swallow
            return False
