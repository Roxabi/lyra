"""JetStream publisher for factory.event.* — fire-and-forget, best-effort."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import nats.errors

from roxabi_contracts.event import LyraEvent, per_service_event

if TYPE_CHECKING:
    from nats.js.client import JetStreamContext

log = logging.getLogger(__name__)


class EventPublisher:
    """Publish LyraEvent to the factory-events stream."""

    def __init__(self, js: JetStreamContext) -> None:
        self._js = js

    async def publish(self, event: LyraEvent, *, msg_id: str | None = None) -> bool:
        subject = per_service_event(event.service, event.kind)
        headers = {"Nats-Msg-Id": msg_id} if msg_id else None
        try:
            await self._js.publish(
                subject,
                event.model_dump_json().encode("utf-8"),
                headers=headers,
            )
            log.info("ingress published %s trace_id=%s", subject, event.trace_id)
            return True
        except (nats.errors.Error, OSError, RuntimeError) as exc:
            log.warning("ingress publish failed subject=%s: %s", subject, exc)
            return False