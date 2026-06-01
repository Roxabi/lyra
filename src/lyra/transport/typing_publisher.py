from __future__ import annotations

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

    async def publish_started(self, scope: WorkScope) -> None:
        if not self._enabled:
            return
        key = (scope.platform, scope.bot_id, scope.scope_id)
        new = self._refcount.get(key, 0) + 1
        self._refcount[key] = new
        if new != 1:
            return  # sub-scope, no wire emit (AC2 ref-count)
        await self._publish(TypingEvent(kind="started", scope=scope, ts=time.time()))

    async def publish_ended(self, scope: WorkScope) -> None:
        if not self._enabled:
            return
        key = (scope.platform, scope.bot_id, scope.scope_id)
        current = self._refcount.get(key, 0)
        if current <= 0:
            log.debug("typing_publisher: ended for absent scope key=%r", key)
            return  # AC3 idempotent no-op (also covers post-restart resilience)
        self._refcount[key] = current - 1
        if self._refcount[key] != 0:
            return
        del self._refcount[key]
        await self._publish(TypingEvent(kind="ended", scope=scope, ts=time.time()))

    @asynccontextmanager
    async def scope(self, work_scope: WorkScope):
        try:
            await self.publish_started(work_scope)
            yield
        finally:
            await self.publish_ended(work_scope)

    async def _publish(self, event: TypingEvent) -> None:
        subject = f"lyra.typing.{event.scope.platform}.{event.scope.bot_id}"
        try:
            await self._nc.publish(subject, event.model_dump_json().encode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.warning("typing_publisher: publish failed: %s", exc)  # AC5 swallow
